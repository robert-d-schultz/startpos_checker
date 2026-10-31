"""Run every check against CA's own start_pos data, with vanilla tables only.

    python calibrate.py <any .pack> [--campaign wh3_main_combi] [--map wh3_main_combi_map_7] [-v]

Anything reported here is something CA's shipped data does, so the matching
check should not be an error. Re-run after a game update. RPFM needs a pack
open to work at all; any mod pack will do and its own contents are ignored.
"""
import argparse
import os
from collections import Counter

from game_checks import GameChecker
from loader import GameDB, MapData, ReferenceData
from paths import rpfm_server
from rpfm_client import Rpfm


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("anchor", help="any mod .pack, for RPFM to open")
    ap.add_argument("--campaign", default="wh3_main_combi")
    ap.add_argument("--map", default="wh3_main_combi_map_7", help="campaign_maps folder of the campaign's map_name")
    ap.add_argument("--verbose", "-v", action="store_true", help="list the findings, not just counts")
    ap.add_argument("--rpfm", help="path to rpfm_server.exe (default: rpfm_server in settings.ini)")
    a = ap.parse_args()
    a.anchor = os.path.abspath(a.anchor)
    with Rpfm(rpfm_server(a.rpfm)) as rpfm:
        rpfm.job("session.set_game", {"game": "warhammer_3", "rebuild_dependencies": False})
        anchor = rpfm.call("pack.open", {"paths": [a.anchor]})
        rpfm.job("dependencies.rebuild")
        deps = rpfm.call("dependencies.info")
        ref = ReferenceData(rpfm, anchor, deps).load()
        db = GameDB(rpfm, anchor, [], deps, vanilla_only=True)
        game_map = MapData.load(rpfm, anchor["key"], a.map, deps, [], None)
        out = GameChecker(ref, [a.campaign], db, {a.campaign: (game_map, None)}).run()
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
