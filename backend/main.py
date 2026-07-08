"""
main.py — Hotel Aditya Grand Order Assistant API
==================================================
FastAPI application serving the AI-powered recommendation system.
Run with: cd e:\\Finternship\\backend && py -m uvicorn main:app --reload --port 8000
API docs: http://localhost:8000/docs
"""

import os
import sys
import io
import json
import sqlite3
import re
import tempfile
import threading
from datetime import date, timedelta, datetime
from typing import List, Optional
from math import ceil
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')

def get_today() -> date:
    return datetime.now(IST).date()

def get_today_str() -> str:
    return get_today().isoformat()

from fastapi import FastAPI, Query, APIRouter, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

# ── Fix Windows console encoding ──────────────────────────────────────────────
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

# ── Load .env ─────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE       = os.path.dirname(os.path.abspath(__file__))
ROOT        = os.path.join(_HERE, '..')
DB_PATH     = os.environ.get('DB_PATH', os.path.join(_HERE, 'hotel_aditya.db'))
FRONTEND    = os.path.join(ROOT, 'frontend')
CONFIG_PATH = os.environ.get('CONFIG_PATH')
if not CONFIG_PATH:
    if os.path.dirname(DB_PATH) and os.path.dirname(DB_PATH) != _HERE:
        CONFIG_PATH = os.path.join(os.path.dirname(DB_PATH), 'config.json')
    else:
        CONFIG_PATH = os.path.join(_HERE, 'config.json')

# Seed config.json if it doesn't exist on the persistent disk
import shutil as _shutil
_BUNDLED_CONFIG = os.path.join(_HERE, 'config.json')
if CONFIG_PATH != _BUNDLED_CONFIG and not os.path.exists(CONFIG_PATH) and os.path.exists(_BUNDLED_CONFIG):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        _shutil.copy2(_BUNDLED_CONFIG, CONFIG_PATH)
        print(f'[startup] Seeded config: {_BUNDLED_CONFIG} → {CONFIG_PATH}')
    except Exception as _cfg_err:
        print(f'[startup] Config seed failed ({_cfg_err})')

from database import get_db_connection, initialize_database, IS_POSTGRES

def _ensure_unit_price_column(db_path):
    try:
        import sqlite3 as _sqlite3
        if not os.path.exists(db_path):
            return
        conn = _sqlite3.connect(db_path)
        info = conn.execute("PRAGMA table_info(menu_items)").fetchall()
        cols = [c[1] for c in info]
        if 'unit_price' not in cols:
            conn.execute("ALTER TABLE menu_items ADD COLUMN unit_price REAL DEFAULT 0.0")
            conn.commit()
            conn.execute("""
                UPDATE menu_items
                SET unit_price = COALESCE(
                    (SELECT CAST(SUM(gross_revenue) AS REAL) / SUM(qty_sold)
                     FROM daily_sales
                     WHERE daily_sales.item_name = menu_items.item_name
                       AND qty_sold > 0 AND gross_revenue > 0),
                    0.0
                )
            """)
            conn.commit()
            print(f"[startup] Added unit_price column and populated averages in {db_path}")
        conn.close()
    except Exception as e:
        print(f"[startup] Failed to ensure unit_price column in {db_path}: {e}")

if not IS_POSTGRES:
    _BUNDLED_DB = os.path.join(_HERE, 'hotel_aditya.db')
    _ensure_unit_price_column(_BUNDLED_DB)
    if DB_PATH != _BUNDLED_DB and os.path.exists(_BUNDLED_DB):
        _disk_exists = os.path.exists(DB_PATH)
        _disk_size   = os.path.getsize(DB_PATH) if _disk_exists else 0
        if _disk_size == 0:
            try:
                os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
                _shutil.copy2(_BUNDLED_DB, DB_PATH)
                print(f'[startup] Fresh disk — seeded DB from bundled ({os.path.getsize(_BUNDLED_DB)}B)')
            except Exception as _seed_err:
                print(f'[startup] DB seed failed ({_seed_err}), falling back to bundled DB')
                DB_PATH = _BUNDLED_DB
        else:
            try:
                import sqlite3 as _sqlite3
                _src_conn = _sqlite3.connect(_BUNDLED_DB)
                _dst_conn = _sqlite3.connect(DB_PATH)
                _dst_conn.execute('PRAGMA journal_mode=WAL')
                _dst_info = _dst_conn.execute("PRAGMA table_info(menu_items)").fetchall()
                _dst_cols = [c[1] for c in _dst_info]
                if 'unit_price' not in _dst_cols:
                    _dst_conn.execute("ALTER TABLE menu_items ADD COLUMN unit_price REAL DEFAULT 0.0")
                    _dst_conn.commit()
                _sales_rows = _src_conn.execute('SELECT date, item_name, qty_sold, gross_revenue, source FROM daily_sales').fetchall()
                _dst_conn.executemany('INSERT OR IGNORE INTO daily_sales (date, item_name, qty_sold, gross_revenue, source) VALUES (?,?,?,?,?)', _sales_rows)
                _menu_rows = _src_conn.execute('SELECT item_name, category, avg_qty, unit_price FROM menu_items').fetchall()
                _dst_conn.executemany('INSERT OR IGNORE INTO menu_items (item_name, category, avg_qty, unit_price) VALUES (?,?,?,?)', _menu_rows)
                _dst_conn.commit()
                _src_conn.close()
                _dst_conn.close()
                print(f'[startup] Merged bundled DB into persistent disk')
            except Exception as _merge_err:
                print(f'[startup] DB merge failed ({_merge_err})')
    _ensure_unit_price_column(DB_PATH)


# Run DB init in a background thread so the server can accept /ping immediately.
# If Supabase is slow to connect, this prevents a startup hang that blocks uvicorn.
def _bg_init():
    try:
        initialize_database()
    except Exception as _init_err:
        print(f'[startup] initialize_database failed: {_init_err}')

threading.Thread(target=_bg_init, daemon=True).start()



# ── Config helpers ─────────────────────────────────────────────────────────────

# Keys that must NEVER be stored in or read from config.json.
# They must come from environment variables only.
_SECRET_KEYS = {'openweather_api_key', 'gemini_api_key'}

# In-memory key store: holds keys set via the Settings UI for the current session.
# These are lost on server restart — set Render env vars for persistence.
_RUNTIME_KEYS: dict = {}


def _load_config() -> dict:
    """Load config.json, stripping any API keys so they are never read from disk."""
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    # Remove any secrets that may have leaked into config.json previously
    for k in _SECRET_KEYS:
        cfg.pop(k, None)
    return cfg


def _save_config(updates: dict) -> None:
    """Persist non-secret settings to config.json. API keys are silently ignored."""
    cfg = _load_config()
    # Strip secrets before writing — they must live in env vars only
    safe_updates = {k: v for k, v in updates.items() if k not in _SECRET_KEYS}
    cfg.update(safe_updates)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)


# ── DB helper ──────────────────────────────────────────────────────────────────

def _conn():
    return get_db_connection()


# ── Engine imports ─────────────────────────────────────────────────────────────

try:
    from engine.recommender import generate_recommendations, get_recommendation_context, save_recommendations
    RECS_AVAILABLE = True
except Exception as _recs_err:
    RECS_AVAILABLE = False
    print(f'[main] Recommender not available: {_recs_err}')
    def generate_recommendations(date_str: str) -> list:
        return []
    def get_recommendation_context(date_str: str) -> dict:
        return {}

try:
    from engine.ml_engine import train_model as _ml_train, get_model_info
    ML_AVAILABLE = True
except Exception as _ml_err:
    ML_AVAILABLE = False
    print(f'[main] ML engine not available: {_ml_err}')
    def get_model_info() -> dict:
        return {'model_type': 'unavailable'}


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title='Hotel Aditya Grand — Order Assistant API',
    description=(
        'AI-powered next-day order recommendations for Hotel Aditya Grand, Kandukur. '
        'Uses 45+ days of historical POS data + weather + festival signals. '
        'Powered by LightGBM with rule-based fallback.'
    ),
    version='2.0.0',
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        'http://localhost:8000',
        'http://localhost:5500',
        'http://127.0.0.1:5500',
        'https://harsha-mandala.github.io',          # GitHub Pages frontend
        'https://finternship-elite-crew.onrender.com', # Render backend (self-requests)
        '*',                                           # Fallback for any other origin
    ],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

@app.get('/health', tags=['Health'])
def health_check():
    """Full health check — DB stats + model info."""
    try:
        with get_db_connection() as conn:
            sales_rows  = conn.execute('SELECT COUNT(*) FROM daily_sales').fetchone()[0]
            menu_items  = conn.execute('SELECT COUNT(*) FROM menu_items').fetchone()[0]
        db_status = 'ok'
    except Exception as e:
        sales_rows = 0
        menu_items = 0
        db_status  = f'error: {e}'

    return {
        'status':     'ok',
        'service':    'Hotel Aditya Grand Order Assistant',
        'version':    '2.0.0',
        'db':         db_status,
        'sales_rows': sales_rows,
        'menu_items': menu_items,
        'model_info': get_model_info(),
        'docs':       '/docs',
    }


@app.get('/ping', tags=['Health'])
def ping():
    """Lightweight health-check."""
    return {'pong': True}


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

dash_router = APIRouter(prefix='/dashboard', tags=['Dashboard'])


@dash_router.get('/summary')
def dashboard_summary():
    """
    Today's revenue, top-selling items, weather, and festival info.
    """
    today = get_today_str()
    with get_db_connection() as conn:
        # Today's revenue and units sold
        rev_row = conn.execute(
            'SELECT COALESCE(SUM(gross_revenue), 0), COALESCE(SUM(qty_sold), 0) '
            'FROM daily_sales WHERE date = ?', (today,)
        ).fetchone()
        today_revenue  = round(float(rev_row[0]), 2)
        today_qty_sold = int(rev_row[1])

        # Top 5 items by qty today
        top_rows = conn.execute(
            'SELECT item_name, SUM(qty_sold) AS qty, SUM(gross_revenue) AS rev '
            'FROM daily_sales WHERE date = ? '
            'GROUP BY item_name ORDER BY qty DESC LIMIT 5',
            (today,)
        ).fetchall()
        top_items = [
            {'item_name': r[0], 'qty_sold': int(r[1]), 'revenue': round(float(r[2]), 2)}
            for r in top_rows
        ]

        # Total menu items count
        menu_count = conn.execute('SELECT COUNT(*) FROM menu_items').fetchone()[0]

        # Weather for today (or tomorrow if today not available)
        weather = None
        try:
            from engine.weather_service import get_weather_for_date
            weather = get_weather_for_date(today)
            if weather is None:
                weather = get_weather_for_date((get_today() + timedelta(days=1)).isoformat())
        except Exception:
            pass

        # Festival for today
        festival_info: dict = {}
        try:
            from engine.festival_service import get_festival_multiplier, get_upcoming_festivals
            mult, name = get_festival_multiplier(today)
            upcoming   = get_upcoming_festivals(today, lookahead_days=7)
            festival_info = {
                'today': {'name': name, 'multiplier': mult} if name else None,
                'upcoming': upcoming[:3],
            }
        except Exception:
            pass

        # Total revenue this month
        month_start = today[:7] + '-01'
        month_rev   = conn.execute(
            'SELECT COALESCE(SUM(gross_revenue), 0) FROM daily_sales WHERE date >= ?',
            (month_start,)
        ).fetchone()[0]

    return {
        'date':           today,
        'today_revenue':  today_revenue,
        'total_qty_sold': today_qty_sold,
        'menu_items':     int(menu_count),
        'month_revenue':  round(float(month_rev), 2),
        'top_items':      top_items,
        'weather':        dict(weather) if weather else None,
        'festival':       festival_info,
    }


@dash_router.get('/revenue-trend')
def revenue_trend(
    days: Optional[int] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None)
):
    """Daily gross revenue for a date range or the last N days."""
    if start_date and end_date:
        with get_db_connection() as conn:
            rows = conn.execute(
                'SELECT date, ROUND(SUM(gross_revenue), 2) AS revenue '
                'FROM daily_sales WHERE date >= ? AND date <= ? '
                'GROUP BY date ORDER BY date ASC',
                (start_date, end_date)
            ).fetchall()
    else:
        d = days or 30
        cutoff = (get_today() - timedelta(days=d)).isoformat()
        with get_db_connection() as conn:
            rows = conn.execute(
                'SELECT date, ROUND(SUM(gross_revenue), 2) AS revenue '
                'FROM daily_sales WHERE date >= ? '
                'GROUP BY date ORDER BY date ASC',
                (cutoff,)
            ).fetchall()
    return {
        'start_date': start_date,
        'end_date': end_date,
        'series': [
            {
                'date': r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]),
                'revenue': float(r[1])
            }
            for r in rows
        ],
    }


@dash_router.get('/category-trends')
def category_trends(
    days: Optional[int] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None)
):
    """Revenue and qty by category for a date range or the last N days."""
    if start_date and end_date:
        with get_db_connection() as conn:
            rows = conn.execute(
                'SELECT mi.category, '
                '       ROUND(SUM(ds.gross_revenue), 2) AS revenue, '
                '       SUM(ds.qty_sold) AS qty '
                'FROM daily_sales ds '
                'LEFT JOIN menu_items mi ON ds.item_name = mi.item_name '
                'WHERE ds.date >= ? AND ds.date <= ? '
                'GROUP BY mi.category '
                'ORDER BY revenue DESC',
                (start_date, end_date)
            ).fetchall()
    else:
        d = days or 30
        cutoff = (get_today() - timedelta(days=d)).isoformat()
        with get_db_connection() as conn:
            rows = conn.execute(
                'SELECT mi.category, '
                '       ROUND(SUM(ds.gross_revenue), 2) AS revenue, '
                '       SUM(ds.qty_sold) AS qty '
                'FROM daily_sales ds '
                'LEFT JOIN menu_items mi ON ds.item_name = mi.item_name '
                'WHERE ds.date >= ? '
                'GROUP BY mi.category '
                'ORDER BY revenue DESC',
                (cutoff,)
            ).fetchall()
    return {
        'start_date': start_date,
        'end_date': end_date,
        'categories': [
            {'category': r[0] or 'other', 'revenue': float(r[1]), 'qty': int(r[2])}
            for r in rows
        ],
    }


@dash_router.get('/actual-vs-predicted')
def actual_vs_predicted():
    """Compare today's actual sales vs predicted sales by category."""
    today = get_today_str()
    with get_db_connection() as conn:
        # 1. Actuals for today by category
        actual_rows = conn.execute(
            'SELECT mi.category, SUM(ds.qty_sold) '
            'FROM daily_sales ds '
            'LEFT JOIN menu_items mi ON ds.item_name = mi.item_name '
            'WHERE ds.date = ? '
            'GROUP BY mi.category',
            (today,)
        ).fetchall()
        
        actuals = { (r[0] or 'other'): int(r[1]) for r in actual_rows }
        
        # 2. Predicted for today by category
        predicted = {}
        if RECS_AVAILABLE:
            try:
                recs = generate_recommendations(today)
                for r in recs:
                    cat = r.get('category', 'other')
                    predicted[cat] = predicted.get(cat, 0) + int(r.get('recommended_qty', 0))
            except Exception as e:
                print(f'[main] Error generating predictions for chart: {e}')
        
    all_cats = set(actuals.keys()).union(set(predicted.keys()))
    results = []
    for cat in all_cats:
        results.append({
            'category': cat,
            'actual_qty': actuals.get(cat, 0),
            'predicted_qty': predicted.get(cat, 0)
        })
        
    results.sort(key=lambda x: -(x['actual_qty'] + x['predicted_qty']))
    return {'date': today, 'categories': results}


@dash_router.get('/insights')
def dashboard_insights():
    """Generate business insights using SQL queries on daily_sales and menu_items."""
    try:
        with get_db_connection() as conn:
            # Find date range of available data
            dates = conn.execute('SELECT MIN(date), MAX(date) FROM daily_sales').fetchone()
            if not dates or not dates[0]:
                return {'insights': []}
            
            min_date = dates[0].isoformat() if hasattr(dates[0], 'isoformat') else str(dates[0])
            max_date = dates[1].isoformat() if hasattr(dates[1], 'isoformat') else str(dates[1])
            max_dt = datetime.strptime(max_date, '%Y-%m-%d')

            # Define latest week and previous week boundaries
            w1_start = (max_dt - timedelta(days=6)).strftime('%Y-%m-%d')
            w1_end = max_date
            w2_start = (max_dt - timedelta(days=13)).strftime('%Y-%m-%d')
            w2_end = (max_dt - timedelta(days=7)).strftime('%Y-%m-%d')

            insights = []

            # 1. Best Day of Week
            dow_rows = conn.execute("""
                SELECT CAST(strftime('%w', date) AS INTEGER) AS dow, AVG(daily_rev) AS avg_rev
                FROM (
                    SELECT date, SUM(gross_revenue) AS daily_rev
                    FROM daily_sales
                    GROUP BY date
                )
                GROUP BY dow
                ORDER BY avg_rev DESC
            """).fetchall()
            if dow_rows:
                DOW_MAP = {0: 'Sunday', 1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday', 6: 'Saturday'}
                best_dow = DOW_MAP.get(int(dow_rows[0][0]))
                best_rev = float(dow_rows[0][1])
                insights.append({
                    'type': 'best_day',
                    'title': '📅 Best Day of the Week',
                    'text': f"On average, **{best_dow}s** are your highest earning days, generating **₹{best_rev:,.0f}** in revenue, followed closely by other weekend slots.",
                    'badge': 'Surge Pattern',
                    'color': 'success'
                })

            # 2. Trending Items (Week-over-week growth)
            trend_rows = conn.execute("""
                SELECT
                    w1.item_name,
                    w1.w1_qty,
                    COALESCE(w2.w2_qty, 0) AS w2_qty,
                    w1.w1_qty - COALESCE(w2.w2_qty, 0) AS diff
                FROM (
                    SELECT item_name, SUM(qty_sold) AS w1_qty
                    FROM daily_sales
                    WHERE date >= ? AND date <= ?
                    GROUP BY item_name
                ) w1
                LEFT JOIN (
                    SELECT item_name, SUM(qty_sold) AS w2_qty
                    FROM daily_sales
                    WHERE date >= ? AND date <= ?
                    GROUP BY item_name
                ) w2 ON w1.item_name = w2.item_name
                ORDER BY diff DESC
                LIMIT 1
            """, (w1_start, w1_end, w2_start, w2_end)).fetchone()

            if trend_rows and trend_rows[3] > 0:
                item_name, w1_qty, w2_qty, diff = trend_rows[0], int(trend_rows[1]), int(trend_rows[2]), int(trend_rows[3])
                insights.append({
                    'type': 'trending_up',
                    'title': '📈 Trending Up This Week',
                    'text': f"Demand for **{item_name}** has surged. You sold **{w1_qty} units** this week, up from **{w2_qty} units** last week (a +{diff} plates increase).",
                    'badge': 'Trending Up',
                    'color': 'primary'
                })

            # 3. Weekend vs Weekday Surge
            weekend_rows = conn.execute("""
                SELECT
                    AVG(CASE WHEN CAST(strftime('%w', date) AS INTEGER) IN (0, 5, 6) THEN daily_rev END) AS weekend_avg,
                    AVG(CASE WHEN CAST(strftime('%w', date) AS INTEGER) NOT IN (0, 5, 6) THEN daily_rev END) AS weekday_avg
                FROM (
                    SELECT date, SUM(gross_revenue) AS daily_rev
                    FROM daily_sales
                    GROUP BY date
                )
            """).fetchone()
            if weekend_rows and weekend_rows[0] and weekend_rows[1]:
                wend, wday = float(weekend_rows[0]), float(weekend_rows[1])
                ratio = wend / wday
                insights.append({
                    'type': 'weekend_ratio',
                    'title': '⚖️ Weekend Surge vs Weekdays',
                    'text': f"Weekend daily sales (Fri-Sun) average **₹{wend:,.0f}**, which is **{ratio:.1f}x higher** than your weekday average (₹{wday:,.0f}). Optimize inventory counts for weekend prep.",
                    'badge': 'Sales Balance',
                    'color': 'warning'
                })

            # 4. Most Consistent Star Performer
            consistent_row = conn.execute("""
                SELECT item_name, COUNT(DISTINCT date) AS active_days
                FROM daily_sales
                GROUP BY item_name
                ORDER BY active_days DESC, SUM(qty_sold) DESC
                LIMIT 1
            """).fetchone()
            total_days = conn.execute("SELECT COUNT(DISTINCT date) FROM daily_sales").fetchone()[0]
            if consistent_row and total_days:
                item_name, active_days = consistent_row[0], int(consistent_row[1])
                insights.append({
                    'type': 'star_performer',
                    'title': '⭐ Most Consistent Menu Item',
                    'text': f"**{item_name}** is your most reliable dish, appearing in the daily bill logs on **{active_days} out of {total_days} days** of recorded operations.",
                    'badge': 'Menu Star',
                    'color': 'success'
                })

            # 5. Slow Categories
            slow_cat_row = conn.execute("""
                SELECT mi.category, SUM(ds.gross_revenue) AS cat_rev
                FROM daily_sales ds
                LEFT JOIN menu_items mi ON ds.item_name = mi.item_name
                WHERE ds.date >= ? AND ds.date <= ?
                GROUP BY mi.category
                ORDER BY cat_rev ASC
                LIMIT 1
            """, (w1_start, w1_end)).fetchone()
            if slow_cat_row and slow_cat_row[0]:
                cat_name, cat_rev = slow_cat_row[0], float(slow_cat_row[1])
                insights.append({
                    'type': 'slow_category',
                    'title': '⚠️ Slowest Category This Week',
                    'text': f"The **{cat_name.replace('_', ' ').capitalize()}** category brought in the lowest weekly revenue (**₹{cat_rev:,.0f}**). Consider a promo or price tweak.",
                    'badge': 'Category Warning',
                    'color': 'danger'
                })

        return {'insights': insights}
    except Exception as e:
        return {'insights': [], 'error': str(e)}


app.include_router(dash_router)


# ═══════════════════════════════════════════════════════════════════════════════
# ITEMS ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

items_router = APIRouter(prefix='/items', tags=['Items'])


@items_router.get('/')
def list_items(category: Optional[str] = Query(default=None)):
    """List all menu items, optionally filtered by category."""
    with get_db_connection() as conn:
        if category:
            rows = conn.execute(
                'SELECT item_name, category, avg_qty, unit_price FROM menu_items '
                'WHERE LOWER(category) = LOWER(?) ORDER BY item_name',
                (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT item_name, category, avg_qty, unit_price FROM menu_items ORDER BY category, item_name'
            ).fetchall()
    return {
        'count': len(rows),
        'items': [
            {'item_name': r[0], 'category': r[1], 'avg_qty': r[2], 'unit_price': r[3]}
            for r in rows
        ],
    }



@items_router.get('/categories')
def list_categories():
    """List all distinct categories."""
    with get_db_connection() as conn:
        rows  = conn.execute(
            'SELECT DISTINCT category, COUNT(*) AS item_count '
            'FROM menu_items GROUP BY category ORDER BY category'
        ).fetchall()
    return {'categories': [{'category': r[0], 'item_count': r[1]} for r in rows]}


class ItemCategoryUpdate(BaseModel):
    category: str

class RenameCategoryBody(BaseModel):
    old_category: str
    new_category: str

class NewItemBody(BaseModel):
    item_name: str
    category: str
    avg_qty: Optional[float] = 0.0
    unit_price: Optional[float] = 0.0

class ItemPriceUpdate(BaseModel):
    unit_price: float

@items_router.patch('/{item_name}/category')
def update_item_category(item_name: str, body: ItemCategoryUpdate):
    """Update the category of a single menu item."""
    category = body.category.strip().lower().replace(' ', '_')
    if not category:
        raise HTTPException(status_code=400, detail='Category cannot be empty')
    with get_db_connection() as conn:
        result = conn.execute(
            'UPDATE menu_items SET category = ? WHERE item_name = ?',
            (category, item_name)
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f'Item "{item_name}" not found')
    return {'ok': True, 'item_name': item_name, 'new_category': category}


@items_router.patch('/{item_name}/price')
def update_item_price(item_name: str, body: ItemPriceUpdate):
    """Update the unit price of a single menu item."""
    price = body.unit_price
    if price < 0:
        raise HTTPException(status_code=400, detail='Price cannot be negative')
    with get_db_connection() as conn:
        result = conn.execute(
            'UPDATE menu_items SET unit_price = ? WHERE item_name = ?',
            (price, item_name)
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f'Item "{item_name}" not found')
    return {'ok': True, 'item_name': item_name, 'unit_price': price}


@items_router.post('/rename-category')
def rename_category(body: RenameCategoryBody):
    """Rename a category — updates ALL items in that category to the new name."""
    old = body.old_category.strip().lower().replace(' ', '_')
    new = body.new_category.strip().lower().replace(' ', '_')
    if not old or not new:
        raise HTTPException(status_code=400, detail='Category names cannot be empty')
    with get_db_connection() as conn:
        result = conn.execute(
            'UPDATE menu_items SET category = ? WHERE LOWER(category) = ?',
            (new, old)
        )
    return {'ok': True, 'old_category': old, 'new_category': new, 'items_updated': result.rowcount}


@items_router.post('/add')
def add_menu_item(body: NewItemBody):
    """Add a new item to the menu."""
    name = body.item_name.strip()
    category = body.category.strip().lower().replace(' ', '_')
    if not name or not category:
        raise HTTPException(status_code=400, detail='item_name and category are required')
    try:
        with get_db_connection() as conn:
            conn.execute(
                'INSERT INTO menu_items (item_name, category, avg_qty, unit_price) VALUES (?, ?, ?, ?)',
                (name, category, body.avg_qty or 0.0, body.unit_price or 0.0)
            )
    except Exception as e:
        raise HTTPException(status_code=409, detail=f'Item already exists or error: {e}')
    return {'ok': True, 'item_name': name, 'category': category, 'unit_price': body.unit_price or 0.0}



@items_router.delete('/{item_name}')
def delete_menu_item(item_name: str):
    """Delete a menu item."""
    with get_db_connection() as conn:
        result = conn.execute(
            'DELETE FROM menu_items WHERE item_name = ?',
            (item_name,)
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f'Item "{item_name}" not found')
    return {'ok': True, 'item_name': item_name}


app.include_router(items_router)


# ═══════════════════════════════════════════════════════════════════════════════
# SALES ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

sales_router = APIRouter(prefix='/sales', tags=['Sales'])


class SaleEntry(BaseModel):
    date:          str
    item_name:     str
    qty_sold:      int
    gross_revenue: Optional[float] = None
    source:        Optional[str]   = 'manual'


@sales_router.get('/')
def get_sales(date_filter: Optional[str] = Query(default=None, alias='date'),
              limit: int = Query(default=100, ge=1, le=1000)):
    """Get sales records, optionally filtered by date."""
    with get_db_connection() as conn:
        if date_filter:
            rows = conn.execute(
                'SELECT date, item_name, qty_sold, gross_revenue, source '
                'FROM daily_sales WHERE date = ? ORDER BY item_name LIMIT ?',
                (date_filter, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT date, item_name, qty_sold, gross_revenue, source '
                'FROM daily_sales ORDER BY date DESC, item_name LIMIT ?',
                (limit,)
            ).fetchall()
    return {
        'count': len(rows),
        'sales': [
            {
                'date': r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]),
                'item_name': r[1],
                'qty_sold': r[2],
                'gross_revenue': float(r[3]),
                'source': r[4],
            }
            for r in rows
        ],
    }


@sales_router.post('/log')
def log_sales(entries: List[SaleEntry]):
    """
    Upsert one or more sales entries.
    If gross_revenue is not provided, estimate it from menu_items.unit_price.
    Because daily_sales has UNIQUE(date, item_name), INSERT OR REPLACE
    will UPDATE the existing row if it already exists, so clicking
    'Save' multiple times never duplicates data.
    """
    saved  = 0
    errors = []

    with get_db_connection() as conn:
        # Fetch configured unit prices from menu_items catalog
        price_rows = conn.execute(
            'SELECT item_name, unit_price FROM menu_items'
        ).fetchall()
        price_map  = {r[0]: float(r[1]) for r in price_rows if r[1] is not None}

        for entry in entries:
            # Use provided revenue; if missing/zero, estimate from unit price
            if entry.gross_revenue is not None and entry.gross_revenue > 0:
                revenue = entry.gross_revenue
            else:
                unit_price = price_map.get(entry.item_name, 0.0)
                revenue = round(entry.qty_sold * unit_price, 2)

            try:
                conn.execute(
                    'INSERT OR REPLACE INTO daily_sales '
                    '(date, item_name, qty_sold, gross_revenue, source) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (entry.date, entry.item_name, entry.qty_sold,
                     revenue, entry.source or 'manual')
                )
                saved += 1
            except Exception as e:
                errors.append({'item': entry.item_name, 'error': str(e)})

    return {'saved': saved, 'errors': errors}


@sales_router.get('/trends')
def item_trend(
    item: str = Query(..., description='Item name to get trend for'),
    days: Optional[int] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
):
    """Daily qty_sold trend for a specific item over a range or last N days."""
    if start_date and end_date:
        with get_db_connection() as conn:
            rows = conn.execute(
                'SELECT date, SUM(qty_sold) AS qty '
                'FROM daily_sales WHERE item_name = ? AND date >= ? AND date <= ? '
                'GROUP BY date ORDER BY date ASC',
                (item, start_date, end_date)
            ).fetchall()
    else:
        d = days or 30
        cutoff = (get_today() - timedelta(days=d)).isoformat()
        with get_db_connection() as conn:
            rows = conn.execute(
                'SELECT date, SUM(qty_sold) AS qty '
                'FROM daily_sales WHERE item_name = ? AND date >= ? '
                'GROUP BY date ORDER BY date ASC',
                (item, cutoff)
            ).fetchall()
    return {
        'item': item,
        'start_date': start_date,
        'end_date': end_date,
        'series': [
            {
                'date': r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]),
                'qty': int(r[1])
            }
            for r in rows
        ],
    }


@sales_router.post('/upload-pdf')
async def upload_pdf(file: UploadFile = File(...)):
    """
    Upload a POS PDF bill and extract sales records via OCR.
    Returns a Server-Sent Events stream with progress updates.
    """
    file_bytes = await file.read()
    filename   = file.filename or 'upload.pdf'

    async def generate():
        def event(status: str, msg: str, progress: int = 0, rows: int = None) -> str:
            d: dict = {'status': status, 'msg': msg, 'progress': progress}
            if rows is not None:
                d['rows'] = rows
            return f'data: {json.dumps(d)}\n\n'

        yield event('reading', f'\U0001f4c4 Reading PDF: {filename}', 5)

        try:
            import fitz   # PyMuPDF
            import asyncio
            
            # Read Gemini key: env var first (persistent), then runtime store (session-only)
            gemini_key = (os.getenv("GEMINI_API_KEY") or os.environ.get('GEMINI_API_KEY', '')).strip() or _RUNTIME_KEYS.get('gemini_api_key', '')

            # ── Known items from DB ────────────────────────────────────────────
            with get_db_connection() as conn2:
                known_items = set(
                    r[0] for r in conn2.execute('SELECT item_name FROM menu_items').fetchall()
                )
            
            # ── Parse date from filename ───────────────────────────────────────
            date_match = re.search(r'(\d{2})-(\d{2})-(\d{4})', filename)
            if date_match:
                sale_date = f'{date_match.group(3)}-{date_match.group(2)}-{date_match.group(1)}'
            else:
                iso_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
                sale_date = iso_match.group(0) if iso_match else datetime.now(IST).date().isoformat()
                
            rows_to_insert: List[tuple] = []
            yield event('date', f'📅 Sale date detected: {sale_date}', 8)

            if gemini_key:
                try:
                    from engine.gemini_ocr import process_pdf_with_gemini, get_current_model
                    active_model = get_current_model()
                    yield event('ocr', f'🤖 Using {active_model} for OCR...', 20)
                    items = await asyncio.to_thread(
                        process_pdf_with_gemini, file_bytes, gemini_key, list(known_items), sale_date
                    )

                    # Build case-insensitive lookup for known items
                    known_lower = {n.lower(): n for n in known_items}
                    matched = 0
                    unmatched = []

                    for item in items:
                        name = (item.get('item_name') or '').strip()
                        qty  = item.get('qty')
                        if not name or not isinstance(qty, (int, float)) or int(qty) <= 0:
                            continue
                        # Try exact match first, then case-insensitive
                        canonical = None
                        if name in known_items:
                            canonical = name
                        elif name.lower() in known_lower:
                            canonical = known_lower[name.lower()]
                        else:
                            unmatched.append(name)
                            continue
                        gross = float(item.get('gross') or 0.0)
                        rows_to_insert.append((sale_date, canonical, int(qty), gross))
                        matched += 1

                    if unmatched:
                        print(f'[main] Gemini returned {len(unmatched)} unmatched items: {unmatched[:10]}')

                    yield event('normalizing', f'\U0001f9f9 AI matched {matched} items to menu...', 75)

                except Exception as e:
                    import traceback
                    print(f'[main] Gemini OCR error: {traceback.format_exc()}')
                    # Fall through to EasyOCR instead of killing the stream
                    yield event('ocr', f'\u26a0\ufe0f Gemini failed ({e}), falling back to standard OCR...', 15)
                    gemini_key = ''  # trigger EasyOCR block below

                    
            else:
                # EasyOCR Fallback
                pdf     = fitz.open(stream=file_bytes, filetype='pdf')
                n_pages = len(pdf)
                yield event('ocr', f'\U0001f916 Starting OCR on {n_pages} page(s)...', 10)
    
                all_text: List[str] = []
    
                try:
                    import easyocr
                    reader = easyocr.Reader(['en'], gpu=False, verbose=False)
                    for i, page in enumerate(pdf):
                        pct = 10 + int((i / n_pages) * 60)
                        yield event('ocr', f'\U0001f916 OCR page {i + 1} of {n_pages}...', pct)
                        mat      = fitz.Matrix(2, 2)
                        pix      = page.get_pixmap(matrix=mat)
                        img_bytes = pix.tobytes('png')
                        # Run CPU-bound OCR in a separate thread so it doesn't block the FastAPI event loop
                        results  = await asyncio.to_thread(reader.readtext, img_bytes, detail=0)
                        all_text.extend(str(t) for t in results)
    
                except ImportError:
                    yield event('ocr', '\U0001f4dd Extracting text from PDF (no EasyOCR)...', 50)
                    for page in pdf:
                        all_text.extend(page.get_text().split('\n'))
    
                pdf.close()
                yield event('normalizing', '\U0001f9f9 Normalizing item names...', 75)
                
                # ── Normalize function ─────────────────────────────────────────────
                data_pipeline = os.path.join(ROOT, 'data_pipeline')
                if data_pipeline not in sys.path:
                    sys.path.insert(0, data_pipeline)
                try:
                    from clean_data import normalize_item_name
                except ImportError:
                    def normalize_item_name(x: str) -> str:  # type: ignore
                        return x.strip().title()
                        
                # ── Parse item + qty pairs from OCR text ───────────────────────────
                qty_pattern   = re.compile(r'^\d+\.?\d*$')
                lines = [l for l in all_text if l]
    
                i = 0
                while i < len(lines):
                    line = str(lines[i]).strip()
                    if len(line) > 2:
                        normalized = normalize_item_name(line)
                        if normalized in known_items:
                            # Look ahead up to 4 lines for a standalone integer qty
                            for j in range(i + 1, min(i + 5, len(lines))):
                                candidate = str(lines[j]).strip()
                                if qty_pattern.match(candidate):
                                    try:
                                        qty = int(float(candidate))
                                        if 0 < qty < 1000:
                                            rows_to_insert.append((sale_date, normalized, qty))
                                        break
                                    except (ValueError, TypeError):
                                        pass
                    i += 1

            yield event('saving', f'\U0001f4be Saving {len(rows_to_insert)} records...', 90)

            saved = 0
            if rows_to_insert:
                with get_db_connection() as conn:
                    for row in rows_to_insert:
                        sale_date_r, item_name, qty = row[0], row[1], row[2]
                        rev = float(row[3]) if len(row) > 3 else 0.0
                        try:
                            conn.execute(
                                'INSERT OR REPLACE INTO daily_sales '
                                '(date, item_name, qty_sold, gross_revenue, source) '
                                'VALUES (?, ?, ?, ?, ?)',
                                (sale_date_r, item_name, qty, rev, 'pdf_upload')
                            )
                            saved += 1
                        except Exception as insert_err:
                            print(f'[main] PDF upload insert error: {insert_err}')

            yield event(
                'done',
                f'\u2705 Done! {saved} sales records added for {sale_date}.',
                100,
                saved,
            )

        except Exception as exc:
            import traceback
            print(f'[main] PDF upload error: {traceback.format_exc()}')
            yield event('error', f'\u274c Error: {exc}', 0)

    return StreamingResponse(
        generate(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


app.include_router(sales_router)


# ═══════════════════════════════════════════════════════════════════════════════
# RECOMMENDATIONS ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

rec_router = APIRouter(prefix='/recommendations', tags=['Recommendations'])


class OverrideEntry(BaseModel):
    date:         str
    item_name:    str
    merchant_qty: int
    reason:       Optional[str] = None


@rec_router.get('/')
def get_recommendations(date_param: Optional[str] = Query(default=None, alias='date')):
    """
    Generate AI recommendations for a given date.
    Defaults to tomorrow if no date provided.
    """
    if date_param is None:
        date_param = (datetime.now(IST) + timedelta(days=1)).strftime('%Y-%m-%d')

    if RECS_AVAILABLE:
        try:
            recs = generate_recommendations(date_param)
            # Save to DB so accuracy back-test can compare vs actual sales
            try:
                save_recommendations(date_param, recs)
            except Exception as _save_err:
                print(f'[main] Could not save recommendations: {_save_err}')
            return {'date': date_param, 'recommendations': recs, 'count': len(recs)}
        except Exception as e:
            print(f'[main] Recommendation error: {e}')

    return {
        'date': date_param,
        'recommendations': [],
        'count': 0,
        'error': 'Recommendation engine unavailable',
    }


@rec_router.get('/context')
def get_context(date_param: Optional[str] = Query(default=None, alias='date')):
    """
    Return weather + festival context for a date.
    Useful for displaying context cards on the frontend.
    """
    if date_param is None:
        date_param = (datetime.now(IST) + timedelta(days=1)).strftime('%Y-%m-%d')

    if RECS_AVAILABLE:
        try:
            ctx = get_recommendation_context(date_param)
            return ctx
        except Exception as e:
            print(f'[main] Context error: {e}')

    return {'date': date_param, 'error': 'Context engine unavailable'}


@rec_router.put('/override')
def save_override(entry: OverrideEntry):
    """
    Save merchant's manual quantity override for a recommendation.
    Updates the recommendations table's merchant_override column.
    """
    with get_db_connection() as conn:
        # Check if recommendation row exists
        existing = conn.execute(
            'SELECT id FROM recommendations WHERE date = ? AND item_name = ?',
            (entry.date, entry.item_name)
        ).fetchone()

        if existing:
            conn.execute(
                'UPDATE recommendations SET merchant_override = ?, reason = COALESCE(?, reason) '
                'WHERE date = ? AND item_name = ?',
                (entry.merchant_qty, entry.reason, entry.date, entry.item_name)
            )
        else:
            conn.execute(
                'INSERT INTO recommendations (date, item_name, merchant_override, reason) '
                'VALUES (?, ?, ?, ?)',
                (entry.date, entry.item_name, entry.merchant_qty, entry.reason)
            )

    return {'status': 'saved', 'date': entry.date, 'item_name': entry.item_name}


@rec_router.get('/accuracy')
def recommendation_accuracy(days: int = Query(default=14, ge=1, le=90)):
    """
    Back-test: compare past recommendations vs actual sales.
    Returns per-item MAE, daily MAE trend, and overall accuracy percentage.
    """
    cutoff = (get_today() - timedelta(days=days)).isoformat()
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                r.date,
                r.item_name,
                r.recommended_qty,
                COALESCE(r.merchant_override, r.recommended_qty) AS final_qty,
                COALESCE(s.actual_qty, 0) AS actual_qty
            FROM recommendations r
            INNER JOIN (
                SELECT DISTINCT date FROM daily_sales
            ) ds_dates ON r.date = ds_dates.date
            LEFT JOIN (
                SELECT date, item_name, SUM(qty_sold) AS actual_qty
                FROM daily_sales
                GROUP BY date, item_name
            ) s ON r.date = s.date AND r.item_name = s.item_name
            WHERE r.date >= ? AND r.recommended_qty IS NOT NULL
            ORDER BY r.date DESC, r.item_name
            """,
            (cutoff,)
        ).fetchall()

    if not rows:
        return {
            'days': days,
            'message': 'No recommendation data found',
            'overall_mae': 0.0,
            'overall_accuracy': 100.0,
            'total_records': 0,
            'items': [],
            'daily_series': []
        }

    total_error   = 0.0
    total_records = 0
    total_actual  = 0.0
    item_stats: dict = {}
    daily_stats: dict = {}

    for r in rows:
        recommended = float(r[2] or 0)
        final_qty   = float(r[3] or 0)
        actual      = float(r[4] or 0)
        error       = abs(final_qty - actual)

        total_error   += error
        total_actual  += actual
        total_records += 1

        name = r[1]
        if name not in item_stats:
            item_stats[name] = {'total_error': 0, 'count': 0}
        item_stats[name]['total_error'] += error
        item_stats[name]['count']       += 1

        dt = r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0])
        if dt not in daily_stats:
            daily_stats[dt] = {'total_error': 0.0, 'count': 0}
        daily_stats[dt]['total_error'] += error
        daily_stats[dt]['count']       += 1

    overall_mae = round(total_error / total_records, 2) if total_records else 0.0
    avg_actual  = total_actual / total_records if total_records else 1.0
    
    # Accuracy percentage based on mean absolute percentage error (scaled relative to average order size)
    overall_accuracy = round(max(0.0, 100.0 - (overall_mae / (avg_actual if avg_actual > 0 else 1.0) * 100.0)), 1)

    item_results = sorted(
        [
            {
                'item_name': name,
                'mae':       round(s['total_error'] / s['count'], 2),
                'records':   s['count'],
            }
            for name, s in item_stats.items()
        ],
        key=lambda x: -x['mae'],
    )

    daily_results = sorted(
        [
            {
                'date': dt,
                'mae':  round(s['total_error'] / s['count'], 2),
            }
            for dt, s in daily_stats.items()
        ],
        key=lambda x: x['date'],
    )

    return {
        'days':             days,
        'overall_mae':      overall_mae,
        'overall_accuracy': overall_accuracy,
        'total_records':    total_records,
        'items':            item_results,
        'daily_series':     daily_results,
    }


app.include_router(rec_router)


# ═══════════════════════════════════════════════════════════════════════════════
# SETTINGS ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

settings_router = APIRouter(prefix='/settings', tags=['Settings'])


class SettingsUpdate(BaseModel):
    openweather_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@settings_router.get('/')
def get_settings():
    """Return current settings and DB stats."""
    cfg  = _load_config()
    try:
        with get_db_connection() as conn:
            rows  = conn.execute('SELECT COUNT(*) FROM daily_sales').fetchone()[0]
            items = conn.execute('SELECT COUNT(*) FROM menu_items').fetchone()[0]
            dates = conn.execute('SELECT MIN(date), MAX(date) FROM daily_sales').fetchone()
            
            d_min = dates[0].isoformat() if hasattr(dates[0], 'isoformat') else str(dates[0]) if dates and dates[0] else None
            d_max = dates[1].isoformat() if hasattr(dates[1], 'isoformat') else str(dates[1]) if dates and dates[1] else None
            date_range = f'{d_min} to {d_max}' if d_min and d_max else 'N/A'
    except Exception:
        rows, items, date_range = 0, 0, 'N/A'

    mi = get_model_info()

    return {
        # Check env var first (persistent), then runtime in-memory store (session, set via Settings UI)
        'openweather_key_set': bool(os.getenv("WEATHER_API_KEY", "").strip() or os.environ.get('OPENWEATHER_API_KEY', '').strip() or _RUNTIME_KEYS.get('openweather_api_key', '')),
        'gemini_key_set':      bool(os.getenv("GEMINI_API_KEY", "").strip() or os.environ.get('GEMINI_API_KEY', '').strip() or _RUNTIME_KEYS.get('gemini_api_key', '')),
        'latitude':            cfg.get('latitude', None),
        'longitude':           cfg.get('longitude', None),
        'weather_source':      cfg.get('weather_source', 'mock'),
        'model_info':          mi,
        'db_stats': {
            'total_rows':  rows,
            'menu_items':  items,
            'date_range':  date_range,
        },
        'last_cron_ping':      cfg.get('last_cron_ping', None),
        'last_retrain_time':   cfg.get('last_retrain_time', None),
        'last_retrain_status': cfg.get('last_retrain_status', None),
        'last_retrain_error':  cfg.get('last_retrain_error', None),
    }


@settings_router.post('/')
def save_settings(body: SettingsUpdate):
    """Save API key and related settings. When location changes, clears weather cache and re-fetches."""
    updates: dict = {}
    location_changed = False

    if body.openweather_api_key is not None:
        updates['openweather_api_key'] = body.openweather_api_key
        updates['weather_source'] = 'real' if body.openweather_api_key.strip() else 'mock'

        # Update weather service API key in memory
        try:
            from engine import weather_service
            weather_service.update_api_key(body.openweather_api_key)
        except Exception as e:
            print(f'[main] Could not update weather_service API key: {e}')

    if body.gemini_api_key is not None:
        updates['gemini_api_key'] = body.gemini_api_key
        # Store in session memory (lost on restart — set GEMINI_API_KEY env var on Render for persistence)
        _RUNTIME_KEYS['gemini_api_key'] = body.gemini_api_key.strip()
        # Reset model index so we start fresh with the new key
        try:
            from engine.gemini_ocr import reset_model_index
            reset_model_index()
        except Exception as e:
            print(f'[main] Could not reset gemini model index: {e}')

    if body.latitude is not None:
        updates['latitude'] = body.latitude
        location_changed = True

    if body.longitude is not None:
        updates['longitude'] = body.longitude
        location_changed = True

    if updates:
        _save_config(updates)

    # If location changed, clear weather cache and re-fetch in background
    weather_refresh_result = None
    if location_changed:
        try:
            from engine import weather_service
            # Run synchronously (fast — just 1-2 API calls)
            weather_refresh_result = weather_service.refresh_weather_for_location()
            # Also clear the dashboard cache key so next load gets fresh weather
            print(f'[main] Location updated → weather refreshed: {weather_refresh_result}')
        except Exception as e:
            print(f'[main] Could not refresh weather after location change: {e}')

    resp = {'status': 'saved', 'updates': list(updates.keys())}
    if weather_refresh_result:
        resp['weather_refreshed'] = True
        resp['new_weather'] = weather_refresh_result.get('today')
    return resp


@settings_router.post('/refresh-weather')
def refresh_weather():
    """
    Clear weather cache for today+future and re-fetch using current config coordinates.
    Called by the frontend "Refresh Weather" button in Settings.
    """
    try:
        from engine.weather_service import refresh_weather_for_location
        result = refresh_weather_for_location()
        return {
            'status':         'refreshed',
            'cleared_rows':   result.get('cleared_rows', 0),
            'today_weather':  result.get('today'),
            'tomorrow_weather': result.get('tomorrow'),
            'lat':            result.get('lat'),
            'lon':            result.get('lon'),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Weather refresh failed: {e}')


@settings_router.get('/gemini-model')
def get_gemini_model():
    """Return the currently active Gemini model name."""
    try:
        from engine.gemini_ocr import get_current_model, APP_STATE
        return {'current_model': get_current_model(), 'model_idx': APP_STATE['model_idx']}
    except Exception as e:
        return {'current_model': 'unknown', 'error': str(e)}


@settings_router.post('/retrain')
def retrain_model(source: Optional[str] = None):
    """Trigger a background model retrain."""
    if not ML_AVAILABLE:
        raise HTTPException(status_code=400, detail='ML engine not available')

    if source == 'cron' or source == 'cron-job.org':
        try:
            cfg = _load_config()
            cfg['last_cron_ping'] = datetime.now().isoformat()
            _save_config(cfg)
            print(f'[main] Cron retrain ping logged from source: {source}')
        except Exception as e:
            print(f'[main] Failed to log cron ping: {e}')

    def _do_train():
        try:
            info = _ml_train()
            print(f'[main] Model retrained successfully: {info}')
            try:
                cfg = _load_config()
                cfg['last_retrain_time'] = datetime.now().isoformat()
                cfg['last_retrain_status'] = 'success'
                cfg['last_retrain_error'] = None
                _save_config(cfg)
            except Exception as ce:
                print(f'[main] Failed to save success stats: {ce}')
        except Exception as e:
            print(f'[main] Training failed: {e}')
            try:
                cfg = _load_config()
                cfg['last_retrain_time'] = datetime.now().isoformat()
                cfg['last_retrain_status'] = 'failed'
                cfg['last_retrain_error'] = str(e)
                _save_config(cfg)
            except Exception as ce:
                print(f'[main] Failed to save fail stats: {ce}')

    threading.Thread(target=_do_train, daemon=True).start()
    return {
        'status':  'training_started',
        'message': 'Model retraining started in background (~30s). Check /settings for completion.',
    }


app.include_router(settings_router)


# ═══════════════════════════════════════════════════════════════════════════════
# WEATHER ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

weather_router = APIRouter(prefix='/weather', tags=['Weather'])


@weather_router.get('/')
def get_weather(date_param: Optional[str] = Query(default=None, alias='date')):
    """
    Get weather for a specific date.
    If not cached, fetches/generates it.
    """
    if date_param is None:
        date_param = (datetime.now(IST) + timedelta(days=1)).strftime('%Y-%m-%d')

    try:
        from engine.weather_service import get_weather_for_date, fetch_and_store_weather
        weather = get_weather_for_date(date_param)
        if weather is None:
            weather = fetch_and_store_weather(date_param)
        return {'date': date_param, 'weather': weather}
    except Exception as e:
        return {'date': date_param, 'weather': None, 'error': str(e)}


@weather_router.get('/all')
def get_all_weather():
    """Return all cached weather records."""
    try:
        from engine.weather_service import get_all_weather
        return {'records': get_all_weather()}
    except Exception as e:
        return {'records': [], 'error': str(e)}


app.include_router(weather_router)


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOM EVENTS ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

events_router = APIRouter(prefix='/events', tags=['Custom Events'])

CUSTOM_EVENTS_PATH = os.path.join(_HERE, '..', 'data', 'custom_events.json')

def _load_custom_events() -> list:
    try:
        if os.path.exists(CUSTOM_EVENTS_PATH):
            with open(CUSTOM_EVENTS_PATH, encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return []

def _save_custom_events(events: list) -> None:
    os.makedirs(os.path.dirname(CUSTOM_EVENTS_PATH), exist_ok=True)
    with open(CUSTOM_EVENTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

@events_router.get('/')
def get_custom_events():
    """Get all custom local events."""
    return {'events': _load_custom_events()}

class CustomEvent(BaseModel):
    name: str
    date_from: str
    date_to: str
    demand_multiplier: float = 1.3

@events_router.post('/')
def add_custom_event(event: CustomEvent):
    """Add a custom local event."""
    events = _load_custom_events()
    entry = {
        'name': event.name,
        'date_from': event.date_from,
        'date_to': event.date_to,
        'demand_multiplier': event.demand_multiplier,
        'id': len(events),
    }
    events.append(entry)
    _save_custom_events(events)
    # Reload festival service to include new event dates
    try:
        from engine.festival_service import reload_festivals
        reload_festivals()
    except Exception:
        pass
    return {'ok': True, 'event': entry}

@events_router.delete('/{event_id}')
def delete_custom_event(event_id: int):
    """Delete a custom event by ID."""
    events = _load_custom_events()
    if 0 <= event_id < len(events):
        events.pop(event_id)
        # Re-index IDs
        for i, e in enumerate(events):
            e['id'] = i
        _save_custom_events(events)
        # Reload festival service to reflect deletion
        try:
            from engine.festival_service import reload_festivals
            reload_festivals()
        except Exception:
            pass
        return {'ok': True}
    return {'ok': False, 'error': 'Event not found'}

app.include_router(events_router)


# ═══════════════════════════════════════════════════════════════════════════════
# FRONTEND STATIC FILES
# ═══════════════════════════════════════════════════════════════════════════════

if os.path.isdir(FRONTEND):
    _css = os.path.join(FRONTEND, 'css')
    _js  = os.path.join(FRONTEND, 'js')
    _img = os.path.join(FRONTEND, 'images')

    if os.path.isdir(_css):
        app.mount('/css',    StaticFiles(directory=_css), name='css')
    if os.path.isdir(_js):
        app.mount('/js',     StaticFiles(directory=_js),  name='js')
    if os.path.isdir(_img):
        app.mount('/images', StaticFiles(directory=_img), name='images')

    @app.get('/', include_in_schema=False)
    def serve_index():
        return FileResponse(os.path.join(FRONTEND, 'index.html'))

    @app.get('/{path:path}', include_in_schema=False)
    def serve_spa(path: str):
        # Try exact file first
        full_path = os.path.join(FRONTEND, path)
        if os.path.isfile(full_path):
            return FileResponse(full_path)
        # Fall back to SPA index
        return FileResponse(os.path.join(FRONTEND, 'index.html'))
