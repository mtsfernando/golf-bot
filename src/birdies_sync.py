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

                # Fill login form
                if page.locator("input[type='email'], input[name='email'], input[name='username']").count() > 0:
                    page.fill("input[type='email'], input[name='email'], input[name='username']", email)
                
                if page.locator("input[type='password'], input[name='password']").count() > 0:
                    page.fill("input[type='password'], input[name='password']", password)

                # Submit login / request data
                submit_button = page.locator("button[type='submit'], input[type='submit'], button:has-text('Log In'), button:has-text('Download')")
                if submit_button.count() > 0:
                    submit_button.first.click()

                # Wait for download event or confirmation
                try:
                    with page.expect_download(timeout=20000) as download_info:
                        download_btn = page.locator("button:has-text('Download Data'), a:has-text('Download Data')")
                        if download_btn.count() > 0:
                            download_btn.first.click()
                    download = download_info.value
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    target_file = self.data_dir / f"{player_name}_{timestamp}.json"
                    download.save_as(str(target_file))
                    print(f"[18Birdies] Successfully downloaded archive to {target_file}")
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
                    print(f"[18Birdies] Download event timed out or confirmation queued for {player_name}: {dl_err}")
                    update_player_sync_status(
                        player_name=player_name,
                        email=email,
                        success=False,
                        error_message=str(dl_err)
                    )
                    log_activity("BIRDIES_SYNC", "FAILURE", f"Download failed for {player_name}: {dl_err}")
                
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

                for item in rounds_list:
                    parsed = self.parse_round_data(
                        item,
                        default_player=detected_player_name,
                        club_map=club_map
                    )
                    if not parsed:
                        continue

                    round_id = parsed["round_id"]
                    if not is_round_processed(round_id):
                        print(f"[18Birdies] Detected new round: {parsed['player_name']} at {parsed['course_name']} ({parsed['round_date']}) - Strokes: {parsed['total_score']}")
                        
                        # Generate Anura Kumara's witty roast
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

                        # Update sync status table with latest round info
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
        msg += "Anura Kumara says: Comrades, if your scorecard is missing from the registry, submit your declaration on 18Birdies immediately! Transparency is non-negotiable! 🧭"
        return msg
