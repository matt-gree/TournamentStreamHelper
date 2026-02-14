import re
import unicodedata
from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
import os
import traceback
from loguru import logger


# ---------------------------------------------------------------------------
# Inline US + Canada state/province data — replaces the 37 MB JSON download.
# This keeps memory usage minimal while covering all MSB competitors.
# ---------------------------------------------------------------------------
_US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico", "VI": "Virgin Islands", "GU": "Guam",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}

_CA_PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon",
}

_BUILTIN_COUNTRIES = {
    "US": {
        "name": "United States", "display_name": "United States",
        "en_name": "United States", "code": "US",
        "latitude": "38.00000000", "longitude": "-97.00000000",
        "states": {
            code: {
                "name": name, "code": code,
                "original_code": code,
                "latitude": None, "longitude": None,
            }
            for code, name in _US_STATES.items()
        },
    },
    "CA": {
        "name": "Canada", "display_name": "Canada",
        "en_name": "Canada", "code": "CA",
        "latitude": "60.00000000", "longitude": "-95.00000000",
        "states": {
            code: {
                "name": name, "code": code,
                "original_code": code,
                "latitude": None, "longitude": None,
            }
            for code, name in _CA_PROVINCES.items()
        },
    },
}


class TSHCountryHelperSignals(QObject):
    countriesUpdated = Signal()


class TSHCountryHelper(QObject):
    instance: "TSHCountryHelper" = None

    countries_json = {}
    countries = {}
    cities = {}
    countryModel = None
    signals = TSHCountryHelperSignals()

    def __init__(self) -> None:
        super().__init__()
        # Load inline data immediately — no download, no large JSON parse
        TSHCountryHelper.LoadCountries()

    # ------------------------------------------------------------------
    # Kept for API compatibility — no longer downloads anything.
    # ------------------------------------------------------------------
    def UpdateCountriesFile(self):
        pass

    def remove_accents_lower(input_str):
        nfkd_form = unicodedata.normalize('NFKD', input_str)
        return u"".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()

    def GetBasicCountryInfo(country_code):
        if country_code not in TSHCountryHelper.countries:
            return {}

        c = TSHCountryHelper.countries[country_code]
        return {
            "name": c["name"],
            "display_name": c["display_name"],
            "en_name": c["en_name"],
            "code": c["code"],
            "latitude": c["latitude"],
            "longitude": c["longitude"],
            "asset": f'./assets/country_flag/{country_code.lower()}.png'
        }

    def LoadCountries():
        try:
            # Use hardcoded US/CA data instead of parsing 37 MB JSON
            TSHCountryHelper.countries = {}
            TSHCountryHelper.cities = {}

            for iso2, cdata in _BUILTIN_COUNTRIES.items():
                TSHCountryHelper.countries[iso2] = {
                    "name": cdata["name"],
                    "display_name": cdata["display_name"],
                    "en_name": cdata["en_name"],
                    "code": cdata["code"],
                    "latitude": cdata["latitude"],
                    "longitude": cdata["longitude"],
                    "states": dict(cdata["states"]),
                }

            # Build the Qt model
            TSHCountryHelper.countryModel = QStandardItemModel()

            noCountry = QStandardItem()
            noCountry.setData({}, Qt.ItemDataRole.UserRole)
            TSHCountryHelper.countryModel.appendRow(noCountry)

            for country_code in TSHCountryHelper.countries:
                item = QStandardItem()
                item.setIcon(
                    QIcon(f'./assets/country_flag/{country_code.lower()}.png'))
                countryData = TSHCountryHelper.GetBasicCountryInfo(country_code)
                item.setData(countryData, Qt.ItemDataRole.UserRole)
                c = TSHCountryHelper.countries[country_code]
                item.setData(
                    f'{c["display_name"]} / {c["en_name"]} ({country_code})',
                    Qt.ItemDataRole.EditRole)
                TSHCountryHelper.countryModel.appendRow(item)

            TSHCountryHelper.signals.countriesUpdated.emit()
        except:
            TSHCountryHelper.countries_json = {}
            logger.error(traceback.format_exc())

    def FindState(countryCode, city):
        """Find state/province code from a city string."""
        split = city.replace(" - ", ",").split(",")

        logger.debug(f"Finding State from city string [{city}]")

        for part in split[::-1]:
            part = part.strip()

            state = next(
                (st for st in TSHCountryHelper.countries.get(countryCode, {}).get("states", {}).values()
                 if TSHCountryHelper.remove_accents_lower(st["code"]) == TSHCountryHelper.remove_accents_lower(part)),
                None
            )
            if state is None:
                state = next(
                    (st for st in TSHCountryHelper.countries.get(countryCode, {}).get("states", {}).values()
                     if TSHCountryHelper.remove_accents_lower(st["name"]) == TSHCountryHelper.remove_accents_lower(part)),
                    None
                )
            if state is not None:
                logger.debug(
                    f"State was explicit: [{city}] -> [{part}] = {state}")
                return state["original_code"]

        # No explicit state found, try city-based lookup
        for part in split[::-1]:
            part = part.strip()

            state = TSHCountryHelper.cities.get(countryCode, {}).get(
                TSHCountryHelper.remove_accents_lower(part), None)

            if state is not None:
                logger.debug(f"Got state from city name: [{city}] -> [{part}] = {state}")
                return state

        return None


TSHCountryHelper.instance = TSHCountryHelper()
