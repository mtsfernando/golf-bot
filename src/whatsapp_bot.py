import os
import io
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from neonize.client import NewClient
from neonize.events import MessageEv, ConnectedEv, PairStatusEv
from neonize.utils import log
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message, EventMessage
from neonize.proto.def_pb2 import JID

from src.config import Config
from src.db import save_tee_time, get_next_tee_time, get_upcoming_tee_times, log_activity
from src.gemini_client import GeminiService
from src.weather import WeatherService
from src.birdies_sync import BirdiesSyncService
from src.web_dashboard import set_bot_connected

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GolfBot")

class GolfWhatsAppBot:
    def __init__(self):
        self.session_db_path = str(Config.SESSION_DIR / "whatsapp.db")
        self.client = NewClient(self.session_db_path)
        self.gemini = GeminiService()
        self.weather = WeatherService()
        self.birdies = BirdiesSyncService(self.gemini)
        self.active_group_jid: Optional[JID] = None
        self._register_events()

    def _register_events(self):
        @self.client.event(ConnectedEv)
        def on_connected(client: NewClient, event: ConnectedEv):
            logger.info("⛳ [WhatsApp] Golf Bot connected successfully to WhatsApp network!")
            set_bot_connected(True)
            log_activity("CONNECTIVITY", "SUCCESS", "WhatsApp bot connected successfully")

        @self.client.event(PairStatusEv)
        def on_pair_status(client: NewClient, event: PairStatusEv):
            logger.info(f"📱 [WhatsApp] Pairing Status: {event.ID.User}")
            set_bot_connected(True, phone=event.ID.User)
            log_activity("CONNECTIVITY", "INFO", f"WhatsApp device paired as {event.ID.User}")

        @self.client.event(MessageEv)
        def on_message(client: NewClient, event: MessageEv):
            self._handle_incoming_message(client, event)

    def _handle_incoming_message(self, client: NewClient, event: MessageEv):
        try:
            # Ignore messages sent by bot itself
            if event.Info.MessageSource.IsFromMe:
                return

            chat_jid = event.Info.MessageSource.Chat
            sender_push_name = event.Info.PushName or "Golfer"

            # Track group JID if target group matches or if active
            if chat_jid.Server == "g.us":
                self.active_group_jid = chat_jid

            msg_pb = event.Message
            if not msg_pb:
                return

            # -------------------------------------------------------------
            # 1. Check for Image Message (Tee Time Booking Screenshot)
            # -------------------------------------------------------------
            if msg_pb.imageMessage and msg_pb.imageMessage.URL:
                logger.info(f"📸 Image received from {sender_push_name}. Checking for tee time booking screenshot...")
                try:
                    img_bytes = client.download_any(msg_pb)
                    if img_bytes:
                        self._process_potential_tee_time_image(client, chat_jid, img_bytes, sender_push_name)
                except Exception as e:
                    logger.error(f"Error downloading image: {e}")
                return

            # -------------------------------------------------------------
            # 2. Extract Text Message
            # -------------------------------------------------------------
            text = (
                msg_pb.conversation or 
                (msg_pb.extendedTextMessage.text if msg_pb.extendedTextMessage else None) or
                ""
            ).strip()

            if not text:
                return

            # Check if bot is mentioned or keyword is used
            trigger = Config.BOT_TRIGGER_KEYWORD.lower()
            is_triggered = trigger in text.lower() or text.lower().startswith("!caddy") or text.lower().startswith("!golf")

            # Direct messages to bot don't strictly require trigger keyword
            is_direct_chat = chat_jid.Server == "s.whatsapp.net"

            if not (is_triggered or is_direct_chat):
                return

            logger.info(f"💬 Command/Query received from {sender_push_name}: {text}")
            log_activity("MESSAGE_RECEIVED", "INFO", f"From {sender_push_name}: {text[:120]}")
            self._process_text_command(client, chat_jid, text, sender_push_name)

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            log_activity("ERROR", "FAILURE", f"Message handling error: {e}")

    def _process_potential_tee_time_image(self, client: NewClient, chat_jid: JID, img_bytes: bytes, sender_name: str):
        """
        Uses Gemini to detect and parse tee time booking screenshot, then creates WhatsApp event.
        """
        try:
            booking_data = self.gemini.analyze_tee_time_image(img_bytes)
        except Exception as e:
            log_activity("TEE_TIME_OCR", "FAILURE", f"Gemini OCR error: {e}")
            return

        if not booking_data:
            logger.info("Image was not recognized as a golf booking screenshot.")
            return

        course = booking_data.get("course_name") or Config.DEFAULT_COURSE_NAME
        date_str = booking_data.get("date") or datetime.now().strftime("%Y-%m-%d")
        start_time = booking_data.get("start_time") or "08:00"
        end_time = booking_data.get("end_time")
        players_list = booking_data.get("players") or [sender_name]
        players_str = ", ".join(players_list)
        booking_ref = booking_data.get("booking_ref") or "App Booking"

        # Calculate timestamps for WhatsApp Event
        try:
            start_dt = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M")
        except Exception:
            start_dt = datetime.now() + timedelta(days=1)

        end_dt = start_dt + timedelta(hours=4, minutes=30)
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)

        # Save to database
        tee_id = save_tee_time(
            course_name=course,
            date_str=date_str,
            start_time=start_time,
            end_time=end_time or end_dt.strftime("%H:%M"),
            players=players_str,
            booking_ref=booking_ref,
            latitude=Config.DEFAULT_COURSE_LAT,
            longitude=Config.DEFAULT_COURSE_LON,
            raw_details=str(booking_data)
        )

        logger.info(f"✅ Tee time saved to DB (ID: {tee_id}). Creating WhatsApp event...")

        # 1. Try to send native WhatsApp EventMessage
        try:
            event_msg = EventMessage(
                name=f"🏌️ {course} Round",
                description=f"Tee Time: {start_time}\nPlayers: {players_str}\nRef: {booking_ref}",
                startTime=start_ms,
                endTime=end_ms,
            )
            client.send_message(chat_jid, Message(eventMessage=event_msg))
            logger.info("Sent native WhatsApp EventMessage.")
            log_activity("EVENT_CREATED", "SUCCESS", f"Native WhatsApp event: {course} ({date_str} {start_time})")
        except Exception as ev_err:
            logger.warning(f"Could not send native eventMessage (falling back to card): {ev_err}")
            log_activity("EVENT_CREATED", "WARNING", f"Fallback to card: {ev_err}")

        # 2. Send Jehan Ratnatunga's Confirmation and Banter Card
        confirmation_msg = (
            f"⛳ *NEW TEE TIME LOCKED IN!* ⛳\n\n"
            f"Ado machan! {sender_name} just sorted out our next round! Event is locked in:\n\n"
            f"📍 *Course:* {course}\n"
            f"📅 *Date:* {date_str} ({start_dt.strftime('%A')})\n"
            f"⏰ *Tee Off:* {start_time}\n"
            f"👥 *The Boys:* {players_str}\n"
            f"🔖 *Booking Ref:* {booking_ref}\n\n"
            f"Jehan's advice: Do not show up late because of Monash Freeway traffic men! "
            f"Pack two mutton rolls in your golf bag and bring at least 8 balls, because I know what happens on hole 3! 😂🏌️‍♂️"
        )
        self.send_text(client, chat_jid, confirmation_msg)

    def _process_text_command(self, client: NewClient, chat_jid: JID, text: str, sender_name: str):
        cleaned = text.lower()
        trigger = Config.BOT_TRIGGER_KEYWORD.lower()
        query = cleaned.replace(trigger, "").replace("!caddy", "").replace("!golf", "").strip()

        # Command: Next Tee Time
        if any(k in query for k in ["next", "tee time", "when do we play", "game", "schedule"]):
            next_tt = get_next_tee_time()
            if not next_tt:
                msg = (
                    "Aiyo machan, no upcoming tee times booked in the system! "
                    "Drop a screenshot from your club app and I'll create the WhatsApp event right now men! ⛳"
                )
            else:
                msg = (
                    f"🏌️ *NEXT TEE TIME DETAILS* 🏌️\n\n"
                    f"📍 *Course:* {next_tt['course_name']}\n"
                    f"📅 *Date:* {next_tt['date_str']}\n"
                    f"⏰ *Time:* {next_tt['start_time']}\n"
                    f"👥 *The Boys:* {next_tt['players'] or 'The Boys'}\n\n"
                    f"Jehan says: Look men, start practicing your putting on the carpet at home! Even my Amma chips better than this! 😂🏌️‍♂️"
                )
            self.send_text(client, chat_jid, msg)
            return

        # Command: Weather Check
        if "weather" in query or "rain" in query:
            next_tt = get_next_tee_time()
            if next_tt:
                report = self.weather.check_tee_time_weather(next_tt)
                if report:
                    warning = self.weather.format_caddy_weather_report(report, "On-Demand Check")
                    self.send_text(client, chat_jid, warning)
                    return
            # General fallback weather
            report = self.weather.check_tee_time_weather({
                "id": 0,
                "course_name": Config.DEFAULT_COURSE_NAME,
                "date_str": datetime.now().strftime("%Y-%m-%d"),
                "start_time": datetime.now().strftime("%H:%M"),
                "latitude": Config.DEFAULT_COURSE_LAT,
                "longitude": Config.DEFAULT_COURSE_LON
            })
            if report:
                self.send_text(client, chat_jid, self.weather.format_caddy_weather_report(report, "Today's Course Forecast"))
            else:
                self.send_text(client, chat_jid, "Sky looks clear enough machan, but it's Melbourne — keep a jacket in the car just in case! ☀️")
            return

        # Command: Sync 18Birdies rounds & check status
        if "sync status" in query or "status" in query:
            status_text = self.birdies.get_sync_status_summary()
            self.send_text(client, chat_jid, status_text)
            return

        if "sync" in query or "birdies" in query or "rounds" in query:
            self.send_text(client, chat_jid, "🔄 Checking 18Birdies for all the boys, hold on machan...")
            self.birdies.sync_all_players()
            new_rounds = self.birdies.scan_for_new_rounds()
            if not new_rounds:
                self.send_text(client, chat_jid, "All synced! No new un-roasted rounds found on 18Birdies right now men! 😂")
            else:
                for r in new_rounds:
                    self.send_text(client, chat_jid, r["summary_message"])
            return

        # Default: Persona Chat & Banter
        next_tt = get_next_tee_time()
        context = ""
        if next_tt:
            context = f"Next scheduled tee time: {next_tt['course_name']} on {next_tt['date_str']} at {next_tt['start_time']} with {next_tt['players']}."

        reply = self.gemini.chat_as_caddy(user_message=f"{sender_name}: {query or text}", context=context)
        self.send_text(client, chat_jid, reply)

    def send_text(self, client: Optional[NewClient], chat_jid: JID, text: str):
        c = client or self.client
        try:
            msg = Message(conversation=text)
            c.send_message(chat_jid, msg)
            log_activity("MESSAGE_SENT", "SUCCESS", text[:120])
        except Exception as e:
            logger.error(f"[WhatsApp] Failed to send message: {e}")
            log_activity("MESSAGE_SENT", "FAILURE", f"Failed sending to {chat_jid}: {e}")

    def broadcast_to_group(self, text: str):
        """
        Sends an alert or message to the active WhatsApp group from background tasks.
        """
        if self.active_group_jid:
            self.send_text(self.client, self.active_group_jid, text)
        else:
            logger.warning("[WhatsApp] Cannot broadcast: No active group JID recorded yet. Wait for a message in the group.")

    def start(self):
        logger.info("🚀 Starting WhatsApp Golf Bot Client...")
        init_db_if_needed()
        self.client.connect()

def init_db_if_needed():
    from src.db import init_db
    init_db()
