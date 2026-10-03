import sqlite3
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_PATH = os.getenv("GOLFBOT_DB_PATH", "data/golfbot.db")

def get_db_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Tee times table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tee_times (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_name TEXT NOT NULL,
        date_str TEXT NOT NULL,       -- YYYY-MM-DD
        start_time TEXT NOT NULL,     -- HH:MM
        end_time TEXT,                -- HH:MM
        players TEXT,                 -- comma-separated or JSON list
        booking_ref TEXT,
        latitude REAL,
        longitude REAL,
        raw_details TEXT,
        whatsapp_event_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Weather alerts sent (to prevent duplicate spam)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS weather_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tee_time_id INTEGER NOT NULL,
        alert_window TEXT NOT NULL,   -- e.g. "24h" or "2h"
        rain_prob INTEGER,
        precipitation REAL,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tee_time_id) REFERENCES tee_times (id),
        UNIQUE(tee_time_id, alert_window)
    );
    """)

    # 3. 18Birdies Sync Status (Tracks last pull & sync health per player)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS player_sync_status (
        player_name TEXT PRIMARY KEY,
        email TEXT,
        last_attempt_at TIMESTAMP,
        last_successful_pull_at TIMESTAMP,
        status TEXT,                  -- 'SUCCESS', 'FAILED', 'IN_PROGRESS'
        rounds_count INTEGER DEFAULT 0,
        latest_round_id TEXT,
        latest_round_date TEXT,
        error_message TEXT
    );
    """)

    # 4. Processed 18Birdies rounds (Tracks every round to avoid duplicate roasts)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS processed_rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_name TEXT NOT NULL,
        round_date TEXT NOT NULL,
        course_name TEXT NOT NULL,
        total_score INTEGER,
        score_to_par INTEGER,
        external_round_id TEXT UNIQUE,
        raw_stats_json TEXT,
        summary_posted INTEGER DEFAULT 0,
        posted_at TIMESTAMP
    );
    """)

    # 5. Activity Logs (For lightweight monitoring of messages, sync, OCR, errors)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,      -- 'MESSAGE_SENT', 'MESSAGE_RECEIVED', 'TEE_TIME_OCR', 'EVENT_CREATED', 'WEATHER_ALERT', 'BIRDIES_SYNC', 'CONNECTIVITY', 'ERROR'
        status TEXT NOT NULL,          -- 'SUCCESS', 'FAILURE', 'INFO', 'WARNING'
        details TEXT,                  -- Human-readable message or JSON payload
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_event_type ON activity_logs (event_type);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_status ON activity_logs (status);")
    # 6. WhatsApp Groups joined by the bot
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS whatsapp_groups (
        group_jid TEXT PRIMARY KEY,
        group_name TEXT NOT NULL,
        participant_count INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        left_at TIMESTAMP,
        last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Column migrations if table existed previously without them
    for col, col_def in [
        ("is_active", "INTEGER DEFAULT 1"),
        ("joined_at", "TIMESTAMP"),
        ("left_at", "TIMESTAMP")
    ]:
        try:
            cursor.execute(f"ALTER TABLE whatsapp_groups ADD COLUMN {col} {col_def};")
        except Exception:
            pass

    # 7. Group messages (for live monitoring / audit in dashboard)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS group_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_jid TEXT NOT NULL,
        group_name TEXT,
        sender_name TEXT NOT NULL,
        sender_jid TEXT,
        message_text TEXT NOT NULL,
        is_from_bot INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_group_messages_jid ON group_messages (group_jid);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_group_messages_created ON group_messages (created_at);")

    conn.commit()
    conn.close()

# ----------------- Tee Time Queries -----------------

def save_tee_time(
    course_name: str,
    date_str: str,
    start_time: str,
    end_time: Optional[str] = None,
    players: Optional[str] = None,
    booking_ref: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    raw_details: Optional[str] = None
) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO tee_times (course_name, date_str, start_time, end_time, players, booking_ref, latitude, longitude, raw_details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (course_name, date_str, start_time, end_time, players, booking_ref, latitude, longitude, raw_details))
    tee_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return tee_id

def get_next_tee_time() -> Optional[Dict[str, Any]]:
    today = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM tee_times
        WHERE date_str > ? OR (date_str = ? AND start_time >= ?)
        ORDER BY date_str ASC, start_time ASC
        LIMIT 1
    """, (today, today, now_time))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_upcoming_tee_times(limit: int = 5) -> List[Dict[str, Any]]:
    today = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM tee_times
        WHERE date_str > ? OR (date_str = ? AND start_time >= ?)
        ORDER BY date_str ASC, start_time ASC
        LIMIT ?
    """, (today, today, now_time, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ----------------- Weather Alert Tracking -----------------

def has_weather_alert_been_sent(tee_time_id: int, alert_window: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM weather_alerts
        WHERE tee_time_id = ? AND alert_window = ?
    """, (tee_time_id, alert_window))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def record_weather_alert(tee_time_id: int, alert_window: str, rain_prob: int, precipitation: float):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO weather_alerts (tee_time_id, alert_window, rain_prob, precipitation)
        VALUES (?, ?, ?, ?)
    """, (tee_time_id, alert_window, rain_prob, precipitation))
    conn.commit()
    conn.close()

# ----------------- 18Birdies Sync Tracking -----------------

def update_player_sync_status(
    player_name: str,
    email: str,
    success: bool,
    rounds_count: int = 0,
    latest_round_id: Optional[str] = None,
    latest_round_date: Optional[str] = None,
    error_message: Optional[str] = None
):
    """
    Updates the sync status for a friend after a pull attempt from 18Birdies.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("SELECT last_successful_pull_at FROM player_sync_status WHERE player_name = ?", (player_name,))
    existing = cursor.fetchone()
    last_success = existing["last_successful_pull_at"] if existing else None

    if success:
        last_success = now_str
        status = "SUCCESS"
    else:
        status = "FAILED"

    cursor.execute("""
        INSERT INTO player_sync_status (
            player_name, email, last_attempt_at, last_successful_pull_at, 
            status, rounds_count, latest_round_id, latest_round_date, error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_name) DO UPDATE SET
            email=excluded.email,
            last_attempt_at=excluded.last_attempt_at,
            last_successful_pull_at=COALESCE(excluded.last_successful_pull_at, player_sync_status.last_successful_pull_at),
            status=excluded.status,
            rounds_count=MAX(player_sync_status.rounds_count, excluded.rounds_count),
            latest_round_id=COALESCE(excluded.latest_round_id, player_sync_status.latest_round_id),
            latest_round_date=COALESCE(excluded.latest_round_date, player_sync_status.latest_round_date),
            error_message=excluded.error_message
    """, (
        player_name, email, now_str, last_success, status,
        rounds_count, latest_round_id, latest_round_date, error_message
    ))
    conn.commit()
    conn.close()

def get_all_player_sync_statuses() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM player_sync_status ORDER BY player_name ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_player_sync_status(player_name: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM player_sync_status WHERE player_name = ?", (player_name,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

# ----------------- 18Birdies Round Tracking -----------------

def is_round_processed(external_round_id: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed_rounds WHERE external_round_id = ?", (external_round_id,))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def record_processed_round(
    player_name: str,
    round_date: str,
    course_name: str,
    total_score: int,
    score_to_par: int,
    external_round_id: str,
    raw_stats_json: str,
    summary_posted: bool = True
):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO processed_rounds 
        (player_name, round_date, course_name, total_score, score_to_par, external_round_id, raw_stats_json, summary_posted, posted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (player_name, round_date, course_name, total_score, score_to_par, external_round_id, raw_stats_json, 1 if summary_posted else 0))
    conn.commit()
    conn.close()

def get_latest_rounds(limit: int = 5) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM processed_rounds
        ORDER BY round_date DESC, posted_at DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_last_round_for_player(player_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Retrieves the most recent round for a player (matching exact name or substring),
    or the latest round overall if no player is specified.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    if player_name:
        cursor.execute("""
            SELECT * FROM processed_rounds
            WHERE LOWER(player_name) = LOWER(?) OR LOWER(player_name) LIKE LOWER(?)
            ORDER BY round_date DESC, posted_at DESC, id DESC
            LIMIT 1
        """, (player_name.strip(), f"%{player_name.strip()}%"))
    else:
        cursor.execute("""
            SELECT * FROM processed_rounds
            ORDER BY round_date DESC, posted_at DESC, id DESC
            LIMIT 1
        """)
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

# ----------------- Activity Logging & Monitoring -----------------

def log_activity(event_type: str, status: str, details: str):
    """
    Logs an activity or error into the database for the monitoring dashboard.
    event_type: 'MESSAGE_SENT', 'MESSAGE_RECEIVED', 'TEE_TIME_OCR', 'EVENT_CREATED', 
                'WEATHER_ALERT', 'BIRDIES_SYNC', 'CONNECTIVITY', 'ERROR'
    status: 'SUCCESS', 'FAILURE', 'INFO', 'WARNING'
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO activity_logs (event_type, status, details)
            VALUES (?, ?, ?)
        """, (event_type, status, str(details)[:1000]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] Error logging activity: {e}")

def get_activity_logs(limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Returns the latest activity logs, optionally filtered by status.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    if status:
        cursor.execute("""
            SELECT * FROM activity_logs
            WHERE status = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """, (status, limit))
    else:
        cursor.execute("""
            SELECT * FROM activity_logs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_bot_summary_metrics() -> Dict[str, Any]:
    """
    Computes key performance metrics and counts for the monitoring dashboard.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Event counts
    cursor.execute("SELECT event_type, status, COUNT(*) as cnt FROM activity_logs GROUP BY event_type, status")
    rows = cursor.fetchall()

    metrics = {
        "messages_sent": 0,
        "messages_received": 0,
        "events_created": 0,
        "weather_alerts": 0,
        "birdies_syncs": 0,
        "total_failures": 0,
        "total_tee_times": 0,
        "total_processed_rounds": 0
    }

    for r in rows:
        etype = r["event_type"]
        stat = r["status"]
        cnt = r["cnt"]

        if stat == "FAILURE":
            metrics["total_failures"] += cnt

        if etype == "MESSAGE_SENT" and stat == "SUCCESS":
            metrics["messages_sent"] += cnt
        elif etype == "MESSAGE_RECEIVED":
            metrics["messages_received"] += cnt
        elif etype == "EVENT_CREATED" and stat == "SUCCESS":
            metrics["events_created"] += cnt
        elif etype == "WEATHER_ALERT" and stat == "SUCCESS":
            metrics["weather_alerts"] += cnt
        elif etype == "BIRDIES_SYNC":
            metrics["birdies_syncs"] += cnt

    # Total counts in primary tables
    cursor.execute("SELECT COUNT(*) FROM tee_times")
    metrics["total_tee_times"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM processed_rounds")
    metrics["total_processed_rounds"] = cursor.fetchone()[0]

    conn.close()
    return metrics

# ----------------- WhatsApp Groups & Group Messages -----------------

def record_group_joined(group_jid: str, group_name: str, participant_count: int = 0) -> bool:
    """
    Records or updates a WhatsApp group that the bot joined or is currently in.
    Logs an activity event if this is a newly joined group or if the bot was previously marked as left.
    Returns True if newly joined/reactivated, False if already active.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT group_name, is_active, joined_at FROM whatsapp_groups WHERE group_jid = ?", (group_jid,))
    row = cursor.fetchone()

    was_inactive_or_new = (row is None) or (row["is_active"] == 0) or (row["joined_at"] is None)
    effective_name = group_name or (row["group_name"] if row else "") or "Golf Group"

    cursor.execute("""
    INSERT INTO whatsapp_groups (group_jid, group_name, participant_count, is_active, joined_at, left_at, last_message_at, updated_at)
    VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT(group_jid) DO UPDATE SET
        group_name = CASE WHEN excluded.group_name != '' THEN excluded.group_name ELSE whatsapp_groups.group_name END,
        participant_count = CASE WHEN excluded.participant_count > 0 THEN excluded.participant_count ELSE whatsapp_groups.participant_count END,
        is_active = 1,
        joined_at = COALESCE(whatsapp_groups.joined_at, CURRENT_TIMESTAMP),
        left_at = NULL,
        updated_at = CURRENT_TIMESTAMP;
    """, (group_jid, effective_name, participant_count))
    conn.commit()
    conn.close()

    if was_inactive_or_new:
        count_str = f" with {participant_count} members" if participant_count > 0 else ""
        log_activity("GROUP_JOINED", "SUCCESS", f"Bot added to WhatsApp group '{effective_name}' ({group_jid}){count_str}")
        return True
    return False

def record_group_left(group_jid: str, group_name: Optional[str] = None):
    """
    Marks a WhatsApp group as left / removed and logs the activity event.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT group_name, is_active FROM whatsapp_groups WHERE group_jid = ?", (group_jid,))
    row = cursor.fetchone()

    name = group_name or (row["group_name"] if row else "") or "Golf Group"
    was_active = row is not None and row["is_active"] == 1

    cursor.execute("""
    UPDATE whatsapp_groups
    SET is_active = 0, left_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
    WHERE group_jid = ?;
    """, (group_jid,))
    conn.commit()
    conn.close()

    if was_active or row is None:
        log_activity("GROUP_LEFT", "WARNING", f"Bot removed from or left WhatsApp group '{name}' ({group_jid})")

def get_active_group_jids() -> List[str]:
    """
    Returns list of JIDs for groups currently marked as active in the database.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT group_jid FROM whatsapp_groups WHERE is_active = 1")
    rows = cursor.fetchall()
    conn.close()
    return [r["group_jid"] for r in rows]

def upsert_whatsapp_group(group_jid: str, group_name: str, participant_count: int = 0):
    """
    Inserts or updates a joined WhatsApp group record in the database.
    """
    record_group_joined(group_jid, group_name, participant_count)

def save_group_message(
    group_jid: str,
    group_name: str,
    sender_name: str,
    sender_jid: str,
    message_text: str,
    is_from_bot: bool = False
):
    """
    Stores an incoming or outgoing group message for live dashboard monitoring.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO group_messages (group_jid, group_name, sender_name, sender_jid, message_text, is_from_bot)
    VALUES (?, ?, ?, ?, ?, ?);
    """, (group_jid, group_name, sender_name, sender_jid, message_text, 1 if is_from_bot else 0))

    # Ensure group exists and update last_message_at
    cursor.execute("""
    INSERT INTO whatsapp_groups (group_jid, group_name, participant_count, is_active, last_message_at, updated_at)
    VALUES (?, ?, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT(group_jid) DO UPDATE SET
        last_message_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP;
    """, (group_jid, group_name or "Golf Group"))

    conn.commit()
    conn.close()

def get_whatsapp_groups_with_recent_messages(messages_limit: int = 5) -> List[Dict[str, Any]]:
    """
    Returns all WhatsApp groups (active first, then left/removed) with their last N messages.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT group_jid, group_name, participant_count, is_active, joined_at, left_at, last_message_at, updated_at
    FROM whatsapp_groups
    ORDER BY is_active DESC, last_message_at DESC;
    """)
    groups = [dict(row) for row in cursor.fetchall()]

    for g in groups:
        cursor.execute("""
        SELECT id, sender_name, sender_jid, message_text, is_from_bot, created_at
        FROM group_messages
        WHERE group_jid = ?
        ORDER BY id DESC
        LIMIT ?;
        """, (g["group_jid"], messages_limit))
        # Reverse to show in chronological order
        msgs = [dict(r) for r in cursor.fetchall()]
        msgs.reverse()
        g["recent_messages"] = msgs

    conn.close()
    return groups

def get_recent_group_messages(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Returns the latest group messages across all groups.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT id, group_jid, group_name, sender_name, sender_jid, message_text, is_from_bot, created_at
    FROM group_messages
    ORDER BY id DESC
    LIMIT ?;
    """, (limit,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows
