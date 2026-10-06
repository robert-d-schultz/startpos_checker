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


class SettingsError(RuntimeError):
    pass


def _settings():
    if not os.path.isfile(SETTINGS) and os.path.isfile(SETTINGS_EXAMPLE):
        shutil.copyfile(SETTINGS_EXAMPLE, SETTINGS)
    cfg = configparser.ConfigParser()
    try:
        cfg.read(SETTINGS, encoding="utf-8-sig")
    except configparser.Error as e:
        raise SettingsError(f"{SETTINGS} could not be read: {e}") from None
    return cfg


def game(override=None):
    """The Game to check: `override` (--game) or settings.ini's game, else Warhammer III."""
    from games import DEFAULT, GAMES
    flag = (override or _settings().get("paths", "game", fallback="") or DEFAULT).strip().lower()
    if flag not in GAMES:
        raise SettingsError(f"unknown game {flag!r} (use one of: {', '.join(GAMES)})")
    return GAMES[flag]


def rpfm_server(override=None):
    """rpfm_server.exe: `override` (--rpfm) or settings.ini's rpfm_server."""
    path = override
    if not path:
        path = _settings().get("paths", "rpfm_server", fallback="")
        if not path.strip():
            raise SettingsError(f"Set rpfm_server in {SETTINGS} to the full path of rpfm_server.exe "
                                "(it is in your RPFM 5 folder, next to rpfm_ui.exe).")
    path = path.strip().strip('"')
    if not os.path.isfile(path):
        raise SettingsError(f"rpfm_server.exe not found at {path}. Fix rpfm_server in {SETTINGS}.")
    return path


def rpfm_settings():
    """The string settings of RPFM ({'warhammer_3': <game folder>, ...}), or None
    if RPFM has never been run."""
    try:
        with open(os.path.join(RPFM_CONFIG, "settings.json"), encoding="utf-8") as fh:
            return json.load(fh).get("string", {})
    except (OSError, ValueError):
        return None


def rpfm_setup_problems(game):
    """What is missing from RPFM's setup for the checker to work on `game`, as messages."""
    settings = rpfm_settings()
    if settings is None:
        return ["RPFM has not been set up on this computer. Open rpfm_ui.exe once, go to "
                f"PackFile > Settings and fill in the {game.rpfm_name} Game Folder."]
    out = []
    folder = settings.get(game.key, "")
    if not folder or not os.path.isfile(os.path.join(folder, game.exe)):
        out.append(f"RPFM does not know where {game.name} is installed. In rpfm_ui.exe, go to "
                   f"PackFile > Settings and set the {game.rpfm_name} Game Folder (the folder with "
                   f"{game.exe} in it).")
    deps = os.path.join(RPFM_CONFIG, "dependencies")
    if not (os.path.isdir(deps) and any(f.startswith(game.deps_prefix) for f in os.listdir(deps))):
        out.append(f"RPFM has no dependencies cache for {game.name}. In rpfm_ui.exe, select "
                   f"Game Selected > {game.rpfm_name}, then Game Selected > Generate Dependencies Cache, "
                   "and wait for it to finish.")
    return out
