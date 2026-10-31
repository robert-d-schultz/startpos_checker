"""start_pos consistency checks.

RPFM's diagnostics already cover schema-level problems (bad references to
game tables, duplicated keys). These checks cover what they cannot see:
the ID links between start_pos tables after fragment overrides are applied,
campaign scoping, and the per-faction / per-region / per-army rules the
start_pos builder relies on.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass

ERROR, WARN, INFO = "error", "warning", "info"
INT32_MAX = 2**31 - 1
MAX_ARMY_UNITS = 19   # land-unit rows per general; the general's own unit is not listed
MAX_FACTIONS = 1024   # start_pos_factions rows per campaign


@dataclass
class Finding:
    severity: str
    check: str
    table: str
    where: str
    message: str


class Checker:
    # What differs between games in the start_pos tables these checks read.
    TYPE = "Type"                    # start_pos_characters agent type column
    MAX_ARMY_UNITS = MAX_ARMY_UNITS
    SETTLEMENT_REGION_SEV = ERROR    # start_pos_settlements.region that resolves to nothing
    UNOWNED_CAPITAL_SEV = ERROR      # faction_capital region without an owner
    # Character placed in a settlement its faction doesn't own. CA's WH3 combi has one
    # (Lazarghs at Mount Thug) and builds, so a warning there.
    GARRISON_OWNER_SEV = WARN
    # Tables that name a faction by its *key* (faction-absent check).
    FACTION_KEY_TABLES = ("start_pos_technologies", "start_pos_entity_association_faction_regions",
                          "start_pos_region_foreign_slots")

    def __init__(self, data, campaigns):
        self.d = data
        self.campaigns = set(campaigns)
        self.out = []
        t = data.table
        self.factions = t("start_pos_factions")
        self.characters = t("start_pos_characters")
        self.regions = t("start_pos_regions")
        self.settlements = t("start_pos_settlements")
        self.units = t("start_pos_land_units")
        # Effective rows by ID, and IDs that only survive in overridden rows.
        self.faction_by_id = {r["ID"]: r for r in self.factions.rows}
        self.char_by_id = {r["ID"]: r for r in self.characters.rows}
        self.region_by_id = {}
        for r in self.regions.rows:
            self.region_by_id.setdefault(str(r["id"]), r)
        self.settlement_by_id = {str(r["id"]): r for r in self.settlements.rows}

    def add(self, severity, check, table, where, message):
        self.out.append(Finding(severity, check, table, where, message))

    # ------------------------------------------------------------ helpers
    def in_campaign(self, faction_row):
        return faction_row is not None and faction_row["campaign"] in self.campaigns

    def char_campaign_ok(self, char):
        return self.in_campaign(self.faction_by_id.get(char["faction"]))

    def subtype_of(self, c):
        return c["subtype"]

    def text_id_table(self, name):
        """Whether start_pos table `name` has a text "id" (a key, not a number)."""
        return False

    def is_off_map(self, c):
        """A character that doesn't start on the campaign map."""
        return c["is_in_generals_pool"]

    def is_parked(self, c):
        """A position CA uses for characters that aren't really placed on the map."""
        return c["startx"] == 0 and c["starty"] in (0, 1)

    def describe_char(self, c):
        f = self.faction_by_id.get(c["faction"])
        return f"character {c['ID']} ({self.subtype_of(c)}, {f['faction'] if f else 'faction ' + c['faction']})"

    def resolve(self, table, row, column, target_rows, target_name, overridden_ids=(), sev=ERROR):
        """Check a start_pos ID reference against effective rows."""
        v = str(row[column])
        if v == "" or v == "0" and target_name == "start_pos_factions":
            return None
        hit = target_rows.get(v)
        if hit is None:
            if v in overridden_ids:
                self.add(sev, "dangling-id", table, row.where(),
                         f"{column}={v} only matches a row of {target_name} that is overridden by another fragment")
            else:
                self.add(sev, "dangling-id", table, row.where(),
                         f"{column}={v} does not exist in {target_name}")
        return hit

    # ------------------------------------------------------------ run
    def run(self):
        for fn in (self.check_overrides, self.check_id_format, self.check_unique_ids,
                   self.check_campaign_scope, self.check_factions, self.check_regions,
                   self.check_settlements, self.check_characters, self.check_armies,
                   self.check_character_links, self.check_faction_links,
                   self.check_diplomacy, self.check_region_links):
            fn()
        return self.out

    def check_overrides(self):
        for name, t in self.d.tables.items():
            for lose, win in t.overridden:
                key = "|".join(map(str, t.key_of(win)))
                if lose.fragment == win.fragment:
                    self.add(ERROR, "duplicate-key", name, lose.where(),
                             f"key {key} already used at {win.where()}; this row is dropped")
                    continue
                diff = [k for k in lose.values if lose[k] != win[k]]
                what = ", ".join(f"{k}: {lose[k]!r} -> {win[k]!r}" for k in diff) or "identical row"
                self.add(INFO, "override", name, lose.where(),
                         f"key {key} overridden by {win.where()} ({what})")

    def check_id_format(self):
        """start_pos IDs are numbers; several columns store them as I32."""
        for name, t in self.d.tables.items():
            if not name.startswith("start_pos_"):
                continue
            id_cols = [f["name"] for f in t.fields
                       if (f["name"].lower() == "id" and not self.text_id_table(name))
                       or (f["is_reference"] and f["is_reference"][0].startswith("start_pos_")
                           and not self.text_id_table(f["is_reference"][0])
                           and f["is_reference"][1].lower() == "id")
                       or (f["is_reference"] and f["is_reference"][0] == "names")]
            for r in t.all_rows:
                for c in id_cols:
                    v = r[c]
                    if v == "" or v is None:
                        continue
                    try:
                        n = int(v)
                    except (TypeError, ValueError):
                        self.add(ERROR, "id-format", name, r.where(), f"{c}={v!r} is not a number")
                        continue
                    if not 0 <= n <= INT32_MAX and not (c == "id" and t.col(c)["field_type"] == "I64"):
                        self.add(WARN, "id-range", name, r.where(),
                                 f"{c}={v} does not fit in 32 bits (the Assembly Kit defines start_pos IDs "
                                 f"and name references as 32-bit integer/autonumber columns)")

    def check_unique_ids(self):
        """Non-key ID columns that other tables point at must still be unique."""
        for name, col in (("start_pos_regions", "id"), ("start_pos_settlements", "id"),
                          ("start_pos_diplomacy", "key"), ("start_pos_region_slot_templates", "id")):
            t = self.d.table(name)
            seen = {}
            for r in t.rows:
                v = r[col]
                if v in seen:
                    self.add(ERROR, "duplicate-id", name, r.where(),
                             f"{col}={v} is also used by {seen[v].where()}")
                else:
                    seen[v] = r

    def check_campaign_scope(self):
        cam = self.d.table("campaigns")
        cal = self.d.table("start_pos_calendars")
        cam_keys = {r["campaign_name"] for r in cam.rows}
        cal_keys = {r["campaign"] for r in cal.rows}
        for c in sorted(self.campaigns):
            if c not in cal_keys:
                self.add(ERROR, "campaign", "start_pos_calendars", "-", f"no calendar row for campaign {c}")
            if cam.rows and c not in cam_keys:
                self.add(WARN, "campaign", "campaigns", "-", f"campaign {c} has no campaigns row in this pack")
        for name, t in self.d.tables.items():
            if not name.startswith("start_pos_") or not t.col("campaign"):
                continue
            other = Counter(r["campaign"] for r in t.rows if r["campaign"] not in self.campaigns)
            for c, n in other.items():
                self.add(WARN, "campaign-scope", name, "-",
                         f"{n} row(s) for campaign {c!r}, which this pack does not build; they are ignored")

    def check_factions(self):
        seen = {}
        for r in self.factions.rows:
            k = (r["faction"], r["campaign"])
            if r["campaign"] not in self.campaigns:
                continue
            if k in seen:
                self.add(ERROR, "faction-duplicate", "start_pos_factions", r.where(),
                         f"faction {r['faction']} already has start_pos ID {seen[k]['ID']} in {r['campaign']}")
            else:
                seen[k] = r
        for camp, n in sorted(Counter(r["campaign"] for r in self.factions.rows).items()):
            if camp in self.campaigns and n > MAX_FACTIONS:
                self.add(ERROR, "faction-limit", "start_pos_factions", "-",
                         f"campaign {camp} has {n} factions; the game allows at most {MAX_FACTIONS}")

        owned = defaultdict(list)
        capitals = defaultdict(list)
        for r in self.regions.rows:
            if r["campaign"] in self.campaigns and r["owning_faction"] not in ("", "0"):
                owned[r["owning_faction"]].append(r)
                if r["faction_capital"]:
                    capitals[r["owning_faction"]].append(r)
        on_map = defaultdict(list)
        for c in self.characters.rows:
            if not self.is_off_map(c):
                on_map[c["faction"]].append(c)
        hordes = {str(r["general"]) for r in self.d.table("start_pos_horde_details").rows}

        for f in self.factions.rows:
            if f["campaign"] not in self.campaigns:
                continue
            fid, fk = f["ID"], f["faction"]
            caps = capitals.get(fid, [])
            # CA's own combi data has both of these, so they are only informational.
            if len(caps) > 1:
                self.add(INFO, "faction-capital", "start_pos_regions", caps[1].where(),
                         f"{fk} has {len(caps)} capitals: " + ", ".join(r["region"] for r in caps))
            if owned.get(fid) and not caps:
                self.add(INFO, "faction-capital", "start_pos_factions", f.where(),
                         f"{fk} owns {len(owned[fid])} region(s) but none is flagged faction_capital")
            chars = on_map.get(fid, [])
            generals = [c for c in chars if c[self.TYPE] == "general"]
            if not owned.get(fid) and not generals:
                self.add(INFO, "faction-dead", "start_pos_factions", f.where(),
                         f"{fk} owns no region and has no general on the map, so it starts dead")
            elif not owned.get(fid) and not any(c["ID"] in hordes for c in generals):
                self.add(INFO, "faction-landless", "start_pos_factions", f.where(),
                         f"{fk} owns no region and none of its generals has start_pos_horde_details")

    def check_regions(self):
        for r in self.regions.rows:
            if r["campaign"] not in self.campaigns:
                continue
            owner = r["owning_faction"]
            if owner in ("", "0"):
                if r["faction_capital"]:
                    self.add(self.UNOWNED_CAPITAL_SEV, "faction-capital", "start_pos_regions", r.where(),
                             f"{r['region']} is flagged faction_capital but has no owner")
                continue
            f = self.faction_by_id.get(owner)
            if f is None:
                self.add(ERROR, "dangling-id", "start_pos_regions", r.where(),
                         f"{r['region']} owning_faction={owner} does not exist in start_pos_factions")
            elif f["campaign"] != r["campaign"]:
                self.add(ERROR, "campaign-mismatch", "start_pos_regions", r.where(),
                         f"{r['region']} is owned by start_pos faction {owner} ({f['faction']}) of campaign {f['campaign']}")

    def check_settlements(self):
        overridden_region_ids = {str(l["id"]) for l, _ in self.regions.overridden} - set(self.region_by_id)
        per_region = defaultdict(list)
        for s in self.settlements.rows:
            reg = self.resolve("start_pos_settlements", s, "region", self.region_by_id,
                               "start_pos_regions.id", overridden_region_ids, self.SETTLEMENT_REGION_SEV)
            if reg is None:
                continue
            per_region[str(s["region"])].append(s)
            if reg["campaign"] not in self.campaigns:
                continue
            if s["settlement_id"] != "settlement:" + reg["region"]:
                self.add(INFO, "settlement-name", "start_pos_settlements", s.where(),
                         f"{s['settlement_id']} is attached to region {reg['region']}")
            if reg["owning_faction"] not in ("", "0") and not s["primary_building"]:
                self.add(WARN, "settlement-building", "start_pos_settlements", s.where(),
                         f"{s['settlement_id']} is owned but has no primary_building")
        for rid, r in self.region_by_id.items():
            if r["campaign"] not in self.campaigns:
                continue
            n = len(per_region.get(rid, []))
            if n == 0:
                self.add(ERROR, "region-settlement", "start_pos_regions", r.where(),
                         f"{r['region']} (id {rid}) has no start_pos_settlements row")
            elif n > 1:
                self.add(ERROR, "region-settlement", "start_pos_regions", r.where(),
                         f"{r['region']} (id {rid}) has {n} settlements: "
                         + ", ".join(s["settlement_id"] for s in per_region[rid]))

    def check_characters(self):
        overridden = {l["ID"] for l, _ in self.factions.overridden} - set(self.faction_by_id)
        positions = defaultdict(list)
        garrisoned = {r["character"] for r in self.d.table("start_pos_character_to_settlements").rows}
        for c in self.characters.rows:
            f = self.resolve("start_pos_characters", c, "faction", self.faction_by_id,
                             "start_pos_factions", overridden)
            if f is None:
                continue
            if f["campaign"] not in self.campaigns:
                continue
            if self.is_off_map(c):
                continue
            if self.is_parked(c) and c["ID"] not in garrisoned:
                self.add(INFO, "character-position", "start_pos_characters", c.where(),
                         f"{self.describe_char(c)} is on the map at ({c['startx']}, {c['starty']})")
            if c[self.TYPE] == "general" and c["ID"] not in garrisoned and not self.is_parked(c):
                positions[(f["campaign"], c["startx"], c["starty"])].append(c)
        for (_, *pos), cs in positions.items():
            if len(cs) > 1:
                # Several lords of one faction on one spot happens in CA's data; different factions is a clash.
                sev = WARN if len({c["faction"] for c in cs}) > 1 else INFO
                self.add(sev, "character-position", "start_pos_characters", cs[1].where(),
                         f"{len(cs)} generals share position {tuple(pos)}: " + ", ".join(self.describe_char(c) for c in cs))

    def check_armies(self):
        per_general = defaultdict(list)
        for u in self.units.rows:
            per_general[u["general"]].append(u)
        overridden = {l["ID"] for l, _ in self.characters.overridden} - set(self.char_by_id)
        for gid, units in per_general.items():
            g = self.resolve("start_pos_land_units", units[0], "general", self.char_by_id,
                             "start_pos_characters", overridden)
            if g is None or not self.char_campaign_ok(g):
                continue
            if g[self.TYPE] != "general":
                self.add(ERROR, "army", "start_pos_land_units", units[0].where(),
                         f"{len(units)} unit(s) belong to {self.describe_char(g)}, which is a {g[self.TYPE]}, not a general")
            if len(units) > self.MAX_ARMY_UNITS:
                self.add(ERROR, "army-size", "start_pos_land_units", units[self.MAX_ARMY_UNITS].where(),
                         f"{self.describe_char(g)} has {len(units)} units (max {self.MAX_ARMY_UNITS} plus the general)")
            if g["is_in_generals_pool"]:
                self.add(WARN, "army", "start_pos_land_units", units[0].where(),
                         f"{self.describe_char(g)} is in the generals pool but has {len(units)} unit(s)")
            bad = [u for u in units if int(u["soldiers"]) <= 0]
            for u in bad:
                self.add(WARN, "army", "start_pos_land_units", u.where(),
                         f"{u['unit_type']} has soldiers={u['soldiers']}")

    def check_character_links(self):
        overridden = {l["ID"] for l, _ in self.characters.overridden} - set(self.char_by_id)
        links = (("start_pos_character_ancillaries", "character_id"),
                 ("start_pos_character_traits", "character_id"),
                 ("start_pos_character_to_settlements", "character"),
                 ("start_pos_horde_details", "general"),
                 ("start_pos_starting_general_options", "general"))
        for name, col in links:
            for r in self.d.table(name).rows:
                self.resolve(name, r, col, self.char_by_id, "start_pos_characters", overridden)

        # Characters inside a settlement must belong to the faction that owns it.
        for r in self.d.table("start_pos_character_to_settlements").rows:
            c = self.char_by_id.get(r["character"])
            s = self.resolve("start_pos_character_to_settlements", r, "settlement",
                             self.settlement_by_id, "start_pos_settlements.id")
            if c is None or s is None or not self.char_campaign_ok(c):
                continue
            reg = self.region_by_id.get(str(s["region"]))
            if reg and reg["owning_faction"] != c["faction"]:
                owner = self.faction_by_id.get(reg["owning_faction"])
                self.add(self.GARRISON_OWNER_SEV, "garrison-owner", "start_pos_character_to_settlements", r.where(),
                         f"{self.describe_char(c)} is placed in {reg['region']}, owned by "
                         f"{owner['faction'] if owner else 'nobody'}")
            if c["is_in_generals_pool"]:
                self.add(WARN, "garrison-owner", "start_pos_character_to_settlements", r.where(),
                         f"{self.describe_char(c)} is in the generals pool but placed in a settlement")

        for r in self.d.table("start_pos_horde_details").rows:
            c = self.char_by_id.get(str(r["general"]))
            if c is None or not self.char_campaign_ok(c):
                continue
            if c[self.TYPE] != "general":
                self.add(ERROR, "horde", "start_pos_horde_details", r.where(),
                         f"{self.describe_char(c)} has horde details but is a {c[self.TYPE]}")
            if not r["primary_building"]:
                self.add(WARN, "horde", "start_pos_horde_details", r.where(),
                         f"{self.describe_char(c)} horde has no primary_building")

        leaders = defaultdict(list)
        for r in self.d.table("start_pos_starting_general_options").rows:
            c = self.char_by_id.get(str(r["general"]))
            if c is None or not self.char_campaign_ok(c):
                continue
            if c[self.TYPE] != "general":
                self.add(ERROR, "general-option", "start_pos_starting_general_options", r.where(),
                         f"{self.describe_char(c)} is a {c[self.TYPE]}, not a general")
            leaders[(self.faction_by_id[c["faction"]]["campaign"], r["frontend_faction_leader"])].append((r, c))
        for (camp, leader), rows in leaders.items():
            factions = {c["faction"] for _, c in rows}
            if len(factions) > 1:
                self.add(WARN, "general-option", "start_pos_starting_general_options", rows[1][0].where(),
                         f"frontend_faction_leader {leader} is used by {len(factions)} factions: "
                         + ", ".join(self.faction_by_id[f]["faction"] for f in sorted(factions) if f in self.faction_by_id))

    def check_faction_links(self):
        overridden = {l["ID"] for l, _ in self.factions.overridden} - set(self.faction_by_id)
        for name, cols in (("start_pos_past_events", ("source", "target")),
                           ("start_pos_victory_conditions", ("start_pos_faction",))):
            for r in self.d.table(name).rows:
                fs = {col: self.resolve(name, r, col, self.faction_by_id, "start_pos_factions", overridden)
                      for col in cols}
                camps = {f["campaign"] for f in fs.values() if f is not None}
                if camps & self.campaigns and camps - self.campaigns:
                    self.add(ERROR, "campaign-mismatch", name, r.where(), ", ".join(
                        f"{col}={r[col]} is {f['faction']} ({f['campaign']})" for col, f in fs.items() if f))
        # Tables keyed by faction *key*: the faction should be present in the campaign.
        present = {f["faction"] for f in self.factions.rows if f["campaign"] in self.campaigns}
        for name in self.FACTION_KEY_TABLES:
            missing = Counter(r["faction"] for r in self.d.table(name).rows
                              if r["faction"] not in present
                              and (not r.get("campaign") or r["campaign"] in self.campaigns))
            for fk, n in missing.items():
                self.add(INFO, "faction-absent", name, "-",
                         f"{n} row(s) for {fk}, which has no start_pos_factions row in this campaign")

    def check_diplomacy(self):
        overridden = {l["ID"] for l, _ in self.factions.overridden} - set(self.faction_by_id)
        pairs = {}
        for r in self.d.table("start_pos_diplomacy").rows:
            a = self.resolve("start_pos_diplomacy", r, "faction1", self.faction_by_id, "start_pos_factions", overridden)
            b = self.resolve("start_pos_diplomacy", r, "faction2", self.faction_by_id, "start_pos_factions", overridden)
            if not (self.in_campaign(a) or self.in_campaign(b)):
                continue
            if r["faction1"] == r["faction2"]:
                self.add(ERROR, "diplomacy", "start_pos_diplomacy", r.where(),
                         f"faction {r['faction1']} has diplomacy with itself")
                continue
            if a and b and a["campaign"] != b["campaign"]:
                self.add(ERROR, "campaign-mismatch", "start_pos_diplomacy", r.where(),
                         f"{a['faction']} ({a['campaign']}) vs {b['faction']} ({b['campaign']})")
            pairs[(r["faction1"], r["faction2"])] = r
        for (x, y), r in pairs.items():
            o = pairs.get((y, x))
            if o is not None and x < y:
                diff = [k for k in ("stance", "grants_military_access", "grants_trade_agreement",
                                    "non_aggression_pact") if r[k] != o[k]]
                if diff:
                    fx = self.faction_by_id.get(x, {"faction": x})["faction"]
                    fy = self.faction_by_id.get(y, {"faction": y})["faction"]
                    self.add(WARN, "diplomacy", "start_pos_diplomacy", r.where(),
                             f"{fx}/{fy} is defined both ways ({o.where()}) and disagrees on " + ", ".join(diff))

    def check_region_links(self):
        present = {r["region"] for r in self.regions.rows if r["campaign"] in self.campaigns}
        for name in ("start_pos_victory_conditions", "start_pos_entity_association_faction_regions",
                     "start_pos_region_foreign_slots", "start_pos_region_slot_templates"):
            for r in self.d.table(name).rows:
                if r.get("campaign") and r["campaign"] not in self.campaigns:
                    continue
                if r["region"] not in present:
                    # CA's combi data has an entity association for a chaos-map region, so that table is info only.
                    sev = INFO if name == "start_pos_entity_association_faction_regions" else WARN
                    self.add(sev, "region-absent", name, r.where(),
                             f"region {r['region']} has no start_pos_regions row in this campaign")
