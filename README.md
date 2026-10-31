# startpos_checker

Checks a **Total War: WARHAMMER III** start_pos pack (the `db/start_pos_*_tables`
fragments you build a `startpos.esf` from) for errors before you build it.

The game gives almost no feedback here. A bad start_pos either crashes the
build with no log, or builds a campaign that is subtly broken. This tool
catches the mistakes it knows about in about a minute: IDs that point at
nothing, buildings a faction can't have, settlements missing a
`settlement_type`, factions without a faction leader, and so on.

**Warhammer III only.** The checks are written against WH3's tables and map
format, and their severities were tuned on CA's own WH3 start_pos. The
approach would carry over to other Total War games that build a start_pos
from DB tables, but the checks would have to be rewritten and re-calibrated
for each one (see [Other Total War games](#other-total-war-games)).

## What you need

- **Windows** and **Warhammer III**.
- **[RPFM 5.3 or newer](https://github.com/Frodo45127/rpfm/releases)**. The checker uses
  `rpfm_server.exe`, which ships next to `rpfm_ui.exe`, through its v2
  (JSON-RPC) API; older RPFM versions won't work. Before the first run,
  open `rpfm_ui.exe` and:
  1. In **PackFile > Settings**, set the Warhammer 3 **Game Folder** (the
     folder with `Warhammer3.exe` in it). Setting the **Assembly Kit Folder**
     too is recommended.
  2. Select **Game Selected > Warhammer 3**, then **Game Selected > Generate
     Dependencies Cache**, and wait for it to finish. Do this again after
     game updates, when RPFM asks for it.
- **[Python 3.10 or newer](https://www.python.org/downloads/)**. The
  installer's defaults are fine.

## Checking a pack

1. Put this folder anywhere.
2. Copy `settings.example.ini` to `settings.ini` and set `rpfm_server` to the
   full path of your `rpfm_server.exe`:
   ```ini
   [paths]
   rpfm_server = C:\Modding\RPFM\rpfm_server.exe
   ```
   (If you skip this, the first run creates `settings.ini` for you and tells
   you to fill it in.)
3. **Drag your start_pos `.pack` onto `check_startpos.bat`.** The first run
   installs the Python package it needs (`requirements.txt`).
4. Wait about a minute. The report shows in the window and is saved next to
   the `.bat` as `<pack name>_report.txt`. Post that file if you ask for help.

### Reading the report

```
my_start_pos.pack: 19 tables, 9691 rows; campaign(s): cr_combi_expanded  (55.5s)
2 error(s), 5 warning(s), 591 info

[ERROR] settlement-type (2)
  start_pos_settlements / !!!my_regions row 12: ...
```

Findings are grouped by check (see the [table of checks](#checks)). Each line
gives the table, then the fragment file and row number where the problem is,
which is what you look for in RPFM.

- **ERROR**: fix these first. They break the build or the campaign, and CA's
  own start_pos never does them.
- **WARNING**: the start_pos still builds, but it's probably not what you
  meant. CA's own data does some of these, so judge each one.
- **info**: patterns that CA's data has too. Hidden unless you run the
  command line with `-v`.

## Troubleshooting

- **"Set rpfm_server in ...settings.ini"** or **"rpfm_server.exe not found
  at ..."**: see step 2 above.
- **"RPFM does not know where Warhammer III is installed"** or **"no
  dependencies cache"**: do the RPFM steps under [What you need](#what-you-need).
- **"Python 3 is not installed"** although it is: reinstall from python.org.
  The `python` command that comes with Windows only opens the Microsoft Store.
- **"dependency ... is not installed"**: the pack lists another mod pack as a
  dependency, and that pack isn't in the game's data folder or subscribed to
  on the Workshop.

## Other Total War games

Everything here is WH3-specific: the start_pos tables and columns, the game
tables the checks cross-reference (`building_culture_variants`,
`campaign_group_settlement_type_sets`, ...) and the `map_data.esf` layout.
What carries over to other games is the method:

- merge DB fragments the way the game does (load order, file-name order,
  first row for a key wins) before checking anything;
- check every start_pos reference against the merged game tables and the
  campaign map;
- run every rule against CA's own start_pos, and only make a rule an error if
  CA's data never breaks it.

## Command line

```
python startpos_check.py "<path to>\my_start_pos.pack"
python startpos_check.py <pack> -v              # also list info findings
python startpos_check.py <pack> --json out.json # every finding as JSON
python startpos_check.py <pack> --txt out.txt   # also save the report as text
python startpos_check.py <pack> --campaign cr_combi_expanded
python startpos_check.py <pack> --no-rpfm-diagnostics   # skip RPFM's own checks
python startpos_check.py <pack> --no-map                # skip map_data.esf (saves ~10 s per map)
python startpos_check.py <pack> --rpfm <rpfm_server.exe>  # instead of settings.ini
python calibrate.py <any mod .pack> [-v]                # run all checks on CA's own combi data
```

A run takes 40 to 60 s: the dependency rebuild takes about 25 s and reading the
map about 10 s (per map, if the pack builds campaigns on different maps). The exit code is 1 when there is at least one error, and 2
when the check couldn't run.

The RPFM server is started from `settings.ini`'s `rpfm_server` (or `--rpfm`)
if it isn't already running.

## How it works

1. Opens the pack in the RPFM server. Pack dependencies that RPFM couldn't load
   (not installed) are errors.
2. Decodes every DB fragment in the pack. Per table, fragments are applied in
   file-name order and **the first row for a key wins**, so `!!!!corrections`
   overrides `!!!vanilla`. All later checks use these effective rows.
3. Campaigns come from RPFM's `startpos.campaigns` (what "Build
   Startpos" would build), unless `--campaign` is given. All of them are
   checked; each campaign only against its own rows (rows of campaigns that
   aren't built are ignored, apart from `dangling-id` and the `campaign-scope`
   warning) and its own map.
4. Rebuilds dependencies and runs RPFM's own diagnostics on the pack.
   Invalid references into tables the game never reads (Assembly Kit-only
   tables, such as `campaign_maps`) are downgraded to info. Duplicate keys are
   replaced by the override report below.
5. Game tables (`factions`, `building_levels`, ...) are merged from vanilla,
   the pack's dependency packs and the pack itself: a path shipped by several
   packs comes from the first in load order (mods alphabetically, then
   vanilla), then fragments apply in file-name order as above. Assembly Kit
   copies are used only for tables no pack ships, plus the AK `names` /
   `agent_subtypes` as a fallback for name ids and `names_group`. Characters of
   factions that aren't in any loaded pack can't be checked against game data;
   they're reported as info.
6. Reads `campaign_maps/<campaigns.map_name>/map_data.esf` of each campaign
   (or `display_location` if `map_name` has none; they're the same folder in
   vanilla, not in e.g. `cr_darklands`) through RPFM, once per map. RPFM only decodes ESF with its
   `enable_esf_editor` setting on, so the tool switches it on for its own
   server session only (your RPFM settings are not changed). `HEX_MAP_DATA` there
   is the hex grid `startx/starty` are measured in (1440×970 for vanilla combi,
   1600×970 for cr_combi_expanded_map_1). `REGION_DATA` lists the map's regions
   (flag 1 = sea).
7. Runs the start_pos checks in `checks.py` and the game-data/map checks in
   `game_checks.py`.

## Checks

| check | severity | what |
|---|---|---|
| `pack-dependency`, `decode` | error | missing dependency pack; fragment RPFM can't decode |
| `rpfm-*` | error/warning | RPFM diagnostics (invalid references, outdated tables, ...) |
| `duplicate-key` | error | same key twice in **one** fragment (the second row is dropped) |
| `override` | info | a row replaced by another fragment, with the changed columns |
| `dangling-id` | error | start_pos ID link (faction, character, region id, settlement id) that resolves to nothing, or only to an overridden row |
| `duplicate-id` | error | non-key ID columns other tables point at (`start_pos_regions.id`, `start_pos_settlements.id`, ...) used twice |
| `id-format` / `id-range` | error / warning | ID that isn't a number / doesn't fit 32 bits (the AK defines these as `autonumber`/`integer`) |
| `campaign`, `campaign-scope`, `campaign-mismatch` | error/warning | missing calendar; rows for campaigns not being built; links across campaigns |
| `faction-duplicate` | error | a faction key with two start_pos IDs in one campaign |
| `faction-limit` | error | more than 1024 `start_pos_factions` rows in one campaign |
| `region-settlement` | error | region with no settlement, or several |
| `army` / `army-size` | error/warning | units on a non-general or a pool general, more than 19 units, soldiers ≤ 0 |
| `garrison-owner` | warning | character placed in a settlement its faction doesn't own |
| `horde`, `general-option` | error/warning | horde details / frontend options on non-generals; one frontend leader used by several factions |
| `character-position` | warning | generals of different factions on the same spot |
| `region-absent`, `faction-absent` | warning/info | rows naming a region/faction that isn't in the campaign |
| `map-bounds` | error | character (not in the pool or a settlement) outside the map's hex grid |
| `map-region` | error | start_pos region the map doesn't have; settled land region of the map with no start_pos row |
| `slot-template` | error | region without a primary slot template |
| `building-slot` | error (primary) / warning |  building whose chain the region's slot templates of that type (primary / port / secondary) don't permit, or no template of that type at all. Templates resolve through `slot_template_permitted_building_chains` to chains, `building_chain_sets` (+ `building_chain_set_items`, parent sets) and superchains; adds, then removes |
| `building-owner` | error (primary) / warning | settlement or horde building the owning faction can't have: no matching `building_culture_variants` row (faction / subculture / culture), only disabling rows, or the chain's `building_chain_availability_sets` / `building_chain_availabilities` don't allow the faction in this campaign |
| `building-settlement-type` | error (primary) / warning | chain excluded by, or missing from, `settlement_type_to_building_chains_junctions` for the settlement's type |
| `building-climate` | error (primary) / warning | chain not allowed on the settlement's climate (`building_chain_climate_restrictions`; empty in vanilla WH3) |
| `building-capital` | error (primary) / warning | `only_in_capital` level outside a faction capital |
| `building-duplicate-chain` | error (primary) / warning | one chain twice in a settlement |
| `settlement-type` | error / info | settlement whose owner is in a campaign group with settlement types (`campaign_group_settlement_type_sets` → `settlement_type_sets_to_settlement_types`; membership from `campaign_group_members` + the faction / subculture / culture / campaign criteria tables) but whose `settlement_type` is empty or not one of that group's types. This covers Warriors of Chaos (membership is per faction, so new chs factions have to be added to `wh3_dlc20_feature_chaos_warriors`), Chaos Dwarfs, Norsca, Sayl, Dechala, Glottkin, Nagash and Daemons. An empty type crashes the build in `WORLD::WORLD`. The error suggests the types whose building-chain junctions allow the primary building. Info: a typed settlement whose owner is in no such group (CA's Festus at brass_keep) |
| `building-required` | warning | `building_level_required_buildings` not met (CA has one) |
| `agent-permission` | warning | (faction, agent, subtype) missing from `faction_agent_permitted_subtypes`, grouped per subtype. Such characters count as stand-ins: their units and names are not checked (CA has 9, all legendary lords) |
| `agent-cap` | warning | more characters of a subtype than `agent_subtypes.cap` |
| `unit-permission` | warning | units outside the faction's `units_to_groupings_military_permissions` roster |
| `faction-leader-position` | error / warning | faction with no `faction_leader` row in `ministerial_positions_culture_details` matching its culture, subculture or faction. Vanilla Wood Elves are defined per faction, so a new wef faction has none. Error if the faction has characters; a warning if it has none (CA's `wh3_dlc27_wef_wood_elves_dm`) |
| `faction-leader` | warning | faction with characters but none with `ministerial_position` `faction_leader`, or more than one |
| `name-group` | warning | Name/Surname from a names group other than the subtype's `names_group`, or else the faction's `name_group` |
| `faction-capital`, `faction-dead`, `faction-landless`, `settlement-name` | info | patterns that CA's own data also has |

The `building-*` checks are errors only for `primary_building` (settlement or horde); a
problem with a port, secondary or horde secondary building is a warning, because the
start_pos still builds.

## Calibration

Severities were set by running every check on CA's `wh3_main_combi` start_pos
with vanilla tables only (`calibrate.py`): rules that CA's shipped data breaks
are warnings or info, not errors. That baseline uses the game `db.pack` copies
of the five start_pos tables the game ships and the Assembly Kit copies of the
rest. CA's AK characters and land-units tables are not self-consistent, so its
`dangling-id` findings mean nothing. Re-run `calibrate.py` after a game update.

Rules that CA's data breaks and were therefore dropped: name gender vs
`is_male` (35 mismatches in CA's data), surname type, "more secondary buildings
than secondary slot templates", and "abandoned settlements only hold
`settlement_abandoment_buildings`".

## Files

- `check_startpos.bat`: drag-and-drop wrapper; finds Python and installs `requirements.txt`
- `settings.example.ini`: template for `settings.ini` (path to `rpfm_server.exe`)
- `startpos_check.py`: command line and report
- `paths.py`: reads `settings.ini`; checks RPFM's own setup
- `rpfm_client.py`: RPFM server v2 API (JSON-RPC over WebSocket) client (launches the server)
- `loader.py`: pack tables, merged game tables, CA's baseline, `map_data.esf`
- `checks.py`: start_pos-internal checks
- `game_checks.py`: checks against game tables and the map
- `calibrate.py`: runs everything on CA's combi start_pos with vanilla tables only
