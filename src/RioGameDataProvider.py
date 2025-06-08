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
    Provides live game data from Project Rio, including server-based and HUD (local) games.
    Emits signals when new data is available.
    """

    live_games_updated = Signal(list)
    live_game_selected = Signal(dict)

    def __init__(self, hud_folder=None):
        super().__init__()
        self.API_URL = "https://api.projectrio.app/populate_db/ongoing_game/"
        self.hud_folder = hud_folder or (Path(__file__).parent.parent / "test")
        self.live_games = []
        self.current_game = None
        self.threadPool = QThreadPool()

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

        # Fetch HUD games
        try:
            hud_file = self.hud_folder / "liveGameExample.json"
            with open(hud_file, "r") as f:
                data = json.load(f)
                hud_games = data.get("ongoing_games", [])
                for g in hud_games:
                    g["source"] = "hud"
                games.extend(hud_games)
        except Exception as e:
            print(f"[RioGameDataProvider] Failed to load HUD data: {e}")

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
                    rioLU.CHAR_NAME[game_json[f"{team}_roster_{j}_char"]]
                    for j in range(9)
                ]
                data["entrants"][i][0]["roster"] = roster
                data["entrants"][i][0]["captainIndex"] = game_json[f"{team}_captain"]
                data["entrants"][i][0]['rioName'] = game_json[f'{team}_player']
                
                print(f"[DEBUG] Parsed game data for team {team}: {data['entrants'][i][0]}")

            data['half_inning'] = 'Top' if game_json["half_inning"] == 0 else 'Bottom'
            data['inning'] = game_json["inning"]

        except Exception as e:
            print(f"[RioGameDataProvider] Failed to parse game data: {e}")

        return data