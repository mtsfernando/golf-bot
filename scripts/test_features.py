"""
Test utility to verify bot features independently (Weather, Gemini Caddy Roaster, 18Birdies Parser)
Usage:
    python scripts/test_features.py --weather
    python scripts/test_features.py --caddy-chat "Who is the worst golfer in Sri Lanka?"
    python scripts/test_features.py --test-roast
"""

import sys
import json
import argparse
from datetime import datetime

from src.config import Config
from src.weather import WeatherService
from src.gemini_client import GeminiService
from src.birdies_sync import BirdiesSyncService

def test_weather():
    print("Testing Open-Meteo Weather forecast...")
    weather = WeatherService()
    report = weather.check_tee_time_weather({
        "id": 999,
        "course_name": Config.DEFAULT_COURSE_NAME,
        "date_str": datetime.now().strftime("%Y-%m-%d"),
        "start_time": "08:00",
        "latitude": Config.DEFAULT_COURSE_LAT,
        "longitude": Config.DEFAULT_COURSE_LON
    })
    if report:
        print("\n[Weather Report Generated]")
        print(f"Precipitation: {report['total_precip_mm']} mm (Category: {report['category']})")
        print(f"Prior Day Rainfall: {report['prior_day_rain_mm']} mm (Heavy Prior Rain: {report['prior_day_heavy_rain']})")
        print(f"Max Rain Probability: {report['max_rain_prob']}% | Temp: {report['temp_c']}°C")
        caddy_msg = weather.format_caddy_weather_report(report, "Tomorrow's Round")
        print("\n[Anura Kumara's Weather Brief Sample]:")
        print(caddy_msg)
    else:
        print("Failed to fetch weather forecast.")

def test_caddy_chat(message: str):
    print(f"Testing Anura Kumara Gemini Chat with query: '{message}'...")
    gemini = GeminiService()
    reply = gemini.chat_as_caddy(user_message=message)
    print("\n[Anura Kumara's Response]:")
    print(reply)

def test_roast():
    print("Testing 18Birdies scorecard parser and roast generation...")
    gemini = GeminiService()
    sync = BirdiesSyncService(gemini)

    sample_file = "data/18birdies/sample_18birdies_export.json"
    if os.path.exists(sample_file):
        print(f"Loading real 18Birdies export from {sample_file}...")
        with open(sample_file, "r") as f:
            content = json.load(f)
        
        my_data = content.get("myData", {})
        player_name = my_data.get("accountData", {}).get("userName", "Thilina Fernando")
        club_map = {c["clubId"]: c["name"] for c in my_data.get("clubData", {}).get("playedClubs", []) if c.get("clubId")}
        rounds = my_data.get("activityData", {}).get("rounds", [])

        print(f"Found {len(rounds)} rounds in export for {player_name} across {len(club_map)} clubs.")
        if rounds:
            sample_r = rounds[1] if len(rounds) > 1 else rounds[0]
            parsed = sync.parse_round_data(sample_r, default_player=player_name, club_map=club_map)
            print("\n[Parsed Real 18Birdies Scorecard]:")
            print(f"Player: {parsed['player_name']}")
            print(f"Course: {parsed['course_name']}")
            print(f"Date: {parsed['round_date']}")
            print(f"Strokes: {parsed['total_score']} (Score to Par: {parsed['score_to_par']})")
            print(f"Best: {parsed['best_hole']}")
            print(f"Worst: {parsed['worst_hole']}")
            print(f"Putts: {parsed['total_putts']} | Fairway %: {parsed['fairway_pct']}% | GIR %: {parsed['gir_pct']}%")
            print("\n[Anura Kumara's Witty Caddy Comment]:")
            roast = gemini.generate_round_summary(parsed)
            print(roast)
            return

    # Fallback synthetic round
    sample_round = {
        "id": "sample-12345",
        "player_name": "Kasun",
        "course_name": "Beaconhills Golf Club",
        "played_at": datetime.now().strftime("%Y-%m-%d"),
        "strokes": 104,
        "score": 33,
        "holeStrokes": [5, 4, 8, 6, 4, 7, 5, 9, 6],
        "stats": {"birdies": 0, "pars": 1, "bogeys": 3, "doubleBogeyOrWorse": 5, "putts": 4}
    }
    parsed = sync.parse_round_data(sample_round, default_player="Kasun")
    print("\n[Anura Kumara's Sri Lankan Caddy Roast]:")
    roast = gemini.generate_round_summary(parsed)
    print(roast)

def test_players():
    print("Testing loaded players configuration...")
    players = Config.get_players()
    print(f"Total players configured: {len(players)}")
    for p in players:
        # Mask password for security
        pwd_mask = "***" if p.get("birdies_password") else "(not set)"
        print(f" - {p.get('name')}: email={p.get('birdies_email')}, password={pwd_mask}, handicap={p.get('handicap')}")

def test_dashboard():
    print("Testing DB activity logging and dashboard metrics...")
    from src.db import init_db, log_activity, get_activity_logs, get_bot_summary_metrics
    init_db()
    log_activity("CONNECTIVITY", "SUCCESS", "Test connection established")
    log_activity("MESSAGE_SENT", "SUCCESS", "Anura Kumara: Game on at Beaconhills!")
    log_activity("MESSAGE_RECEIVED", "INFO", "Thilina: when is next tee time?")
    log_activity("BIRDIES_SYNC", "SUCCESS", "Synced 50 rounds for Thilina Fernando")
    log_activity("ERROR", "FAILURE", "Sample failure logged for dashboard testing")

    metrics = get_bot_summary_metrics()
    print("\n[Current Bot Metrics]:")
    for k, v in metrics.items():
        print(f" - {k}: {v}")

    logs = get_activity_logs(limit=5)
    print(f"\n[Latest {len(logs)} Logs in DB]:")
    for l in logs:
        print(f" [{l['created_at']}] [{l['event_type']}] [{l['status']}] {l['details']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Golf Bot Features")
    parser.add_argument("--weather", action="store_true", help="Test weather check")
    parser.add_argument("--caddy-chat", type=str, help="Test Anura Kumara chat")
    parser.add_argument("--test-roast", action="store_true", help="Test 18Birdies scorecard roast")
    parser.add_argument("--test-players", action="store_true", help="Verify configured player credentials from env/json")
    parser.add_argument("--test-dashboard", action="store_true", help="Test DB activity logging and metrics")

    args = parser.parse_args()
    if args.weather:
        test_weather()
    elif args.caddy_chat:
        test_caddy_chat(args.caddy_chat)
    elif args.test_roast:
        test_roast()
    elif args.test_players:
        test_players()
    elif args.test_dashboard:
        test_dashboard()
    else:
        parser.print_help()
