"""Check a Total War start_pos pack (WARHAMMER III or THREE KINGDOMS) for errors.

    python startpos_check.py <pack> [--game wh3|3k] [--campaign KEY] [--verbose] [--json FILE] [--txt FILE]

Uses the RPFM server (started on demand) to open the pack and its
dependencies, runs RPFM's own diagnostics on it, then the start_pos
consistency checks in checks.py and the game's own checks (game_checks.py
for WH3, checks_3k.py for Three Kingdoms).
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict

from checks import ERROR, INFO, WARN, Finding
from loader import GameDB, MapData, PackData
from paths import SettingsError, game, rpfm_server, rpfm_setup_problems
from rpfm_client import Rpfm, RpfmError

DB_FOLDERS = ("db", "ceo_db")   # 3K keeps its CEO tables in ceo_db/


def rpfm_diagnostics(rpfm, pack, data, deps):
    """RPFM's diagnostics for the target pack, as Findings."""
    res = rpfm.call({"DiagnosticsCheck": [[], True]})["Diagnostics"]["results"]
    game_tables = {p["path"].split("/")[1] for src in ("vanilla_packed_files", "parent_packed_files")
                   for p in deps.get(src, []) if p["path"].split("/")[0] in DB_FOLDERS}
    game_tables |= {f.split("/")[1] for f in data.files if f.split("/")[0] in DB_FOLDERS}
    out = []
    for entry in res:
        kind, payload = next(iter(entry.items()))
        if os.path.normcase(payload.get("pack") or pack) != os.path.normcase(pack):
            continue
        path = payload.get("path", "")
        parts = path.split("/")
        table = parts[1][:-len("_tables")] if len(parts) == 3 and parts[0] in DB_FOLDERS else path
        frag = parts[-1]
        for r in payload.get("results", []):
            rt = r.get("report_type")
            name = rt if isinstance(rt, str) else next(iter(rt))
            detail = None if isinstance(rt, str) else rt[name]
            cells = r.get("cells_affected") or []
            where = f"{frag} row {cells[0][0] + 1}" if cells else frag
            cols = r.get("column_names") or []
            if name == "DuplicatedCombinedKeys":
                continue   # reported with more detail by the override check
            sev = WARN
            msg = f"{name}: {detail}" if detail is not None else name
            if name == "InvalidReference":
                value, col = detail
                t = data.tables.get(table)
                ref = t.col(col)["is_reference"] if t and t.col(col) else None
                msg = f"{col}={value!r} not found" + (f" in {ref[0]}.{ref[1]}" if ref else "")
                sev = ERROR
                if ref and f"{ref[0]}_tables" not in game_tables:
                    sev = INFO
                    msg += " (Assembly Kit-only table, not read by the game)"
            elif cols:
                msg += f" [{', '.join(cols)}]"
            out.append(Finding(sev, f"rpfm-{kind.lower()}", table, where, msg))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack", help="path to the start_pos .pack")
    ap.add_argument("--game", help="wh3 or 3k (default: game in settings.ini, else wh3)")
    ap.add_argument("--campaign", action="append", help="campaign key(s) to check (default: what RPFM would build)")
    ap.add_argument("--no-rpfm-diagnostics", action="store_true", help="skip RPFM's own diagnostics")
    ap.add_argument("--no-map", action="store_true",
                    help="skip reading the campaigns' map_data.esf (bounds/region checks, ~10 s per map)")
    ap.add_argument("--verbose", "-v", action="store_true", help="also list info findings")
    ap.add_argument("--json", help="write every finding to this JSON file")
    ap.add_argument("--txt", help="also write the report to this text file")
    ap.add_argument("--rpfm", help="path to rpfm_server.exe (default: rpfm_server in settings.ini)")
    a = ap.parse_args(argv)

    pack = os.path.abspath(a.pack.strip('"'))
    if not os.path.isfile(pack):
        ap.error(f"no such pack: {pack}")
    try:
        a.game = game(a.game)
        a.rpfm = rpfm_server(a.rpfm)
    except SettingsError as e:
        print(f"ERROR: {e}")
        return 2
    problems = rpfm_setup_problems(a.game)
    for msg in problems:
        print(f"ERROR: {msg}\n")
    if problems:
        return 2

    print(f"Checking {os.path.basename(pack)} ({a.game.name}); this takes about a minute...\n", flush=True)
    t0 = time.time()
    try:
        campaigns, data, findings = run(pack, a)
    except RpfmError as e:
        print(f"ERROR: {e}")
        return 2

    lines = report(pack, campaigns, data, findings, a.verbose, time.time() - t0)
    print("\n".join(lines))
    if a.txt:
        with open(a.txt, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"pack": pack, "game": a.game.key, "campaigns": campaigns,
                       "findings": [asdict(f) for f in findings]}, fh, indent=1)
    return 1 if any(f.severity == ERROR for f in findings) else 0


def run(pack, a):
    findings = []
    with Rpfm(a.rpfm) as rpfm:
        rpfm.call({"SetGameSelected": [a.game.key, False]})
        rpfm.call({"OpenPackFiles": [pack]})
        missing = [name for ok, name in rpfm.call({"GetDependencyPackFilesList": pack})["VecBoolString"] if not ok]
        for name in missing:
            findings.append(Finding(ERROR, "pack-dependency", "-", "-", f"dependency {name} is not installed"))
        data = PackData(rpfm, pack).load()
        for path, err in data.decode_errors:
            # RPFM can't open an empty fragment of a table version it has no definition for
            # (3K packs carry many of the AK's empty start_pos tables); it has no rows to lose.
            sev = INFO if "the table is empty" in err else ERROR
            findings.append(Finding(sev, "decode", path.split("/")[1] if "/" in path else path, path, err))
        campaigns = a.campaign or sorted(rpfm.call({"BuildStarposGetCampaingIds": pack})["HashSetString"])
        deps = rpfm.call({"RebuildDependencies": False})["DependenciesInfo"]
        if not deps.get("vanilla_packed_files"):
            raise RpfmError(f"RPFM loaded no {a.game.name} game files, so there is nothing to check against. "
                            f"In rpfm_ui.exe, select Game Selected > {a.game.rpfm_name}, then Game Selected > "
                            "Generate Dependencies Cache, and wait for it to finish.")
        if not deps.get("asskit_tables"):
            findings.append(Finding(WARN, "setup", "-", "-",
                                    "RPFM's dependencies cache has no Assembly Kit tables; tables only the "
                                    "Assembly Kit has can't be checked (set the Assembly Kit Folder in RPFM's "
                                    "settings and regenerate the cache)"))
        if not a.no_rpfm_diagnostics:
            findings += rpfm_diagnostics(rpfm, pack, data, deps)
        db = GameDB(rpfm, pack, data.files, deps)
        maps = {} if a.no_map else load_maps(rpfm, pack, data, db, deps, campaigns)
        if not campaigns:
            findings.append(Finding(ERROR, "campaign", "-", "-", "no buildable campaign found in the pack"))
        findings += a.game.checker()(data, campaigns, db, maps).run()
        for path, err in db.decode_errors:
            findings.append(Finding(WARN, "decode", path.split("/")[1] if "/" in path else path, path, err))
    return campaigns, data, findings


def load_maps(rpfm, pack, data, db, deps, campaigns):
    """campaign -> (map_data.esf, None) or (None, why not). The map is campaigns.map_name
    (cr_darklands_map_1 has display_location cr_oldworld_map_1), else display_location;
    vanilla has the same folder in both. Campaigns on the same map share one read."""
    rows = {r["campaign_name"]: r for r in db.table("campaigns").rows}
    by_loc, out = {}, {}
    for c in campaigns:
        if c not in rows:
            out[c] = None, f"campaign {c} has no campaigns row, so its map could not be found"
            continue
        locs = tuple(dict.fromkeys(rows[c][k] for k in ("map_name", "display_location") if rows[c][k]))
        if locs not in by_loc:
            by_loc[locs] = None, (f"campaign_maps/{' or '.join(locs)}/map_data.esf is in none of the "
                                  f"loaded packs; map checks skipped")
            for loc in locs:
                try:
                    m = MapData.load(rpfm, pack, loc, deps, data.files, None)
                except RpfmError as e:
                    by_loc[locs] = None, f"could not read campaign_maps/{loc}/map_data.esf: {e}"
                    break
                if m:
                    by_loc[locs] = m, None
                    break
        out[c] = by_loc[locs]
    return out


def report(pack, campaigns, data, findings, verbose, elapsed):
    """The report, as lines."""
    sev_order = {ERROR: 0, WARN: 1, INFO: 2}
    rows = sum(len(t.all_rows) for t in data.tables.values())
    out = [f"{os.path.basename(pack)}: {len(data.tables)} tables, {rows} rows; "
           f"campaign(s): {', '.join(campaigns) or '-'}  ({elapsed:.1f}s)"]
    counts = Counter(f.severity for f in findings)
    out += [f"{counts[ERROR]} error(s), {counts[WARN]} warning(s), {counts[INFO]} info", ""]

    groups = defaultdict(list)
    for f in findings:
        groups[(sev_order[f.severity], f.severity, f.check)].append(f)
    for (_, sev, check), fs in sorted(groups.items()):
        if sev == INFO and not verbose:
            continue
        out.append(f"[{sev.upper()}] {check} ({len(fs)})")
        for f in sorted(fs, key=lambda f: (f.table, _natural(f.where))):
            out.append(f"  {f.table} / {f.where}: {f.message}")
        out.append("")
    if not verbose and counts[INFO]:
        info = Counter(f.check for f in findings if f.severity == INFO)
        out += ["info (use -v to list): " + ", ".join(f"{k} {v}" for k, v in sorted(info.items())), ""]
    if counts[ERROR] or counts[WARN]:
        out.append("ERROR: fix before building; CA's own start_pos never does this. "
                   "WARNING: still builds, but probably not what you meant. "
                   "Most locations read <table> / <fragment file> row <n>.")
    else:
        out.append("No errors or warnings.")
    return out


def _natural(s):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s)]


if __name__ == "__main__":
    sys.exit(main())
