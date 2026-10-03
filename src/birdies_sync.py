import os
import json
import glob
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from src.config import Config
from src.db import (
    is_round_processed,
    record_processed_round,
    update_player_sync_status,
    get_all_player_sync_statuses,
    get_last_round_for_player,
    log_activity
)
from src.gemini_client import GeminiService

class BirdiesSyncService:
    def __init__(self, gemini_service: Optional[GeminiService] = None):
        self.gemini = gemini_service or GeminiService()
        self.data_dir = Config.BIRDIES_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def download_account_data_playwright(self, email: str, password: str, player_name: str) -> Optional[Path]:
        """
        Automates login and download from https://18birdies.com/download-account-data/
        using Playwright in headless mode.
        """
        if not email or not password:
            print(f"[18Birdies] Skipping download for {player_name}: email or password missing.")
            update_player_sync_status(
                player_name=player_name,
                email=email,
                success=False,
                error_message="Missing email or password"
            )
            return None

        try:
            from playwright.sync_api import sync_playwright
            print(f"[18Birdies] Initiating headless download for {player_name} ({email})...")
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()

                # Navigate to the data download page
                page.goto("https://18birdies.com/download-account-data/", timeout=30000)
                page.wait_for_load_state("networkidle")

                # Fill login form with resilient selectors matching 18Birdies live page
                email_input = page.locator("input[placeholder*='Email'], input[placeholder*='Phone'], input[type='email'], input[name='email'], input[name='username']").first
                if email_input.count() > 0:
                    email_input.fill(email)
                else:
                    all_inputs = page.locator("input").all()
                    if all_inputs:
                        all_inputs[0].fill(email)

                pass_input = page.locator("input[type='password'], input[placeholder*='Password'], input[name='password']").first
                if pass_input.count() > 0:
                    pass_input.fill(password)
                else:
                    all_inputs = page.locator("input").all()
                    if len(all_inputs) > 1:
                        all_inputs[1].fill(password)

                # Submit login and capture download event
                submit_button = page.locator("button:has-text('Request My Data'), button:has-text('Log In'), button:has-text('Download'), button[type='submit']").first
                if submit_button.count() == 0:
                    raise ValueError("Could not find submit button on 18Birdies page")

                print(f"[18Birdies] Requesting data archive for {player_name}...")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                try:
                    with page.expect_download(timeout=20000) as download_info:
                        submit_button.click()

                    download = download_info.value
                    suggested_name = download.suggested_filename
                    ext = ".zip" if str(suggested_name).endswith(".zip") else ".json"
                    target_file = self.data_dir / f"{player_name}_{timestamp}{ext}"
                    download.save_as(str(target_file))
                    print(f"[18Birdies] Successfully downloaded archive to {target_file}")

                    # If downloaded file is a zip archive, extract it
                    if ext == ".zip":
                        try:
                            import zipfile
                            with zipfile.ZipFile(str(target_file), 'r') as zip_ref:
                                zip_ref.extractall(str(self.data_dir))
                            print(f"[18Birdies] Extracted zip archive to {self.data_dir}")
                        except Exception as ze:
                            print(f"[18Birdies] Error extracting zip archive: {ze}")

                    browser.close()

                    # Mark sync success in database
                    update_player_sync_status(
                        player_name=player_name,
                        email=email,
                        success=True
                    )
                    log_activity("BIRDIES_SYNC", "SUCCESS", f"Downloaded archive for {player_name}")
                    return target_file

                except Exception as dl_err:
                    # Check if error message is displayed on page
                    page.wait_for_timeout(2000)
                    body_text = page.locator("body").inner_text()
                    if "did not match" in body_text.lower():
                        raise ValueError("18Birdies email and password did not match.")
                    if "account does not exist" in body_text.lower() or "user not found" in body_text.lower():
                        raise ValueError("18Birdies account does not exist or user not found.")
                    if "submitted" in body_text.lower() or "processing" in body_text.lower() or "generating" in body_text.lower():
                        msg = f"18Birdies request queued for {player_name} (data archive is being generated)."
                        print(f"[18Birdies] {msg}")
                        update_player_sync_status(player_name=player_name, email=email, success=True, error_message="Archive queued")
                        log_activity("BIRDIES_SYNC", "INFO", msg)
                        browser.close()
                        return None
                    raise dl_err

                browser.close()
        except ImportError:
            print("[18Birdies] Playwright is not installed or available. Using local file sync.")
            update_player_sync_status(
                player_name=player_name,
                email=email,
                success=False,
                error_message="Playwright browser not available"
            )
            log_activity("BIRDIES_SYNC", "FAILURE", f"Playwright not available for {player_name}")
        except Exception as e:
            print(f"[18Birdies] Error during automated download for {player_name}: {e}")
            update_player_sync_status(
                player_name=player_name,
                email=email,
                success=False,
                error_message=str(e)
            )
            log_activity("BIRDIES_SYNC", "FAILURE", f"Error syncing {player_name}: {e}")
        return None

    def sync_player(self, player_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synchronizes 18Birdies data on-demand for a single player with matching credentials in .env.
        """
        name = player_info.get("name", "Golfer")
        email = player_info.get("birdies_email", "").strip()
        password = player_info.get("birdies_password", "").strip()

        if not email or not password:
            return {
                "success": False,
                "player_name": name,
                "email": email,
                "downloaded_file": None,
                "new_rounds": [],
                "last_round": None,
                "message": f"Missing 18Birdies email or password for {name} in .env file.",
                "error": "Missing credentials"
            }

        print(f"[18Birdies] Running on-demand sync for {name} ({email})...")
        downloaded_file = self.download_account_data_playwright(email, password, name)

        # Scan for any new rounds
        new_rounds = self.scan_for_new_rounds()
        player_new_rounds = [
            r for r in new_rounds
            if (name.lower() in r["round_data"].get("player_name", "").lower()) or
               (r["round_data"].get("player_name", "").lower() in name.lower())
        ]

        last_round = self.get_player_last_round(name)

        if downloaded_file:
            return {
                "success": True,
                "player_name": name,
                "email": email,
                "downloaded_file": str(downloaded_file),
                "new_rounds": player_new_rounds,
                "last_round": last_round,
                "message": f"Successfully pulled latest 18Birdies archive for {name}.",
                "error": None
            }

        from src.db import get_player_sync_status
        sync_status = get_player_sync_status(name)
        err = sync_status.get("error_message") if sync_status else "Download timed out"
        is_success = (sync_status.get("status") == "SUCCESS") if sync_status else False

        return {
            "success": is_success,
            "player_name": name,
            "email": email,
            "downloaded_file": None,
            "new_rounds": player_new_rounds,
            "last_round": last_round,
            "message": f"Sync status for {name}: {err}",
            "error": err if not is_success else None
        }

    def sync_all_players(self):
        """
        Runs automated download for all configured friends in .env or players.json.
        """
        players = Config.get_players()
        if not players:
            print("[18Birdies] No players configured to sync.")
            return

        print(f"[18Birdies] Polling 18Birdies data for {len(players)} friends: {[p['name'] for p in players]}")
        for p in players:
            name = p.get("name", "Golfer")
            email = p.get("birdies_email") or ""
            password = p.get("birdies_password") or ""
            if email and password:
                self.download_account_data_playwright(email, password, name)

    def parse_round_data(
        self,
        round_obj: Dict[str, Any],
        default_player: str = "Golfer",
        club_map: Optional[Dict[str, str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Parses 18Birdies round JSON object into structured stats.
        Fully compatible with official 18Birdies myData.activityData.rounds schema!
        """
        try:
            club_map = club_map or {}
            
            # 1. Round ID
            round_id = str(round_obj.get("id") or round_obj.get("round_id") or round_obj.get("roundId") or hash(str(round_obj)))

            # 2. Date from timestamp (epoch milliseconds)
            round_date = datetime.now().strftime("%Y-%m-%d")
            ts = round_obj.get("timestamp")
            if ts:
                try:
                    # Milliseconds to seconds if needed
                    epoch_sec = ts / 1000.0 if ts > 1e11 else ts
                    round_date = datetime.fromtimestamp(epoch_sec).strftime("%Y-%m-%d")
                except Exception:
                    pass
            elif round_obj.get("played_at") or round_obj.get("date"):
                round_date = str(round_obj.get("played_at") or round_obj.get("date")).split("T")[0]

            # 3. Course / Club Name resolution
            club_id = None
            if isinstance(round_obj.get("clubId"), dict):
                club_id = round_obj["clubId"].get("id")
            elif isinstance(round_obj.get("clubId"), str):
                club_id = round_obj["clubId"]

            course_name = (
                club_map.get(club_id) or 
                round_obj.get("course_name") or 
                round_obj.get("courseName") or 
                Config.DEFAULT_COURSE_NAME
            )

            # 4. Total strokes and Score relative to par
            # In 18Birdies: 'strokes' is gross score (e.g. 107), 'score' is score to par (e.g. 37 or 8)
            strokes = round_obj.get("strokes")
            score_to_par_val = round_obj.get("score")
            
            hole_strokes = round_obj.get("holeStrokes") or []
            if strokes is None:
                strokes = sum(hole_strokes) if hole_strokes else (round_obj.get("total_score") or 90)

            if score_to_par_val is not None:
                score_to_par = f"+{score_to_par_val}" if score_to_par_val > 0 else (str(score_to_par_val) if score_to_par_val < 0 else "E")
            else:
                score_to_par = round_obj.get("score_to_par", "N/A")

            # 5. Front 9 & Back 9 splits
            num_holes = len(hole_strokes)
            front_9_score = sum(hole_strokes[:9]) if num_holes >= 9 else sum(hole_strokes)
            back_9_score = sum(hole_strokes[9:]) if num_holes > 9 else "N/A"

            # 6. Granular Stats (18Birdies 'stats' dict)
            stats = round_obj.get("stats") or {}
            birdies = stats.get("birdies", 0)
            eagles = stats.get("eagles", 0)
            pars = stats.get("pars", 0)
            bogeys = stats.get("bogeys", 0)
            double_bogeys = stats.get("doubleBogeyOrWorse", 0)

            fairways_hit = stats.get("fairwayMiddles", 0)
            fairways_total = stats.get("fairwayHoleCount", 14 if num_holes >= 18 else 7)
            fairway_pct = round((fairways_hit / fairways_total * 100), 1) if fairways_total > 0 else 0

            gir_hit = stats.get("gir", 0)
            gir_total = stats.get("girHoleCount", num_holes or 18)
            gir_pct = round((gir_hit / gir_total * 100), 1) if gir_total > 0 else 0

            total_putts = stats.get("putts", 0)
            putts_per_hole = round(total_putts / num_holes, 2) if (total_putts > 0 and num_holes > 0) else 0.0

            # 7. Best & Worst Hole Identification
            worst_hole = "N/A"
            best_hole = "N/A"
            if hole_strokes:
                max_stroke = max(hole_strokes)
                min_stroke = min(hole_strokes)
                worst_hole_num = hole_strokes.index(max_stroke) + 1
                best_hole_num = hole_strokes.index(min_stroke) + 1
                worst_hole = f"Hole {worst_hole_num} ({max_stroke} strokes)"
                if double_bogeys > 0:
                    worst_hole += f" [{double_bogeys} Double+ holes]"

                if eagles > 0:
                    best_hole = f"Eagle on Hole {best_hole_num}!"
                elif birdies > 0:
                    best_hole = f"Birdie on Hole {best_hole_num}!"
                elif pars > 0:
                    best_hole = f"Par on Hole {best_hole_num} ({pars} pars total)"
                else:
                    best_hole = f"Hole {best_hole_num} ({min_stroke} strokes)"

            # 8. Longest Shot / Drive from shotEntries
            shot_entries = round_obj.get("shotEntries") or []
            max_drive_yds = 0
            max_drive_hole = None
            for s in shot_entries:
                dist = s.get("distanceInYards") or 0
                if dist > max_drive_yds:
                    max_drive_yds = dist
                    max_drive_hole = s.get("holeNumber")

            if max_drive_yds > 180 and max_drive_hole:
                best_hole += f" | {int(max_drive_yds)}y drive on Hole {max_drive_hole}"

            return {
                "round_id": round_id,
                "player_name": round_obj.get("player_name") or default_player,
                "course_name": course_name,
                "round_date": round_date,
                "total_score": strokes,
                "score_to_par": score_to_par,
                "front_9": front_9_score,
                "back_9": back_9_score,
                "fairways_hit": fairways_hit,
                "fairways_total": fairways_total,
                "fairway_pct": fairway_pct,
                "gir_hit": gir_hit,
                "gir_total": gir_total,
                "gir_pct": gir_pct,
                "total_putts": total_putts,
                "putts_per_hole": putts_per_hole,
                "best_hole": best_hole,
                "worst_hole": worst_hole,
                "penalties": double_bogeys,
                "handicap": round_obj.get("roundHandicap"),
                "raw_obj": round_obj
            }
        except Exception as e:
            print(f"[18Birdies] Error parsing individual round: {e}")
            return None

    def scan_for_new_rounds(self) -> List[Dict[str, Any]]:
        """
        Scans all JSON files in data/18birdies/ and detects unprocessed rounds.
        Accurately parses 18Birdies schema:
        - myData.accountData.userName
        - myData.clubData.playedClubs
        - myData.activityData.rounds
        """
        new_round_summaries = []
        json_files = glob.glob(str(self.data_dir / "*.json"))

        for filepath in json_files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = json.load(f)

                # Build club map if present
                club_map: Dict[str, str] = {}
                detected_player_name = Path(filepath).stem.split("_")[0]

                # 18Birdies myData root structure
                rounds_list = []
                if isinstance(content, dict) and "myData" in content:
                    my_data = content["myData"]
                    # Extract player name
                    detected_player_name = my_data.get("accountData", {}).get("userName") or detected_player_name
                    
                    # Extract clubs lookup map
                    clubs = my_data.get("clubData", {}).get("playedClubs") or []
                    for c in clubs:
                        if c.get("clubId") and c.get("name"):
                            club_map[c["clubId"]] = c["name"]
                    
                    rounds_list = my_data.get("activityData", {}).get("rounds") or []
                elif isinstance(content, list):
                    rounds_list = content
                elif isinstance(content, dict):
                    rounds_list = content.get("rounds") or content.get("activityData", {}).get("rounds") or [content]

                # Collect all unprocessed rounds in this file
                unprocessed_rounds = []
                for item in rounds_list:
                    parsed = self.parse_round_data(
                        item,
                        default_player=detected_player_name,
                        club_map=club_map
                    )
                    if parsed and not is_round_processed(parsed["round_id"], player_name=parsed["player_name"]):
                        unprocessed_rounds.append(parsed)

                if not unprocessed_rounds:
                    continue

                # Sort by date descending (newest first)
                unprocessed_rounds.sort(key=lambda r: str(r.get("round_date", "")), reverse=True)

                # Only roast at most the top 2 newest rounds to protect Gemini quota & avoid spamming chat
                rounds_to_roast = unprocessed_rounds[:2]
                older_rounds = unprocessed_rounds[2:]

                for parsed in rounds_to_roast:
                    round_id = parsed["round_id"]
                    print(f"[18Birdies] Detected new round to roast: {parsed['player_name']} at {parsed['course_name']} ({parsed['round_date']}) - Strokes: {parsed['total_score']}")
                    
                    # Generate Caddy's short round summary
                    summary_text = self.gemini.generate_round_summary(parsed)

                    # Record in database
                    record_processed_round(
                        player_name=parsed["player_name"],
                        round_date=parsed["round_date"],
                        course_name=parsed["course_name"],
                        total_score=parsed["total_score"],
                        score_to_par=int(str(parsed["score_to_par"]).replace("+", "").replace("E", "0") or 0),
                        external_round_id=round_id,
                        raw_stats_json=json.dumps(parsed),
                        summary_posted=True
                    )

                    update_player_sync_status(
                        player_name=parsed["player_name"],
                        email="",
                        success=True,
                        rounds_count=len(rounds_list),
                        latest_round_id=round_id,
                        latest_round_date=parsed["round_date"]
                    )

                    log_activity("ROUND_ROASTED", "SUCCESS", f"{parsed['player_name']} scored {parsed['total_score']} ({parsed['score_to_par']}) at {parsed['course_name']}")

                    new_round_summaries.append({
                        "round_data": parsed,
                        "summary_message": summary_text
                    })

                # For any older historical rounds in a fresh archive, record them without calling Gemini
                for parsed in older_rounds:
                    round_id = parsed["round_id"]
                    record_processed_round(
                        player_name=parsed["player_name"],
                        round_date=parsed["round_date"],
                        course_name=parsed["course_name"],
                        total_score=parsed["total_score"],
                        score_to_par=int(str(parsed["score_to_par"]).replace("+", "").replace("E", "0") or 0),
                        external_round_id=round_id,
                        raw_stats_json=json.dumps(parsed),
                        summary_posted=True
                    )
                if older_rounds:
                    print(f"[18Birdies] Automatically recorded {len(older_rounds)} older historical rounds for {detected_player_name} without separate roasts.")
            except Exception as e:
                print(f"[18Birdies] Error reading JSON file {filepath}: {e}")

        return new_round_summaries

    def get_sync_status_summary(self) -> str:
        """
        Formats a friendly summary of sync health and last round for WhatsApp.
        """
        statuses = get_all_player_sync_statuses()
        if not statuses:
            return "No 18Birdies sync data recorded yet, machan! Run a sync first."

        msg = "📊 *18BIRDIES SYNC & ROUND STATUS* 📊\n\n"
        for s in statuses:
            status_icon = "✅" if s["status"] == "SUCCESS" else "⚠️"
            last_pull = s["last_successful_pull_at"] or "Never"
            latest_date = s["latest_round_date"] or "None"
            msg += (
                f"{status_icon} *{s['player_name']}*\n"
                f"   • Last Pull: {last_pull}\n"
                f"   • Latest Round: {latest_date}\n"
                f"   • Status: {s['status']}\n\n"
            )
        msg += "Played a round? Log it on 18Birdies and run `@caddy sync`, machan."
        return msg

    def get_player_last_round(self, player_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Retrieves the last round for a given player:
        1. Checks processed_rounds in DB.
        2. If not found in DB, scans all JSON files in data/18birdies/.
        """
        db_round = get_last_round_for_player(player_name)
        if db_round and db_round.get("raw_stats_json"):
            try:
                stats = json.loads(db_round["raw_stats_json"])
                return stats
            except Exception:
                return {
                    "player_name": db_round["player_name"],
                    "course_name": db_round["course_name"],
                    "round_date": db_round["round_date"],
                    "total_score": db_round["total_score"],
                    "score_to_par": f"+{db_round['score_to_par']}" if db_round['score_to_par'] > 0 else str(db_round['score_to_par']),
                    "best_hole": "N/A",
                    "worst_hole": "N/A",
                    "total_putts": "N/A",
                    "fairway_pct": "N/A",
                    "gir_pct": "N/A"
                }

        # Check local JSON files in data/18birdies
        json_files = glob.glob(str(self.data_dir / "*.json"))
        matching_rounds = []
        for filepath in json_files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = json.load(f)

                club_map: Dict[str, str] = {}
                file_player = Path(filepath).stem.split("_")[0]

                rounds_list = []
                if isinstance(content, dict) and "myData" in content:
                    my_data = content["myData"]
                    file_player = my_data.get("accountData", {}).get("userName") or file_player
                    clubs = my_data.get("clubData", {}).get("playedClubs") or []
                    for c in clubs:
                        if c.get("clubId") and c.get("name"):
                            club_map[c["clubId"]] = c["name"]
                    rounds_list = my_data.get("activityData", {}).get("rounds") or []
                elif isinstance(content, list):
                    rounds_list = content
                elif isinstance(content, dict):
                    rounds_list = content.get("rounds") or content.get("activityData", {}).get("rounds") or [content]

                for item in rounds_list:
                    parsed = self.parse_round_data(item, default_player=file_player, club_map=club_map)
                    if parsed:
                        if not player_name or (player_name.lower() in parsed["player_name"].lower()) or (parsed["player_name"].lower() in player_name.lower()):
                            matching_rounds.append(parsed)
            except Exception as e:
                print(f"[18Birdies] Error parsing {filepath}: {e}")

        if matching_rounds:
            matching_rounds.sort(key=lambda r: str(r.get("round_date", "")), reverse=True)
            return matching_rounds[0]

        return None

    def format_round_recap(self, round_data: Dict[str, Any], roast_comment: Optional[str] = None) -> str:
        """
        Formats a comprehensive post-round recap card for WhatsApp.
        """
        player = round_data.get("player_name", "Golfer")
        course = round_data.get("course_name", Config.DEFAULT_COURSE_NAME)
        date_str = round_data.get("round_date", datetime.now().strftime("%Y-%m-%d"))
        strokes = round_data.get("total_score", "N/A")
        score_to_par = round_data.get("score_to_par", "")
        if score_to_par and not str(score_to_par).startswith(("+", "-", "E")):
            score_to_par = f"+{score_to_par}"

        score_display = f"{strokes} ({score_to_par})" if score_to_par else str(strokes)

        f9 = round_data.get("front_9", "N/A")
        b9 = round_data.get("back_9", "N/A")

        fw_hit = round_data.get("fairways_hit", 0)
        fw_tot = round_data.get("fairways_total", 0)
        fw_pct = round_data.get("fairway_pct", 0)
        fw_display = f"{fw_hit}/{fw_tot} ({fw_pct}%)" if fw_tot else "N/A"

        gir_hit = round_data.get("gir_hit", 0)
        gir_tot = round_data.get("gir_total", 0)
        gir_pct = round_data.get("gir_pct", 0)
        gir_display = f"{gir_hit}/{gir_tot} ({gir_pct}%)" if gir_tot else "N/A"

        putts = round_data.get("total_putts", "N/A")
        putts_per_hole = round_data.get("putts_per_hole")
        putts_display = f"{putts} ({putts_per_hole}/hole)" if putts_per_hole else str(putts)

        best_hole = round_data.get("best_hole", "N/A")
        worst_hole = round_data.get("worst_hole", "N/A")

        # Prefer the monster drive as the highlight if one was recorded
        highlight = best_hole.split(" | ")[-1] if best_hole and best_hole != "N/A" else None
        # Drop the "[n Double+ holes]" suffix for a cleaner low point
        low_point = worst_hole.split(" [")[0] if worst_hole and worst_hole != "N/A" else None

        stat_bits = [f"{putts} putts" if putts not in (None, "N/A", 0) else None]
        if gir_tot:
            stat_bits.append(f"GIR {gir_hit}/{gir_tot}")
        if fw_tot:
            stat_bits.append(f"FIR {fw_hit}/{fw_tot}")
        stat_line = " · ".join(b for b in stat_bits if b)

        split = f" (F9 {f9} / B9 {b9})" if b9 not in (None, "N/A") else ""
        lines = [f"⛳ *{player}* — {course}, {date_str}", f"🎯 {score_display}{split}"]
        if highlight:
            lines.append(f"✅ {highlight}")
        if low_point:
            lines.append(f"⚠️ {low_point}")
        if stat_line:
            lines.append(f"📊 {stat_line}")
        if roast_comment:
            lines.append(f"\n{roast_comment}")

        return "\n".join(lines)

