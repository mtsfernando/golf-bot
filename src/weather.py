import httpx
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List

from src.config import Config
from src.db import (
    get_upcoming_tee_times,
    has_weather_alert_been_sent,
    record_weather_alert,
    log_activity
)

class WeatherService:
    def __init__(self):
        self.rain_threshold = Config.WEATHER_RAIN_THRESHOLD_PERCENT
        self.default_lat = Config.DEFAULT_COURSE_LAT
        self.default_lon = Config.DEFAULT_COURSE_LON

    def get_forecast(self, lat: float, lon: float, past_days: int = 1) -> Optional[Dict[str, Any]]:
        """
        Fetches hourly forecast and recent past rain from Open-Meteo API.
        """
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m",
            "past_days": past_days,
            "timezone": "auto"
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.get(url, params=params)
                if res.status_code == 200:
                    return res.json()
                print(f"[Weather] Open-Meteo returned status {res.status_code}: {res.text}")
        except Exception as e:
            print(f"[Weather] Error fetching forecast: {e}")
        return None

    def check_tee_time_weather(self, tee_time: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Checks weather for a tee time (4-hour playing window) and evaluates:
        - Round precipitation in mm (< 1mm, 1-2mm, > 2mm)
        - Prior day rainfall (for wet / soggy course conditions)
        """
        lat = tee_time.get("latitude") or self.default_lat
        lon = tee_time.get("longitude") or self.default_lon

        forecast_data = self.get_forecast(lat, lon, past_days=1)
        if not forecast_data or "hourly" not in forecast_data:
            return None

        hourly = forecast_data["hourly"]
        times = hourly.get("time", [])
        precips = hourly.get("precipitation", [])
        rain_probs = hourly.get("precipitation_probability", [])
        temps = hourly.get("temperature_2m", [])
        winds = hourly.get("wind_speed_10m", [])

        date_str = tee_time["date_str"]
        start_hour_str = tee_time["start_time"][:2]

        # Calculate prior day date string (YYYY-MM-DD)
        try:
            round_dt = datetime.strptime(date_str, "%Y-%m-%d")
            prior_date_str = (round_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            prior_date_str = ""

        # 1. Calculate prior day rain mm
        prior_day_rain_mm = 0.0
        if prior_date_str:
            for idx, t in enumerate(times):
                if t.startswith(prior_date_str) and idx < len(precips):
                    prior_day_rain_mm += precips[idx] or 0.0

        # 2. Identify 4-hour playing window on round day
        window_indices = []
        for idx, t in enumerate(times):
            if t.startswith(date_str):
                hour = int(t.split("T")[1][:2])
                start_h = int(start_hour_str)
                if start_h <= hour <= start_h + 4:
                    window_indices.append(idx)

        if not window_indices:
            return None

        total_precip = sum([precips[i] for i in window_indices if i < len(precips)] or [0.0])
        max_rain_prob = max([rain_probs[i] for i in window_indices if i < len(rain_probs)] or [0])
        avg_temp = sum([temps[i] for i in window_indices if i < len(temps)]) / len(window_indices)
        max_wind = max([winds[i] for i in window_indices if i < len(winds)] or [0])

        total_precip_mm = round(total_precip, 1)
        prior_day_rain_mm = round(prior_day_rain_mm, 1)

        # Categorize precipitation level:
        # - CLEAR: < 1.0mm (Good weather)
        # - DRIZZLE: 1.0mm - 2.0mm (Light drizzle)
        # - WARNING: > 2.0mm (Heavy rain / weather warning)
        if total_precip_mm < 1.0:
            category = "CLEAR"
        elif total_precip_mm <= 2.0:
            category = "DRIZZLE"
        else:
            category = "WARNING"

        return {
            "tee_time_id": tee_time["id"],
            "course_name": tee_time["course_name"],
            "date_str": date_str,
            "start_time": tee_time["start_time"],
            "total_precip_mm": total_precip_mm,
            "prior_day_rain_mm": prior_day_rain_mm,
            "max_rain_prob": max_rain_prob,
            "temp_c": round(avg_temp, 1),
            "max_wind_kmh": round(max_wind, 1),
            "category": category,
            "prior_day_heavy_rain": prior_day_rain_mm >= 3.0
        }

    def format_caddy_weather_report(self, report: Dict[str, Any], window_label: str = "Tomorrow") -> str:
        """
        Creates a short, witty Sri Lankan caddy weather report tailored to precipitation mm
        and prior day course wetness.
        """
        precip = report["total_precip_mm"]
        prior_rain = report["prior_day_rain_mm"]
        course = report["course_name"]
        date = report["date_str"]
        time = report["start_time"]
        temp = report["temp_c"]
        category = report["category"]

        prior_context = ""
        if report["prior_day_heavy_rain"]:
            prior_context = f"\n⚠️ {prior_rain}mm fell yesterday — expect soft, plugged lies."

        # Case 1: Under 1mm -> Weather is Good
        if category == "CLEAR":
            return (
                f"☀️ *Weather ({window_label})* — {course}, {date} {time}\n"
                f"🌡️ {temp}°C | 💧 {precip}mm. Perfect conditions machan, no excuses today.{prior_context}"
            )

        # Case 2: 1-2mm -> Light Drizzle
        elif category == "DRIZZLE":
            return (
                f"🌦️ *Weather ({window_label})* — {course}, {date} {time}\n"
                f"🌡️ {temp}°C | 💧 ~{precip}mm drizzle. Bring a towel and a spare glove.{prior_context}"
            )

        # Case 3: Over 2mm -> Rain Warning
        else:
            return (
                f"🌧️ *Rain warning ({window_label})* — {course}, {date} {time}\n"
                f"💧 {precip}mm ({report['max_rain_prob']}% chance) | 💨 {report['max_wind_kmh']} km/h. Aiyo — waterproofs, or we call it?{prior_context}"
            )

    def check_and_generate_pending_alerts(self) -> List[Tuple[int, str, str]]:
        """
        Scans upcoming tee times:
        1. 24h before: ALWAYS sends an automated weather brief (Good weather <1mm, Drizzle 1-2mm, or Warning >2mm)
           with prior-day rain ground conditions.
        2. 2h before: Sends a short pre-round reminder if drizzle or rain (>1mm) is expected.
        """
        alerts_to_send = []
        upcoming = get_upcoming_tee_times(limit=5)
        now = datetime.now()

        for tt in upcoming:
            tee_id = tt["id"]
            try:
                tt_datetime = datetime.strptime(f"{tt['date_str']} {tt['start_time']}", "%Y-%m-%d %H:%M")
            except Exception:
                continue

            time_diff = tt_datetime - now
            hours_until = time_diff.total_seconds() / 3600.0

            # -----------------------------------------------------------------
            # 1. Automated Day-Before Check (18h to 26h before tee off)
            # -----------------------------------------------------------------
            if 18.0 <= hours_until <= 26.0:
                if not has_weather_alert_been_sent(tee_id, "24h"):
                    report = self.check_tee_time_weather(tt)
                    if report:
                        message = self.format_caddy_weather_report(report, window_label="Round Tomorrow")
                        record_weather_alert(tee_id, "24h", report["max_rain_prob"], report["total_precip_mm"])
                        log_activity("WEATHER_ALERT", "SUCCESS", f"24h brief for {report['course_name']} ({report['category']})")
                        alerts_to_send.append((tee_id, "24h", message))

            # -----------------------------------------------------------------
            # 2. 2-Hour Pre-Round Alert (1h to 3.5h before tee off)
            # -----------------------------------------------------------------
            elif 1.0 <= hours_until <= 3.5:
                if not has_weather_alert_been_sent(tee_id, "2h"):
                    report = self.check_tee_time_weather(tt)
                    # Only send 2h reminder if drizzle or rain is expected (>= 1.0mm)
                    if report and report["total_precip_mm"] >= 1.0:
                        message = self.format_caddy_weather_report(report, window_label="2-Hour Pre-Round")
                        record_weather_alert(tee_id, "2h", report["max_rain_prob"], report["total_precip_mm"])
                        log_activity("WEATHER_ALERT", "SUCCESS", f"2h pre-round warning for {report['course_name']} ({report['total_precip_mm']}mm)")
                        alerts_to_send.append((tee_id, "2h", message))

        return alerts_to_send
