"""The games the checker supports, and what differs between them outside the checks.

Each game has its own checker class: WH3's in game_checks.py, Three
Kingdoms' in checks_3k.py. `--game` (or settings.ini's `game`) picks one.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Game:
    flag: str              # --game value
    key: str               # RPFM's game key (SetGameSelected, settings.json)
    name: str              # for messages
    rpfm_name: str         # how RPFM's menus name the game
    exe: str               # in the game folder
    deps_prefix: str       # RPFM dependencies cache file prefix
    calibrate_campaign: str
    calibrate_map: str

    def checker(self):
        if self.flag == "3k":
            from checks_3k import ThreeKingdomsChecker
            return ThreeKingdomsChecker
        from game_checks import GameChecker
        return GameChecker


GAMES = {
    "wh3": Game("wh3", "warhammer_3", "Warhammer III", "Warhammer 3", "Warhammer3.exe", "wh3.",
                "wh3_main_combi", "wh3_main_combi_map_7"),
    "3k": Game("3k", "three_kingdoms", "Three Kingdoms", "Three Kingdoms", "Three_Kingdoms.exe", "3k.",
               "3k_main_campaign_map", "3k_dlc07_main_map"),
}
DEFAULT = "wh3"
