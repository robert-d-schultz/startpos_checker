# startpos_checker

Checks a Total War start_pos pack (the `db/start_pos_*_tables` fragments you
build a `startpos.esf` from) for errors before you build it. It supports two
games:

- **Total War: WARHAMMER III** (`wh3`, the default)
- **Total War: THREE KINGDOMS** (`3k`)

The game gives almost no feedback here. A bad start_pos either crashes the
build with no log, or builds a campaign that is subtly broken. This tool finds
the mistakes it knows about in about a minute: IDs that point at nothing,
buildings a faction can't have, characters off the map, settlements missing a
`settlement_type`, diplomacy deals the 3K build chokes on, and so on.

Every check was run against CA's own start_pos for that game, and a rule is
only an error if CA's data never breaks it (see [Calibration](#calibration)).

## What you need

- **Windows**, and the game whose pack you're checking.
- **[RPFM 5.3 or newer](https://github.com/Frodo45127/rpfm/releases)**. The
  checker talks to `rpfm_server.exe`, which ships next to `rpfm_ui.exe`, through
  its v2 (JSON-RPC) API. Older RPFM versions won't work. Before the first run,
  open `rpfm_ui.exe` and, for each game you'll check:
  1. In **PackFile > Settings**, set the game's **Game Folder** (the folder
     with `Warhammer3.exe` or `Three_Kingdoms.exe` in it) and its **Assembly
     Kit Folder**. The Assembly Kit is recommended for Warhammer III and
     required for Three Kingdoms, which ships none of its start_pos tables.
  2. Select **Game Selected > Warhammer 3** (or **Three Kingdoms**), then
     **Game Selected > Generate Dependencies Cache**, and wait for it to
     finish. Do this again after game updates, when RPFM asks for it.
- **[Python 3.10 or newer](https://www.python.org/downloads/)**. The
  installer's defaults are fine.

## Checking a pack

1. Put this folder anywhere.
2. Copy `settings.example.ini` to `settings.ini` and set `rpfm_server` to the
   full path of your `rpfm_server.exe`:
   ```ini
   [paths]
   rpfm_server = C:\Modding\RPFM\rpfm_server.exe
   game = wh3
   ```
   `game` is the game `check_startpos.bat` checks when you don't say
   otherwise: `wh3` or `3k`. (If you skip this step, the first run creates
   `settings.ini` for you and tells you to fill it in.)
3. Drag your start_pos `.pack` onto:
   - **`check_startpos.bat`** for the game in `settings.ini` (Warhammer III if
     unset), or
   - **`check_startpos_3k.bat`** for a Three Kingdoms pack.

   The first run installs the Python package it needs (`requirements.txt`).
4. Wait about a minute. The report shows in the window and is saved next to
   the `.bat` as `<pack name>_report.txt`. Post that file if you ask for help.

### Reading the report

```
my_start_pos.pack: 19 tables, 9691 rows; campaign(s): cr_combi_expanded  (55.5s)
2 error(s), 5 warning(s), 591 info

[ERROR] settlement-type (2)
  start_pos_settlements / !!!my_regions row 12: ...
```

Findings are grouped by check (see [Checks](#checks)). Each line gives the
table, then the fragment and row number where the problem is, which is what
you look for in RPFM.

- **ERROR**: fix these first. They break the build or the campaign, and CA's
  own start_pos never does them.
- **WARNING**: the start_pos still builds, but it's probably not what you
  meant. CA's own data does some of these, so judge each one.
- **info**: patterns CA's data has too. Hidden unless you run the command line
  with `-v`.

## Troubleshooting

- **"Set rpfm_server in ...settings.ini"** or **"rpfm_server.exe not found
  at ..."**: see step 2 of [Checking a pack](#checking-a-pack).
- **"RPFM does not know where Warhammer III (or Three Kingdoms) is
  installed"**, **"no dependencies cache"** or **"RPFM loaded no ... game
  files"**: do the RPFM steps under [What you need](#what-you-need) for that
  game.
- **"unknown game"**: `--game` and `settings.ini`'s `game` take `wh3` or `3k`.
- **`decode` errors or a crash on a pack that opens fine in RPFM**: check that
  the game being checked (on the first line, "Checking ... (Warhammer III)")
  is the pack's game. For a Three Kingdoms pack, use `check_startpos_3k.bat`
  or set `game = 3k`.
- **"Python 3 is not installed"** although it is: reinstall from python.org.
  The `python` command that comes with Windows only opens the Microsoft Store.
- **"dependency ... is not installed"**: the pack lists another mod pack as a
  dependency, and that pack isn't in the game's data folder or subscribed to
  on the Workshop.

## Command line

```
python startpos_check.py "<path to>\my_start_pos.pack"
python startpos_check.py <pack> --game 3k       # Three Kingdoms (default: settings.ini's game, else wh3)
python startpos_check.py <pack> -v              # also list info findings
python startpos_check.py <pack> --json out.json # every finding as JSON
python startpos_check.py <pack> --txt out.txt   # also save the report as text
python startpos_check.py <pack> --campaign cr_combi_expanded
python startpos_check.py <pack> --no-rpfm-diagnostics     # skip RPFM's own checks
python startpos_check.py <pack> --no-map                  # skip map_data.esf (saves ~10 s per map)
python startpos_check.py <pack> --rpfm <rpfm_server.exe>  # instead of settings.ini
python calibrate.py <any mod .pack of the game> [--game 3k] [--campaign KEY] [--map MAP] [-v]
```

A run takes 40 to 60 s: rebuilding the dependencies takes about 25 s and
reading the map about 10 s (per map, if the pack builds campaigns on different
maps). The exit code is 1 when there is at least one error, and 2 when the
check couldn't run.

The RPFM server is started from `settings.ini`'s `rpfm_server` (or `--rpfm`)
if it isn't already running.

## How it works

1. Opens the pack in the RPFM server, with the game selected. Dependency packs
   RPFM couldn't load (not installed) are errors.
2. Decodes every DB fragment in the pack. Per table, fragments are applied in
   file-name order and **the first row for a key wins**, so `!!!!corrections`
   overrides `!!!vanilla`. All later checks use these effective rows. An
   empty fragment of a table version RPFM has no definition for is info, not
   an error: it has no rows to lose (3K packs often carry the Assembly Kit's
   empty start_pos tables).
3. Campaigns come from RPFM's `startpos.campaigns` (what "Build Startpos"
   would build), unless `--campaign` is given. All of them are checked; each
   campaign only against its own rows (rows of campaigns that aren't built are
   ignored, apart from `dangling-id` and the `campaign-scope` warning) and its
   own map.
4. Rebuilds the dependencies and runs RPFM's own diagnostics on the pack.
   Invalid references into tables the game never reads (Assembly Kit-only
   tables, such as `campaign_maps`) are downgraded to info. Duplicate keys are
   left to the override report below. Tables in 3K's `ceo_db/` folder are
   handled like `db/` ones.
5. Game tables (`factions`, `building_levels`, ...) are merged from vanilla,
   the pack's dependency packs and the pack itself: a path shipped by several
   packs comes from the first in load order (mods alphabetically, then
   vanilla), then fragments apply in file-name order as above. Assembly Kit
   copies are used only for tables no pack ships, plus the AK `names` /
   `agent_subtypes` as a fallback for name ids and `names_group`. Characters of
   factions that aren't in any loaded pack can't be checked against game data;
   they're reported as info.
6. Reads each campaign's map (see [The campaign map](#the-campaign-map)).
7. Runs the shared start_pos checks (`checks.py`), then the game's own:
   `game_checks.py` for Warhammer III, `checks_3k.py` for Three Kingdoms.

### The campaign map

The map checks read `campaign_maps/<folder>/map_data.esf`, the compiled map
the game loads, from the pack, its dependencies or vanilla, in that order. The
folder is the campaign's `campaigns.map_name`, or `display_location` if that
has none. They're the same folder in vanilla but not always in mods
(`cr_darklands` is on `cr_oldworld_map_1`). Each map is read once, even if
several campaigns use it.

RPFM only decodes ESF files with its `enable_esf_editor` setting on, so the
checker turns it on for its own server session. Your RPFM settings are not
changed.

From the file, the checker uses:

- `theatres > CAMPAIGN_THEATRE > HEX_MAP_DATA`: the hex grid that
  `startx` / `starty` are measured in;
- `REGIONS_DATA > MASKED_REGIONS_DATA > REGIONS_BLOCK > REGION_DATA`: the
  map's regions, with flag 1 for sea regions.

Both games write map_data.esf as MAP_DATA version 2, and these records have
the same layout in both. Three Kingdoms' file differs elsewhere (ABCA rather
than ABCB encoding, packed area references, per-hex area runs as an array),
but the checker reads none of those parts.

| game | vanilla map | hex grid |
|---|---|---|
| Warhammer III | `wh3_main_combi_map_7` (`wh3_main_combi`) | 1440 × 970 |
| Three Kingdoms | `3k_dlc07_main_map` (all five vanilla campaigns) | 892 × 702 |

`cr_combi_expanded_map_1`, for comparison, is 1600 × 970.

## Checks

These run for both games, except where the Three Kingdoms section says
otherwise.

| check | severity | what |
|---|---|---|
| `pack-dependency`, `decode` | error | missing dependency pack; fragment RPFM can't decode |
| `rpfm-*` | error / warning | RPFM diagnostics (invalid references, outdated tables, ...) |
| `duplicate-key` | error | same key twice in **one** fragment (the second row is dropped) |
| `override` | info | a row replaced by another fragment, with the changed columns |
| `dangling-id` | error / warning | start_pos ID link (faction, character, region id, settlement id) that resolves to nothing, or only to an overridden row. A warning for a settlement whose region doesn't exist: CA's data has these and still builds |
| `duplicate-id` | error | non-key ID columns other tables point at (`start_pos_regions.id`, `start_pos_settlements.id`, ...) used twice |
| `id-format` / `id-range` | error / warning | ID that isn't a number / doesn't fit 32 bits (the AK defines these as `autonumber` / `integer`) |
| `campaign`, `campaign-scope`, `campaign-mismatch` | error / warning | missing calendar; rows for campaigns not being built; links across campaigns |
| `faction-duplicate` | error | a faction key with two start_pos IDs in one campaign |
| `faction-limit` | error | more than 1024 `start_pos_factions` rows in one campaign |
| `faction-capital` | error / info | a `faction_capital` region with no owner (a warning in 3K); info: a faction with regions but no capital |
| `region-settlement` | error | region with no settlement, or several |
| `army` / `army-size` | error / warning | units on a non-general or a pool general, more units than an army holds (19 in WH3, a 6-unit retinue in 3K), soldiers ≤ 0 |
| `garrison-owner` | warning (WH3) / error (3K) | character placed in a settlement its faction doesn't own. CA's WH3 data has one (Lazarghs at Mount Thug); in 3K it makes the start_pos fail to generate |
| `horde`, `general-option` | error / warning | horde details / frontend options on non-generals; one frontend leader used by several factions |
| `character-position` | warning | generals of different factions on the same spot |
| `region-absent`, `faction-absent` | warning / info | rows naming a region / faction that isn't in the campaign |
| `map-bounds` | error | character (not in the pool or a settlement) outside the map's hex grid |
| `map-region` | error | start_pos region the map doesn't have; settled land region of the map with no start_pos row |
| `map` | warning | the campaign's map_data.esf couldn't be found or read, so the map checks were skipped |
| `slot-template` | error | region without a primary slot template |
| `building-slot` | error (primary) / warning | building whose chain the region's slot templates of that type (primary / port / secondary) don't permit, or no template of that type at all. WH3 templates resolve through `slot_template_permitted_building_chains` to chains, `building_chain_sets` (+ `building_chain_set_items`, parent sets) and superchains, adds then removes; 3K's through `slot_template_to_building_superchain_junctions` |
| `building-owner` | error (primary) / warning | settlement or horde building the owning faction can't have: no matching `building_culture_variants` row (faction / subculture / culture), only disabling rows, or the chain's `building_chain_availability_sets` / `building_chain_availabilities` don't allow the faction in this campaign |
| `building-capital` | error (primary) / warning | `only_in_capital` level outside a faction capital |
| `building-duplicate-chain` | error (primary) / warning | one chain twice in a settlement |
| `agent-permission` | warning | (faction, agent, subtype) missing from `faction_agent_permitted_subtypes`, grouped per subtype. Such characters count as stand-ins: their units and names are not checked (CA's WH3 data has 9, all legendary lords) |
| `agent-cap` | warning | more characters of a subtype than `agent_subtypes.cap` |
| `unit-permission` | warning | units outside the faction's `units_to_groupings_military_permissions` roster |
| `faction-leader-position` | error / warning | faction with no `faction_leader` row in `ministerial_positions_culture_details` matching its culture, subculture or faction. Error if the faction has characters, a warning if it has none |
| `faction-leader` | warning | faction with characters but none with `ministerial_position` `faction_leader`, or more than one |
| `faction-dead`, `faction-landless`, `settlement-name` | info | patterns that CA's own data also has |

The `building-*` checks are errors only for `primary_building` (settlement or
horde). A problem with a port, secondary or horde secondary building is a
warning, because the start_pos still builds.

### Warhammer III only

| check | severity | what |
|---|---|---|
| `building-settlement-type` | error (primary) / warning | chain excluded by, or missing from, `settlement_type_to_building_chains_junctions` for the settlement's type |
| `building-climate` | error (primary) / warning | chain not allowed on the settlement's climate (`building_chain_climate_restrictions`; empty in vanilla) |
| `building-required` | warning | `building_level_required_buildings` not met (CA has one) |
| `settlement-type` | error / info | settlement whose owner is in a campaign group with settlement types (`campaign_group_settlement_type_sets` → `settlement_type_sets_to_settlement_types`; membership from `campaign_group_members` + the faction / subculture / culture / campaign criteria tables) but whose `settlement_type` is empty or not one of that group's types. This covers Warriors of Chaos (membership is per faction, so new chs factions have to be added to `wh3_dlc20_feature_chaos_warriors`), Chaos Dwarfs, Norsca, Sayl, Dechala, Glottkin, Nagash and Daemons. An empty type crashes the build in `WORLD::WORLD`. The error suggests the types whose building-chain junctions allow the primary building. Info: a typed settlement whose owner is in no such group (CA's Festus at brass_keep) |
| `name-group` | warning | Name / Surname from a names group other than the subtype's `names_group`, or else the faction's `name_group` |

Vanilla Wood Elves define their `faction_leader` per faction, so a new Wood
Elves faction has no `faction-leader-position` row (CA's
`wh3_dlc27_wef_wood_elves_dm` has none and starts with no characters).

### Three Kingdoms

3K's start_pos is laid out differently, and the checks adapt:

- **Characters** have no `subtype`, `Name` or `Surname`. Their subtype comes
  from `template` (`character_generation_templates.subtype`), so
  `name-group` doesn't run and unknown templates are left to RPFM's
  diagnostics. A character with `start_on_map` off counts as off the map, and
  (1, 1) is a parking spot like (0, 0): CA parks 246 on-map characters there.
- **Buildings**: 3K has no settlement types, climate restrictions or required
  buildings, so those checks don't run.
- **Technologies**: `start_pos_technologies.faction` is a start_pos faction
  ID, not a faction key.
- **Armies**: a general's `start_pos_land_units` (empty in vanilla) are one
  retinue, at most 6 units. A force is a commanding general plus up to two
  non-commanding generals.

Checks only 3K has:

| check | severity | what |
|---|---|---|
| `diplomacy-deal` | error / info | a `start_pos_diplomacy_deals` row with no `start_pos_diplomacy_simple_deals` (or `_complex_deals`) row of the same id, or a simple deal with no deals row. **The AK's `start_pos_diplomacy_deals` has 35 deals (CA's hidden treaties) with no simple deal; copied into a pack, they make the start_pos build fail.** Copy only the deals your simple deals use. Info: how many such deals there are |
| `dangling-id` | error / warning | deal orderings and simple-deal parameters naming a deal that doesn't exist; proposers, recipients and faction parameters naming no start_pos faction. Warning: a technology whose faction doesn't exist, or a religion or pooled resource whose region doesn't exist (CA's AK has these left over from deleted factions and regions) |
| `campaign-mismatch` | error | a deal, deal parameter, force, family / relationship link, employment history entry, technology or world power token joining rows of different campaigns |
| `diplomacy` | info | a deal proposed to the proposer itself (CA's automatic treaties do this) |
| `force` | error / warning | in `start_pos_non_commanding_generals`: a commander that isn't an on-map general, a non-commanding general that isn't a general, serves another faction or commands a force itself, or more than 2 per force; captains / captain retinue modifiers on a non-general. Warning: captain retinue modifiers on a general with no captains |
| `character-link` | warning | a family relationship or relationship trigger from a character to itself |
| `region-religion` | warning | a region's religions adding up to more than 100% |

## Calibration

`calibrate.py` runs every check on CA's own start_pos for one campaign, with
vanilla tables only:

```
python calibrate.py <any WH3 .pack>                 # wh3_main_combi on wh3_main_combi_map_7
python calibrate.py <any 3K .pack> --game 3k        # 3k_main_campaign_map on 3k_dlc07_main_map
python calibrate.py <any 3K .pack> --game 3k --campaign 8p_start_pos -v
```

The pack is only there because RPFM needs one of the game's packs open; its
contents aren't used. `--map` is the campaign's map folder, if it isn't the
default campaign's.
Anything reported is something CA's shipped data does, so that rule must not
be an error. Re-run it after a game update, and on every vanilla campaign when
changing a check.

The baseline is the game's `db.pack` copies of the start_pos tables it ships
and the Assembly Kit copies of the rest. Warhammer III ships five; Three
Kingdoms ships none, so its baseline is entirely the AK's. RPFM decodes the
AK's start_pos IDs as integers but pack fragments as strings, so
`calibrate.py` converts the AK's to strings. Without that, no ID link between
AK tables resolves.

- **Warhammer III** (`wh3_main_combi`): no errors. The warnings are 9
  `agent-permission` (legendary lords), 9 `unit-permission`, 2 `army` (pool
  generals with a unit), 1 `garrison-owner` (Lazarghs), 1 `building-required`,
  1 `faction-leader-position` (`wh3_dlc27_wef_wood_elves_dm`), 1
  `dangling-id` (a settlement left over from a deleted region), and
  `campaign-scope` for the other campaigns' rows.
- **Three Kingdoms** (all five vanilla campaigns): the only errors left are
  the 35 orphan `diplomacy-deal` rows, which really do break the build, and 9
  AK characters whose faction no longer exists.

Rules that CA's data breaks and that were therefore dropped: name gender vs
`is_male` (35 mismatches in CA's WH3 data), surname type, "more secondary
buildings than secondary slot templates", and "abandoned settlements only
hold `settlement_abandoment_buildings`".

## Adding another Total War game

The loading, merging, report, map reading and most start_pos checks are
shared. What's game-specific is the start_pos columns, the game tables the
checks cross-reference and the calibration. To add a game:

1. Add a `Game` to `games.py`: RPFM's game key, the executable, the name of
   RPFM's dependencies cache, the calibration campaign and map, and which
   checker class to use.
2. Write a checker class. Subclass `GameChecker` (`game_checks.py`) and
   override what differs. `Checker` and `GameChecker` keep the columns and
   severities that vary between games in class attributes (`TYPE`,
   `MAX_ARMY_UNITS`, `FACTION_KEY_TABLES`, `GARRISON_OWNER_SEV`, ...) and
   hooks (`subtype_of`, `is_off_map`, `is_parked`, `text_id_table`,
   `_permitted_chains`). `checks_3k.py` is the worked example.
3. Check that the game's map_data.esf has the records listed under
   [The campaign map](#the-campaign-map). Games before Warhammer III's
   MAP_DATA v2 don't: Rome II and Attila, for example, have no
   `MASKED_REGIONS_DATA`, so `MapData._parse` would need a branch for them.
4. Calibrate: run `calibrate.py --game <flag>` on every vanilla campaign, and
   make any rule CA's data breaks a warning or info.

## Files

- `check_startpos.bat`: drag-and-drop wrapper; finds Python and installs `requirements.txt`
- `check_startpos_3k.bat`: the same, for Three Kingdoms packs
- `settings.example.ini`: template for `settings.ini` (path to `rpfm_server.exe`, default game)
- `startpos_check.py`: command line and report
- `games.py`: the supported games (RPFM game key, executable, calibration defaults, checker class)
- `paths.py`: reads `settings.ini`; checks RPFM's own setup for the game
- `rpfm_client.py`: client for the RPFM server's v2 API (JSON-RPC over WebSocket); starts the server
- `loader.py`: pack tables, merged game tables, CA's baseline, `map_data.esf`
- `checks.py`: start_pos-internal checks, shared by every game
- `game_checks.py`: Warhammer III's checks against game tables and the map
- `checks_3k.py`: Three Kingdoms' checks, built on the Warhammer III ones
- `calibrate.py`: runs everything on CA's own start_pos with vanilla tables only
