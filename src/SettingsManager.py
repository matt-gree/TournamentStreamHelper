import os
import threading
import orjson
from .Helpers.TSHDictHelper import *


class SettingsManager:
    settings = {}
    _save_timer = None
    _DEBOUNCE_MS = 200

    def SaveSettings():
        with open("./user_data/settings.json", 'wb') as file:
            file.write(orjson.dumps(SettingsManager.settings, option=orjson.OPT_NON_STR_KEYS))

    def _ScheduleSave():
        if SettingsManager._save_timer is not None:
            SettingsManager._save_timer.cancel()
        SettingsManager._save_timer = threading.Timer(
            SettingsManager._DEBOUNCE_MS / 1000.0, SettingsManager.SaveSettings)
        SettingsManager._save_timer.daemon = True
        SettingsManager._save_timer.start()

    def LoadSettings():
        try:
            with open("./user_data/settings.json", 'rb') as file:
                SettingsManager.settings = orjson.loads(file.read())
        except:
            SettingsManager.settings = {}

    def Set(key: str, value):
        deep_set(SettingsManager.settings, key, value)
        SettingsManager._ScheduleSave()

    def Unset(key: str):
        deep_unset(SettingsManager.settings, key)
        SettingsManager._ScheduleSave()

    def Get(key: str, default=None):
        return deep_get(SettingsManager.settings, key, default)


if not os.path.isfile("./user_data/settings.json"):
    SettingsManager.SaveSettings()

SettingsManager.LoadSettings()
