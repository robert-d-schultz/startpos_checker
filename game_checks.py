"""Checks that need the game's own tables and the compiled campaign map.

- map:        characters inside the map's hex grid; start_pos regions
              matching the regions the map actually has.
- buildings:  every settlement / horde building against the slot templates,
              culture variants, chain availabilities and settlement types that
              decide whether the owning faction can have it.
- characters: agent subtypes, units and name groups against the faction;
              the faction_leader ministerial position.

Every rule here was run against CA's own wh3_main_combi start_pos first;
rules CA's data breaks are warnings, not errors (see README).
"""
from collections import Counter, defaultdict

from checks import ERROR, INFO, WARN, Checker

SETTLEMENT_SLOTS = (("primary", ("primary_building",)),
                    ("port", ("port_building",)),
                    ("secondary", tuple(f"building{i}" for i in range(1, 6))))
HORDE_COLUMNS = ("primary_building",) + tuple(f"secondary_building_{i}" for i in range(1, 11))
# campaign_group_member_criteria_<table> columns a faction can be matched on at start.
GROUP_CRITERIA = (("factions", "faction"), ("subcultures", "subculture"),
                  ("cultures", "culture"), ("campaigns", "campaign"))
FACTION_LEADER = "faction_leader"   # ministerial_positions key of a faction's leader


def _match(row, faction, subculture, culture, campaign=None, cols=("faction", "subculture", "culture")):
    """Culture-variant style matching: an empty column matches anything."""
    want = dict(zip(cols, (faction, subculture, culture)))
    if any(row.get(c) and row.get(c) != v for c, v in want.items()):
        return False
    return not (campaign and row.get("campaign") and row.get("campaign") != campaign)


class GameChecker(Checker):
    def __init__(self, data, campaigns, db, maps=None):
        """`maps`: campaign -> (MapData, None), or (None, why it couldn't be read)."""
        super().__init__(data, campaigns)
        self.db = db
        self.maps = maps or {}
        self.garrisoned = {r["character"] for r in data.table("start_pos_character_to_settlements").rows}
        fdb = db.table("factions")
        self.faction_db = fdb.lookup("key")
        self.subculture_culture = {r["subculture"]: r["culture"] for r in db.table("cultures_subcultures").rows}
        self.permitted_agents = {(r["faction"], r["agent"], r["subtype"])
                                 for r in db.table("faction_agent_permitted_subtypes").rows if not r.get("mod_disabled")}

    def is_placeholder(self, c, fk):
        """A character whose subtype the faction can't have (e.g. a stand-in lord)."""
        return (fk, c[self.TYPE], self.subtype_of(c)) not in self.permitted_agents

    def run(self):
        super().run()
        for camp, (m, note) in sorted(self.maps.items()):
            if m is not None:
                self.check_map_characters(camp, m)
                self.check_map_regions(camp, m)
            elif note:
                self.add(WARN, "map", "campaigns", "-", note)
        self.check_buildings()
        self.check_settlement_types()
        self.check_horde_buildings()
        self.check_agents_and_units()
        self.check_names()
        self.check_faction_leaders()
        return self.out

    # ------------------------------------------------------------ helpers
    def owner_of(self, faction_id):
        f = self.faction_by_id.get(faction_id)
        if f is None or f["campaign"] not in self.campaigns:
            return None
        return f["faction"]

    def culture_of(self, faction_key):
        f = self.faction_db.get(faction_key)
        if f is None:
            return None
        return faction_key, f["subculture"], self.subculture_culture.get(f["subculture"])

    def campaign_chars(self):
        for c in self.characters.rows:
            f = self.faction_by_id.get(c["faction"])
            if f is not None and f["campaign"] in self.campaigns:
                yield c, f["faction"]

    # ------------------------------------------------------------ map
    def check_map_characters(self, camp, m):
        for c, _ in self.campaign_chars():
            if self.faction_by_id[c["faction"]]["campaign"] != camp:
                continue
            if self.is_off_map(c) or c["ID"] in self.garrisoned:
                continue
            x, y = c["startx"], c["starty"]
            if not m.contains(x, y):
                self.add(ERROR, "map-bounds", "start_pos_characters", c.where(),
                         f"{self.describe_char(c)} at ({x}, {y}) is outside the map's hex grid "
                         f"(0..{m.width - 1}, 0..{m.height - 1} in {m.path})")

    def check_map_regions(self, camp, m):
        settled = {r["settlement_id"] for r in self.db.table("campaign_map_settlements").rows}
        have = {}
        for r in self.regions.rows:
            if r["campaign"] == camp:
                have[r["region"]] = r
        for key, r in sorted(have.items()):
            if key not in m.regions:
                self.add(ERROR, "map-region", "start_pos_regions", r.where(),
                         f"{key} is not a region of the map ({m.path})")
        for key, mr in sorted(m.regions.items()):
            # flag 1 = sea region; land regions with a map settlement need a start_pos row
            if mr.flag == 0 and ("settlement:" + key) in settled and key not in have:
                self.add(ERROR, "map-region", "start_pos_regions", "-",
                         f"map region {key} has settlement:{key} but no start_pos_regions row in {camp}")

    # ------------------------------------------------------------ buildings
    def _building_tables(self):
        if hasattr(self, "_bt"):
            return self._bt
        db = self.db
        levels = db.table("building_levels").lookup("level_name")
        chains = db.table("building_chains").lookup("key")
        by_super = defaultdict(set)
        for k, r in chains.items():
            by_super[r["building_superchain"]].add(k)
        set_items = db.table("building_chain_set_items").index("set")
        parent = {r["key"]: r["parent_set"] for r in db.table("building_chain_sets").rows}
        tmpl_rows = db.table("slot_template_permitted_building_chains").index("slot_template")
        memo_set, memo_tmpl = {}, {}

        def expand(rows, base=()):
            out = set(base)
            for r in sorted(rows, key=lambda r: bool(r["remove"])):   # adds, then removes
                got = set()
                if r.get("chain"):
                    got.add(r["chain"])
                if r.get("super_chain"):
                    got |= by_super.get(r["super_chain"], set())
                if r.get("chain_set"):
                    got |= expand_set(r["chain_set"])
                out = out - got if r["remove"] else out | got
            return out

        def expand_set(key):
            if key not in memo_set:
                memo_set[key] = set()   # cycle guard
                base = expand_set(parent[key]) if parent.get(key) else ()
                memo_set[key] = expand(set_items.get(key, []), base)
            return memo_set[key]

        def permitted(template):
            if template not in memo_tmpl:
                memo_tmpl[template] = self._permitted_chains(template, tmpl_rows, by_super, expand)
            return memo_tmpl[template]

        climate_rules = defaultdict(lambda: ([], []))   # chain -> (allowed climates, forbidden climates)
        for r in db.table("building_chain_climate_restrictions").rows:
            climate_rules[r["building_chain"]][0 if r["inclusive"] else 1].append(r["climate"])
        stj = defaultdict(dict)
        for r in db.table("settlement_type_to_building_chains_junctions").rows:
            stj[r["settlement_type"]][r["building_chain"]] = bool(r["exclude"])
        self._bt = dict(
            levels=levels, permitted=permitted, stj=stj, climate_rules=climate_rules,
            climate={r["settlement_id"]: r["climate_type"] for r in db.table("campaign_map_settlements").rows},
            variants=db.table("building_culture_variants").index("building"),
            avail_sets=db.table("building_chain_availability_sets").index("building_chain"),
            avail=db.table("building_chain_availabilities").index("set_id"),
            required=db.table("building_level_required_buildings").index("building_level"),
        )
        return self._bt

    def _permitted_chains(self, template, tmpl_rows, by_super, expand):
        """The building chains slot template `template` permits."""
        return expand(tmpl_rows.get(template, []))

    def _ownership_problems(self, level_key, chain, cu, campaign):
        """Why faction `cu` = (faction, subculture, culture) can't have this building in `campaign`, if it can't."""
        bt = self._building_tables()
        rows = [r for r in bt["variants"].get(level_key, []) if _match(r, *cu)]
        if not rows:
            return f"has no building_culture_variants row for {cu[0]} ({cu[1]} / {cu[2]})"
        if all(r["disables"] for r in rows):
            return f"is disabled for {cu[0]} by building_culture_variants"
        sets = [r["id"] for r in bt["avail_sets"].get(chain, [])]
        if sets and not any(_match(a, *cu, campaign=campaign,
                                   cols=("faction", "sub_culture", "culture"))
                            for s in sets for a in bt["avail"].get(s, [])):
            return (f"chain {chain} is in availability set(s) {', '.join(sets[:3])} "
                    f"but none allows {cu[0]} ({cu[1]} / {cu[2]})")
        return None

    def check_buildings(self):
        bt = self._building_tables()
        templates = defaultdict(lambda: defaultdict(list))
        for r in self.d.table("start_pos_region_slot_templates").rows:
            if r["campaign"] in self.campaigns:
                templates[(r["campaign"], r["region"])][r["slot_type"]].append(r["slot_template"])
        for r in self.regions.rows:
            if r["campaign"] in self.campaigns and "primary" not in templates[(r["campaign"], r["region"])]:
                self.add(ERROR, "slot-template", "start_pos_region_slot_templates", r.where(),
                         f"{r['region']} has no primary slot template")

        for s in self.settlements.rows:
            reg = self.region_by_id.get(str(s["region"]))
            if reg is None or reg["campaign"] not in self.campaigns:
                continue
            region = reg["region"]
            owner = self.owner_of(reg["owning_faction"])
            cu = self.culture_of(owner) if owner else None
            if owner and cu is None:
                self.add(WARN, "building-owner", "start_pos_settlements", s.where(),
                         f"{region} owner {owner} is not in the factions table; its buildings were not checked")
            placed = [(slot, col, s[col]) for slot, cols in SETTLEMENT_SLOTS for col in cols if s[col]]
            chains_here = defaultdict(list)
            for slot, col, b in placed:
                lvl = bt["levels"].get(b)
                if lvl is None:
                    continue    # RPFM's diagnostics report unknown building levels
                chain = lvl["chain"]
                sev = ERROR if col == "primary_building" else WARN
                chains_here[chain].append(col)
                tmpls = templates[(reg["campaign"], region)].get(slot, [])
                if not tmpls:
                    self.add(sev, "building-slot", "start_pos_settlements", s.where(),
                             f"{region} {col}={b}, but the region has no {slot} slot template")
                elif not any(chain in bt["permitted"](t) for t in tmpls):
                    self.add(sev, "building-slot", "start_pos_settlements", s.where(),
                             f"{region} {col}={b}: chain {chain} is not permitted by its {slot} slot template(s) "
                             + ", ".join(tmpls))
                if cu:
                    why = self._ownership_problems(b, chain, cu, reg["campaign"])
                    if why:
                        self.add(sev, "building-owner", "start_pos_settlements", s.where(),
                                 f"{region} {col}={b} {why}")
                st = s.get("settlement_type")
                why = self._type_forbids(st, chain) if st else None
                if why:
                    self.add(sev, "building-settlement-type", "start_pos_settlements", s.where(),
                             f"{region} {col}={b}: chain {chain} {why}")
                climate = bt["climate"].get(s["settlement_id"])
                allowed, forbidden = bt["climate_rules"].get(chain, ([], []))
                if climate and ((allowed and climate not in allowed) or climate in forbidden):
                    self.add(sev, "building-climate", "start_pos_settlements", s.where(),
                             f"{region} {col}={b}: chain {chain} can't be built on {climate} "
                             f"(building_chain_climate_restrictions)")
                if lvl["only_in_capital"] and not reg["faction_capital"]:
                    self.add(sev, "building-capital", "start_pos_settlements", s.where(),
                             f"{region} {col}={b} is only_in_capital, but {region} is not a faction capital")
                built = {x for _, _, x in placed}
                for rq in bt["required"].get(b, []):
                    if rq["required"] not in built:
                        # CA's combi data has one of these (pools_of_despair farm), so a warning.
                        self.add(WARN, "building-required", "start_pos_settlements", s.where(),
                                 f"{region} {col}={b} requires {rq['required']}, which the settlement doesn't have")
            for chain, cols in chains_here.items():
                if len(cols) > 1:
                    sev = ERROR if "primary_building" in cols else WARN
                    self.add(sev, "building-duplicate-chain", "start_pos_settlements", s.where(),
                             f"{region} has chain {chain} {len(cols)} times ({', '.join(cols)})")

    def _type_forbids(self, settlement_type, chain):
        """Why settlement_type_to_building_chains_junctions rules chain out, if it does."""
        rule = self._building_tables()["stj"].get(settlement_type)
        if not rule:
            return None
        if rule.get(chain) is True:
            return f"is excluded for settlement_type {settlement_type}"
        if chain not in rule and any(v is False for v in rule.values()):
            return f"is not among the chains settlement_type {settlement_type} allows"
        return None

    def _faction_settlement_types(self):
        """(campaign, faction) -> {settlement type: campaign group} for factions whose campaign
        groups carry settlement type sets (campaign_group_settlement_type_sets).
        A group member matches when every criteria column it has (faction,
        subculture, culture, campaign) contains the faction's value. Members with
        none of those columns can't be judged at start and are skipped; other
        criteria (e.g. Glottkin's pooled resource) are taken as met."""
        if hasattr(self, "_fst"):
            return self._fst
        db = self.db
        group_sets = defaultdict(list)
        for r in db.table("campaign_group_settlement_type_sets").rows:
            group_sets[r["campaign_group"]].append(r["settlement_type_sets"])
        set_types = defaultdict(list)
        for r in db.table("settlement_type_sets_to_settlement_types").rows:
            set_types[r["set"]].append(r["settlement_type"])
        member_groups = defaultdict(set)
        for r in db.table("campaign_group_members").rows:
            if r["group"] in group_sets:
                member_groups[r["id"]].add(r["group"])
        criteria = defaultdict(lambda: defaultdict(set))   # member -> column -> values
        for table, col in GROUP_CRITERIA:
            for r in db.table(f"campaign_group_member_criteria_{table}").rows:
                if r["member"] in member_groups:
                    criteria[r["member"]][col].add(r[col])
        out = defaultdict(dict)
        owners = {(r["campaign"], self.owner_of(r["owning_faction"])) for r in self.regions.rows}
        for camp, fk in owners:
            cu = self.culture_of(fk) if fk else None
            if cu is None:
                continue
            have = dict(zip(("faction", "subculture", "culture"), cu))
            for member, groups in member_groups.items():
                crit = criteria.get(member)
                if not crit or not any(c in crit for c in have):
                    continue
                if any(have[c] not in crit[c] for c in have if c in crit):
                    continue
                if "campaign" in crit and camp not in crit["campaign"]:
                    continue
                for g in groups:
                    for s in group_sets[g]:
                        for t in set_types[s]:
                            out[(camp, fk)].setdefault(t, g)
        self._fst = out
        return out

    def check_settlement_types(self):
        """A settlement whose owner is in a campaign group with settlement type sets
        (Warriors of Chaos, Chaos Dwarfs, Norsca, Daemons, Nagash, ...) needs one of
        that group's types in start_pos_settlements.settlement_type. An empty one
        crashes the build in WORLD::WORLD's region loop."""
        bt = self._building_tables()
        fst = self._faction_settlement_types()
        for s in self.settlements.rows:
            reg = self.region_by_id.get(str(s["region"]))
            if reg is None or reg["campaign"] not in self.campaigns:
                continue
            owner = self.owner_of(reg["owning_faction"])
            allowed = fst.get((reg["campaign"], owner))
            st = s["settlement_type"]
            region = reg["region"]
            if not allowed:
                if owner and st:
                    self.add(INFO, "settlement-type", "start_pos_settlements", s.where(),
                             f"{region} has settlement_type {st}, but its owner {owner} is in no "
                             f"campaign group with settlement types")
                continue
            if st in allowed:
                continue
            groups = sorted(set(allowed.values()))
            pb = s["primary_building"]
            lvl = bt["levels"].get(pb) if pb else None
            fits = [t for t in allowed if lvl and not self._type_forbids(t, lvl["chain"])]
            want = " or ".join(fits or allowed)
            what = f"settlement_type {st} isn't one of its types" if st else "settlement_type is empty"
            self.add(ERROR, "settlement-type", "start_pos_settlements", s.where(),
                     f"{region} is owned by {owner} (campaign group {', '.join(groups)}) but its "
                     f"{what}; needs {want}" + (f" (primary_building {pb})" if fits else ""))

    def check_horde_buildings(self):
        bt = self._building_tables()
        for r in self.d.table("start_pos_horde_details").rows:
            c = self.char_by_id.get(str(r["general"]))
            if c is None:
                continue
            owner = self.owner_of(c["faction"])
            cu = self.culture_of(owner) if owner else None
            if cu is None:
                continue
            for col in HORDE_COLUMNS:
                b = r.get(col)
                lvl = bt["levels"].get(b) if b else None
                if lvl is None:
                    continue
                why = self._ownership_problems(b, lvl["chain"], cu, self.faction_by_id[c["faction"]]["campaign"])
                if why:
                    self.add(ERROR if col == "primary_building" else WARN, "building-owner",
                             "start_pos_horde_details", r.where(),
                             f"{self.describe_char(c)} horde {col}={b} {why}")

    # ------------------------------------------------------------ characters
    def check_agents_and_units(self):
        groups = defaultdict(set)
        for r in self.db.table("units_to_groupings_military_permissions").rows:
            groups[r["military_group"]].add(r["unit"])
        units_of = defaultdict(list)
        for u in self.units.rows:
            units_of[u["general"]].append(u)

        not_permitted = defaultdict(list)     # (Type, subtype) -> [(faction, char)]
        unknown = Counter()
        bad_units = defaultdict(Counter)      # faction -> unit -> rows
        first_row = {}
        for c, fk in self.campaign_chars():
            f = self.faction_db.get(fk)
            if f is None:
                unknown[fk] += 1
                continue
            if self.is_placeholder(c, fk):
                not_permitted[(c[self.TYPE], self.subtype_of(c))].append((fk, c))
                continue   # a placeholder lord's army isn't the faction's real roster; don't check it
            for u in units_of.get(c["ID"], []):
                if u["unit_type"] not in groups[f["military_group"]]:
                    bad_units[fk][u["unit_type"]] += 1
                    first_row.setdefault((fk, u["unit_type"]), u)

        caps = {r["key"]: r["cap"] for r in self.db.table("agent_subtypes").rows if r.get("cap")}
        used = defaultdict(list)
        for c, fk in self.campaign_chars():
            if self.subtype_of(c) in caps:
                used[(fk, self.subtype_of(c))].append(c)
        for (fk, sub), cs in sorted(used.items()):
            if caps[sub] > 0 and len(cs) > caps[sub]:
                self.add(WARN, "agent-cap", "start_pos_characters", cs[caps[sub]].where(),
                         f"{fk} has {len(cs)} {sub} characters; agent_subtypes.cap is {caps[sub]}")

        for (typ, sub), rows in sorted(not_permitted.items()):
            factions = sorted({fk for fk, _ in rows})
            skipped = sum(len(units_of.get(c["ID"], [])) for _, c in rows)
            where = rows[0][1].where() if len(rows) == 1 else f"{len(rows)} characters"
            msg = (f"{typ} subtype {sub} is not in faction_agent_permitted_subtypes for "
                   + (", ".join(factions) if len(factions) <= 6 else f"{len(factions)} factions ({', '.join(factions[:4])}, ...)"))
            msg += "; their names" + (f" and {skipped} unit(s)" if skipped else "") + " were not checked"
            self.add(WARN, "agent-permission", "start_pos_characters", where, msg)
        for fk, n in sorted(unknown.items()):
            self.add(INFO, "agent-permission", "start_pos_characters", "-",
                     f"{n} character(s) of {fk}, which is not in the factions table of this pack's dependencies")
        for fk, units in sorted(bad_units.items()):
            grp = self.faction_db[fk]["military_group"]
            u0 = first_row[(fk, next(iter(units)))]
            self.add(WARN, "unit-permission", "start_pos_land_units", u0.where(),
                     f"{fk} (military group {grp}) has units outside its roster: "
                     + ", ".join(f"{u} x{n}" if n > 1 else u for u, n in units.items()))

    def check_names(self):
        names = {str(r["id"]): r for r in self.db.table("names").rows}
        for r in self.db.ak_table("names").rows:
            names.setdefault(str(r["id"]), r)
        sub_group = {r["key"]: r.get("names_group") for r in self.db.ak_table("agent_subtypes").rows}
        for r in self.db.table("agent_subtypes").rows:
            if r.get("names_group"):
                sub_group[r["key"]] = r["names_group"]
        for c, fk in self.campaign_chars():
            f = self.faction_db.get(fk)
            if f is None or self.is_placeholder(c, fk):
                continue
            expected = sub_group.get(c["subtype"]) or f["name_group"]
            source = "agent_subtypes.names_group" if sub_group.get(c["subtype"]) else f"{fk}'s factions.name_group"
            for col in ("Name", "Surname"):
                v = c[col]
                n = names.get(str(v)) if v else None
                if n is None:
                    continue    # RPFM's diagnostics report unknown name ids
                if n["names_group"] != expected:
                    self.add(WARN, "name-group", "start_pos_characters", c.where(),
                             f"{self.describe_char(c)} {col}={v} is from {n['names_group']}; "
                             f"{source} is {expected}")

    def check_faction_leaders(self):
        """A faction needs a faction_leader row in ministerial_positions_culture_details,
        matched on culture, subculture or faction (vanilla Wood Elves are per faction,
        so a new wef faction has none), and one of its characters should hold it."""
        defs = [r for r in self.db.table("ministerial_positions_culture_details").rows
                if r["ministerial_position_key"] == FACTION_LEADER]
        chars = defaultdict(list)
        for c, _ in self.campaign_chars():
            chars[c["faction"]].append(c)
        for f in self.factions.rows:
            if f["campaign"] not in self.campaigns:
                continue
            fk, cs = f["faction"], chars.get(f["ID"], [])
            cu = self.culture_of(fk)
            if cu and not any(_match(r, *cu, cols=("faction_key", "subculture_key", "culture_key"))
                              and r.get("campaign_key") in (None, "", f["campaign"]) for r in defs):
                # CA's combi has one, wh3_dlc27_wef_wood_elves_dm, which starts with no characters.
                self.add(ERROR if cs else WARN, "faction-leader-position", "start_pos_factions", f.where(),
                         f"{fk} ({cu[1]} / {cu[2]}) has no {FACTION_LEADER} row in "
                         f"ministerial_positions_culture_details for its culture, subculture or faction"
                         + ("" if cs else "; it starts with no characters"))
            leaders = [c for c in cs if c["ministerial_position"] == FACTION_LEADER]
            if cs and not leaders:
                self.add(WARN, "faction-leader", "start_pos_characters", cs[0].where(),
                         f"{fk} has {len(cs)} character(s) but none has ministerial_position {FACTION_LEADER}")
            elif len(leaders) > 1:
                self.add(WARN, "faction-leader", "start_pos_characters", leaders[1].where(),
                         f"{fk} has {len(leaders)} {FACTION_LEADER} characters: "
                         + ", ".join(c["ID"] for c in leaders))
