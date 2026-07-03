"""
feature_builder.py — Hotel Aditya Grand AI Engine
====================================================
Builds feature matrices for the LightGBM recommendation model.

FEATURE_COLS (16 features):
    day_of_week, is_weekend, month,
    max_temp, min_temp, rainfall_mm, is_rainy, is_hot,
    is_festival, festival_multiplier,
    item_label, category_label,
    lag_1, lag_7, rolling_7_median, rolling_14_mean, trend_slope
"""

import os
import sqlite3
import json
import joblib
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, date
from typing import Optional, List, Tuple

def _norm_date(val) -> str:
    if val is None:
        return ''
    if hasattr(val, 'strftime'):
        return val.strftime('%Y-%m-%d')
    return str(val)

# ── Paths ──────────────────────────────────────────────────────────────────────
# __file__ is backend/engine/feature_builder.py  →  .. is backend/
DB_PATH = os.environ.get(
    'DB_PATH',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'hotel_aditya.db')
)

# ── Feature column order (must match model training) ──────────────────────────
FEATURE_COLS = [
    'day_of_week',       # 0=Monday … 6=Sunday
    'is_weekend',        # 1 if Sat/Sun
    'month',             # 1–12
    'max_temp',          # °C
    'min_temp',          # °C
    'rainfall_mm',       # mm
    'is_rainy',          # 1 if rainfall_mm > 2
    'is_hot',            # 1 if max_temp >= 38
    'is_festival',       # 1 if a festival falls on this date
    'festival_multiplier',  # e.g. 1.7, 1.0 if no festival
    'item_label',        # integer-encoded item_name
    'category_label',    # integer-encoded category
    'lag_1',             # qty_sold yesterday (0 if missing)
    'lag_7',             # qty_sold same weekday last week
    'rolling_7_median',  # median of last 7 appearances
    'rolling_14_mean',   # mean of last 14 appearances
    'trend_slope',       # (avg last 2 days) / (avg days 3-5) - 1.0, capped [-0.3, 0.3]
]


class FeatureBuilder:
    """
    Encodes items/categories and builds feature rows for training / inference.
    Must call fit_encoders(conn) before using build_* methods.
    """

    def __init__(self):
        self.item_encoder: dict  = {}   # item_name  -> int label
        self.cat_encoder:  dict  = {}   # category   -> int label
        self.item_decoder: dict  = {}   # int label  -> item_name
        self.cat_decoder:  dict  = {}   # int label  -> category
        self.fitted: bool        = False

    # ── Encoder helpers ────────────────────────────────────────────────────────

    def fit_encoders(self, conn: sqlite3.Connection) -> None:
        """
        Build label encoders from the menu_items table.
        Must be called once before building features.
        """
        rows = conn.execute(
            'SELECT DISTINCT item_name, category FROM menu_items ORDER BY item_name'
        ).fetchall()

        # Also ensure any items that appear only in daily_sales get encoded
        sales_items = conn.execute(
            'SELECT DISTINCT item_name FROM daily_sales ORDER BY item_name'
        ).fetchall()

        all_items = set()
        cats_per_item: dict = {}

        for r in rows:
            name = r[0]
            cat  = r[1] or 'other'
            all_items.add(name)
            cats_per_item[name] = cat

        for r in sales_items:
            name = r[0]
            if name not in all_items:
                all_items.add(name)
                cats_per_item[name] = 'other'

        sorted_items = sorted(all_items)
        all_cats     = sorted(set(cats_per_item.values()))

        self.item_encoder = {name: i for i, name in enumerate(sorted_items)}
        self.item_decoder = {i: name for name, i in self.item_encoder.items()}
        self.cat_encoder  = {cat: i for i, cat in enumerate(all_cats)}
        self.cat_decoder  = {i: cat for cat, i in self.cat_encoder.items()}

        self.item_cats = cats_per_item   # item_name -> category (for convenience)
        self.fitted = True
        print(f'[FeatureBuilder] Fitted: {len(self.item_encoder)} items, '
              f'{len(self.cat_encoder)} categories.')

    def _encode_item(self, item_name: str) -> int:
        return self.item_encoder.get(item_name, -1)

    def _encode_cat(self, category: str) -> int:
        return self.cat_encoder.get(category or 'other', 0)

    # ── Festival lookup ────────────────────────────────────────────────────────

    def _get_festival_info(self, date_str: str) -> Tuple[int, float]:
        """
        Returns (is_festival: 0|1, multiplier: float).
        Uses engine.festival_service if available, else returns (0, 1.0).
        """
        try:
            from engine.festival_service import get_festival_multiplier
            mult, name = get_festival_multiplier(date_str)
            return (1 if name else 0), mult
        except Exception:
            return 0, 1.0

    # ── Weather lookup ─────────────────────────────────────────────────────────

    def _get_weather_for_date(self, date_str: str, conn: sqlite3.Connection) -> dict:
        """
        Return weather dict from DB, or sensible Kandukur defaults.
        """
        try:
            row = conn.execute(
                'SELECT max_temp, min_temp, rainfall_mm, condition '
                'FROM weather_data WHERE date = ?', (date_str,)
            ).fetchone()
            if row:
                return {
                    'max_temp':    row[0] or 38.0,
                    'min_temp':    row[1] or 28.0,
                    'rainfall_mm': row[2] or 0.0,
                    'condition':   row[3] or 'Sunny',
                }
        except Exception:
            pass
        # Defaults: typical Kandukur April-May day
        return {'max_temp': 38.0, 'min_temp': 28.0, 'rainfall_mm': 0.0, 'condition': 'Sunny'}

    # ── Lag features ───────────────────────────────────────────────────────────

    def _compute_lags(self, item_name: str, before_date: str,
                      conn: sqlite3.Connection) -> dict:
        """
        Compute all lag / rolling features for an item relative to before_date.

        Returns dict with keys:
            lag_1, lag_7, rolling_7_median, rolling_14_mean, trend_slope
        """
        before_dt = datetime.strptime(before_date, '%Y-%m-%d')

        def _date_str(delta_days: int) -> str:
            return (before_dt - timedelta(days=delta_days)).strftime('%Y-%m-%d')

        history_cache = getattr(self, '_history_cache', None)
        sales_map_cache = getattr(self, '_sales_map_cache', None)

        if history_cache is not None and sales_map_cache is not None:
            # 1. lag_1
            lag_1_val = sales_map_cache.get((item_name, _date_str(1)))
            lag_1 = lag_1_val if lag_1_val is not None else 0.0

            # 2. lag_7
            lag_7_val = sales_map_cache.get((item_name, _date_str(7)))
            lag_7 = lag_7_val if lag_7_val is not None else 0.0

            # 3. rolling averages
            history = history_cache.get(item_name, [])
            vals_desc = [qty for d, qty in history if d < before_date][::-1]

            vals14 = vals_desc[:14]
            rolling_14_mean   = float(np.mean(vals14))   if vals14 else 0.0
            rolling_7_median  = float(np.median(vals14[:7])) if vals14 else 0.0

            # 4. trend slope
            vals5 = vals_desc[:5]
            if len(vals5) >= 4:
                recent_avg = float(np.mean(vals5[:2]))     # last 2
                older_avg  = float(np.mean(vals5[2:5]))    # days 3-5
                if older_avg > 0:
                    slope = (recent_avg / older_avg) - 1.0
                else:
                    slope = 0.0
                trend_slope = float(np.clip(slope, -0.3, 0.3))
            else:
                trend_slope = 0.0

            return {
                'lag_1':            lag_1,
                'lag_7':            lag_7,
                'rolling_7_median': rolling_7_median,
                'rolling_14_mean':  rolling_14_mean,
                'trend_slope':      trend_slope,
            }

        # Fallback to slow sequential database queries
        def _qty_on(date_s: str) -> Optional[float]:
            row = conn.execute(
                'SELECT SUM(qty_sold) FROM daily_sales '
                'WHERE item_name = ? AND date = ?',
                (item_name, date_s)
            ).fetchone()
            v = row[0] if row else None
            return float(v) if v is not None else None

        # lag_1: qty sold the day immediately before target_date
        lag_1_val = _qty_on(_date_str(1))
        lag_1 = lag_1_val if lag_1_val is not None else 0.0

        # lag_7: qty sold 7 days before
        lag_7_val = _qty_on(_date_str(7))
        lag_7 = lag_7_val if lag_7_val is not None else 0.0

        # Rolling: last 14 appearances before before_date (by date desc)
        rows14 = conn.execute(
            'SELECT qty_sold FROM daily_sales '
            'WHERE item_name = ? AND date < ? '
            'ORDER BY date DESC LIMIT 14',
            (item_name, before_date)
        ).fetchall()
        vals14 = [float(r[0]) for r in rows14 if r[0] is not None]

        rolling_14_mean   = float(np.mean(vals14))   if vals14 else 0.0
        rolling_7_median  = float(np.median(vals14[:7])) if vals14 else 0.0

        # trend_slope: (avg of last 2 days) / (avg of days 3-5) - 1.0
        rows5 = conn.execute(
            'SELECT qty_sold FROM daily_sales '
            'WHERE item_name = ? AND date < ? '
            'ORDER BY date DESC LIMIT 5',
            (item_name, before_date)
        ).fetchall()
        vals5 = [float(r[0]) for r in rows5 if r[0] is not None]

        if len(vals5) >= 4:
            recent_avg = float(np.mean(vals5[:2]))     # last 2
            older_avg  = float(np.mean(vals5[2:5]))    # days 3-5
            if older_avg > 0:
                slope = (recent_avg / older_avg) - 1.0
            else:
                slope = 0.0
            trend_slope = float(np.clip(slope, -0.3, 0.3))
        else:
            trend_slope = 0.0

        return {
            'lag_1':            lag_1,
            'lag_7':            lag_7,
            'rolling_7_median': rolling_7_median,
            'rolling_14_mean':  rolling_14_mean,
            'trend_slope':      trend_slope,
        }

    # ── Active items ───────────────────────────────────────────────────────────

    def _get_active_items(self, before_date: str, conn: sqlite3.Connection,
                          min_days: int = 5) -> List[str]:
        """
        Return items that have at least min_days of sales records before before_date.
        """
        rows = conn.execute(
            """
            SELECT item_name
            FROM daily_sales
            WHERE date < ?
            GROUP BY item_name
            HAVING COUNT(DISTINCT date) >= ?
            ORDER BY item_name
            """,
            (before_date, min_days)
        ).fetchall()
        return [r[0] for r in rows]

    # ── Date-level feature helpers ─────────────────────────────────────────────

    def _date_features(self, date_str: str) -> dict:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        dow = dt.weekday()   # 0=Monday … 6=Sunday
        return {
            'day_of_week': dow,
            'is_weekend':  int(dow >= 5),
            'month':       dt.month,
        }

    def _weather_features(self, weather: dict) -> dict:
        max_t = float(weather.get('max_temp',    38.0))
        min_t = float(weather.get('min_temp',    28.0))
        rain  = float(weather.get('rainfall_mm',  0.0))
        return {
            'max_temp':    max_t,
            'min_temp':    min_t,
            'rainfall_mm': rain,
            'is_rainy':    int(rain > 2.0),
            'is_hot':      int(max_t >= 38.0),
        }

    # ── Training features ──────────────────────────────────────────────────────

    def build_training_features(self, conn: sqlite3.Connection) -> pd.DataFrame:
        """
        Read ALL daily_sales rows and join with weather_data + festivals.
        Returns a DataFrame with FEATURE_COLS + 'qty_sold' (target).
        Each row represents one (date, item_name) observation.
        """
        if not self.fitted:
            self.fit_encoders(conn)

        # Pull all sales with category
        raw_sales = conn.execute("""
            SELECT
                ds.date,
                ds.item_name,
                SUM(ds.qty_sold) AS qty_sold,
                COALESCE(mi.category, 'other') AS category
            FROM daily_sales ds
            LEFT JOIN menu_items mi ON ds.item_name = mi.item_name
            GROUP BY ds.date, ds.item_name, mi.category
            ORDER BY ds.date ASC
        """).fetchall()
        sales_rows = [(_norm_date(r[0]), r[1], r[2], r[3]) for r in raw_sales]

        if not sales_rows:
            return pd.DataFrame()

        all_dates = sorted(set(r[0] for r in sales_rows))

        # Pre-fetch all sales history to avoid N+1 query latency on remote DB during training
        item_history = {}
        sales_map = {}
        raw_history = conn.execute(
            'SELECT item_name, date, SUM(qty_sold) FROM daily_sales GROUP BY item_name, date ORDER BY date ASC'
        ).fetchall()
        history_rows = [(r[0], _norm_date(r[1]), float(r[2]) if r[2] is not None else 0.0) for r in raw_history]
        for r in history_rows:
            i_name, d_str, qty = r[0], r[1], r[2]
            if i_name not in item_history:
                item_history[i_name] = []
            item_history[i_name].append((d_str, qty))
            sales_map[(i_name, d_str)] = qty

        self._history_cache = item_history
        self._sales_map_cache = sales_map

        # Pre-fetch all weather into a dict
        weather_map: dict = {}
        for r in conn.execute('SELECT * FROM weather_data').fetchall():
            weather_map[_norm_date(r[0])] = dict(r)

        records = []
        for row in sales_rows:
            date_str  = row[0]
            item_name = row[1]
            qty_sold  = float(row[2]) if row[2] is not None else 0.0
            category  = row[3] or 'other'

            df_feats  = self._date_features(date_str)
            weather   = weather_map.get(date_str) or self._get_weather_for_date(date_str, conn)
            wf_feats  = self._weather_features(weather)
            is_fest, mult = self._get_festival_info(date_str)
            lags      = self._compute_lags(item_name, date_str, conn)

            record = {
                **df_feats,
                **wf_feats,
                'is_festival':        is_fest,
                'festival_multiplier': mult,
                'item_label':         self._encode_item(item_name),
                'category_label':     self._encode_cat(category),
                **lags,
                'qty_sold':           qty_sold,
            }
            records.append(record)

        df = pd.DataFrame(records, columns=FEATURE_COLS + ['qty_sold'])
        print(f'[FeatureBuilder] Built training set: {len(df)} rows.')
        return df

    # ── Prediction features ────────────────────────────────────────────────────

    def build_prediction_features(self, target_date: str,
                                  conn: sqlite3.Connection) -> pd.DataFrame:
        """
        For each active item (>= 5 days history before target_date),
        build one prediction row using lag features from data before target_date.

        Returns DataFrame with FEATURE_COLS (no qty_sold column).
        Index is item_name for easy lookup after prediction.
        """
        if not self.fitted:
            self.fit_encoders(conn)

        active_items = self._get_active_items(target_date, conn, min_days=5)
        if not active_items:
            return pd.DataFrame(columns=FEATURE_COLS)

        df_feats  = self._date_features(target_date)
        weather   = self._get_weather_for_date(target_date, conn)
        wf_feats  = self._weather_features(weather)
        is_fest, mult = self._get_festival_info(target_date)

        # Also fetch category map from menu_items
        cat_map: dict = {}
        for r in conn.execute('SELECT item_name, category FROM menu_items').fetchall():
            cat_map[r[0]] = r[1] or 'other'

        # Pre-fetch all sales history to avoid N+1 query latency on remote DB
        item_history = {}
        sales_map = {}
        raw_history = conn.execute(
            'SELECT item_name, date, SUM(qty_sold) FROM daily_sales GROUP BY item_name, date ORDER BY date ASC'
        ).fetchall()
        history_rows = [(r[0], _norm_date(r[1]), float(r[2]) if r[2] is not None else 0.0) for r in raw_history]
        for r in history_rows:
            i_name, d_str, qty = r[0], r[1], r[2]
            if i_name not in item_history:
                item_history[i_name] = []
            item_history[i_name].append((d_str, qty))
            sales_map[(i_name, d_str)] = qty

        self._history_cache = item_history
        self._sales_map_cache = sales_map

        records = []
        item_names = []
        for item_name in active_items:
            category = cat_map.get(item_name) or getattr(self, 'item_cats', {}).get(item_name, 'other')
            lags     = self._compute_lags(item_name, target_date, conn)

            record = {
                **df_feats,
                **wf_feats,
                'is_festival':        is_fest,
                'festival_multiplier': mult,
                'item_label':         self._encode_item(item_name),
                'category_label':     self._encode_cat(category),
                **lags,
            }
            records.append(record)
            item_names.append(item_name)

        df = pd.DataFrame(records, columns=FEATURE_COLS)
        df.index = item_names
        return df

    # ── Serialisation ──────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Persist this FeatureBuilder to disk using joblib."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump(self, path)
        print(f'[FeatureBuilder] Saved to {path}')

    @staticmethod
    def load(path: str) -> 'FeatureBuilder':
        """Load a FeatureBuilder from disk. Returns the loaded instance."""
        instance = joblib.load(path)
        print(f'[FeatureBuilder] Loaded from {path}')
        return instance
