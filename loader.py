"""Load tables and map data through the RPFM server.

Table merging follows how the game reads DB tables: a path present in
several packs comes from the first pack in load order (mods alphabetically,
then vanilla), then every fragment of a table is applied in file-name order
and the first row for a key wins. That is how "!!!!corrections" fragments
override "!!!vanilla" ones, and how any mod fragment overrides "data__".
"""
from dataclasses import dataclass, field

from rpfm_client import RpfmError, cell


@dataclass
class Row:
    values: dict
    fragment: str          # file name of the fragment, e.g. "!!!vanilla_regions"
    index: int             # row index inside that fragment

    def __getitem__(self, k):
        return self.values[k]

    def get(self, k, default=None):
        return self.values.get(k, default)

    def where(self):
        return f"{self.fragment} row {self.index + 1}"


@dataclass
class Table:
    name: str                              # e.g. "start_pos_regions"
    fields: list = field(default_factory=list)   # processed field dicts
    keys: list = field(default_factory=list)     # key column names
    rows: list = field(default_factory=list)     # effective rows (after overrides)
    all_rows: list = field(default_factory=list) # every row of every fragment
    overridden: list = field(default_factory=list)  # (loser Row, winner Row)
    fragments: list = field(default_factory=list)

    def key_of(self, row):
        return tuple(row.values[k] for k in self.keys)

    def col(self, name):
        return next((f for f in self.fields if f["name"] == name), None)

    def index(self, column):
        """Map column value -> list of effective rows."""
        out = {}
        for r in self.rows:
            out.setdefault(r.values[column], []).append(r)
        return out

    def lookup(self, column):
        """Map column value -> first effective row."""
        out = {}
        for r in self.rows:
            out.setdefault(r.values[column], r)
        return out


class _Decoder:
    def __init__(self, rpfm, pack_key):
        self.rpfm = rpfm
        self.pack = pack_key
        self.decode_errors = []   # (path, message)
        self._fields = {}

    def _decode_into(self, t, path, source, label):
        try:
            res = self.rpfm.call({"DecodePackedFile": [self.pack, path, source]})
        except RpfmError as e:
            self.decode_errors.append((path, str(e)))
            return
        if "DBRFileInfo" not in res:
            self.decode_errors.append((path, f"not decoded as DB ({next(iter(res))})"))
            return
        db = res["DBRFileInfo"][0]["table"]
        key = (t.name, db["definition"]["version"], source == "AssKitFiles")
        if key not in self._fields:
            self._fields[key] = self.rpfm.call({"FieldsProcessed": db["definition"]})["VecField"]
        fields = self._fields[key]
        names = [f["name"] for f in fields]
        if not t.fields:
            t.fields = fields
            t.keys = [f["name"] for f in fields if f["is_key"]]
        elif names != [f["name"] for f in t.fields]:
            # Rows are read by column name, so a different version still merges;
            # columns missing from this fragment just come back as None.
            pass
        t.fragments.append(label)
        for i, raw in enumerate(db["table_data"]):
            t.all_rows.append(Row(dict(zip(names, map(cell, raw))), label, i))

    @staticmethod
    def _finalize(t):
        seen = {}
        for r in t.all_rows:
            k = tuple(r.values.get(c) for c in t.keys) if t.keys else None
            if k is not None and k in seen:
                t.overridden.append((r, seen[k]))
                continue
            if k is not None:
                seen[k] = r
            t.rows.append(r)
        return t


class PackData(_Decoder):
    """Every DB table in the target pack, merged per table."""

    def __init__(self, rpfm, pack_path):
        super().__init__(rpfm, pack_path)
        self.tables = {}
        self.files = []

    def table(self, name):
        return self.tables.get(name) or Table(name)

    def load(self):
        tv = self.rpfm.call({"GetPackFileDataForTreeView": self.pack})
        _, infos = tv["ContainerInfoVecRFileInfo"]
        self.files = sorted(i["path"] for i in infos)
        by_table = {}
        for path in self.files:
            parts = path.split("/")
            if len(parts) == 3 and parts[0] == "db" and parts[1].endswith("_tables"):
                by_table.setdefault(parts[1][:-len("_tables")], []).append(path)
        for name, paths in sorted(by_table.items()):
            t = Table(name)
            for path in sorted(paths, key=lambda p: p.rsplit("/", 1)[1]):
                self._decode_into(t, path, "PackFile", path.rsplit("/", 1)[1])
            self.tables[name] = self._finalize(t)
        return self


class GameDB(_Decoder):
    """Game tables as the game would see them with the target pack loaded.

    Tables are loaded lazily by name. `vanilla_only` restricts to CA's files
    (used for calibrating against CA's own data). Assembly Kit copies are
    used only for tables that no pack ships.
    """

    def __init__(self, rpfm, pack_path, pack_files, deps, vanilla_only=False):
        super().__init__(rpfm, pack_path)
        self.vanilla_only = vanilla_only
        self._cache = {}
        self._index = {}   # table folder -> list of (pack priority, fragment, path, source, label)
        pack_name = pack_path.replace("\\", "/").rsplit("/", 1)[-1]
        sources = [("GameFiles", deps.get("vanilla_packed_files", []), 2),
                   ("AssKitFiles", deps.get("asskit_tables", []), 3)]
        if not vanilla_only:
            sources.append(("ParentFiles", deps.get("parent_packed_files", []), 1))
            sources.append(("PackFile", [{"path": p, "container_name": pack_name} for p in pack_files], 1))
        for source, infos, prio in sources:
            for info in infos:
                parts = info["path"].split("/")
                if len(parts) != 3 or parts[0] != "db":
                    continue
                pack = info.get("container_name") or ("assembly kit" if source == "AssKitFiles" else "vanilla")
                self._index.setdefault(parts[1], []).append(((prio, pack), parts[2], info["path"], source,
                                                             f"{pack}:{parts[2]}"))

    def ak_table(self, name):
        """The Assembly Kit's copy of a table, even when the game ships one."""
        key = "ak:" + name
        if key not in self._cache:
            t = Table(name)
            for e in self._index.get(name + "_tables", []):
                if e[3] == "AssKitFiles":
                    self._decode_into(t, e[2], e[3], e[4])
            self._cache[key] = self._finalize(t)
        return self._cache[key]

    def table(self, name):
        if name in self._cache:
            return self._cache[name]
        entries = self._index.get(name + "_tables", [])
        if any(e[0][0] < 3 for e in entries):
            entries = [e for e in entries if e[0][0] < 3]   # AK copy only as a last resort
        by_path = {}
        for e in sorted(entries):
            by_path.setdefault(e[2], e)                  # first pack in load order owns a path
        t = Table(name)
        for _, _, path, source, label in sorted(by_path.values(), key=lambda e: e[1]):
            self._decode_into(t, path, source, label)
        self._cache[name] = self._finalize(t)
        return self._cache[name]


class ReferenceData(PackData):
    """CA's own start_pos tables: the game's db.pack copies of the tables it
    ships, the Assembly Kit copies of the rest. A known-good baseline.
    """

    def __init__(self, rpfm, pack_key, deps):
        super().__init__(rpfm, pack_key)
        self.deps = deps

    def load(self, prefix="start_pos_"):
        sources = {}
        for src, key in (("AssKitFiles", "asskit_tables"), ("GameFiles", "vanilla_packed_files")):
            for info in self.deps.get(key, []):
                parts = info["path"].split("/")
                if len(parts) == 3 and parts[0] == "db" and parts[1].startswith(prefix):
                    sources[parts[1]] = (src, info["path"])
        for folder, (src, path) in sources.items():
            t = Table(folder[:-len("_tables")])
            self._decode_into(t, path, src, ("AK " if src == "AssKitFiles" else "game ") + path.rsplit("/", 1)[1])
            if src == "AssKitFiles":
                self._stringify_ids(t)
            self.tables[t.name] = self._finalize(t)
        return self

    @staticmethod
    def _stringify_ids(t):
        """The AK defines start_pos IDs as integers; packs (and the game's copies) carry them
        as strings. Make the AK's strings too, or no ID link between the tables resolves."""
        cols = [f["name"] for f in t.fields
                if f["name"].lower() == "id"
                or (f["is_reference"] and f["is_reference"][0].startswith("start_pos_")
                    and f["is_reference"][1].lower() == "id")]
        for r in t.all_rows:
            for c in cols:
                if isinstance(r.values.get(c), int) and not isinstance(r.values[c], bool):
                    r.values[c] = str(r.values[c])


@dataclass
class MapRegion:
    key: str
    flag: int
    display_min: tuple
    display_max: tuple
    hex_min: tuple
    hex_max: tuple


@dataclass
class MapData:
    """The parts of campaign_maps/<display_location>/map_data.esf the checks use."""
    path: str
    width: int                 # HEX_MAP_DATA: the logical grid startx/starty live on
    height: int
    display_max: tuple         # CAMPAIGN_THEATRE display-space bounds
    regions: dict              # key -> MapRegion

    def contains(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height

    @classmethod
    def load(cls, rpfm, pack_key, display_location, deps, pack_files, pack_paths):
        """Find and decode map_data.esf. RPFM only decodes ESF with its
        enable_esf_editor setting on, so it is switched on for the call and restored."""
        path = f"campaign_maps/{display_location}/map_data.esf"
        if path in pack_files:
            candidates = [(pack_key, "PackFile")]
        else:
            candidates = [(pack_key, "ParentFiles")] if any(
                i["path"] == path for i in deps.get("parent_packed_files", [])) else []
            if any(i["path"] == path for i in deps.get("vanilla_packed_files", [])):
                candidates.append((pack_key, "GameFiles"))
        if not candidates:
            return None
        old = rpfm.call({"SettingsGetBool": "enable_esf_editor"}).get("Bool", False)
        try:
            if not old:
                rpfm.call({"SettingsSetBool": ["enable_esf_editor", True]})
            res = rpfm.call({"DecodePackedFile": [candidates[0][0], path, candidates[0][1]]})
        finally:
            if not old:
                rpfm.call({"SettingsSetBool": ["enable_esf_editor", False]})
        if "ESFRFileInfo" not in res:
            raise RpfmError(f"{path} did not decode as ESF")
        return cls._parse(path, res["ESFRFileInfo"][0]["root_node"])

    @classmethod
    def _parse(cls, path, root):
        def rec(node, name):
            for block in node["Record"]["children"]:
                for c in block:
                    if isinstance(c, dict) and "Record" in c and c["Record"]["name"] == name:
                        return c
            return None

        def val(v):
            k, x = next(iter(v.items()))
            if isinstance(x, dict) and "value" in x:
                return x["value"]
            if k == "Coord2d":
                return (x["x"], x["y"])
            return x

        theatre = rec(rec(root, "theatres"), "CAMPAIGN_THEATRE")
        tvals = theatre["Record"]["children"][0]
        hexmap = rec(theatre, "HEX_MAP_DATA")["Record"]["children"][0]
        w, h = val(hexmap[0]), val(hexmap[1])
        regions = {}
        masked = rec(rec(root, "REGIONS_DATA"), "MASKED_REGIONS_DATA")
        for block in masked["Record"]["children"]:
            for c in block:
                if not (isinstance(c, dict) and "Record" in c and c["Record"]["name"] == "REGIONS_BLOCK"):
                    continue
                for rblock in c["Record"]["children"]:
                    for rd in rblock:
                        if not (isinstance(rd, dict) and "Record" in rd and rd["Record"]["name"] == "REGION_DATA"):
                            continue
                        prims = [val(x) for x in rd["Record"]["children"][0]
                                 if not (isinstance(x, dict) and "Record" in x)][:8]
                        k = prims[0]
                        regions[k] = MapRegion(k, prims[1], prims[2], prims[3], (prims[4], prims[5]), (prims[6], prims[7]))
        return cls(path, w, h, val(tvals[1]), regions)
