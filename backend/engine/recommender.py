"""
recommender.py — Main Recommendation Orchestrator
===================================================
Routes to ML engine (LightGBM) with rule-based fallback.

Priority:
  1. ML engine (ml_engine.predict_for_date) — uses LightGBM
  2. Rule-based (7-day rolling avg × DOW × weather × festival × trend)

Each returned item dict has:
    item_name, category, recommended_qty, reason, base_avg, model_used
"""

import os
import sys
import sqlite3
_HERE   = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.append(_BACKEND)
from database import get_db_connection
from datetime import datetime, timedelta
from math import ceil
from typing import Optional, List

from engine.festival_service import get_festival_multiplier, get_upcoming_festivals
from engine.weather_service import get_weather_for_date, fetch_and_store_weather
from engine.category_weather_map import compute_weather_factor

try:
    from engine.ml_engine import predict_for_date as ml_predict, get_model_info
    ML_AVAILABLE = True
except Exception as _ml_err:
    ML_AVAILABLE = False
    print(f'[recommender] ML not available: {_ml_err}')

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get(
    'DB_PATH',
    os.path.join(_HERE, '..', '..', 'hotel_aditya.db')
)

DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def _get_conn():
    return get_db_connection()


# ── Rule-based helpers ─────────────────────────────────────────────────────────

def _get_rolling_avg(item_name: str, before_date: str, days: int,
                     conn: sqlite3.Connection) -> float:
    """Average qty sold in the last `days` records before before_date."""
    row = conn.execute("""
        SELECT AVG(qty_sold)
        FROM (
            SELECT qty_sold FROM daily_sales
            WHERE item_name = ? AND date < ?
            ORDER BY date DESC
            LIMIT ?
        )
    """, (item_name, before_date, days)).fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def _get_dow_multiplier(item_name: str, target_dow: int,
                        conn: sqlite3.Connection) -> float:
    """
    How much more/less does this item sell on target_dow vs its overall average.
    target_dow: 0=Monday … 6=Sunday (Python weekday())
    SQLite strftime('%w'): 0=Sunday … 6=Saturday
    """
    sqlite_dow = (target_dow + 1) % 7   # Python weekday → SQLite %w

    row_dow = conn.execute("""
        SELECT AVG(qty_sold) FROM daily_sales
        WHERE item_name = ? AND CAST(strftime('%w', date) AS INTEGER) = ?
    """, (item_name, sqlite_dow)).fetchone()
    dow_avg = float(row_dow[0]) if row_dow and row_dow[0] else 0.0

    row_all = conn.execute(
        'SELECT AVG(qty_sold) FROM daily_sales WHERE item_name = ?', (item_name,)
    ).fetchone()
    overall_avg = float(row_all[0]) if row_all and row_all[0] else 1.0

    if overall_avg == 0:
        return 1.0
    factor = dow_avg / overall_avg
    return round(max(0.5, min(2.0, factor)), 3)


def _get_trend_factor(item_name: str, before_date: str,
                      conn: sqlite3.Connection) -> float:
    """
    3-day micro-trend factor.
    Positive slope (rising) → 1.05, negative → 0.97, flat → 1.0.
    """
    rows = conn.execute("""
        SELECT qty_sold FROM daily_sales
        WHERE item_name = ? AND date < ?
        ORDER BY date DESC LIMIT 3
    """, (item_name, before_date)).fetchall()

    if len(rows) < 2:
        return 1.0

    qtys  = [float(r[0]) for r in rows]   # newest first
    diffs = [qtys[i] - qtys[i + 1] for i in range(len(qtys) - 1)]
    avg_diff = sum(diffs) / len(diffs)

    if avg_diff > 0:
        return 1.05
    elif avg_diff < 0:
        return 0.97
    return 1.0


def _get_weather_description(weather: dict) -> str:
    """Human-readable weather summary."""
    if not weather:
        return 'Weather data unavailable'
    temp      = weather.get('max_temp', '--')
    condition = weather.get('condition', '--')
    rain      = float(weather.get('rainfall_mm', 0) or 0)
    desc      = f'{condition}, {temp}°C'
    if rain > 2:
        desc += f' ({rain:.1f} mm rain expected)'
    return desc


# ── Rule-based main logic ──────────────────────────────────────────────────────

def _rule_based_generate(target_date: str) -> List[dict]:
    """
    Full rule-based recommendation pipeline.
    Returns list of recommendation dicts.
    """
    target_dt  = datetime.strptime(target_date, '%Y-%m-%d')
    target_dow = target_dt.weekday()
    dow_name   = DAY_NAMES[target_dow]

    # Weather: use cached or generate mock
    weather = get_weather_for_date(target_date)
    if weather is None:
        weather = fetch_and_store_weather(target_date)
    if weather is None:
        weather = {'max_temp': 38.0, 'min_temp': 28.0, 'rainfall_mm': 0.0, 'condition': 'Sunny'}

    # Festival
    festival_mult, festival_name = get_festival_multiplier(target_date)

    # Upcoming festivals (for reason string)
    upcoming = get_upcoming_festivals(target_date, lookahead_days=7)

    recommendations: List[dict] = []

    with get_db_connection() as conn:
        # All menu items
        items = conn.execute(
            'SELECT item_name, category FROM menu_items ORDER BY item_name'
        ).fetchall()

        for item_row in items:
            item_name = item_row[0]
            category  = item_row[1] or 'other'

            # Base: try 7-day rolling avg, fall back to 30-day
            base_avg = _get_rolling_avg(item_name, target_date, days=7, conn=conn)
            if base_avg == 0:
                base_avg = _get_rolling_avg(item_name, target_date, days=30, conn=conn)
            if base_avg == 0:
                continue   # Never sold — skip

            dow_factor     = _get_dow_multiplier(item_name, target_dow, conn)
            weather_factor = compute_weather_factor(category, weather)
            trend_factor   = _get_trend_factor(item_name, target_date, conn)

            raw_qty   = base_avg * dow_factor * weather_factor * festival_mult * trend_factor
            final_qty = max(1, ceil(raw_qty * 1.10))   # +10% safety buffer

            # ── Reason ─────────────────────────────────────────────────────────────
            reasons: List[str] = []

            if festival_name:
                reasons.append(f'\U0001f389 {festival_name}')
            elif upcoming:
                nxt = upcoming[0]
                reasons.append(f'\U0001f4c5 {nxt["name"]} in {nxt["days_away"]}d')

            if dow_factor >= 1.10:
                reasons.append(f'↑ {dow_name} peak day')
            elif dow_factor <= 0.90:
                reasons.append(f'↓ Slow {dow_name}')

            if weather_factor >= 1.15:
                reasons.append(f'↑ {_get_weather_description(weather)}')
            elif weather_factor <= 0.85:
                reasons.append(f'↓ {_get_weather_description(weather)}')

            if trend_factor > 1.0:
                reasons.append('↑ Trending up')
            elif trend_factor < 1.0:
                reasons.append('↓ Trending down')

            reason = ' | '.join(reasons) if reasons else f'Based on {dow_name} average'

            recommendations.append({
                'item_name':       item_name,
                'category':        category,
                'recommended_qty': final_qty,
                'reason':          reason,
                'base_avg':        round(base_avg, 1),
                'dow_factor':      round(dow_factor, 3),
                'weather_factor':  round(weather_factor, 3),
                'festival_factor': round(festival_mult, 3),
                'trend_factor':    round(trend_factor, 3),
                'model_used':      'rule_based',
            })

    # Sort: category asc, then recommended_qty desc
    recommendations.sort(key=lambda x: (x['category'], -x['recommended_qty']))
    return recommendations


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_recommendations(target_date: str) -> List[dict]:
    """
    Generate next-day order recommendations.

    Tries ML engine first; falls back to rule-based if ML fails.

    Args:
        target_date: 'YYYY-MM-DD' — the date you're ordering FOR.

    Returns:
        List of recommendation dicts sorted by category then qty desc.
    """
    if ML_AVAILABLE:
        try:
            recs = ml_predict(target_date)
            if recs:
                return recs
            print('[recommender] ML returned empty list — using rule-based.')
        except Exception as e:
            print(f'[recommender] ML prediction failed ({e}) — using rule-based.')

    return _rule_based_generate(target_date)


def get_recommendation_context(target_date: str) -> dict:
    """
    Return weather + festival context for the target date.
    Used by the /recommendations/context endpoint.
    """
    # Weather
    weather = get_weather_for_date(target_date)
    if weather is None:
        weather = fetch_and_store_weather(target_date)
    if weather is None:
        weather = {'max_temp': 38.0, 'min_temp': 28.0, 'rainfall_mm': 0.0, 'condition': 'Sunny'}

    # Festival
    festival_mult, festival_name = get_festival_multiplier(target_date)
    upcoming = get_upcoming_festivals(target_date, lookahead_days=7)

    # Day of week
    dt      = datetime.strptime(target_date, '%Y-%m-%d')
    dow     = dt.weekday()
    dow_name = DAY_NAMES[dow]

    # Model info
    model_info = get_model_info() if ML_AVAILABLE else {'model_type': 'rule_based'}

    return {
        'date':          target_date,
        'day_of_week':   dow_name,
        'weather':       weather,
        'weather_description': _get_weather_description(weather),
        'festival': {
            'name':       festival_name,
            'multiplier': festival_mult,
            'is_festival': festival_name is not None,
        },
        'upcoming_festivals': upcoming,
        'model_info': model_info,
    }


def save_recommendations(target_date: str, recs: List[dict]) -> None:
    """
    Persist recommendations to the recommendations table.
    """
    with get_db_connection() as conn:
        conn.execute('DELETE FROM recommendations WHERE date = ?', (target_date,))
        for r in recs:
            conn.execute("""
                INSERT INTO recommendations
                    (date, item_name, recommended_qty, merchant_override, reason)
                VALUES (?, ?, ?, ?, ?)
            """, (
                target_date,
                r.get('item_name'),
                r.get('recommended_qty'),
                r.get('merchant_override', r.get('merchant_qty')),
                r.get('reason'),
            ))
