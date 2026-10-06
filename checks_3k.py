"""Three Kingdoms checks.

3K's start_pos differs from WH3's: characters are generated from a
`template` (character_generation_templates) instead of a subtype and name
ids, diplomacy is a set of deals (start_pos_diplomacy_deals + simple / complex
deals) instead of faction pairs, armies are a commanding general plus up to
two non-commanding generals and their retinues, and there are no settlement
types or climate rules. The WH3 building, agent, faction-leader and map
checks carry over; this module swaps out what doesn't and adds the 3K-only
tables.
"""
from collections import Counter, defaultdict

from checks import ERROR, INFO, WARN
from game_checks import GameChecker

MAX_RETINUE_UNITS = 6          # land-unit rows per general (one retinue)
MAX_NON_COMMANDING = 2         # non-commanding generals per force
DEAL_TABLES = (("start_pos_diplomacy_simple_deals", "id"), ("start_pos_diplomacy_complex_deals", "deal"))


class ThreeKingdomsChecker(GameChecker):
    TYPE = "type"
    MAX_ARMY_UNITS = MAX_RETINUE_UNITS
    FACTION_KEY_TABLES = ()    # 3K's start_pos_technologies.faction is a start_pos faction ID
    # CA's AK start_pos has 1022 settlements (and their religion / pooled resource rows) and 90
    # technologies left over from regions and factions it deleted, and still builds.
    SETTLEMENT_REGION_SEV = WARN
    UNOWNED_CAPITAL_SEV = WARN  # CA's 8p_start_pos has 2
    GARRISON_OWNER_SEV = ERROR  # 3K's start_pos fails to generate (CA's data never does it)

    def __init__(self, data, campaigns, db, maps=None):
        super().__init__(data, campaigns, db, maps)
        self.templates = db.table("character_generation_templates").lookup("key")

    # ------------------------------------------------------------ hooks
    def subtype_of(self, c):
        t = self.templates.get(c["template"])
        return t["subtype"] if t else c["template"]

    def text_id_table(self, name):
        return name.startswith("start_pos_diplomacy_")

    def is_off_map(self, c):
        return c["is_in_generals_pool"] or not c["start_on_map"]

    def is_parked(self, c):
        # CA's 3K start_pos also parks 246 on-map characters at (1, 1).
        return super().is_parked(c) or (c["startx"], c["starty"]) == (1, 1)

    def _permitted_chains(self, template, tmpl_rows, by_super, expand):
        if not hasattr(self, "_tmpl_super"):
            self._tmpl_super = defaultdict(set)
            for r in self.db.table("slot_template_to_building_superchain_junctions").rows:
                self._tmpl_super[r["slot_template"]].add(r["building_superchain"])
        return set().union(*(by_super.get(s, set()) for s in self._tmpl_super.get(template, ())))

    # ------------------------------------------------------------ run
    def run(self):
        super().run()
        self.check_deals()
        self.check_simple_deals()
        self.check_forces()
        self.check_character_links_3k()
        self.check_faction_links_3k()
        self.check_region_links_3k()
        return self.out

    def check_settlement_types(self):
        pass    # 3K has no settlement types

    def check_names(self):
        pass    # names come from the template; RPFM's diagnostics report unknown templates

    def is_placeholder(self, c, fk):
        # A character whose template isn't loaded can't be judged; RPFM reports the template.
        return c["template"] in self.templates and super().is_placeholder(c, fk)

    # ------------------------------------------------------------ helpers
    def _overridden(self, t, col, live):
        return {str(l[col]) for l, _ in t.overridden} - set(live)

    def _resolve_chars(self, name, cols):
        """Resolve character ID columns of `name`; rows -> {col: character row or None}."""
        overridden = self._overridden(self.characters, "ID", self.char_by_id)
        out = []
        for r in self.d.table(name).rows:
            out.append((r, {col: self.resolve(name, r, col, self.char_by_id, "start_pos_characters", overridden)
                            for col in cols}))
        return out

    def _resolve_factions(self, name, cols):
        overridden = self._overridden(self.factions, "ID", self.faction_by_id)
        out = []
        for r in self.d.table(name).rows:
            out.append((r, {col: self.resolve(name, r, col, self.faction_by_id, "start_pos_factions", overridden)
                            for col in cols}))
        return out

    def _campaign_of_char(self, c):
        f = self.faction_by_id.get(c["faction"]) if c else None
        return f["campaign"] if f else None

    def _mismatch(self, name, r, rows, campaign_of):
        """Rows linking things of different campaigns, when one of them is being built."""
        camps = {col: campaign_of(x) for col, x in rows.items() if x is not None}
        if len(set(camps.values())) > 1 and set(camps.values()) & self.campaigns:
            self.add(ERROR, "campaign-mismatch", name, r.where(),
                     ", ".join(f"{col}={r[col]} is in {camp}" for col, camp in camps.items()))
            return True
        return False

    # ------------------------------------------------------------ diplomacy
    def check_deals(self):
        """Every start_pos_diplomacy_deals row needs a simple (or complex) deal of the same id,
        and every simple deal a deals row. The AK's start_pos_diplomacy_deals has 35 deals with
        no simple deal (CA's hidden treaties); copied over, they make the start_pos build fail."""
        deals = self.d.table("start_pos_diplomacy_deals")
        deal_ids = {r["id"] for r in deals.rows}
        backed = {r[col] for t, col in DEAL_TABLES for r in self.d.table(t).rows}
        orphans = [r for r in deals.rows if r["id"] not in backed]
        for r in orphans:
            self.add(ERROR, "diplomacy-deal", "start_pos_diplomacy_deals", r.where(),
                     f"deal {r['id']} ({r['negotiation_type']}) has no start_pos_diplomacy_simple_deals row; "
                     f"remove it, the start_pos build fails on it")
        if len(orphans) > 1:
            self.add(INFO, "diplomacy-deal", "start_pos_diplomacy_deals", "-",
                     f"{len(orphans)} deals without a simple deal (the Assembly Kit's own "
                     f"start_pos_diplomacy_deals has 35 of these; copy only the deals your simple deals use)")
        for t, col in DEAL_TABLES:
            for r in self.d.table(t).rows:
                if r[col] not in deal_ids:
                    self.add(ERROR, "diplomacy-deal", t, r.where(),
                             f"deal {r[col]} has no start_pos_diplomacy_deals row")
        overridden = self._overridden(deals, "id", deal_ids)
        by_id = {r["id"]: r for r in deals.rows}
        for r in self.d.table("start_pos_diplomacy_deal_orderings").rows:
            self.resolve("start_pos_diplomacy_deal_orderings", r, "deal", by_id,
                         "start_pos_diplomacy_deals", overridden)

    def check_simple_deals(self):
        name = "start_pos_diplomacy_simple_deals"
        simple = {r["id"]: r for r in self.d.table(name).rows}
        campaign_of = lambda f: f["campaign"]
        for r, fs in self._resolve_factions(name, ("proposer", "recipient")):
            if self._mismatch(name, r, fs, campaign_of):
                continue
            if r["proposer"] == r["recipient"] and self.in_campaign(fs["proposer"]):
                # CA's automatic treaties (e.g. the emperor token holder) are proposed to self.
                self.add(INFO, "diplomacy", name, r.where(),
                         f"deal {r['id']} has {fs['proposer']['faction']} as proposer and recipient")
        overridden = self._overridden(self.d.table(name), "id", simple)
        for t, cols in (("start_pos_diplomacy_simple_deal_alliance_parameters", ("alliance_faction_parameter",)),
                        ("start_pos_diplomacy_simple_deal_faction_parameters", ("faction_parameter",)),
                        ("start_pos_diplomacy_simple_deal_alliance_oath_parameters", ()),
                        ("start_pos_diplomacy_simple_deal_deal_parameters", ()),
                        ("start_pos_diplomacy_simple_deal_value_parameters", ())):
            for r, fs in self._resolve_factions(t, cols):
                deal = self.resolve(t, r, "deal", simple, name, overridden)
                if deal is None:
                    continue
                owner = self.faction_by_id.get(str(deal["proposer"]))
                for col, f in fs.items():
                    if f is not None and owner is not None and f["campaign"] != owner["campaign"] \
                            and {f["campaign"], owner["campaign"]} & self.campaigns:
                        self.add(ERROR, "campaign-mismatch", t, r.where(),
                                 f"{col}={r[col]} is {f['faction']} ({f['campaign']}) but deal {deal['id']} "
                                 f"is proposed by {owner['faction']} ({owner['campaign']})")

    # ------------------------------------------------------------ forces
    def check_forces(self):
        """A force is a commanding general plus up to two non-commanding generals; every one
        of them has to be an on-map general of the commander's faction."""
        name = "start_pos_non_commanding_generals"
        rows = self._resolve_chars(name, ("general_commanding_force", "non_commanding_general"))
        commanders = {str(r["general_commanding_force"]) for r, _ in rows}
        per_force = defaultdict(list)
        for r, cs in rows:
            cmd, sub = cs["general_commanding_force"], cs["non_commanding_general"]
            if cmd is None or sub is None or not self.char_campaign_ok(cmd):
                continue
            if self._mismatch(name, r, cs, self._campaign_of_char):
                continue
            per_force[cmd["ID"]].append(r)
            for role, c in (("commanding", cmd), ("non-commanding", sub)):
                if c[self.TYPE] != "general":
                    self.add(ERROR, "force", name, r.where(),
                             f"{role} {self.describe_char(c)} is a {c[self.TYPE]}, not a general")
            # Only the commander has to be on the map: CA has 18 non-commanding generals that
            # are not start_on_map (2 of them in the pool).
            if self.is_off_map(cmd):
                self.add(ERROR, "force", name, r.where(),
                         f"commanding {self.describe_char(cmd)} is in the generals pool or not start_on_map")
            if sub["faction"] != cmd["faction"]:
                self.add(ERROR, "force", name, r.where(),
                         f"{self.describe_char(sub)} serves under {self.describe_char(cmd)} of another faction")
            if sub["ID"] in commanders:
                self.add(ERROR, "force", name, r.where(),
                         f"{self.describe_char(sub)} is a non-commanding general but also commands a force")
            if sub["ID"] == cmd["ID"]:
                self.add(ERROR, "force", name, r.where(), f"{self.describe_char(cmd)} serves under itself")
        for cid, rs in per_force.items():
            if len(rs) > MAX_NON_COMMANDING:
                self.add(ERROR, "force", name, rs[MAX_NON_COMMANDING].where(),
                         f"{self.describe_char(self.char_by_id[cid])} has {len(rs)} non-commanding generals "
                         f"(max {MAX_NON_COMMANDING})")

        for t, col in (("start_pos_non_commanding_captains", "general_commanding_force"),
                       ("start_pos_captain_retinue_unit_modifiers", "force_commander")):
            for r, cs in self._resolve_chars(t, (col,)):
                c = cs[col]
                if c is None or not self.char_campaign_ok(c):
                    continue
                if c[self.TYPE] != "general" or self.is_off_map(c):
                    self.add(ERROR, "force", t, r.where(),
                             f"{col} {self.describe_char(c)} is not a general on the map")
                elif c["ID"] not in commanders and t == "start_pos_captain_retinue_unit_modifiers" \
                        and not any(str(x["general_commanding_force"]) == c["ID"]
                                    for x in self.d.table("start_pos_non_commanding_captains").rows):
                    self.add(WARN, "force", t, r.where(),
                             f"{self.describe_char(c)} has captain retinue modifiers but no captains")

    # ------------------------------------------------------------ characters
    def check_character_links_3k(self):
        for name, cols, paired in (
                ("start_pos_character_retinue_unit_modifiers", ("character",), False),
                ("start_pos_character_retinue_unit_stats_bonuses", ("character",), False),
                ("start_pos_character_skills_selection_overrides", ("start_pos_character_key",), False),
                ("start_pos_historical_characters_past_experiences", ("character",), False),
                ("start_pos_family_relationships", ("character", "related_to"), True),
                ("start_pos_character_relationship_triggers", ("source", "target"), True)):
            for r, cs in self._resolve_chars(name, cols):
                if not paired or None in cs.values():
                    continue
                if self._mismatch(name, r, cs, self._campaign_of_char):
                    continue
                a, b = cs.values()
                if a["ID"] == b["ID"] and self.char_campaign_ok(a):
                    self.add(WARN, "character-link", name, r.where(),
                             f"{self.describe_char(a)} is linked to itself")

        name = "start_pos_character_employment_history_entries"
        for r, cs in self._resolve_chars(name, ("character",)):
            f = self.resolve(name, r, "faction", self.faction_by_id, "start_pos_factions",
                             self._overridden(self.factions, "ID", self.faction_by_id))
            c = cs["character"]
            if c is not None and f is not None and self._campaign_of_char(c) != f["campaign"] \
                    and {self._campaign_of_char(c), f["campaign"]} & self.campaigns:
                self.add(ERROR, "campaign-mismatch", name, r.where(),
                         f"{self.describe_char(c)} ({self._campaign_of_char(c)}) has employment history "
                         f"with {f['faction']} ({f['campaign']})")

    # ------------------------------------------------------------ factions / regions
    def check_faction_links_3k(self):
        overridden = self._overridden(self.factions, "ID", self.faction_by_id)
        for name, col in (("start_pos_technologies", "faction"),
                          ("start_pos_world_power_tokens", "start_pos_faction")):
            for r in self.d.table(name).rows:
                fs = {col: self.resolve(name, r, col, self.faction_by_id, "start_pos_factions", overridden,
                                        WARN if name == "start_pos_technologies" else ERROR)}
                f = fs[col]
                if f is not None and r.get("campaign") and f["campaign"] != r["campaign"] \
                        and {f["campaign"], r["campaign"]} & self.campaigns:
                    self.add(ERROR, "campaign-mismatch", name, r.where(),
                             f"{col}={r[col]} is {f['faction']} of {f['campaign']}, but the row is for {r['campaign']}")

    def check_region_links_3k(self):
        overridden = {str(l["id"]) for l, _ in self.regions.overridden} - set(self.region_by_id)
        religion = defaultdict(float)
        first = {}
        for name, col in (("start_pos_region_pooled_resources", "region"),
                          ("start_pos_region_religions", "region"),
                          ("start_pos_regions_to_unit_resources", "key")):
            for r in self.d.table(name).rows:
                reg = self.resolve(name, r, col, self.region_by_id, "start_pos_regions.id", overridden,
                                   self.SETTLEMENT_REGION_SEV)
                if name == "start_pos_region_religions" and reg is not None and reg["campaign"] in self.campaigns:
                    religion[str(r[col])] += float(r["percentage"])
                    first.setdefault(str(r[col]), r)
        for rid, total in religion.items():
            if total > 100.001:
                self.add(WARN, "region-religion", "start_pos_region_religions", first[rid].where(),
                         f"{self.region_by_id[rid]['region']} religions add up to {total:g}%")
