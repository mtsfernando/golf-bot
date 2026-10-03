"""
Test utility to verify bot features independently (Weather, Gemini Caddy Roaster, 18Birdies Parser)
Usage:
    python scripts/test_features.py --weather
    python scripts/test_features.py --caddy-chat "Who is the worst golfer in Sri Lanka?"
    python scripts/test_features.py --test-roast
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
        print("\n[Caddy's Weather Brief Sample]:")
        print(caddy_msg)
    else:
        print("Failed to fetch weather forecast.")

def test_caddy_chat(message: str):
    print(f"Testing Caddy Gemini Chat with query: '{message}'...")
    gemini = GeminiService()
    reply = gemini.chat_as_caddy(user_message=message)
    print("\n[Caddy's Response]:")
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
            print("\n[Caddy's Witty Caddy Comment]:")
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
    print("\n[Caddy's Sri Lankan Caddy Roast]:")
    roast = gemini.generate_round_summary(parsed)
    print(roast)

def test_players():
    print("Testing loaded players configuration...")
    players = Config.get_players()
    print(f"Total players configured: {len(players)}")
    for p in players:
        # Mask password for security
        pwd_mask = "***" if p.get("birdies_password") else "(not set)"
        print(f" - {p.get('name')}: phone={p.get('phone') or '(not set)'}, email={p.get('birdies_email')}, password={pwd_mask}, handicap={p.get('handicap')}")

    print("\n[Phone Matching Tests]:")
    print(f" +61412345678 matches 0412345678: {Config.phone_matches('+61412345678', '0412345678')}")
    print(f" 61412345678 matches 0412 345 678: {Config.phone_matches('61412345678', '0412 345 678')}")

def test_recap(player_name: Optional[str] = None):
    print(f"Testing last round recap for '{player_name or 'any player'}'...")
    gemini = GeminiService()
    sync = BirdiesSyncService(gemini)

    round_data = sync.get_player_last_round(player_name)
    if not round_data:
        print("No round found in DB or data/18birdies/. Generating mock round to demonstrate recap...")
        sample_round = {
            "id": "recap-demo-123",
            "player_name": player_name or "Thilina Fernando",
            "course_name": Config.DEFAULT_COURSE_NAME,
            "played_at": datetime.now().strftime("%Y-%m-%d"),
            "strokes": 98,
            "score": 26,
            "holeStrokes": [5, 4, 6, 7, 5, 8, 4, 6, 5, 5, 4, 7, 6, 5, 7, 5, 5, 4],
            "stats": {
                "birdies": 1, "pars": 4, "bogeys": 6, "doubleBogeyOrWorse": 7,
                "putts": 34, "fairwayMiddles": 6, "fairwayHoleCount": 14,
                "gir": 4, "girHoleCount": 18
            }
        }
        round_data = sync.parse_round_data(sample_round, default_player=player_name or "Thilina Fernando")

    roast = gemini.generate_round_summary(round_data)
    recap_msg = sync.format_round_recap(round_data, roast_comment=roast)

    print("\n" + "="*50)
    print("WHATSAPP ROUND RECAP MESSAGE:")
    print("="*50)
    print(recap_msg)
    print("="*50)

def test_dashboard():
    print("Testing DB activity logging and dashboard metrics...")
    from src.db import init_db, log_activity, get_activity_logs, get_bot_summary_metrics
    init_db()
    log_activity("CONNECTIVITY", "SUCCESS", "Test connection established")
    log_activity("MESSAGE_SENT", "SUCCESS", "Caddy: Game on at Beaconhills men!")
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

def test_sync(player_name: Optional[str] = None):
    print(f"Testing on-demand 18Birdies sync for '{player_name or 'primary player'}'...")
    players = Config.get_players()
    target = None
    if player_name:
        target = Config.get_player_by_name(player_name)
    elif players:
        target = players[0]

    if not target:
        print("No matching player found in configuration.")
        return

    pwd_status = "SET" if target.get("birdies_password") else "MISSING"
    print(f"Target player found: {target.get('name')} ({target.get('birdies_email')}, password: {pwd_status})")
    sync = BirdiesSyncService()
    result = sync.sync_player(target)
    print("\n[Sync Result]:")
    for k, v in result.items():
        if k != "new_rounds":
            print(f" - {k}: {v}")
    print(f" - new_rounds count: {len(result['new_rounds'])}")

def test_auth_sync():
    print("Testing on-demand 18Birdies sync authorization logic...\n")
    from unittest.mock import MagicMock
    try:
        from neonize.proto.Neonize_pb2 import JID
    except ImportError:
        from neonize.proto.def_pb2 import JID
    from src.whatsapp_bot import GolfWhatsAppBot

    bot = GolfWhatsAppBot()
    sent_messages = []
    bot.send_text = lambda client, jid, text: sent_messages.append((jid, text))

    group_jid = JID(User="120363430173205792", Server="g.us")

    # Test 1: Unauthorized random user sends "@caddy sync"
    print("--- Test 1: Unauthorized stranger sends '@caddy sync' ---")
    sent_messages.clear()
    bot._process_text_command(
        client=None,
        chat_jid=group_jid,
        text="@caddy sync",
        sender_name="Random Stranger",
        sender_phone="61499999999"
    )
    if sent_messages and "Sync Request Denied" in sent_messages[0][1]:
        print(f" [PASS] Unauthorized user was successfully blocked:\n{sent_messages[0][1]}\n")
    else:
        print(f" [FAIL] Unexpected result: {sent_messages}\n")

    # Test 2: Verified user with matching credentials in .env sends "@caddy sync"
    print("--- Test 2: Authorized user with matching credentials sends '@caddy sync' ---")
    # Mock the actual Playwright sync to test bot routing without triggering headless browser
    bot.birdies.sync_player = MagicMock(return_value={
        "success": True,
        "player_name": "Thilina Fernando",
        "new_rounds": [],
        "last_round": {"round_date": "2026-09-26", "course_name": "Beaconhills Country Golf Club", "total_score": 94},
        "message": "Mocked successful sync",
        "error": None
    })
    sent_messages.clear()
    bot._process_text_command(
        client=None,
        chat_jid=group_jid,
        text="@caddy sync",
        sender_name="Thilina Fernando",
        sender_phone=""
    )
    if any("18BIRDIES SYNC COMPLETE" in m[1] for m in sent_messages):
        print(f" [PASS] Authorized user sync succeeded:\n{sent_messages[-1][1]}\n")
    else:
        print(f" [FAIL] Unexpected result: {sent_messages}\n")

    # Test 3: Authorized user requests sync for another player without credentials in .env
    print("--- Test 3: Request for player not in .env ---")
    sent_messages.clear()
    bot._process_text_command(
        client=None,
        chat_jid=group_jid,
        text="@caddy sync NonExistentGolfer",
        sender_name="Thilina Fernando",
        sender_phone=""
    )
    if sent_messages and "Could not find any golfer" in sent_messages[-1][1]:
        print(f" [PASS] Nonexistent player handled cleanly:\n{sent_messages[-1][1]}\n")
    else:
        print(f" [FAIL] Unexpected result: {sent_messages}\n")

    # Test 4: Requester is verified but has missing password in .env
    print("--- Test 4: Requester has missing credentials in .env ---")
    sent_messages.clear()
    bot._process_text_command(
        client=None,
        chat_jid=group_jid,
        text="@caddy sync",
        sender_name="NoPassUser",
        sender_phone="61411111111"
    )
    if sent_messages and "Sync Request Denied" in sent_messages[0][1]:
        print(f" [PASS] Requester missing credentials was blocked:\n{sent_messages[0][1]}\n")
    else:
        print(f" [FAIL] Unexpected result: {sent_messages}\n")

    # Test 5: Verify _handle_incoming_message with neonize MessageEv
    print("--- Test 5: Incoming MessageEv with '@caddy hi' ---")
    from neonize.events import MessageEv
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as ProtoMessage

    mock_ev = MessageEv()
    mock_ev.Info.MessageSource.Chat.User = "120363430173205792"
    mock_ev.Info.MessageSource.Chat.Server = "g.us"
    mock_ev.Info.MessageSource.Sender.User = "61483707094"
    mock_ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    mock_ev.Info.MessageSource.IsFromMe = False
    mock_ev.Info.Pushname = "Thilina Fernando"
    mock_ev.Message.conversation = "@caddy hi"

    # Mock client and gemini chat
    bot.client.get_group_info = MagicMock(return_value=None)
    bot.gemini.chat_as_caddy = MagicMock(return_value="Ado machan! What's happening men? Ready for golf? 😂")

    sent_messages.clear()
    bot._handle_incoming_message(bot.client, mock_ev)
    if sent_messages and "Ado machan" in sent_messages[-1][1]:
        print(f" [PASS] Incoming MessageEv was processed without errors:\nReply: {sent_messages[-1][1]}\n")
    else:
        print(f" [FAIL] Unexpected result for MessageEv: {sent_messages}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Golf Bot Features")
    parser.add_argument("--weather", action="store_true", help="Test weather check")
    parser.add_argument("--caddy-chat", type=str, help="Test Caddy chat")
    parser.add_argument("--test-roast", action="store_true", help="Test 18Birdies scorecard roast")
    parser.add_argument("--test-players", action="store_true", help="Verify configured player credentials from env/json")
    parser.add_argument("--test-recap", nargs="?", const="", type=str, help="Test last round recap card and roast")
    parser.add_argument("--test-sync", nargs="?", const="", type=str, help="Test on-demand 18Birdies sync for player")
    parser.add_argument("--test-auth-sync", action="store_true", help="Test authorization and credential validation for sync")
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
    elif args.test_recap is not None:
        test_recap(args.test_recap or None)
    elif args.test_sync is not None:
        test_sync(args.test_sync or None)
    elif args.test_auth_sync:
        test_auth_sync()
    elif args.test_dashboard:
        test_dashboard()
    else:
        parser.print_help()
