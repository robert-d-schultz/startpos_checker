"""Paths from settings.ini, and a check of RPFM's own setup.

settings.ini sits next to this file. If it is missing it is created from
settings.example.ini, so there is always a file to fill in.
"""
import configparser
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "settings.ini")
SETTINGS_EXAMPLE = os.path.join(HERE, "settings.example.ini")
RPFM_CONFIG = os.path.join(os.environ.get("APPDATA", ""), "FrodoWazEre", "rpfm", "config")
WH3_EXE = "Warhammer3.exe"


class SettingsError(RuntimeError):
    pass


def rpfm_server(override=None):
    """rpfm_server.exe: `override` (--rpfm) or settings.ini's rpfm_server."""
    path = override
    if not path:
        if not os.path.isfile(SETTINGS) and os.path.isfile(SETTINGS_EXAMPLE):
            shutil.copyfile(SETTINGS_EXAMPLE, SETTINGS)
        cfg = configparser.ConfigParser()
        try:
            cfg.read(SETTINGS, encoding="utf-8-sig")
        except configparser.Error as e:
            raise SettingsError(f"{SETTINGS} could not be read: {e}") from None
        path = cfg.get("paths", "rpfm_server", fallback="")
        if not path.strip():
            raise SettingsError(f"Set rpfm_server in {SETTINGS} to the full path of rpfm_server.exe "
                                "(it is in your RPFM 5 folder, next to rpfm_ui.exe).")
    path = path.strip().strip('"')
    if not os.path.isfile(path):
        raise SettingsError(f"rpfm_server.exe not found at {path}. Fix rpfm_server in {SETTINGS}.")
    return path


def rpfm_all_settings():
    """RPFM's settings.json ({'bool': {...}, 'string': {...}, ...}), or None
    if RPFM has never been run."""
    try:
        with open(os.path.join(RPFM_CONFIG, "settings.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def rpfm_settings():
    """The string settings of RPFM ({'warhammer_3': <game folder>, ...}), or None
    if RPFM has never been run."""
    settings = rpfm_all_settings()
    return None if settings is None else settings.get("string", {})


def rpfm_setup_problems():
    """What is missing from RPFM's setup for the checker to work, as messages."""
    settings = rpfm_settings()
    if settings is None:
        return ["RPFM has not been set up on this computer. Open rpfm_ui.exe once, go to "
                "PackFile > Settings and fill in the Warhammer 3 Game Folder."]
    out = []
    game = settings.get("warhammer_3", "")
    if not game or not os.path.isfile(os.path.join(game, WH3_EXE)):
        out.append("RPFM does not know where Warhammer III is installed. In rpfm_ui.exe, go to "
                   "PackFile > Settings and set the Warhammer 3 Game Folder (the folder with "
                   f"{WH3_EXE} in it).")
    deps = os.path.join(RPFM_CONFIG, "dependencies")
    if not (os.path.isdir(deps) and any(f.startswith("wh3.") for f in os.listdir(deps))):
        out.append("RPFM has no dependencies cache for Warhammer III. In rpfm_ui.exe, select "
                   "Game Selected > Warhammer 3, then Game Selected > Generate Dependencies Cache, "
                   "and wait for it to finish.")
    return out
