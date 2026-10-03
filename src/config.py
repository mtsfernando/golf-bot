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
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    # Comma-separated fallback models used when the primary model hits quota/overload
    GEMINI_FALLBACK_MODELS: List[str] = [
        m.strip() for m in os.getenv(
            "GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-3.5-flash,gemini-3.5-flash-lite"
        ).split(",") if m.strip()
    ]

    # WhatsApp
    BOT_TRIGGER_KEYWORD: str = os.getenv("BOT_TRIGGER_KEYWORD", "@caddy").strip()
    BOT_NAME: str = os.getenv("BOT_NAME", "Caddy")
    WHATSAPP_TARGET_GROUP: str = os.getenv("WHATSAPP_TARGET_GROUP", "").strip()

    # 18Birdies
    BIRDIES_ACCOUNTS: str = os.getenv("BIRDIES_ACCOUNTS", "").strip()
    BIRDIES_EMAIL: str = os.getenv("BIRDIES_EMAIL", "").strip()
    BIRDIES_PASSWORD: str = os.getenv("BIRDIES_PASSWORD", "").strip()
    BIRDIES_PHONE: str = os.getenv("BIRDIES_PHONE", "").strip()
    PLAYERS_CONFIG_PATH: str = os.getenv("PLAYERS_CONFIG_PATH", "config/players.json")

    # Course & Weather defaults
    DEFAULT_COURSE_NAME: str = os.getenv("DEFAULT_COURSE_NAME", "Beaconhills Golf Club")
    DEFAULT_COURSE_ADDRESS: str = os.getenv("DEFAULT_COURSE_ADDRESS", "85-87 Stoney Creek Road, Beaconsfield Upper VIC 3808")
    DEFAULT_COURSE_LAT: float = float(os.getenv("DEFAULT_COURSE_LAT", "-37.9836"))
    DEFAULT_COURSE_LON: float = float(os.getenv("DEFAULT_COURSE_LON", "145.4182"))
    TIMEZONE: str = os.getenv("TIMEZONE", "Australia/Melbourne")

    # Thresholds
    WEATHER_RAIN_THRESHOLD_PERCENT: int = int(os.getenv("WEATHER_RAIN_THRESHOLD_PERCENT", "40"))
    WEATHER_CHECK_HOURS_BEFORE: List[int] = [
        int(h.strip()) for h in os.getenv("WEATHER_CHECK_HOURS_BEFORE", "24,2").split(",") if h.strip()
    ]

    # Scheduler intervals
    BIRDIES_SYNC_INTERVAL_MINUTES: int = int(os.getenv("BIRDIES_SYNC_INTERVAL_MINUTES", "15"))
    WEATHER_CHECK_INTERVAL_MINUTES: int = int(os.getenv("WEATHER_CHECK_INTERVAL_MINUTES", "30"))

    # Web dashboard
    DASHBOARD_PORT: int = int(os.getenv("DASHBOARD_PORT", "8080"))

    # Storage paths
    DATA_DIR: Path = ROOT_DIR / "data"
    SESSION_DIR: Path = DATA_DIR / "session"
    BIRDIES_DATA_DIR: Path = DATA_DIR / "18birdies"

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """
        Normalizes a phone number to digits only.
        Converts Australian local format '04...' to '614...'.
        """
        if not phone:
            return ""
        digits = re.sub(r"\D", "", str(phone))
        if digits.startswith("04") and len(digits) == 10:
            digits = "61" + digits[1:]
        return digits

    @classmethod
    def phone_matches(cls, p1: str, p2: str) -> bool:
        """
        Compares two phone numbers for equality.
        Supports exact match and trailing 9 digits match (e.g. Australian mobile numbers).
        """
        c1 = cls.normalize_phone(p1)
        c2 = cls.normalize_phone(p2)
        if not c1 or not c2:
            return False
        if c1 == c2:
            return True
        if len(c1) >= 9 and len(c2) >= 9 and c1[-9:] == c2[-9:]:
            return True
        return False

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
            # Strip comment lines if any, and remove enclosing quotes if present
            cleaned_accounts = "\n".join(
                line for line in raw_accounts.splitlines() if not line.strip().startswith("#")
            ).strip().strip("'\"").strip()

            # Option A: JSON string
            if cleaned_accounts.startswith("[") and cleaned_accounts.endswith("]"):
                try:
                    parsed = json.loads(cleaned_accounts)
                    if isinstance(parsed, list):
                        for p in parsed:
                            if p.get("email"):
                                phone = p.get("phone") or p.get("whatsapp_phone") or p.get("mobile") or ""
                                players.append({
                                    "name": p.get("name") or p["email"].split("@")[0].capitalize(),
                                    "phone": str(phone).strip(),
                                    "birdies_email": p["email"].strip(),
                                    "birdies_password": p.get("password", "").strip(),
                                    "handicap": p.get("handicap", 18.0)
                                })
                        if players:
                            return players
                except json.JSONDecodeError as err:
                    print(f"[Config] Error parsing BIRDIES_ACCOUNTS JSON: {err}")

            # Option B: Delimited string "Name:Phone:Email:Password, Name:Email:Password"
            parts = [item.strip() for item in raw_accounts.split(",") if item.strip()]
            for item in parts:
                tokens = [t.strip() for t in item.split(":")]
                if len(tokens) >= 4:
                    players.append({
                        "name": tokens[0],
                        "phone": tokens[1],
                        "birdies_email": tokens[2],
                        "birdies_password": tokens[3],
                        "handicap": 18.0
                    })
                elif len(tokens) == 3:
                    # Check if tokens[1] is email or phone
                    if "@" in tokens[1]:
                        players.append({
                            "name": tokens[0],
                            "phone": "",
                            "birdies_email": tokens[1],
                            "birdies_password": tokens[2],
                            "handicap": 18.0
                        })
                    else:
                        players.append({
                            "name": tokens[0],
                            "phone": tokens[1],
                            "birdies_email": tokens[2],
                            "birdies_password": "",
                            "handicap": 18.0
                        })
                elif len(tokens) == 2:
                    players.append({
                        "name": tokens[0].split("@")[0].capitalize(),
                        "phone": "",
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
                                phone = p.get("phone") or p.get("whatsapp_phone") or p.get("mobile") or ""
                                p["phone"] = str(phone).strip()
                                players.append(p)
                        if players:
                            return players
            except Exception as e:
                print(f"[Config] Error loading players config from {path}: {e}")

        # 3. Fallback to single primary account in .env
        if cls.BIRDIES_EMAIL:
            return [{
                "name": "Host",
                "phone": cls.BIRDIES_PHONE,
                "birdies_email": cls.BIRDIES_EMAIL,
                "birdies_password": cls.BIRDIES_PASSWORD,
                "handicap": 18.0
            }]

        return players

    @classmethod
    def get_player_by_phone(cls, phone: str) -> Optional[Dict[str, Any]]:
        """
        Finds a configured player matching the given phone number.
        """
        if not phone:
            return None
        for p in cls.get_players():
            player_phone = p.get("phone", "")
            if player_phone and cls.phone_matches(player_phone, phone):
                return p
        return None

    @classmethod
    def get_player_by_name(cls, name: str) -> Optional[Dict[str, Any]]:
        """
        Finds a configured player by name (case-insensitive exact or partial match).
        """
        if not name:
            return None
        name_clean = name.strip().lower()
        players = cls.get_players()
        # 1. Exact match
        for p in players:
            p_name = p.get("name", "").strip().lower()
            if p_name == name_clean:
                return p
        # 2. Substring match (e.g. "Thilina" matches "Thilina Fernando")
        for p in players:
            p_name = p.get("name", "").strip().lower()
            if (name_clean in p_name) or (p_name in name_clean):
                return p
        return None

# Ensure directories exist
Config.DATA_DIR.mkdir(parents=True, exist_ok=True)
Config.SESSION_DIR.mkdir(parents=True, exist_ok=True)
Config.BIRDIES_DATA_DIR.mkdir(parents=True, exist_ok=True)
