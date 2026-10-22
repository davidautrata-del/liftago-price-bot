"""Konfigurace z proměnných prostředí / souboru .env (žádná tajemství v kódu)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE = Path(__file__).parent

# Appium / zařízení
PLATFORM = os.getenv("PLATFORM", "mac")  # mac = iPhone appka na Macu, android = telefon/emulátor
LIFTAGO_BUNDLE_ID = os.getenv("LIFTAGO_BUNDLE_ID", "")  # zjisti: osascript -e 'id of app "Liftago"'
APPIUM_URL = os.getenv("APPIUM_URL", "http://127.0.0.1:4723")
ANDROID_UDID = os.getenv("ANDROID_UDID", "")  # prázdné = první připojené zařízení/emulátor
LIFTAGO_PACKAGE = os.getenv("LIFTAGO_PACKAGE", "")  # zjisti: adb shell pm list packages | grep -i liftago
LIFTAGO_ACTIVITY = os.getenv("LIFTAGO_ACTIVITY", "")  # volitelné

# Google Sheets. Webhook (SHEET_WEBHOOK_URL) je hlavní cesta; service account je záloha.
# SHEET_GID je list z URL (?gid=). SHEET_TAB je záložní jméno, když list s tím gid není.
SHEET_ID = os.getenv("SHEET_ID", "1B4KfTAtr59zif3DZ6WGJ2j-pNOw4MvohOABXnoZryC8")
SHEET_TAB = os.getenv("SHEET_TAB", "Data")
_gid = os.getenv("SHEET_GID", "1586305980").strip()
SHEET_GID = int(_gid) if _gid else None

# Plánovač
RUNS_PER_DAY = int(os.getenv("RUNS_PER_DAY", "10"))
DAY_START = os.getenv("DAY_START", "09:30")
DAY_END = os.getenv("DAY_END", "17:00")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Prague")

# Mock režim: bez telefonu, jen vygeneruje fiktivní data (test zápisu a plánovače)
MOCK = os.getenv("MOCK", "0") == "1"

# Na Macu výchozí: měření nebere obrazovku, fokus ani myš. BACKGROUND=0 vrátí staré chování.
BACKGROUND = os.getenv("BACKGROUND", "1" if PLATFORM == "mac" else "0") == "1"

CSV_FILE = BASE / "results.csv"
DEBUG_DIR = BASE / "debug"
STATE_DIR = BASE / "state"
