# src/RioGameDataProvider.py

import requests
import json
import time
import platform
import os
from pathlib import Path
from qtpy.QtCore import QThreadPool
from qtpy.QtCore import QObject, Signal
from pyrio.lookup import LookupDicts, Lookup

# Cached Lookup instance — avoid creating a new one for every character lookup
_lookup_instance = None
def get_lookup():
    global _lookup_instance
    if _lookup_instance is None:
        _lookup_instance = Lookup()
    return _lookup_instance

from .Workers import Worker
from qtpy.QtCore import QFileSystemWatcher, QTimer, Qt
from qtpy.QtGui import QStandardItemModel, QStandardItem
from .SettingsManager import SettingsManager

from pyrio.stat_file_parser import HudObj
from pyrio.team_name_algo import In_Game_Team_Names_List, team_name

def get_default_hud_file_path() -> Path:
    """
    Returns the OS-specific path to Project Rio's decoded.hud.json file.
    """
    system = platform.system()

    if system == "Darwin":  # macOS
        home = Path.home()
        return home / "Library" / "Application Support" / "Project Rio" / "HudFiles" / "decoded.hud.json"

    elif system == "Windows":
        home = Path.home()
        return home / "Documents" / "Project Rio" / "HudFiles" / "decoded.hud.json"

    else:
        return Path("/invalid/path")
    
def get_user_hud_path() -> Path | None:
    user_path = SettingsManager.Get("project_rio.hud_path", "")
    if user_path:
        path = Path(user_path)
        if path.exists() and path.is_file() and path.suffix == ".json":
            return path
    return None

class RioGameDataProvider(QObject):
    instance = None

    live_games_updated = Signal(list)
    live_game_selected = Signal(dict)

    def __init__(self):
        super().__init__()
        if RioGameDataProvider.instance is not None:
            raise Exception("RioGameDataProvider is a singleton! Use RioGameDataProvider.instance")
        RioGameDataProvider.instance = self
        self.API_URL = "https://api.projectrio.app/populate_db/ongoing_game/"
        self.hud_file = get_user_hud_path()
        self.live_games = []
        self.current_game = None
        self.threadPool = QThreadPool()
        
        if self.hud_file:
            self.hud_watcher = RioHUDWatcher(self.hud_file)
            self.hud_watcher._reload_game_data()
            self.hud_watcher.hud_game_updated.connect(self._on_hud_game_update)
        else:
            logger.warning(f"[RioGameDataProvider] HUD file not found at {self.hud_file}")

    def reload_hud_path(self):
        new_path = get_user_hud_path()
        if not new_path:
            return
        
        self.hud_file = new_path
        if hasattr(self, "hud_watcher") and self.hud_watcher:
            self.hud_watcher.update_hud_file(self.hud_file)
        else:
            self.hud_watcher = RioHUDWatcher(self.hud_file)
            self.hud_watcher.hud_game_updated.connect(self._on_hud_game_update)
            self.hud_watcher._reload_game_data()

    def FetchHUDGame(self):
        """
        Immediately emit the HUD game data (no network call).
        Called on startup and whenever the user hits refresh.
        """
        self.reload_hud_path()
        games = []
        if hasattr(self, "hud_watcher") and self.hud_watcher.latest_game_data:
            hud_game = dict(self.hud_watcher.latest_game_data)
            hud_game["source"] = "hud"
            games.append(hud_game)
        self.live_games = list(games)
        self.live_games_updated.emit(list(games))
        return games

    def FetchGames(self):
        """
        Fetches games: immediately populates HUD game, then fetches
        server games in background and merges them in.
        """
        # Immediately show HUD game
        hud_games = self.FetchHUDGame()

        # Fetch server games in background (doesn't block the HUD game from showing)
        worker = Worker(self._fetch_server_games)
        worker.signals.result.connect(
            lambda server_games: self._merge_server_games(hud_games, server_games))
        worker.signals.error.connect(lambda e: print(f"[RioGameDataProvider] Error fetching server games: {e}"))
        self.threadPool.start(worker)

    def _fetch_server_games(self, progress_callback=None, cancel_event=None):
        """Fetch only server games (runs on worker thread)."""
        try:
            response = requests.get(self.API_URL, timeout=5)
            response.raise_for_status()
            server_games = response.json().get("ongoing_games", [])
            return [
                {**g, "source": "server"} for g in server_games
                if int(g.get("start_time", 0)) > (time.time() - 60 * 30)
            ]
        except Exception as e:
            logger.debug(f"[RioGameDataProvider] Failed to fetch server games: {e}")
            return []

    def _merge_server_games(self, hud_games, server_games):
        """Merge server games into the list and re-emit."""
        if server_games:
            all_games = list(hud_games) + server_games
            self.live_games = all_games
            self.live_games_updated.emit(all_games)

    def SelectLiveGame(self, game_dict):
        """
        Parses a selected game and emits `live_game_selected`.
        """
        parsed = self.parse_game_data(game_dict)
        parsed["source"] = game_dict.get("source", "unknown")
        self.current_game = parsed
        self.live_game_selected.emit(parsed)

    def parse_game_data(self, game_json):
        """
        Convert a Project Rio game JSON object into a TSH-compatible data format.
        """
        data = {"entrants": [[{}], [{}]]}

        try:
            data["team1score"] = game_json["home_score"]
            data["team2score"] = game_json["away_score"]

            for i in range(2):
                team = "home" if i == 0 else "away"
                roster = [
                    get_lookup().lookup(LookupDicts.CHAR_NAME, game_json[f"{team}_roster_{j}_char"])
                    for j in range(9)
                ]
                data["entrants"][i][0]["roster"] = roster
                data["entrants"][i][0]["captainIndex"] = game_json[f"{team}_captain"]
                data["entrants"][i][0]['rioName'] = game_json[f'{team}_player']
                data["entrants"][i][0]['msb_team'] = self.GetMSBTeamName(data["entrants"][i][0]["roster"], data["entrants"][i][0]["captainIndex"])
                
                batter_index = game_json["batter"]
                pitcher_index = game_json["pitcher"]

            if game_json["half_inning"] == 0:
                data['half_inning'] = 'Top'
                data['batter'] = data["entrants"][1][0]["roster"][batter_index]
                data['pitcher'] = data["entrants"][0][0]["roster"][pitcher_index]
            else:
                data["half_inning"] = 'Bottom'
                data['batter'] = data["entrants"][0][0]["roster"][batter_index]
                data['pitcher'] = data["entrants"][1][0]["roster"][pitcher_index]

            data['inning'] = game_json["inning"]
            data['outs'] = game_json["outs"]
            data['strikes'] = game_json.get("strikes", 0)
            data['balls'] = game_json.get("balls", 0)

            data['runnerOn1'] = game_json["runner_on_first"]
            data['runnerOn2'] = game_json["runner_on_second"]
            data['runnerOn3'] = game_json["runner_on_third"]

        except Exception as e:
            logger.error(f"[RioGameDataProvider] Failed to parse game data: {e}")

        return data
    
    def _on_hud_game_update(self, game_json):
        parsed = self.parse_game_data(game_json)
        parsed["source"] = "hud"
        self.current_game = parsed
        self.live_game_selected.emit(parsed)

    def GetMSBTeamModel(self) -> list:
        return [''] + In_Game_Team_Names_List
    
    def GetMSBTeamName(self, roster, captain_index):
        return team_name(roster, roster[captain_index])
    
class RioHUDWatcher(QObject):
    hud_game_updated = Signal(dict)

    def __init__(self, hud_file: Path):
        super().__init__()
        self.hud_file = hud_file

        self.watcher = QFileSystemWatcher()
        self.latest_game_data = None
        self.watcher.addPath(str(self.hud_file))
        self.watcher.fileChanged.connect(self._on_file_changed)

        self.timer = QTimer()
        self.timer.setInterval(100)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._reload_game_data)

    def _on_file_changed(self):
        self.timer.start()

    def _reload_game_data(self):
        try:
            with open(self.hud_file, "r") as f:
                data = json.load(f)
                game = self.convert_hud_data_format(HudObj(data))
                self.latest_game_data = game  # <-- Save for access
                self.hud_game_updated.emit(game)
        except Exception as e:
            logger.error(f"[RioHUDWatcher] Error reading HUD file: {e}")

    def update_hud_file(self, new_hud_file: Path):
        # Remove old path from watcher
        self.watcher.removePath(str(self.hud_file))
        self.hud_file = new_hud_file
        self.watcher.addPath(str(self.hud_file))
        self._reload_game_data()
        
    def convert_hud_data_format(self, hud_data: HudObj):
    ##TODO FIX Captains index upon Project Rio Update
        game = {
            "away_captain": hud_data.captain_index(1),
            "away_player": hud_data.player(0),
            "away_score": hud_data.score(0),
            "away_stars": hud_data.team_stars(0),
            "batter": hud_data.batter_roster_location(),
            "half_inning": hud_data.half_inning(),
            "home_captain": hud_data.captain_index(0),
            "home_player": hud_data.player(1),
            "home_score": hud_data.score(1),
            "home_stars": hud_data.team_stars(1),
            "inning": hud_data.inning(),
            "outs": hud_data.outs(),
            "pitcher": hud_data.pitcher_roster_location(),
            "runner_on_first": hud_data.runner_on_first(),
            "runner_on_second": hud_data.runner_on_second(),
            "runner_on_third": hud_data.runner_on_third(),
            "stadium_id": -1,
            "start_time": -1,
            "tag_set": -1,
            "balls": hud_data.balls(),
            "strikes": hud_data.strikes()
        }

        def flatten_roster_dict(roster_dict: dict, team_name: str) -> dict:
            flat = {}
            for index, data in roster_dict.items():
                key = f"{team_name}_roster_{index}_char"
                flat[key] = get_lookup().lookup(LookupDicts.CHAR_NAME, data["char_id"])
            return flat
        
        game.update(flatten_roster_dict(hud_data.roster(0), 'away'))
        game.update(flatten_roster_dict(hud_data.roster(1), 'home'))

        return game
    
RioGameDataProvider.instance = RioGameDataProvider()