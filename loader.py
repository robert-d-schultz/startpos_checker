"""Load tables and map data through the RPFM server.

Table merging follows how the game reads DB tables: a path present in
several packs comes from the first pack in load order (mods alphabetically,
then vanilla), then every fragment of a table is applied in file-name order
and the first row for a key wins. That is how "!!!!corrections" fragments
override "!!!vanilla" ones, and how any mod fragment overrides "data__".
"""
import base64
import struct
from dataclasses import dataclass, field

from paths import rpfm_all_settings
from rpfm_client import RpfmError, source as file_source


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
    def __init__(self, rpfm, pack):
        """`pack` is the open pack, as pack.open returns it."""
        self.rpfm = rpfm
        self.pack = pack["key"]
        self.pack_name = pack["name"]
        self.decode_errors = []   # (path, message)
        self.empty_undecodable = []   # paths of empty fragments RPFM has no definition for

    def _decode_into(self, t, path, source, label):
        file = {"source": file_source(source, self.pack), "path": path}
        try:
            info = self.rpfm.call("table.info", {"file": file})
            res = self.rpfm.call("table.rows", {"file": file, "limit": info["row_count"]})
        except RpfmError as e:
            # RPFM can't open a fragment of a table version it has no definition for, even an
            # empty one (3K packs carry many of the AK's empty start_pos tables). Those have no
            # rows to lose, so tell them apart from real decode errors by the header's row count.
            if self._row_count(file) == 0:
                self.empty_undecodable.append(path)
            else:
                self.decode_errors.append((path, str(e)))
            return
        fields = info["columns"]
        names = res["columns"]
        if not t.fields:
            t.fields = fields
            t.keys = [f["name"] for f in fields if f["is_key"]]
        elif names != [f["name"] for f in t.fields]:
            # Rows are read by column name, so a different version still merges;
            # columns missing from this fragment just come back as None.
            pass
        t.fragments.append(label)
        for row in res["rows"]:
            t.all_rows.append(Row(dict(zip(names, row["values"])), label, row["index"]))

    def _row_count(self, file):
        """The row count in a DB fragment's header, or None if it can't be read."""
        try:
            res = self.rpfm.call("file.read", {"file": file, "format": "raw"})
            data = base64.b64decode(res["contents"]["base64"])
            i = 0
            if data[:4] == b"\xfd\xfe\xfc\xff":   # GUID: u16 length, then UTF-16 characters
                i = 6 + 2 * struct.unpack_from("<H", data, 4)[0]
            if data[i:i + 4] == b"\xfc\xfd\xfe\xff":   # version
                i += 8
            return struct.unpack_from("<I", data, i + 1)[0]   # after the mysterious byte
        except (RpfmError, KeyError, ValueError, struct.error):
            return None

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

    def __init__(self, rpfm, pack):
        super().__init__(rpfm, pack)
        self.tables = {}
        self.files = []

    def table(self, name):
        return self.tables.get(name) or Table(name)

    def load(self):
        self.files = sorted(f["path"] for f in self.rpfm.pages("files.list", {"source": {"pack": self.pack}}, "files"))
        by_table = {}
        for path in self.files:
            parts = path.split("/")
            if len(parts) == 3 and parts[0] == "db" and parts[1].endswith("_tables"):
                by_table.setdefault(parts[1][:-len("_tables")], []).append(path)
        for name, paths in sorted(by_table.items()):
            t = Table(name)
            for path in sorted(paths, key=lambda p: p.rsplit("/", 1)[1]):
                self._decode_into(t, path, "pack", path.rsplit("/", 1)[1])
            self.tables[name] = self._finalize(t)
        return self


class GameDB(_Decoder):
    """Game tables as the game would see them with the target pack loaded.

    Tables are loaded lazily by name. `vanilla_only` restricts to CA's files
    (used for calibrating against CA's own data). Assembly Kit copies are
    used only for tables that no pack ships.
    """

    def __init__(self, rpfm, pack, pack_files, deps, vanilla_only=False):
        super().__init__(rpfm, pack)
        self.vanilla_only = vanilla_only
        self._cache = {}
        self._index = {}   # table folder -> list of (pack priority, fragment, path, source, label)
        sources = [("game_files", deps.get("vanilla_packed_files", []), 2),
                   ("assembly_kit", deps.get("asskit_tables", []), 3)]
        if not vanilla_only:
            sources.append(("parent_files", deps.get("parent_packed_files", []), 1))
            sources.append(("pack", [{"path": p, "container_name": self.pack_name} for p in pack_files], 1))
        for source, infos, prio in sources:
            for info in infos:
                parts = info["path"].split("/")
                if len(parts) != 3 or parts[0] != "db":
                    continue
                pack = info.get("container_name") or ("assembly kit" if source == "assembly_kit" else "vanilla")
                self._index.setdefault(parts[1], []).append(((prio, pack), parts[2], info["path"], source,
                                                             f"{pack}:{parts[2]}"))

    def ak_table(self, name):
        """The Assembly Kit's copy of a table, even when the game ships one."""
        key = "ak:" + name
        if key not in self._cache:
            t = Table(name)
            for e in self._index.get(name + "_tables", []):
                if e[3] == "assembly_kit":
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

    def __init__(self, rpfm, pack, deps):
        super().__init__(rpfm, pack)
        self.deps = deps

    def load(self, prefix="start_pos_"):
        sources = {}
        for src, key in (("assembly_kit", "asskit_tables"), ("game_files", "vanilla_packed_files")):
            for info in self.deps.get(key, []):
                parts = info["path"].split("/")
                if len(parts) == 3 and parts[0] == "db" and parts[1].startswith(prefix):
                    sources[parts[1]] = (src, info["path"])
        for folder, (src, path) in sources.items():
            t = Table(folder[:-len("_tables")])
            self._decode_into(t, path, src, ("AK " if src == "assembly_kit" else "game ") + path.rsplit("/", 1)[1])
            if src == "assembly_kit":
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
        enable_esf_editor setting on, so the session is configured with it on
        (sessions have their own settings; RPFM's settings file is untouched)."""
        path = f"campaign_maps/{display_location}/map_data.esf"
        if path in pack_files:
            candidates = ["pack"]
        else:
            candidates = ["parent_files"] if any(
                i["path"] == path for i in deps.get("parent_packed_files", [])) else []
            if any(i["path"] == path for i in deps.get("vanilla_packed_files", [])):
                candidates.append("game_files")
        if not candidates:
            return None
        settings = rpfm_all_settings() or {}
        settings.setdefault("bool", {})["enable_esf_editor"] = True
        rpfm.call("session.configure", {"settings": settings})
        res = rpfm.call("file.read", {"file": {"source": file_source(candidates[0], pack_key), "path": path}})
        data = res["contents"].get("data")
        if not (isinstance(data, dict) and "ESF" in data):
            raise RpfmError(f"{path} did not decode as ESF")
        return cls._parse(path, data["ESF"]["root_node"])

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
