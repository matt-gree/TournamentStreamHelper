# src/RioGameDataProvider.py

import requests
import json
import time
from pathlib import Path
from qtpy.QtCore import QThreadPool
from qtpy.QtCore import QObject, Signal
from pyRio.lookup import LookupDicts as rioLU
from .Workers import Worker

class RioGameDataProvider(QObject):
    """
    Provides live game data from Project Rio, including server-based games and HUD (local) games.
    Emits signals when new data is available.
    """

    live_games_updated = Signal(list)
    live_game_selected = Signal(dict)

    def __init__(self, hud_folder=None):
        super().__init__()
        self.API_URL = "https://api.projectrio.app/populate_db/ongoing_game/"
        self.hud_folder = hud_folder or (Path(__file__).parent / "test")
        self.live_games = []
        self.current_game = None
        self.threadPool = QThreadPool()

    def FetchGamesFromServer(self):
        """
        Starts a background thread to fetch live games from the Project Rio server.
        Emits `live_games_updated` with the results when done.
        """
        worker = Worker(self._fetch_games_from_server)

        worker.signals.result.connect(self._on_live_games_fetched)
        worker.signals.error.connect(lambda e: print(f"[RioGameDataProvider] Error fetching games: {e}"))
        worker.signals.finished.connect(lambda: print("[RioGameDataProvider] Finished fetching games"))

        self.threadPool.start(worker)

    def _fetch_games_from_server(self, progress_callback=None, cancel_event=None):
        response = requests.get(self.API_URL)
        response.raise_for_status()
        games = response.json().get("ongoing_games", [])
        return [
            g for g in games if int(g.get("start_time", 0)) > (time.time() - 60 * 40)
        ]
    
    def _on_live_games_fetched(self, recent_games):
        print(f"[DEBUG] Fetched {len(recent_games)} Rio games")
        self.live_games = recent_games
        self.live_games_updated.emit(self.live_games)

    def FetchGamesFromHUD(self):
        """
        Loads HUD data from a local file (default: test/liveGameExample.json).
        Emits `live_games_updated`.
        """
        hud_file = self.hud_folder / "liveGameExample.json"
        try:
            with open(hud_file, "r") as f:
                data = json.load(f)
                games = data.get("ongoing_games", [])
                self.live_games = games
                self.live_games_updated.emit(games)
        except Exception as e:
            print(f"[RioGameDataProvider] Failed to load HUD data: {e}")
            self.live_games_updated.emit([])

    def SelectLiveGame(self, game_dict):
        """
        Parses a selected game and emits `live_game_selected`.
        """
        parsed = self.parse_game_data(game_dict)
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
                    rioLU.CHAR_NAME[game_json[f"{team}_roster_{j}_char"]]
                    for j in range(9)
                ]
                data["entrants"][i][0]["roster"] = roster
                data["entrants"][i][0]["captainIndex"] = game_json[f"{team}_captain"]
        except Exception as e:
            print(f"[RioGameDataProvider] Failed to parse game data: {e}")

        return data