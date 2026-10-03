import os
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Load .env file from project root
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

class Config:
    # Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # WhatsApp
    BOT_TRIGGER_KEYWORD: str = os.getenv("BOT_TRIGGER_KEYWORD", "@caddy").strip()
    BOT_NAME: str = os.getenv("BOT_NAME", "Anura Kumara")
    WHATSAPP_TARGET_GROUP: str = os.getenv("WHATSAPP_TARGET_GROUP", "").strip()

    # 18Birdies
    BIRDIES_ACCOUNTS: str = os.getenv("BIRDIES_ACCOUNTS", "").strip()
    BIRDIES_EMAIL: str = os.getenv("BIRDIES_EMAIL", "").strip()
    BIRDIES_PASSWORD: str = os.getenv("BIRDIES_PASSWORD", "").strip()
    PLAYERS_CONFIG_PATH: str = os.getenv("PLAYERS_CONFIG_PATH", "config/players.json")

    # Course & Weather defaults
    DEFAULT_COURSE_NAME: str = os.getenv("DEFAULT_COURSE_NAME", "Beaconhills Golf Club")
    DEFAULT_COURSE_LAT: float = float(os.getenv("DEFAULT_COURSE_LAT", "-38.0845"))
    DEFAULT_COURSE_LON: float = float(os.getenv("DEFAULT_COURSE_LON", "145.4385"))
    TIMEZONE: str = os.getenv("TIMEZONE", "Australia/Melbourne")

    # Thresholds
    WEATHER_RAIN_THRESHOLD_PERCENT: int = int(os.getenv("WEATHER_RAIN_THRESHOLD_PERCENT", "40"))
    WEATHER_CHECK_HOURS_BEFORE: List[int] = [
        int(h.strip()) for h in os.getenv("WEATHER_CHECK_HOURS_BEFORE", "24,2").split(",") if h.strip()
    ]

    # Scheduler intervals
    BIRDIES_SYNC_INTERVAL_MINUTES: int = int(os.getenv("BIRDIES_SYNC_INTERVAL_MINUTES", "15"))
    WEATHER_CHECK_INTERVAL_MINUTES: int = int(os.getenv("WEATHER_CHECK_INTERVAL_MINUTES", "30"))

    # Storage paths
    DATA_DIR: Path = ROOT_DIR / "data"
    SESSION_DIR: Path = DATA_DIR / "session"
    BIRDIES_DATA_DIR: Path = DATA_DIR / "18birdies"

    @classmethod
    def get_players(cls) -> List[Dict[str, Any]]:
        """
        Parses players/friends 18Birdies credentials from:
        1. BIRDIES_ACCOUNTS in .env (JSON array or Name:Email:Pass delimited string)
        2. config/players.json file
        3. Single user fallback (BIRDIES_EMAIL & BIRDIES_PASSWORD)
        """
        players: List[Dict[str, Any]] = []

        # 1. Check BIRDIES_ACCOUNTS in .env
        raw_accounts = cls.BIRDIES_ACCOUNTS
        if raw_accounts:
            # Option A: JSON string
            if raw_accounts.startswith("[") and raw_accounts.endswith("]"):
                try:
                    parsed = json.loads(raw_accounts)
                    if isinstance(parsed, list):
                        for p in parsed:
                            if p.get("email"):
                                players.append({
                                    "name": p.get("name") or p["email"].split("@")[0].capitalize(),
                                    "birdies_email": p["email"].strip(),
                                    "birdies_password": p.get("password", "").strip(),
                                    "handicap": p.get("handicap", 18.0)
                                })
                        if players:
                            return players
                except json.JSONDecodeError as err:
                    print(f"[Config] Error parsing BIRDIES_ACCOUNTS JSON: {err}")

            # Option B: Delimited string "Name:Email:Password, Name:Email:Password"
            parts = [item.strip() for item in raw_accounts.split(",") if item.strip()]
            for item in parts:
                tokens = [t.strip() for t in item.split(":")]
                if len(tokens) >= 3:
                    players.append({
                        "name": tokens[0],
                        "birdies_email": tokens[1],
                        "birdies_password": tokens[2],
                        "handicap": 18.0
                    })
                elif len(tokens) == 2:
                    players.append({
                        "name": tokens[0].split("@")[0].capitalize(),
                        "birdies_email": tokens[0],
                        "birdies_password": tokens[1],
                        "handicap": 18.0
                    })
            if players:
                return players

        # 2. Check external players.json
        path = ROOT_DIR / cls.PLAYERS_CONFIG_PATH
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    file_players = json.load(f)
                    if isinstance(file_players, list):
                        for p in file_players:
                            if p.get("birdies_email"):
                                players.append(p)
                        if players:
                            return players
            except Exception as e:
                print(f"[Config] Error loading players config from {path}: {e}")

        # 3. Fallback to single primary account in .env
        if cls.BIRDIES_EMAIL:
            return [{
                "name": "Host",
                "birdies_email": cls.BIRDIES_EMAIL,
                "birdies_password": cls.BIRDIES_PASSWORD,
                "handicap": 18.0
            }]

        return players

# Ensure directories exist
Config.DATA_DIR.mkdir(parents=True, exist_ok=True)
Config.SESSION_DIR.mkdir(parents=True, exist_ok=True)
Config.BIRDIES_DATA_DIR.mkdir(parents=True, exist_ok=True)
