import os
import io
import time
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from neonize.client import NewClient
from neonize.events import MessageEv, ConnectedEv, PairStatusEv, JoinedGroupEv, GroupInfoEv
from neonize.utils import log
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message, EventMessage, MessageContextInfo, LocationMessage
from zoneinfo import ZoneInfo
try:
    from neonize.proto.Neonize_pb2 import JID
except ImportError:
    from neonize.proto.def_pb2 import JID

from src.config import Config
from src.db import (
    save_tee_time,
    get_next_tee_time,
    get_upcoming_tee_times,
    log_activity,
    record_group_joined,
    record_group_left,
    get_active_group_jids,
    upsert_whatsapp_group,
    save_group_message
)
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
        self.bot_phone: str = ""
        self._register_events()

    def sync_joined_groups(self):
        """
        Discovers and registers all WhatsApp groups the bot is currently in.
        Detects newly added groups as well as groups the bot was removed from.
        """
        try:
            joined = self.client.get_joined_groups()
            current_jids = set()
            logger.info(f"📋 [WhatsApp] Discovered {len(joined)} active joined WhatsApp group(s).")
            for g in joined:
                jid_str = f"{g.JID.User}@{g.JID.Server}"
                current_jids.add(jid_str)
                name = g.GroupName.Name if (g.GroupName and g.GroupName.Name) else g.JID.User
                count = len(g.Participants)
                record_group_joined(jid_str, name, count)
                logger.info(f"   👥 Group: '{name}' ({jid_str}, {count} members)")

            # Check if bot was removed from any previously active group
            active_db_jids = get_active_group_jids()
            for db_jid in active_db_jids:
                if db_jid not in current_jids:
                    logger.warning(f"👋 [WhatsApp] Bot is no longer in group {db_jid}. Marking as removed.")
                    record_group_left(db_jid)
        except Exception as e:
            logger.warning(f"Could not sync joined groups: {e}")

    def _register_events(self):
        @self.client.event(ConnectedEv)
        def on_connected(client: NewClient, event: ConnectedEv):
            logger.info("⛳ [WhatsApp] Golf Bot connected successfully to WhatsApp network!")
            phone = self.bot_phone
            try:
                me = client.get_me()
                if me and me.JID and me.JID.User:
                    phone = me.JID.User
                    self.bot_phone = phone
            except Exception:
                pass
            set_bot_connected(True, phone=phone)
            log_activity("CONNECTIVITY", "SUCCESS", f"WhatsApp bot connected successfully ({phone})")
            self.sync_joined_groups()

        @self.client.event(PairStatusEv)
        def on_pair_status(client: NewClient, event: PairStatusEv):
            logger.info(f"📱 [WhatsApp] Pairing Status: {event.ID.User}")
            self.bot_phone = event.ID.User
            set_bot_connected(True, phone=event.ID.User)
            log_activity("CONNECTIVITY", "INFO", f"WhatsApp device paired as {event.ID.User}")
            self.sync_joined_groups()

        @self.client.event(JoinedGroupEv)
        def on_joined_group(client: NewClient, event: JoinedGroupEv):
            try:
                g_info = event.GroupInfo
                jid_str = f"{g_info.JID.User}@{g_info.JID.Server}"
                name = g_info.GroupName.Name if (g_info.GroupName and g_info.GroupName.Name) else g_info.JID.User
                count = len(g_info.Participants) if g_info.Participants else 0
                logger.info(f"🎉 [WhatsApp] Bot was added to WhatsApp group: '{name}' ({jid_str}) with {count} members!")
                record_group_joined(jid_str, name, count)
            except Exception as e:
                logger.error(f"Error handling JoinedGroupEv: {e}")

        @self.client.event(GroupInfoEv)
        def on_group_info(client: NewClient, event: GroupInfoEv):
            try:
                group_jid_str = f"{event.JID.User}@{event.JID.Server}"
                group_name = event.Name.Name if (event.Name and event.Name.Name) else ""

                bot_phone = self.bot_phone
                if not bot_phone:
                    try:
                        me = client.get_me()
                        if me and me.JID and me.JID.User:
                            bot_phone = me.JID.User
                    except Exception:
                        pass

                # 1. Group was deleted
                if event.Delete:
                    logger.warning(f"🚫 [WhatsApp] Group '{group_name}' ({group_jid_str}) was deleted.")
                    record_group_left(group_jid_str, group_name)
                    return

                # 2. Check if our bot was removed or left
                bot_removed = False
                for l in event.Leave:
                    if (bot_phone and l.User == bot_phone) or (bot_phone and Config.phone_matches(l.User, bot_phone)):
                        bot_removed = True
                        break

                if bot_removed:
                    logger.warning(f"👋 [WhatsApp] Bot was removed from or left group: '{group_name}' ({group_jid_str})")
                    record_group_left(group_jid_str, group_name)
                    return

                # 3. Check if our bot joined
                bot_joined = False
                for j in event.Join:
                    if (bot_phone and j.User == bot_phone) or (bot_phone and Config.phone_matches(j.User, bot_phone)):
                        bot_joined = True
                        break

                if bot_joined:
                    logger.info(f"🎉 [WhatsApp] Bot joined group: '{group_name}' ({group_jid_str})")
                    record_group_joined(group_jid_str, group_name)
                    return

                # 4. Group metadata changed (name, etc.)
                if group_name:
                    record_group_joined(group_jid_str, group_name)

            except Exception as e:
                logger.error(f"Error handling GroupInfoEv: {e}")

        @self.client.event(MessageEv)
        def on_message(client: NewClient, event: MessageEv):
            self._handle_incoming_message(client, event)

    def _handle_incoming_message(self, client: NewClient, event: MessageEv):
        try:
            # Ignore messages sent by bot itself
            if event.Info.MessageSource.IsFromMe:
                return

            chat_jid = event.Info.MessageSource.Chat
            sender_push_name = (
                getattr(event.Info, "Pushname", None) or 
                getattr(event.Info, "PushName", None) or 
                getattr(event.Info, "VerifiedName", None) or 
                "Golfer"
            )
            sender_src = getattr(event.Info.MessageSource, "Sender", None)
            sender_alt = getattr(event.Info.MessageSource, "SenderAlt", None)

            is_group = chat_jid.Server == "g.us"
            group_jid_str = f"{chat_jid.User}@{chat_jid.Server}" if is_group else ""
            group_name = ""

            # Extract sender phone (handling WhatsApp LID addresses vs standard phone numbers)
            sender_phone = ""
            if sender_alt and getattr(sender_alt, "Server", "") != "lid" and getattr(sender_alt, "User", ""):
                sender_phone = sender_alt.User
            elif sender_src and getattr(sender_src, "Server", "") != "lid" and getattr(sender_src, "User", ""):
                sender_phone = sender_src.User
            elif sender_src and getattr(sender_src, "User", ""):
                sender_phone = sender_src.User
            elif not is_group and chat_jid and getattr(chat_jid, "User", ""):
                sender_phone = chat_jid.User

            sender_jid_str = f"{sender_src.User}@{sender_src.Server}" if sender_src else (f"{sender_phone}@s.whatsapp.net" if sender_phone else "")

            # If sender_push_name is still default 'Golfer', check if phone is configured in .env
            if sender_push_name == "Golfer" and sender_phone:
                p_match = Config.get_player_by_phone(sender_phone)
                if p_match:
                    sender_push_name = p_match.get("name", "Golfer")

            # Track group JID if target group matches or if active
            if is_group:
                self.active_group_jid = chat_jid
                try:
                    g_info = client.get_group_info(chat_jid)
                    if g_info and g_info.GroupName and g_info.GroupName.Name:
                        group_name = g_info.GroupName.Name
                        record_group_joined(group_jid_str, group_name, len(g_info.Participants))
                except Exception:
                    pass
                if not group_name:
                    group_name = "Golf Group"
                    record_group_joined(group_jid_str, group_name, 0)

            msg_pb = event.Message
            if not msg_pb:
                return

            # -------------------------------------------------------------
            # 1. Check for Image Message (Tee Time Booking Screenshot)
            # -------------------------------------------------------------
            if msg_pb.imageMessage and msg_pb.imageMessage.URL:
                logger.info(f"📸 Image received from {sender_push_name}. Checking for tee time booking screenshot...")
                if is_group:
                    save_group_message(
                        group_jid=group_jid_str,
                        group_name=group_name,
                        sender_name=sender_push_name,
                        sender_jid=sender_jid_str,
                        message_text="[📸 Tee Time Booking Screenshot]",
                        is_from_bot=False
                    )
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

            # Always save group message for live dashboard monitoring
            if is_group:
                save_group_message(
                    group_jid=group_jid_str,
                    group_name=group_name,
                    sender_name=sender_push_name,
                    sender_jid=sender_jid_str,
                    message_text=text,
                    is_from_bot=False
                )

            # Check if bot is mentioned or keyword is used
            trigger = Config.BOT_TRIGGER_KEYWORD.lower()
            text_lower = text.lower()
            is_triggered = (
                trigger in text_lower or
                text_lower.startswith("!caddy") or
                text_lower.startswith("!golf") or
                text_lower.startswith("!recap") or
                text_lower.startswith("!scorecard") or
                text_lower.startswith("!sync") or
                text_lower.startswith("!help")
            )

            # Direct messages to bot don't strictly require trigger keyword
            is_direct_chat = chat_jid.Server == "s.whatsapp.net"

            if not (is_triggered or is_direct_chat):
                return

            if not sender_phone and not is_group and chat_jid and getattr(chat_jid, "User", ""):
                sender_phone = chat_jid.User

            logger.info(f"💬 Command/Query received from {sender_push_name} ({sender_phone}): {text}")
            log_activity("MESSAGE_RECEIVED", "INFO", f"From {sender_push_name} ({sender_phone}): {text[:120]}")
            self._process_text_command(client, chat_jid, text, sender_push_name, sender_phone)

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            log_activity("ERROR", "FAILURE", f"Message handling error: {e}")

    @staticmethod
    def _normalize_player_names(players: List[str]) -> List[str]:
        """
        Converts club-app style "SURNAME, Firstname" to "Firstname Surname" and tidies casing.
        Only explicit "Surname, First" entries are flipped; names already in "First Last"
        order are left alone.
        """
        def tidy(s: str) -> str:
            # Title-case words that are ALL CAPS or all lower (keep mixed case like "McDonald" as-is)
            return " ".join(w.title() if (w.isupper() or w.islower()) else w for w in s.split())

        result = []
        for raw in players or []:
            name = re.sub(r"\s+", " ", str(raw)).strip(" ,")
            if not name:
                continue
            if name.count(",") == 1:
                surname, first = (p.strip() for p in name.split(","))
                if surname and first:
                    name = f"{first} {surname}"
            result.append(tidy(name))
        return result

    @staticmethod
    def _format_course_name(booking_data: Dict[str, Any]) -> str:
        """
        Beaconhills bookings -> "Beaconhills - <Layout> Course" (e.g. "Beaconhills - Cardinia Course").
        Other clubs -> "<Club> - <Layout>" if a layout is shown, else the club name.
        """
        club = (booking_data.get("club_name") or booking_data.get("course_name") or Config.DEFAULT_COURSE_NAME).strip()
        layout = (booking_data.get("course_layout") or "").strip()
        raw = f"{club} {booking_data.get('course_name') or ''} {layout} {booking_data.get('notes') or ''}".lower()

        if "beaconhills" in raw:
            if not layout:
                # Layout may be embedded in the course name (e.g. "Beaconhills Cardinia")
                for known in ("Cardinia", "Ranges"):
                    if known.lower() in raw:
                        layout = known
                        break
            if layout:
                layout = re.sub(r"\s*course$", "", layout, flags=re.IGNORECASE).strip().title()
                return f"Beaconhills - {layout} Course"
            return "Beaconhills"

        if layout and layout.lower() not in club.lower():
            return f"{club} - {layout}"
        return club

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

        course = self._format_course_name(booking_data)
        is_beaconhills = course.lower().startswith("beaconhills")
        date_str = booking_data.get("date") or datetime.now().strftime("%Y-%m-%d")
        start_time = booking_data.get("start_time") or "08:00"
        end_time = booking_data.get("end_time")
        players_list = self._normalize_player_names(booking_data.get("players") or []) or [sender_name]
        players_str = ", ".join(players_list)
        booking_ref = booking_data.get("booking_ref") or "App Booking"

        # Calculate timestamps for WhatsApp Event (tee times are local Melbourne time)
        tz = ZoneInfo(Config.TIMEZONE)
        try:
            start_dt = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except Exception:
            start_dt = (datetime.now(tz) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)

        end_dt = start_dt + timedelta(hours=4, minutes=30)  # only used for the DB record
        # WhatsApp EventMessage expects UNIX timestamps in SECONDS
        start_ts = int(start_dt.timestamp())

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

        # 1. Send native WhatsApp EventMessage (shows as an RSVP-able event in the group)
        event_sent = False
        try:
            # Only Beaconhills has known coordinates; other clubs get a name-only location
            location = (
                LocationMessage(
                    degreesLatitude=Config.DEFAULT_COURSE_LAT,
                    degreesLongitude=Config.DEFAULT_COURSE_LON,
                    name=course,
                    address=Config.DEFAULT_COURSE_ADDRESS
                ) if is_beaconhills else LocationMessage(name=course)
            )
            attendees = "\n".join(p.strip() for p in players_list if p and p.strip())
            event_msg = EventMessage(
                name=course,
                description=f"Tee: {start_time}" + (f"\n{attendees}" if attendees else ""),
                startTime=start_ts,  # no endTime on purpose
                location=location,
                extraGuestsAllowed=False,
                isCanceled=False,
            )
            # messageSecret is REQUIRED for events - without it WhatsApp silently drops the message
            msg = Message(
                eventMessage=event_msg,
                messageContextInfo=MessageContextInfo(messageSecret=os.urandom(32))
            )
            client.send_message(chat_jid, msg)
            event_sent = True
            logger.info(f"📅 Sent native WhatsApp event: {course} {start_dt.isoformat()}")
            log_activity("EVENT_CREATED", "SUCCESS", f"Native WhatsApp event: {course} ({date_str} {start_time})")
        except Exception as ev_err:
            logger.warning(f"Could not send native eventMessage (falling back to text card): {ev_err}")
            log_activity("EVENT_CREATED", "WARNING", f"Fallback to card: {ev_err}")

        # 2. Short confirmation from Caddy (full details only if the event failed)
        when = f"{start_dt.strftime('%a %d %b')}, {start_time}"
        if event_sent:
            confirmation_msg = f"Locked in, machan — {course}, {when}. Tap the event above to RSVP ⛳"
        else:
            confirmation_msg = (
                f"⛳ *New tee time*\n"
                f"📍 {course}\n"
                f"📅 {when}\n"
                f"👥 {players_str}\n"
                f"🔖 {booking_ref}"
            )
        self.send_text(client, chat_jid, confirmation_msg)

    @staticmethod
    def _help_text() -> str:
        t = Config.BOT_TRIGGER_KEYWORD
        return (
            f"⛳ *Caddy* — your golf group's assistant, machan.\n"
            f"I sort tee times, watch the weather and recap your 18Birdies rounds.\n\n"
            f"📸 Post a booking screenshot → I create the group event\n"
            f"📅 `{t} next` → next tee time\n"
            f"🌦️ `{t} weather` → forecast for the round\n"
            f"📋 `{t} recap` → your last round\n"
            f"🔄 `{t} sync` → pull latest 18Birdies rounds\n"
            f"💬 `{t}` + anything → just chat"
        )

    def _reply_weather(self, client: Optional[NewClient], chat_jid: JID):
        """Forecast for the next booked round, or today's course forecast if nothing is booked."""
        tz = ZoneInfo(Config.TIMEZONE)
        now = datetime.now(tz)
        next_tt = get_next_tee_time()

        if next_tt:
            report = self.weather.check_tee_time_weather(next_tt)
            if report:
                self.send_text(client, chat_jid, self.weather.format_caddy_weather_report(report, "Next round"))
                return
            # No forecast data for that day - most likely too far out
            try:
                days_out = (datetime.strptime(next_tt["date_str"], "%Y-%m-%d").date() - now.date()).days
            except Exception:
                days_out = None
            if days_out is not None and days_out > WeatherService.FORECAST_DAYS - 1:
                self.send_text(
                    client, chat_jid,
                    f"Next round is {next_tt['date_str']} — {days_out} days out, too far for a reliable forecast machan. "
                    f"I'll post one automatically the day before ⛳"
                )
            else:
                self.send_text(client, chat_jid, "Couldn't pull the forecast right now, machan. Try again in a bit.")
            return

        # Nothing booked: today's forecast at the home course from the current hour
        report = self.weather.check_tee_time_weather({
            "id": 0,
            "course_name": Config.DEFAULT_COURSE_NAME,
            "date_str": now.strftime("%Y-%m-%d"),
            "start_time": now.strftime("%H:%M"),
            "latitude": Config.DEFAULT_COURSE_LAT,
            "longitude": Config.DEFAULT_COURSE_LON
        })
        if report:
            self.send_text(client, chat_jid, self.weather.format_caddy_weather_report(report, "Today, no round booked"))
        else:
            self.send_text(client, chat_jid, "Couldn't pull the forecast right now, machan. Try again in a bit.")

    def _process_text_command(self, client: NewClient, chat_jid: JID, text: str, sender_name: str, sender_phone: str = ""):
        cleaned = text.lower()
        trigger = Config.BOT_TRIGGER_KEYWORD.lower()
        query = (
            cleaned.replace(trigger, "")
            .replace("!caddy", "")
            .replace("!golf", "")
            .replace("!recap", "recap")
            .replace("!scorecard", "recap")
            .replace("!sync", "sync")
            .replace("!help", "help")
            .strip(" ?!.")
        )

        # Command: Help / quick guide (exact match so "help me with my slice" still goes to chat)
        if query in ("help", "commands", "menu", "guide", "what can you do", "what do you do"):
            self.send_text(client, chat_jid, self._help_text())
            return

        # Command: Last Round Recap
        if any(k in query for k in ["recap", "last round", "scorecard", "how did i play", "how'd i play", "my round", "how was my round", "last game"]):
            configured_players = Config.get_players()
            target_player = None

            # 1. Did the user ask about a specific player? (e.g. "@caddy recap Kasun")
            for p in configured_players:
                p_name = p.get("name", "")
                first_name = p_name.split()[0].lower() if p_name else ""
                if (first_name and first_name in query) or (p_name.lower() in query):
                    target_player = p
                    break

            # 2. Match sender by phone number if not named in query
            if not target_player and sender_phone:
                target_player = Config.get_player_by_phone(sender_phone)

            # 3. Match sender by WhatsApp display push name
            if not target_player and sender_name:
                target_player = Config.get_player_by_name(sender_name)

            # 4. Fallback: if only 1 player is configured in .env, default to them
            if not target_player and len(configured_players) == 1:
                target_player = configured_players[0]

            # If still undetermined and multiple players exist, ask who to recap
            if not target_player and len(configured_players) > 1:
                names = ", ".join([p.get("name", "Golfer") for p in configured_players])
                self.send_text(
                    client,
                    chat_jid,
                    f"Aiyo machan, who's round are we recapping? Register your mobile number in .env or specify their name!\n"
                    f"Players configured: {names}\n"
                    f"Try: `@caddy recap {configured_players[0].get('name', 'Thilina').split()[0]}`"
                )
                return

            player_name = target_player.get("name") if target_player else sender_name
            logger.info(f"🏌️ Retrieving last round recap for '{player_name}' (sender: {sender_name}, phone: {sender_phone})...")

            round_data = self.birdies.get_player_last_round(player_name)

            # If not found yet, scan files in data/18birdies/
            if not round_data:
                self.birdies.scan_for_new_rounds()
                round_data = self.birdies.get_player_last_round(player_name)

            if not round_data:
                self.send_text(
                    client,
                    chat_jid,
                    f"Aiyo {player_name}! Looked through 18Birdies and didn't find any recorded rounds for you yet men! "
                    f"Did you shoot a 115 and hide the scorecard from Amma? 😂 "
                    f"Log your round on 18Birdies and run `@caddy sync`!"
                )
                return

            # Generate roast comment with Gemini
            try:
                roast = self.gemini.generate_round_summary(round_data)
            except Exception as e:
                logger.warning(f"Error generating round summary: {e}")
                roast = None

            recap_msg = self.birdies.format_round_recap(round_data, roast_comment=roast)
            self.send_text(client, chat_jid, recap_msg)
            return

        # Command: Weather Check - must run BEFORE "next tee time", since questions like
        # "is it going to rain for my next tee time?" contain both sets of keywords
        if re.search(r"\b(weather|rain|raining|rainy|forecast|wind|windy|wet|storm|sunny|temperature|cold|hot)\b", query):
            self._reply_weather(client, chat_jid)
            return

        # Command: Next Tee Time
        if any(k in query for k in ["next", "tee time", "when do we play", "game", "schedule"]):
            next_tt = get_next_tee_time()
            if not next_tt:
                msg = "No tee times booked yet, machan. Drop a booking screenshot here and I'll create the event ⛳"
            else:
                msg = (
                    f"⛳ *Next round*\n"
                    f"📍 {next_tt['course_name']}\n"
                    f"📅 {next_tt['date_str']}, {next_tt['start_time']}\n"
                    f"👥 {next_tt['players'] or 'The boys'}"
                )
            self.send_text(client, chat_jid, msg)
            return

        # Command: Sync 18Birdies rounds & check status
        if "sync status" in query or (query == "status"):
            status_text = self.birdies.get_sync_status_summary()
            self.send_text(client, chat_jid, status_text)
            return

        if any(k in query for k in ["sync", "birdies", "update rounds", "pull rounds", "refresh rounds"]):
            configured_players = Config.get_players()
            is_direct_chat = (chat_jid.Server == "s.whatsapp.net")

            # 1. Identify and authenticate the requester
            requester = None
            if sender_phone:
                requester = Config.get_player_by_phone(sender_phone)
            if not requester and sender_name:
                requester = Config.get_player_by_name(sender_name)

            # If sender has no phone configured in .env but is the sole account:
            # Only match if in 1-on-1 direct chat or if sender's push name matches configured player name
            if not requester and len(configured_players) == 1:
                single_p = configured_players[0]
                p_name_lower = single_p.get("name", "").strip().lower()
                s_name_lower = (sender_name or "").strip().lower()
                name_matches = bool(s_name_lower and (s_name_lower in p_name_lower or p_name_lower in s_name_lower))
                if is_direct_chat or name_matches:
                    requester = single_p

            # Check if requester is authenticated and has matching credentials in .env
            requester_has_creds = bool(requester and requester.get("birdies_email") and requester.get("birdies_password"))

            if not requester or not requester_has_creds:
                names = ", ".join([p.get("name", "Golfer") for p in configured_players if p.get("birdies_email")]) or "None"
                self.send_text(
                    client,
                    chat_jid,
                    f"⛔ *Sync Request Denied*\n\n"
                    f"Aiyo {sender_name or 'machan'}! Only users with matching 18Birdies credentials in the .env file can request an on-demand sync men!\n\n"
                    f"Your WhatsApp details ({sender_phone or sender_name or 'unrecognized'}) are not linked to an active 18Birdies account in .env.\n"
                    f"Configured accounts: {names}\n\n"
                    f"Ask the host to add your mobile number and credentials to .env to unlock on-demand sync! 🏌️‍♂️"
                )
                return

            # Check if user asked to sync everyone: "@caddy sync all" or "sync everyone"
            sync_all = "all" in query or "everyone" in query
            if sync_all:
                self.send_text(client, chat_jid, f"🔄 Verified request from *{requester.get('name')}*! Syncing 18Birdies accounts for all configured friends, hold on machan...")
                self.birdies.sync_all_players()
                new_rounds = self.birdies.scan_for_new_rounds()
                if not new_rounds:
                    self.send_text(client, chat_jid, "All accounts synced! No new un-roasted rounds found on 18Birdies right now men! 😂")
                else:
                    for r in new_rounds:
                        self.send_text(client, chat_jid, r["summary_message"])
                return

            # Target player resolution for on-demand sync
            target_player = None
            requested_name = query.replace("sync", "").replace("birdies", "").replace("update", "").replace("pull", "").replace("rounds", "").replace("refresh", "").strip()

            # 1. Did the user specify a player name in the command? (e.g. "@caddy sync Kasun", "@caddy sync Thilina")
            if requested_name:
                for p in configured_players:
                    p_name = p.get("name", "")
                    first_name = p_name.split()[0].lower() if p_name else ""
                    if (first_name and first_name in requested_name) or (p_name.lower() in requested_name):
                        target_player = p
                        break

                if not target_player:
                    names = ", ".join([p.get("name", "Golfer") for p in configured_players])
                    self.send_text(
                        client,
                        chat_jid,
                        f"Aiyo {requester.get('name', 'machan')}! Could not find any golfer named '{requested_name}' in the .env file men! 😂\n\n"
                        f"Configured accounts: {names}\n"
                        f"Try `@caddy sync` to sync your own account or `@caddy sync all`!"
                    )
                    return

            # 2. Default to syncing the requesting user's own account if no player was specified
            if not target_player:
                target_player = requester

            # Verify that target_player has matching 18Birdies credentials in .env
            p_name = target_player.get("name", "Golfer")
            email = target_player.get("birdies_email", "").strip()
            pwd = target_player.get("birdies_password", "").strip()

            if not email or not pwd:
                self.send_text(
                    client,
                    chat_jid,
                    f"Aiyo {p_name}! 18Birdies credentials are not set in the .env file men! "
                    f"I need your 18Birdies email and password to log in and download your scorecard archive! 😂"
                )
                return

            # Execute on-demand sync for this verified user
            self.send_text(
                client,
                chat_jid,
                f"🔄 Syncing 18Birdies on-demand for *{p_name}* ({email})... Logging into 18Birdies, hold on machan! 🏌️‍♂️"
            )

            result = self.birdies.sync_player(target_player)

            if result.get("new_rounds"):
                for r in result["new_rounds"]:
                    self.send_text(client, chat_jid, r["summary_message"])
            elif result.get("success"):
                last_rd = result.get("last_round")
                last_date = last_rd.get("round_date", "recent") if last_rd else "recently"
                course = last_rd.get("course_name", "the course") if last_rd else "the course"
                score = last_rd.get("total_score", "") if last_rd else ""
                score_str = f" ({score} strokes)" if score else ""
                self.send_text(
                    client,
                    chat_jid,
                    f"✅ *18BIRDIES SYNC COMPLETE: {p_name.upper()}* ⛳\n\n"
                    f"Latest scorecard archive successfully pulled and up to date!\n"
                    f"📅 Latest Round: {last_date} at {course}{score_str}\n\n"
                    f"Type `@caddy recap` anytime to view your full scorecard breakdown men! 🏌️‍♂️"
                )
            else:
                err_msg = result.get("error") or "Download timed out"
                if "did not match" in str(err_msg).lower():
                    err_user = "Your 18Birdies email and password did not match! Please check your password in the .env file."
                else:
                    err_user = f"18Birdies sync issue: {err_msg}."

                self.send_text(
                    client,
                    chat_jid,
                    f"⚠️ Aiyo {p_name}! 18Birdies on-demand sync failed: {err_user} 😂\n"
                    f"If you have exported your data manually, drop the JSON file in `data/18birdies/` and I'll read it right away!"
                )
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

            # Record bot's sent message in group for live dashboard monitoring
            if chat_jid.Server == "g.us":
                group_jid_str = f"{chat_jid.User}@{chat_jid.Server}"
                save_group_message(
                    group_jid=group_jid_str,
                    group_name="",
                    sender_name=Config.BOT_NAME,
                    sender_jid=self.bot_phone or "bot",
                    message_text=text,
                    is_from_bot=True
                )
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
