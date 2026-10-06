"""Run every check against CA's own start_pos data, with vanilla tables only.

    python calibrate.py <any .pack> [--game wh3|3k] [--campaign KEY] [--map MAP] [-v]

Defaults: wh3_main_combi on wh3_main_combi_map_7; for 3k, 3k_main_campaign_map
on 3k_dlc07_main_map.

Anything reported here is something CA's shipped data does, so the matching
check should not be an error. Re-run after a game update. RPFM needs a pack
open to work at all; any mod pack will do and its own contents are ignored.
"""
import argparse
import os
from collections import Counter

from loader import GameDB, MapData, ReferenceData
from paths import game, rpfm_server
from rpfm_client import Rpfm


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("anchor", help="any mod .pack, for RPFM to open")
    ap.add_argument("--game", help="wh3 or 3k (default: game in settings.ini, else wh3)")
    ap.add_argument("--campaign", help="campaign to check (default: the game's main campaign)")
    ap.add_argument("--map", help="campaign_maps folder of the campaign's map_name")
    ap.add_argument("--verbose", "-v", action="store_true", help="list the findings, not just counts")
    ap.add_argument("--rpfm", help="path to rpfm_server.exe (default: rpfm_server in settings.ini)")
    a = ap.parse_args()
    a.anchor = os.path.abspath(a.anchor)
    g = game(a.game)
    a.campaign = a.campaign or g.calibrate_campaign
    a.map = a.map or g.calibrate_map
    with Rpfm(rpfm_server(a.rpfm)) as rpfm:
        rpfm.call({"SetGameSelected": [g.key, False]})
        rpfm.call({"OpenPackFiles": [a.anchor]})
        deps = rpfm.call({"RebuildDependencies": False})["DependenciesInfo"]
        ref = ReferenceData(rpfm, a.anchor, deps).load()
        db = GameDB(rpfm, a.anchor, [], deps, vanilla_only=True)
        game_map = MapData.load(rpfm, a.anchor, a.map, deps, [], None)
        out = g.checker()(ref, [a.campaign], db, {a.campaign: (game_map, None)}).run()
    counts = Counter((f.severity, f.check) for f in out)
    for (sev, check), n in sorted(counts.items()):
        print(f"{sev:8} {check:28} {n}")
    if a.verbose:
        for f in out:
            if f.severity != "info":
                print(f"  [{f.severity}] {f.check} {f.table} / {f.where}: {f.message}")
    print("\nNote: CA's Assembly Kit characters/land-units tables are stale, so dangling-id "
          "findings here are expected and say nothing about the checks.")


if __name__ == "__main__":
    main()
