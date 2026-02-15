# src/RioGameDataProvider.py

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
from loguru import logger

from pyrio.api_manager import APIManager
from pyrio.web_functions import stats_endpoint, list_game_modes, live_games_endpoint
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

    live_games_updated = Signal(dict)
    live_game_selected = Signal(dict)
    game_modes_updated = Signal(dict)

    def __init__(self):
        super().__init__()
        if RioGameDataProvider.instance is not None:
            raise Exception("RioGameDataProvider is a singleton! Use RioGameDataProvider.instance")
        RioGameDataProvider.instance = self
        self.rio_api_manager = APIManager()
        self.hud_file = get_user_hud_path()
        self.live_games = []
        self.current_game = None
        self.threadPool = QThreadPool()
        self.away_server_stats = {}
        self.home_server_stats = {}
        self._prev_player_sides = {}  # {"PlayerName": 0, "OtherPlayer": 1}
        self._prev_inning = None
        self._sides_swapped = False  # Persists swap decision for entire game
        self._user_overridden = False  # True when user manually swaps mid-game

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
        Returns a dict with 'HUD' key for populate_rio_dropdown.
        """
        self.reload_hud_path()
        games = {}
        if hasattr(self, "hud_watcher") and self.hud_watcher.latest_game_data:
            games['HUD'] = dict(self.hud_watcher.latest_game_data)
        self.live_games = games
        self.live_games_updated.emit(games)
        return games

    def FetchGames(self):
        """
        Fetches games: immediately populates HUD game, then fetches
        server games in background via pyrio APIManager and merges them in.
        """
        # Immediately show HUD game
        hud_games = self.FetchHUDGame()

        # Fetch server games in background (doesn't block the HUD game from showing)
        worker = Worker(self._fetch_server_games)
        worker.signals.result.connect(
            lambda server_games: self._merge_server_games(hud_games, server_games))
        worker.signals.error.connect(lambda e: logger.error(f"[RioGameDataProvider] Error fetching server games: {e}"))
        self.threadPool.start(worker)

    def _fetch_server_games(self, progress_callback=None, cancel_event=None):
        """Fetch only server games via pyrio API (runs on worker thread)."""
        try:
            server_games = live_games_endpoint(self.rio_api_manager).get('ongoing_games', [])
            return [
                g for g in server_games
                if int(g.get("start_time", 0)) > (time.time() - 60 * 30)
            ]
        except Exception as e:
            logger.debug(f"[RioGameDataProvider] Failed to fetch server games: {e}")
            return []

    def _merge_server_games(self, hud_games, server_games):
        """Merge server games into the dict and re-emit."""
        if server_games:
            all_games = dict(hud_games)
            all_games['server_live'] = server_games
            self.live_games = all_games
            self.live_games_updated.emit(all_games)

    def FetchGameModes(self):
        game_modes_worker = Worker(self._fetch_game_modes)
        game_modes_worker.signals.result.connect(self._on_game_modes_fetched)
        self.threadPool.start(game_modes_worker)

    def _fetch_game_modes(self, progress_callback=None, cancel_event=None):
        game_modes = list_game_modes(self.rio_api_manager, active=True)['Tag Sets']
        game_modes_dict = {-1: 'Select Game Mode'}
        for game_mode in game_modes:
            game_modes_dict[game_mode["id"]] = game_mode["name"]

        return game_modes_dict

    def _on_game_modes_fetched(self, modes):
        if not modes:
            return
        try:
            SettingsManager.Set("project_rio.game_modes", modes)
            self.game_modes_updated.emit(modes)
        except Exception as e:
            logger.error(f"Error saving game modes to settings: {e}")

    def FetchGameModeStats(self, away_player, home_player, game_mode):
        if game_mode in [None, 'Select Game Mode', 'Refresh Game Modes in Settings']:
            return
        game_mode_stats_worker = Worker(
            lambda progress_callback=None, cancel_event=None: self._fetch_game_mode_stats(
                game_mode, away_player, home_player
            )
        )
        self.threadPool.start(game_mode_stats_worker)

    def _fetch_game_mode_stats(self, game_mode, away_player, home_player, progress_callback=None, cancel_event=None):
        params = {
            'by_char': 1,
            'username': [away_player, home_player],
            'exclude_fielding': 1,
            'tag': game_mode,
            'by_user': 1
        }
        result = stats_endpoint(self.rio_api_manager, params)
        stats = result.get('Stats', {}) if result else {}

        self.away_server_stats = stats.get(away_player, {})
        self.home_server_stats = stats.get(home_player, {})

    def _process_batter_stats_dict(self, batter, batting_team_dict):
        batter_stats_dict = batting_team_dict.get(batter, {}).get('Batting', {})
        if batter_stats_dict:
            at_bats = batter_stats_dict.get('summary_at_bats', 0)
            hits = batter_stats_dict.get('summary_hits', 0)
            singles = batter_stats_dict.get('summary_singles', 0)
            doubles = batter_stats_dict.get('summary_doubles', 0)
            triples = batter_stats_dict.get('summary_triples', 0)
            homeruns = batter_stats_dict.get('summary_homeruns', 0)
            strikeouts = batter_stats_dict.get('summary_strikeouts', 0)

            if at_bats == 0:
                avg = 0
                slg = 0
                so_percent = 0
            else:
                avg = hits / at_bats
                slg = (singles + 2*doubles + 3*triples + 4*homeruns) / at_bats
                so_percent = strikeouts / at_bats * 100

            batting_string = f'{batter}  ({at_bats} ABs)\nAVG: {avg:.3f}  SLG: {slg:.3f}  SO%: {so_percent:.0f}%'
            return batting_string

    def create_stats_text(self, half_inning, batter, pitcher):
        if half_inning == 0:
            away_stat_string = self._process_batter_stats_dict(batter, self.away_server_stats)
            home_stat_string = ''
        else:
            away_stat_string = ''
            home_stat_string = self._process_batter_stats_dict(batter, self.home_server_stats)

        return (away_stat_string, home_stat_string)

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
            data["team1score"] = game_json["away_score"]
            data["team2score"] = game_json["home_score"]

            for i in range(2):
                team = "home" if i == 1 else "away"
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
                data['batter'] = data["entrants"][0][0]["roster"][batter_index]
                data['pitcher'] = data["entrants"][1][0]["roster"][pitcher_index]
            else:
                data["half_inning"] = 'Bottom'
                data['batter'] = data["entrants"][1][0]["roster"][batter_index]
                data['pitcher'] = data["entrants"][0][0]["roster"][pitcher_index]

            data['inning'] = game_json["inning"]
            data['outs'] = game_json["outs"]
            data['strikes'] = game_json.get("strikes", 0)
            data['balls'] = game_json.get("balls", 0)

            data['runnerOn1'] = game_json["runner_on_first"]
            data['runnerOn2'] = game_json["runner_on_second"]
            data['runnerOn3'] = game_json["runner_on_third"]

            data['game_mode'] = game_json['tag_set']

        except Exception as e:
            logger.error(f"[RioGameDataProvider] Failed to parse game data: {e}")

        return data

    def _is_new_game(self, current_inning):
        """Detect new game by inning number decreasing."""
        if self._prev_inning is None:
            return True
        return current_inning < self._prev_inning

    def _swap_entrants(self, parsed):
        """Swap entrants[0] and entrants[1] along with their scores."""
        parsed['entrants'].reverse()
        parsed['team1score'], parsed['team2score'] = parsed['team2score'], parsed['team1score']
        return parsed

    def toggle_sides_swapped(self):
        """Called by UI swap buttons to toggle the persistent swap flag.
        Also sets _user_overridden so the pin doesn't fight the user's choice
        until the next game starts."""
        self._sides_swapped = not self._sides_swapped
        self._user_overridden = True
        logger.info(f"[RIO] Manual swap toggled, sides_swapped={self._sides_swapped}, user override active")

    def _preserve_player_sides(self, parsed):
        """Ensure consistent team sides across all HUD events in a game.

        On new game (inning decreased):
          - Reset _user_overridden flag
          - Pinned player or back-to-back detection determines initial sides
          - Set _sides_swapped flag for the duration of this game

        On mid-game events:
          - If user manually swapped (_user_overridden), respect their choice
            via _sides_swapped — pin does NOT fight back
          - Otherwise apply _sides_swapped (which pin or auto-detect set)
        """
        current_inning = parsed.get('inning', 1)
        player0 = parsed['entrants'][0][0].get('rioName', '')
        player1 = parsed['entrants'][1][0].get('rioName', '')

        # Check pinned player setting
        pinned_player = SettingsManager.Get("project_rio.pinned_player", "").strip()
        pinned_side = SettingsManager.Get("project_rio.pinned_side", "Team 1")
        pinned_index = 0 if pinned_side == "Team 1" else 1

        if self._is_new_game(current_inning):
            # New game — reset user override, re-evaluate sides
            self._user_overridden = False
            self._sides_swapped = False

            if pinned_player:
                # Pin determines initial sides for new game
                need_swap = (
                    (player0 == pinned_player and pinned_index == 1) or
                    (player1 == pinned_player and pinned_index == 0)
                )
                if need_swap:
                    self._sides_swapped = True
                    logger.info(f"[RIO] New game: pinned player '{pinned_player}' placed on {pinned_side}")
            else:
                # No pin — check back-to-back detection
                if self._prev_player_sides:
                    prev_side_0 = self._prev_player_sides.get(player0)
                    prev_side_1 = self._prev_player_sides.get(player1)
                    if prev_side_0 == 1 or prev_side_1 == 0:
                        self._sides_swapped = True
                        logger.info(f"[RIO] New game: auto-swapping sides to keep returning player in place")

        # Apply the current swap decision (set by new-game logic or user override)
        if self._sides_swapped:
            parsed = self._swap_entrants(parsed)

        # Update tracking (re-read after potential swap)
        player0 = parsed['entrants'][0][0].get('rioName', '')
        player1 = parsed['entrants'][1][0].get('rioName', '')
        self._prev_player_sides = {player0: 0, player1: 1}
        self._prev_inning = current_inning
        return parsed

    def _on_hud_game_update(self, game_json):
        parsed = self.parse_game_data(game_json)
        parsed = self._preserve_player_sides(parsed)
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
            "strikes": hud_data.strikes(),
            "batter_stats": hud_data.character_offensive_stats(hud_data.batting_team(), hud_data.batter_roster_location()),
            "pitcher_stats": hud_data.character_defensive_stats(hud_data.fielding_team(), hud_data.pitcher_roster_location())
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
