# src/RioGameDataProvider.py

import requests
import json
import time
import platform
import os
from pathlib import Path
from qtpy.QtCore import QThreadPool
from qtpy.QtCore import QObject, Signal
from pyRio.lookup import LookupDicts, Lookup
from .Workers import Worker
from qtpy.QtCore import QFileSystemWatcher, QTimer

from pyRio.stat_file_parser import HudObj

def get_hud_file_path() -> Path:
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
        print("[RioHUDWatcher] Unsupported OS")
        return Path("/invalid/path")

class RioGameDataProvider(QObject):
    """
    Provides live game data from Project Rio, including server-based and HUD (local) games.
    Emits signals when new data is available.
    """

    live_games_updated = Signal(list)
    live_game_selected = Signal(dict)

    def __init__(self):
        super().__init__()
        self.API_URL = "https://api.projectrio.app/populate_db/ongoing_game/"
        self.hud_file = get_hud_file_path()
        self.live_games = []
        self.current_game = None
        self.threadPool = QThreadPool()
        
        self.hud_watcher = RioHUDWatcher(self.hud_file)
        self.hud_watcher._reload_game_data()
        self.hud_watcher.hud_game_updated.connect(self._on_hud_game_update)

    def FetchGames(self):
        """
        Fetches both server and HUD games. Emits a single combined list.
        """
        worker = Worker(self._fetch_all_games)
        worker.signals.result.connect(self._on_live_games_fetched)
        worker.signals.error.connect(lambda e: print(f"[RioGameDataProvider] Error fetching games: {e}"))
        worker.signals.finished.connect(lambda: print("[RioGameDataProvider] Finished fetching all games"))
        self.threadPool.start(worker)

    def _fetch_all_games(self, progress_callback=None, cancel_event=None):
        games = []

        # Fetch server games
        try:
            response = requests.get(self.API_URL)
            response.raise_for_status()
            server_games = response.json().get("ongoing_games", [])
            recent_server_games = [
                {**g, "source": "server"} for g in server_games
                if int(g.get("start_time", 0)) > (time.time() - 60 * 40)
            ]
            games.extend(recent_server_games)
        except Exception as e:
            print(f"[RioGameDataProvider] Failed to fetch server games: {e}")

        # Fetch HUD game from cached watcher state
        hud_game = self.hud_watcher.latest_game_data
        if hud_game:
            hud_game["source"] = "hud"
            games.append(hud_game)

        return games

    def _on_live_games_fetched(self, all_games):
        print(f"[DEBUG] Total fetched games: {len(all_games)}")
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
                    Lookup().lookup(LookupDicts.CHAR_NAME, game_json[f"{team}_roster_{j}_char"])
                    for j in range(9)
                ]
                data["entrants"][i][0]["roster"] = roster
                data["entrants"][i][0]["captainIndex"] = game_json[f"{team}_captain"]
                data["entrants"][i][0]['rioName'] = game_json[f'{team}_player']

                print(f"[DEBUG] Parsed game data for team {team}: {data['entrants'][i][0]}")

            data['half_inning'] = 'Top' if game_json["half_inning"] == 0 else 'Bottom'
            data['inning'] = game_json["inning"]

            data['runnerOn1'] = game_json["runner_on_first"]
            data['runnerOn2'] = game_json["runner_on_second"]
            data['runnerOn3'] = game_json["runner_on_third"]

            print(game_json)

        except Exception as e:
            print(f"[RioGameDataProvider] Failed to parse game data: {e}")

        return data
    
    def _on_hud_game_update(self, game_json):
        parsed = self.parse_game_data(game_json)
        parsed["source"] = "hud"
        self.current_game = parsed
        self.live_game_selected.emit(parsed)
    
class RioHUDWatcher(QObject):
    hud_game_updated = Signal(dict)

    def __init__(self, hud_file: Path):
        super().__init__()
        self.hud_file = hud_file

        print(f"[RioHUDWatcher] Initialized. Watching: {self.hud_file}")

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
            print(f"[RioHUDWatcher] Error reading HUD file: {e}")
        
    def convert_hud_data_format(self, hud_data: HudObj):
        game = {
            "away_captain": hud_data.captain_index(1),
            "away_player": hud_data.player(1),
            "away_score": hud_data.score(1),
            "away_stars": hud_data.team_stars(1),
            "batter": hud_data.batter_roster_location(),
            "half_inning": hud_data.half_inning(),
            "home_captain": hud_data.captain_index(0),
            "home_player": hud_data.player(0),
            "home_score": hud_data.score(0),
            "home_stars": hud_data.team_stars(0),
            "inning": hud_data.inning(),
            "outs": hud_data.outs(),
            "pitcher": hud_data.pitcher_roster_location(),
            "runner_on_first": hud_data.runner_on_first(),
            "runner_on_second": hud_data.runner_on_second(),
            "runner_on_third": hud_data.runner_on_third(),
            "stadium_id": -1,
            "start_time": -1,
            "tag_set": -1
        }

        def flatten_roster_dict(roster_dict: dict, team_name: str) -> dict:
            flat = {}
            for index, data in roster_dict.items():
                key = f"{team_name}_roster_{index}_char"
                flat[key] = Lookup().lookup(LookupDicts.CHAR_NAME, data["char_id"])
            return flat
        
        game.update(flatten_roster_dict(hud_data.roster(0), 'away'))
        game.update(flatten_roster_dict(hud_data.roster(1), 'home'))

        print(game)

        return game