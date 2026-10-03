import os
import time
import logging
import signal
import sys
from apscheduler.schedulers.background import BackgroundScheduler

from src.config import Config
from src.db import init_db
from src.whatsapp_bot import GolfWhatsAppBot
from src.weather import WeatherService
from src.birdies_sync import BirdiesSyncService
from src.web_dashboard import start_dashboard_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("Main")

def run_weather_check_job(bot: GolfWhatsAppBot, weather_svc: WeatherService):
    logger.info("⏰ [Scheduler] Running automated weather check for upcoming tee times...")
    try:
        pending_alerts = weather_svc.check_and_generate_pending_alerts()
        for tee_id, window, warning_text in pending_alerts:
            logger.info(f"🌧️ Sending {window} rain alert for Tee Time ID {tee_id}...")
            bot.broadcast_to_group(warning_text)
    except Exception as e:
        logger.error(f"[Scheduler] Error in weather check job: {e}")

def run_birdies_sync_job(bot: GolfWhatsAppBot, birdies_svc: BirdiesSyncService):
    logger.info("⏰ [Scheduler] Running 18Birdies data sync and scorecard scan...")
    try:
        # 1. Automated download if accounts configured
        birdies_svc.sync_all_players()

        # 2. Check for newly uploaded or downloaded rounds
        new_rounds = birdies_svc.scan_for_new_rounds()
        for item in new_rounds:
            logger.info(f"🏆 Posting witty round roast for {item['round_data']['player_name']}...")
            bot.broadcast_to_group(item["summary_message"])
    except Exception as e:
        logger.error(f"[Scheduler] Error in 18Birdies sync job: {e}")

def run_groups_sync_job(bot: GolfWhatsAppBot):
    try:
        bot.sync_joined_groups()
    except Exception as e:
        logger.error(f"[Scheduler] Error in groups sync job: {e}")

def main():
    logger.info("⛳ =============================================")
    logger.info("⛳ GOLF-BOT: Sri Lankan Caddy WhatsApp Assistant")
    logger.info("⛳ =============================================")

    # 1. Initialize Database
    init_db()

    # 2. Start Web Monitoring Dashboard
    dashboard_port = Config.DASHBOARD_PORT
    start_dashboard_server(port=dashboard_port)

    # 3. Initialize Services
    bot = GolfWhatsAppBot()
    weather_svc = bot.weather
    birdies_svc = bot.birdies

    # 4. Setup Background Scheduler
    scheduler = BackgroundScheduler()

    # Weather check job (every 30 mins)
    scheduler.add_job(
        func=run_weather_check_job,
        args=[bot, weather_svc],
        trigger="interval",
        minutes=Config.WEATHER_CHECK_INTERVAL_MINUTES,
        id="weather_check_job"
    )

    # 18Birdies sync job (every 15 mins)
    scheduler.add_job(
        func=run_birdies_sync_job,
        args=[bot, birdies_svc],
        trigger="interval",
        minutes=Config.BIRDIES_SYNC_INTERVAL_MINUTES,
        id="birdies_sync_job"
    )

    # WhatsApp groups membership reconciliation job (every 5 mins)
    scheduler.add_job(
        func=run_groups_sync_job,
        args=[bot],
        trigger="interval",
        minutes=5,
        id="groups_sync_job"
    )

    scheduler.start()
    logger.info(f"✅ Background scheduler started (Weather: {Config.WEATHER_CHECK_INTERVAL_MINUTES}m, 18Birdies: {Config.BIRDIES_SYNC_INTERVAL_MINUTES}m, Groups: 5m).")

    # Graceful shutdown handler
    def handle_exit(sig, frame):
        logger.info("🛑 Shutting down Golf Bot...")
        scheduler.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    # 4. Start WhatsApp Bot (Blocking event loop)
    try:
        bot.start()
    except KeyboardInterrupt:
        handle_exit(None, None)

if __name__ == "__main__":
    main()
