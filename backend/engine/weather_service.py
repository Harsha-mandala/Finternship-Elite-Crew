"""
weather_service.py — Hotel Aditya Grand
========================================
Fetches daily weather forecast via OpenWeatherMap 3-hourly forecast API
and caches it in the SQLite database.

Location is always read dynamically from config.json (set by user in Settings page).
Slot filtering: only hours 09, 12, 15, 18, 21 (restaurant working hours).
feels_like: taken from the slot closest to 15:00.
"""

import os
import json
import sqlite3
import random
import requests
import sys
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.append(_BACKEND)
from database import get_db_connection
from datetime import date, datetime, timedelta
from typing import Optional

# ── Path constants ─────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, '..', 'config.json')
DB_PATH = os.environ.get(
    'DB_PATH',
    os.path.join(_HERE, '..', 'hotel_aditya.db')   # _HERE = backend/engine/ → .. = backend/
)

UNITS = 'metric'

# ── Restaurant working-hour slots (3-hourly) ───────────────────────────────────
WORK_HOURS = {'09', '12', '15', '18', '21'}

# ── Condition normalisation map ────────────────────────────────────────────────
CONDITION_MAP = {
    'Clear':        'Sunny',
    'Clouds':       'Cloudy',
    'Rain':         'Rainy',
    'Drizzle':      'Drizzle',
    'Thunderstorm': 'Thunderstorm',
    'Mist':         'Misty',
    'Haze':         'Hazy',
    'Fog':          'Foggy',
    'Snow':         'Snow',
    'Squall':       'Squall',
    'Tornado':      'Tornado',
    'Smoke':        'Smoky',
    'Dust':         'Dusty',
    'Sand':         'Dusty',
    'Ash':          'Volcanic Ash',
}


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(updates: dict) -> None:
    cfg = _load_config()
    cfg.update(updates)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)


def _read_api_key() -> str:
    """Read API key from environment variable only. config.json must not store keys."""
    return (os.getenv("WEATHER_API_KEY") or os.environ.get('OPENWEATHER_API_KEY', '')).strip()


def _get_coordinates() -> tuple:
    """
    Always read coordinates from config.json at call time so location changes
    in Settings are immediately reflected without restarting the server.
    Falls back to Kandukur, AP defaults if not set.
    """
    cfg = _load_config()
    lat = cfg.get('latitude')
    lon = cfg.get('longitude')
    # Validate — if None or clearly wrong, use Kandukur defaults
    if lat is None or lon is None:
        lat, lon = 15.2131, 79.9042
    return float(lat), float(lon)


# Module-level mutable so update_api_key() can patch it at runtime.
API_KEY: str = _read_api_key()


def update_api_key(new_key: str) -> None:
    """Update the in-memory API key. Keys are never written to config.json."""
    global API_KEY
    # Update the non-secret weather_source flag in config if needed
    try:
        cfg = _load_config()
        cfg['weather_source'] = 'real' if new_key.strip() else 'mock'
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f'[weather_service] Could not update config.json: {e}')
    API_KEY = new_key.strip()
    print(f'[weather_service] API key updated ({"set" if API_KEY else "cleared"}).')


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _get_conn():
    return get_db_connection()


def _ensure_table() -> None:
    """Create weather_data table if it doesn't exist."""
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weather_data (
                date        TEXT PRIMARY KEY,
                max_temp    REAL,
                min_temp    REAL,
                condition   TEXT,
                rainfall_mm REAL
            )
        """)


# Ensure table exists at import time (safe, idempotent).
try:
    _ensure_table()
except Exception as _e:
    print(f'[weather_service] Warning: could not ensure weather_data table: {_e}')


# ── Condition normalisation ────────────────────────────────────────────────────

def _normalize_condition(raw: str) -> str:
    return CONDITION_MAP.get(raw, raw)


# ── Slot filtering ─────────────────────────────────────────────────────────────

def _filter_restaurant_slots(all_slots: list) -> list:
    """
    Keep only 3-hourly forecast slots whose time falls in WORK_HOURS
    (09, 12, 15, 18, 21) to reflect actual restaurant operating hours.
    """
    filtered = []
    for slot in all_slots:
        dt_txt = slot.get('dt_txt', '')        # e.g. '2026-05-16 12:00:00'
        if len(dt_txt) >= 13:
            hour = dt_txt[11:13]               # '09', '12', etc.
            if hour in WORK_HOURS:
                filtered.append(slot)
    return filtered


def _slot_closest_to_15(slots: list) -> Optional[dict]:
    """Return the slot whose time is closest to 15:00."""
    if not slots:
        return None
    def _dist(slot):
        dt_txt = slot.get('dt_txt', '00:00:00')
        try:
            hour = int(dt_txt[11:13])
        except (ValueError, IndexError):
            hour = 0
        return abs(hour - 15)
    return min(slots, key=_dist)


# ── Mock weather ───────────────────────────────────────────────────────────────

def _mock_weather(target_date: str) -> dict:
    """
    Generate realistic mock weather for a South Indian location (pre-monsoon / monsoon):
    - Very hot: 37-42°C max, 27-31°C min
    - ~25% chance of rain
    """
    # Seed by date so repeated calls for the same date return the same values.
    seed = sum(ord(c) for c in target_date)
    rng = random.Random(seed)

    is_rainy = rng.random() < 0.25
    is_cloudy = not is_rainy and rng.random() < 0.20

    max_temp = round(rng.uniform(37.0, 42.0), 1)
    min_temp = round(rng.uniform(27.0, 31.0), 1)

    if is_rainy:
        condition = 'Rainy'
        rainfall_mm = round(rng.uniform(5.0, 30.0), 1)
    elif is_cloudy:
        condition = 'Cloudy'
        rainfall_mm = 0.0
        max_temp = round(rng.uniform(34.0, 39.0), 1)   # slightly cooler when cloudy
    else:
        condition = 'Sunny'
        rainfall_mm = 0.0

    return {
        'date':        target_date,
        'max_temp':    max_temp,
        'min_temp':    min_temp,
        'condition':   condition,
        'rainfall_mm': rainfall_mm,
        'feels_like':  round(max_temp + rng.uniform(2.0, 5.0), 1),  # humidity factor
    }


# ── Store to DB ────────────────────────────────────────────────────────────────

def _store_weather(weather: dict) -> None:
    """Insert or replace a weather record in weather_data table."""
    with get_db_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO weather_data
                (date, max_temp, min_temp, condition, rainfall_mm)
            VALUES (:date, :max_temp, :min_temp, :condition, :rainfall_mm)
        """, {
            'date':        weather['date'],
            'max_temp':    weather.get('max_temp'),
            'min_temp':    weather.get('min_temp'),
            'condition':   weather.get('condition'),
            'rainfall_mm': weather.get('rainfall_mm', 0.0),
        })


# ── Public API ─────────────────────────────────────────────────────────────────

def get_weather_for_date(target_date: str) -> Optional[dict]:
    """
    Return cached weather from DB for target_date, or None if not found.
    """
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                'SELECT * FROM weather_data WHERE date = ?', (target_date,)
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        print(f'[weather_service] get_weather_for_date error: {e}')
        return None


def clear_weather_cache(days_ahead: int = 10) -> int:
    """
    Delete cached weather records for today and the next N days so they are
    re-fetched from the API with the current (possibly updated) coordinates.
    Returns the number of rows deleted.
    """
    try:
        with get_db_connection() as conn:
            cutoff = date.today().isoformat()
            future = (date.today() + timedelta(days=days_ahead)).isoformat()
            result = conn.execute(
                "DELETE FROM weather_data WHERE date >= ? AND date <= ?",
                (cutoff, future)
            )
            deleted = result.rowcount
        print(f'[weather_service] Cleared {deleted} cached weather rows (today → +{days_ahead} days)')
        return deleted
    except Exception as e:
        print(f'[weather_service] clear_weather_cache error: {e}')
        return 0


def fetch_and_store_weather(target_date: str) -> dict:
    """
    Fetch weather for target_date from OpenWeatherMap (if API key set),
    or generate mock data. Reads coordinates fresh from config.json each call.
    Stores result in DB and returns the dict.

    Args:
        target_date: 'YYYY-MM-DD'

    Returns:
        Weather dict with keys: date, max_temp, min_temp, condition,
        rainfall_mm, feels_like (where available).
    """
    # Always refresh API key and coordinates from config
    key = _read_api_key()
    lat, lon = _get_coordinates()

    if not key:
        print(f'[weather_service] No API key — using mock weather for {target_date}.')
        record = _mock_weather(target_date)
        _store_weather(record)
        return record

    try:
        url = 'https://api.openweathermap.org/data/2.5/forecast'
        params = {
            'lat':   lat,
            'lon':   lon,
            'appid': key,
            'units': UNITS,
            'cnt':   40,   # up to 5 days × 8 slots
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        # --- Filter to slots that match target_date + restaurant hours ---
        all_slots: list = data.get('list', [])
        date_slots = [s for s in all_slots if s.get('dt_txt', '').startswith(target_date)]
        work_slots = _filter_restaurant_slots(date_slots)

        if not work_slots:
            # If no slots found for exact date (e.g. today itself), use first available
            work_slots = _filter_restaurant_slots(all_slots[:16])  # ~2 days
        if not work_slots:
            raise ValueError('No matching forecast slots found')

        temps      = [s['main']['temp']        for s in work_slots]
        conditions = [s['weather'][0]['main']   for s in work_slots]
        rainfall   = sum(s.get('rain', {}).get('3h', 0) for s in work_slots)

        # feels_like from slot closest to 15:00
        best_slot  = _slot_closest_to_15(work_slots)
        feels_like = best_slot['main'].get('feels_like') if best_slot else None

        most_common_condition = max(set(conditions), key=conditions.count)
        record = {
            'date':        target_date,
            'max_temp':    round(max(temps), 1),
            'min_temp':    round(min(temps), 1),
            'condition':   _normalize_condition(most_common_condition),
            'rainfall_mm': round(rainfall, 2),
            'feels_like':  round(feels_like, 1) if feels_like is not None else None,
        }

        _store_weather(record)
        print(f'[weather_service] Fetched real weather for {target_date}: '
              f'{record["condition"]}, {record["max_temp"]}°C '
              f'(lat={lat:.4f}, lon={lon:.4f})')
        return record

    except Exception as e:
        print(f'[weather_service] API fetch failed ({e}), falling back to mock.')
        record = _mock_weather(target_date)
        _store_weather(record)
        return record


def get_or_fetch_weather(target_date: str) -> dict:
    """
    Return cached weather if available, otherwise fetch (real or mock).
    This is the recommended convenience function for other modules.
    """
    cached = get_weather_for_date(target_date)
    if cached:
        return cached
    return fetch_and_store_weather(target_date)


def refresh_weather_for_location() -> dict:
    """
    Clear current weather cache and re-fetch today + tomorrow using the
    current coordinates from config.json.  Called when the user updates
    their location in Settings.
    Returns a summary dict.
    """
    deleted = clear_weather_cache(days_ahead=10)
    today    = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    today_w    = fetch_and_store_weather(today)
    tomorrow_w = fetch_and_store_weather(tomorrow)

    lat, lon = _get_coordinates()
    print(f'[weather_service] Weather refreshed for lat={lat:.4f}, lon={lon:.4f}')
    return {
        'cleared_rows': deleted,
        'today':    today_w,
        'tomorrow': tomorrow_w,
        'lat':      lat,
        'lon':      lon,
    }


def get_all_weather() -> list:
    """Return all weather records ordered by date."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute('SELECT * FROM weather_data ORDER BY date').fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f'[weather_service] get_all_weather error: {e}')
        return []
