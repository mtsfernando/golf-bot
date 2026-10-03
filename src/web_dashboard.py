import os
import json
import logging
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from typing import Dict, Any, Optional

from src.db import (
    get_bot_summary_metrics,
    get_activity_logs,
    get_all_player_sync_statuses,
    get_upcoming_tee_times
)

logger = logging.getLogger("Dashboard")

# Global state for bot connectivity status
BOT_STATUS = {
    "connected": False,
    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "phone": "Not Paired"
}

def set_bot_connected(connected: bool, phone: str = ""):
    BOT_STATUS["connected"] = connected
    BOT_STATUS["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if phone:
        BOT_STATUS["phone"] = phone

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Golf-Bot Monitor | Anura Kumara Console</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --green: #10b981;
      --red: #ef4444;
      --amber: #f59e0b;
      --blue: #3b82f6;
      --purple: #8b5cf6;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 24px; font-size: 14px; }
    .container { max-width: 1200px; margin: 0 auto; }
    header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
    h1 { font-size: 22px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
    .status-pill { padding: 6px 14px; border-radius: 9999px; font-size: 12px; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
    .status-online { background: rgba(16, 185, 129, 0.2); color: var(--green); border: 1px solid var(--green); }
    .status-offline { background: rgba(239, 68, 68, 0.2); color: var(--red); border: 1px solid var(--red); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 18px; }
    .card-title { color: var(--text-muted); font-size: 12px; font-weight: 500; text-transform: uppercase; margin-bottom: 8px; }
    .card-value { font-size: 26px; font-weight: 700; }
    .card-failures { color: var(--red); }
    .card-success { color: var(--green); }
    .section-title { font-size: 16px; font-weight: 600; margin: 24px 0 12px; display: flex; justify-content: space-between; align-items: center; }
    table { width: 100%; border-collapse: collapse; background: var(--card-bg); border-radius: 8px; overflow: hidden; border: 1px solid var(--border); margin-bottom: 24px; }
    th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--border); }
    th { background: rgba(0,0,0,0.2); color: var(--text-muted); font-size: 11px; text-transform: uppercase; }
    tr:last-child td { border-bottom: none; }
    .pill { display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
    .pill-success { background: rgba(16, 185, 129, 0.15); color: var(--green); }
    .pill-failure { background: rgba(239, 68, 68, 0.15); color: var(--red); }
    .pill-info { background: rgba(59, 130, 246, 0.15); color: var(--blue); }
    .pill-warning { background: rgba(245, 158, 11, 0.15); color: var(--amber); }
    .btn { background: var(--card-bg); border: 1px solid var(--border); color: var(--text); padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
    .btn:hover { background: var(--border); }
    .footer { text-align: center; color: var(--text-muted); font-size: 12px; margin-top: 32px; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>🏌️‍♂️ Golf-Bot Monitor <span style="font-size: 13px; color: var(--text-muted); font-weight: normal;">(Anura Kumara Console)</span></h1>
      <div style="display: flex; align-items: center; gap: 12px;">
        <span id="conn-badge" class="status-pill status-offline">● WhatsApp Connecting...</span>
        <button class="btn" onclick="fetchData()">🔄 Refresh</button>
      </div>
    </header>

    <!-- Metrics Counters -->
    <div class="grid">
      <div class="card">
        <div class="card-title">Messages Sent</div>
        <div id="m-sent" class="card-value card-success">0</div>
      </div>
      <div class="card">
        <div class="card-title">Messages Received</div>
        <div id="m-recv" class="card-value">0</div>
      </div>
      <div class="card">
        <div class="card-title">WhatsApp Events</div>
        <div id="m-events" class="card-value" style="color: var(--blue);">0</div>
      </div>
      <div class="card">
        <div class="card-title">18Birdies Syncs</div>
        <div id="m-syncs" class="card-value" style="color: var(--purple);">0</div>
      </div>
      <div class="card">
        <div class="card-title">Failures / Errors</div>
        <div id="m-fails" class="card-value card-failures">0</div>
      </div>
      <div class="card">
        <div class="card-title">Rounds Tracked</div>
        <div id="m-rounds" class="card-value">0</div>
      </div>
    </div>

    <!-- 18Birdies Sync Health -->
    <div class="section-title">
      <span>📊 18Birdies Friends Sync Status</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Player Name</th>
          <th>Last Pull Timestamp</th>
          <th>Status</th>
          <th>Latest Round Date</th>
          <th>Notes / Errors</th>
        </tr>
      </thead>
      <tbody id="sync-table-body">
        <tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No player sync records found yet.</td></tr>
      </tbody>
    </table>

    <!-- Upcoming Tee Times & Weather -->
    <div class="section-title">
      <span>⛳ Upcoming Scheduled Tee Times</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Course</th>
          <th>Date</th>
          <th>Time</th>
          <th>Players</th>
          <th>Booking Ref</th>
        </tr>
      </thead>
      <tbody id="tee-table-body">
        <tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No upcoming tee times booked.</td></tr>
      </tbody>
    </table>

    <!-- Activity & Failure Log -->
    <div class="section-title">
      <span>📜 Recent Activity & Failure Log</span>
      <div style="display: flex; gap: 8px;">
        <button class="btn" onclick="filterLogs('ALL')">All</button>
        <button class="btn" onclick="filterLogs('FAILURE')">Failures Only</button>
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th style="width: 170px;">Timestamp</th>
          <th style="width: 150px;">Event Type</th>
          <th style="width: 100px;">Status</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody id="logs-table-body">
        <tr><td colspan="4" style="text-align: center; color: var(--text-muted);">Loading logs...</td></tr>
      </tbody>
    </table>

    <div class="footer">
      Anura Kumara Bot Console • Auto-refreshing every 10s • Beaconhills Golf Club
    </div>
  </div>

  <script>
    let currentLogs = [];
    let activeFilter = 'ALL';

    async function fetchData() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();

        // Update connection status
        const badge = document.getElementById('conn-badge');
        if (data.bot_status.connected) {
          badge.className = 'status-pill status-online';
          badge.innerText = '🟢 WhatsApp Connected (' + (data.bot_status.phone || 'Active') + ')';
        } else {
          badge.className = 'status-pill status-offline';
          badge.innerText = '🔴 WhatsApp Disconnected / Pairing';
        }

        // Update counters
        document.getElementById('m-sent').innerText = data.metrics.messages_sent;
        document.getElementById('m-recv').innerText = data.metrics.messages_received;
        document.getElementById('m-events').innerText = data.metrics.events_created;
        document.getElementById('m-syncs').innerText = data.metrics.birdies_syncs;
        document.getElementById('m-fails').innerText = data.metrics.total_failures;
        document.getElementById('m-rounds').innerText = data.metrics.total_processed_rounds;

        // Render Sync Table
        const syncBody = document.getElementById('sync-table-body');
        if (data.sync_status && data.sync_status.length > 0) {
          syncBody.innerHTML = data.sync_status.map(s => `
            <tr>
              <td><strong>${escapeHtml(s.player_name)}</strong></td>
              <td>${s.last_successful_pull_at || 'Never'}</td>
              <td><span class="pill ${s.status === 'SUCCESS' ? 'pill-success' : 'pill-failure'}">${s.status}</span></td>
              <td>${s.latest_round_date || 'N/A'}</td>
              <td style="color: ${s.error_message ? 'var(--red)' : 'var(--text-muted)'};">${escapeHtml(s.error_message || 'OK')}</td>
            </tr>
          `).join('');
        } else {
          syncBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No player sync records found yet.</td></tr>';
        }

        // Render Tee Times Table
        const teeBody = document.getElementById('tee-table-body');
        if (data.upcoming_tee_times && data.upcoming_tee_times.length > 0) {
          teeBody.innerHTML = data.upcoming_tee_times.map(t => `
            <tr>
              <td><strong>${escapeHtml(t.course_name)}</strong></td>
              <td>${t.date_str}</td>
              <td>${t.start_time}</td>
              <td>${escapeHtml(t.players || 'The Boys')}</td>
              <td>${escapeHtml(t.booking_ref || '-')}</td>
            </tr>
          `).join('');
        } else {
          teeBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No upcoming tee times booked.</td></tr>';
        }

        // Store and Render Logs
        currentLogs = data.recent_logs || [];
        renderLogs();

      } catch (err) {
        console.error('Error fetching dashboard status:', err);
      }
    }

    function renderLogs() {
      const logBody = document.getElementById('logs-table-body');
      let filtered = currentLogs;
      if (activeFilter === 'FAILURE') {
        filtered = currentLogs.filter(l => l.status === 'FAILURE');
      }

      if (filtered.length > 0) {
        logBody.innerHTML = filtered.map(l => {
          let pillClass = 'pill-info';
          if (l.status === 'SUCCESS') pillClass = 'pill-success';
          if (l.status === 'FAILURE') pillClass = 'pill-failure';
          if (l.status === 'WARNING') pillClass = 'pill-warning';

          return `
            <tr>
              <td style="color: var(--text-muted); font-size: 12px;">${l.created_at}</td>
              <td><span class="pill pill-info">${escapeHtml(l.event_type)}</span></td>
              <td><span class="pill ${pillClass}">${l.status}</span></td>
              <td>${escapeHtml(l.details || '')}</td>
            </tr>
          `;
        }).join('');
      } else {
        logBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No activity logs matching filter.</td></tr>';
      }
    }

    function filterLogs(filter) {
      activeFilter = filter;
      renderLogs();
    }

    function escapeHtml(text) {
      if (!text) return '';
      return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    fetchData();
    setInterval(fetchData, 10000);
  </script>
</body>
</html>
"""

class DashboardRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
            return

        elif parsed.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            payload = {
                "bot_status": BOT_STATUS,
                "metrics": get_bot_summary_metrics(),
                "sync_status": get_all_player_sync_statuses(),
                "upcoming_tee_times": get_upcoming_tee_times(5),
                "recent_logs": get_activity_logs(50)
            }
            self.wfile.write(json.dumps(payload, default=str).encode("utf-8"))
            return

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def log_message(self, format, *args):
        # Suppress noisy HTTP request logging in terminal
        pass

def start_dashboard_server(port: int = 8080):
    """
    Starts the lightweight HTTP monitoring server in a background daemon thread.
    """
    def run_server():
        try:
            server = ThreadingHTTPServer(("0.0.0.0", port), DashboardRequestHandler)
            logger.info(f"🌐 [Dashboard] Monitoring dashboard online at http://0.0.0.0:{port}")
            server.serve_forever()
        except Exception as e:
            logger.error(f"[Dashboard] Error starting dashboard server: {e}")

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    return thread
