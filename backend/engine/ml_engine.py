"""
ml_engine.py — LightGBM-based recommendation engine
======================================================
Single global model trained on all items.
Falls back to rule-based predictions for items with < 5 days of history.

Training flow:
  1. FeatureBuilder.fit_encoders(conn)
  2. FeatureBuilder.build_training_features(conn) -> DataFrame
  3. lgb.LGBMRegressor(**params).fit(X, y)
  4. Save model + feature_builder + model_info.json

Prediction flow:
  1. load_or_train() -> (model, feature_builder)
  2. FeatureBuilder.build_prediction_features(target_date, conn)
  3. model.predict(X) -> raw quantities
  4. Apply buffer + cap
  5. Generate human-readable reasons
"""

import os
import json
import sqlite3
import joblib
import sys
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.append(_BACKEND)
from database import get_db_connection
from datetime import datetime, timedelta
from math import ceil
from typing import Optional, List, Tuple

import numpy as np

try:
    import lightgbm as lgb
    LGBM_AVAILABLE = True
except ImportError:
    LGBM_AVAILABLE = False
    print('[ml_engine] LightGBM not installed — will use rule-based fallback.')

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print('[ml_engine] pandas not installed — ML engine disabled.')

from engine.feature_builder import FeatureBuilder, FEATURE_COLS
from engine.category_weather_map import compute_weather_factor
from engine.festival_service import get_festival_multiplier, get_upcoming_festivals
from engine.weather_service import get_weather_for_date, fetch_and_store_weather

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE          = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR     = os.path.join(_HERE, '..', 'models')
LGBM_MODEL_PATH = os.path.join(MODELS_DIR, 'lgbm_model.pkl')
FB_PATH        = os.path.join(MODELS_DIR, 'feature_builder.pkl')
MODEL_INFO_PATH = os.path.join(MODELS_DIR, 'model_info.json')
DB_PATH        = os.environ.get(
    'DB_PATH',
    os.path.join(_HERE, '..', 'hotel_aditya.db')   # _HERE = backend/engine/  →  .. = backend/
)
CONFIG_PATH = os.environ.get('CONFIG_PATH')
if not CONFIG_PATH:
    if os.path.dirname(DB_PATH) and os.path.dirname(DB_PATH) != os.path.dirname(os.path.abspath(__file__)):
        CONFIG_PATH = os.path.join(os.path.dirname(DB_PATH), 'config.json')
    else:
        CONFIG_PATH = os.path.join(_HERE, '..', 'config.json')

os.makedirs(MODELS_DIR, exist_ok=True)

# ── Day name map ───────────────────────────────────────────────────────────────
DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


# ── DB connection ──────────────────────────────────────────────────────────────

def _conn():
    return get_db_connection()


# ── Model info ─────────────────────────────────────────────────────────────────

def get_model_info() -> dict:
    """Read model_info.json or return a default dict."""
    try:
        with open(MODEL_INFO_PATH, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            'model_type':    'not_trained',
            'trained_at':    None,
            'mae':           None,
            'n_items':       0,
            'training_rows': 0,
        }
    except Exception as e:
        print(f'[ml_engine] Error reading model_info.json: {e}')
        return {'model_type': 'error', 'trained_at': None}


def _save_model_info(info: dict) -> None:
    with open(MODEL_INFO_PATH, 'w', encoding='utf-8') as f:
        json.dump(info, f, indent=2, default=str)


# ── Training ───────────────────────────────────────────────────────────────────

def train_model() -> dict:
    """
    Train a LightGBM regression model on all historical sales data.

    Returns:
        dict with keys: trained_at, mae, n_items, model_type, training_rows
    """
    if not LGBM_AVAILABLE or not PANDAS_AVAILABLE:
        raise RuntimeError('LightGBM or pandas not available — cannot train model.')

    print('[ml_engine] Starting model training...')
    with get_db_connection() as connection:
        fb = FeatureBuilder()
        fb.fit_encoders(connection)

        print('[ml_engine] Building training features (this may take ~15-30s for 3500 rows)...')
        df = fb.build_training_features(connection)

    if df.empty:
        raise ValueError('No training data available in daily_sales table.')

    # Drop rows where target is 0 (no-sale rows don't help the model)
    df = df[df['qty_sold'] > 0].copy()
    if df.empty:
        raise ValueError('All qty_sold values are 0 — cannot train.')

    X = df[FEATURE_COLS].values.astype(np.float32)
    y = df['qty_sold'].values.astype(np.float32)

    # 80/20 time-based split (training on older data, validate on recent)
    split_idx = int(len(X) * 0.8)
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    import gc
    gc.collect()

    params = dict(
        n_estimators=80,       # reduced from 200 to prevent OOM
        learning_rate=0.08,
        max_depth=3,           # reduced from 5 to prevent OOM
        num_leaves=8,          # reduced from 25 to prevent OOM
        min_child_samples=3,
        subsample=0.8,
        colsample_bytree=0.8,
        objective='regression_l1',
        reg_alpha=0.1,
        reg_lambda=0.1,
        random_state=42,
        n_jobs=1,              # limit to single thread to save memory
        verbose=-1,
    )

    model = lgb.LGBMRegressor(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(15, verbose=False), lgb.log_evaluation(period=-1)],
    )

    # Validation MAE
    preds = model.predict(X_val)
    mae = float(np.mean(np.abs(preds - y_val)))

    n_items = int(df['item_label'].nunique())
    trained_at = datetime.now().isoformat()

    # Persist model + feature_builder
    joblib.dump(model, LGBM_MODEL_PATH)
    fb.save(FB_PATH)

    info = {
        'model_type':    'lgbm',
        'trained_at':    trained_at,
        'mae':           round(mae, 3),
        'n_items':       n_items,
        'training_rows': len(df),
    }
    _save_model_info(info)

    # Also update config.json
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg = json.load(f)
        cfg['model_trained_at'] = trained_at
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

    print(f'[ml_engine] Training complete. MAE={mae:.3f}, items={n_items}, rows={len(df)}')
    return info


# ── Load or train ──────────────────────────────────────────────────────────────

def load_or_train() -> Tuple[Optional[object], Optional[FeatureBuilder]]:
    """
    Load existing model + FeatureBuilder from disk.
    Retrains if:
      - Model files are missing, or
      - Model is older than 24 hours.

    Returns:
        (model, feature_builder) or (None, None) if not available.
    """
    if not LGBM_AVAILABLE or not PANDAS_AVAILABLE:
        return None, None

    model_exists = os.path.exists(LGBM_MODEL_PATH) and os.path.exists(FB_PATH)

    if model_exists:
        info = get_model_info()
        trained_at_str = info.get('trained_at')
        if trained_at_str:
            try:
                trained_at = datetime.fromisoformat(trained_at_str)
                age_hours = (datetime.now() - trained_at).total_seconds() / 3600
                if age_hours < 24:
                    model = joblib.load(LGBM_MODEL_PATH)
                    fb    = FeatureBuilder.load(FB_PATH)
                    print(f'[ml_engine] Loaded cached model (age={age_hours:.1f}h).')
                    return model, fb
                else:
                    print(f'[ml_engine] Model is {age_hours:.1f}h old — retraining.')
            except Exception as e:
                print(f'[ml_engine] Could not parse model age: {e}')

    try:
        train_model()
        model = joblib.load(LGBM_MODEL_PATH)
        fb    = FeatureBuilder.load(FB_PATH)
        return model, fb
    except Exception as e:
        print(f'[ml_engine] load_or_train failed: {e}')
        return None, None


# ── Rule-based fallback ────────────────────────────────────────────────────────

def _rule_based_predict(
    item_name: str,
    category:  str,
    target_date: str,
    conn: sqlite3.Connection,
    weather: dict,
    festival_mult: float,
) -> dict:
    """
    Simple rule-based prediction for items with insufficient history.
    Uses 14-day rolling avg × DOW multiplier × weather × festival.
    """
    dow = datetime.strptime(target_date, '%Y-%m-%d').weekday()

    # 14-day rolling average
    rows = conn.execute(
        'SELECT AVG(qty_sold) FROM ('
        '  SELECT qty_sold FROM daily_sales '
        '  WHERE item_name = ? AND date < ? '
        '  ORDER BY date DESC LIMIT 14'
        ')',
        (item_name, target_date)
    ).fetchone()
    base_avg = float(rows[0]) if rows and rows[0] else 1.0

    # DOW multiplier
    sqlite_dow = (dow + 1) % 7
    row_dow = conn.execute(
        'SELECT AVG(qty_sold) FROM daily_sales '
        'WHERE item_name = ? AND CAST(strftime(\'%w\', date) AS INTEGER) = ?',
        (item_name, sqlite_dow)
    ).fetchone()
    row_all = conn.execute(
        'SELECT AVG(qty_sold) FROM daily_sales WHERE item_name = ?',
        (item_name,)
    ).fetchone()
    dow_avg     = float(row_dow[0]) if row_dow and row_dow[0] else base_avg
    overall_avg = float(row_all[0]) if row_all and row_all[0] else 1.0
    dow_factor  = max(0.5, min(2.0, dow_avg / overall_avg)) if overall_avg > 0 else 1.0

    weather_factor = compute_weather_factor(category, weather)
    raw = base_avg * dow_factor * weather_factor * festival_mult
    final_qty = max(1, ceil(raw * 1.10))

    dow_name = DAY_NAMES[dow]
    reasons  = []
    if dow_factor >= 1.10:
        reasons.append(f'↑ {dow_name} peak day')
    elif dow_factor <= 0.90:
        reasons.append(f'↓ Slow {dow_name}')
    if weather_factor >= 1.15:
        reasons.append(f'↑ {weather.get("condition","Hot")} boosts demand')
    elif weather_factor <= 0.85:
        reasons.append(f'↓ {weather.get("condition","Rainy")} reduces demand')
    if festival_mult > 1.0:
        reasons.append('\U0001f389 Festival demand boost')

    reason = ' | '.join(reasons) if reasons else f'Based on {dow_name} average'

    return {
        'item_name':       item_name,
        'category':        category,
        'recommended_qty': final_qty,
        'reason':          reason,
        'base_avg':        round(base_avg, 1),
        'model_used':      'rule_based',
    }


# ── Reason generation ──────────────────────────────────────────────────────────

def _generate_reason(
    row:           dict,
    dow_name:      str,
    weather:       dict,
    festival_name: Optional[str],
    upcoming:      list,
) -> str:
    """
    Build a human-readable reason string for a ML prediction.
    """
    parts: List[str] = []

    # Festival signal (today)
    if festival_name:
        parts.append(f'\U0001f389 {festival_name}')
    elif upcoming:
        nxt = upcoming[0]
        parts.append(f'\U0001f4c5 {nxt["name"]} in {nxt["days_away"]}d')

    # Day-of-week signal
    dow = row.get('day_of_week', 0)
    if dow in (5, 6):   # Saturday / Sunday
        parts.append(f'↑ {dow_name} peak day')

    # Weather signal
    condition    = weather.get('condition', 'Sunny')
    weather_feat = {
        'max_temp':    row.get('max_temp', 38.0),
        'min_temp':    row.get('min_temp', 28.0),
        'rainfall_mm': row.get('rainfall_mm', 0.0),
        'condition':   condition,
    }
    # Compute a representative weather factor (using a neutral category)
    wf = compute_weather_factor('beverage', weather_feat)
    if wf > 1.10:
        parts.append(f'↑ {condition} boosts demand')
    elif wf < 0.90:
        parts.append(f'↓ {condition} reduces demand')

    # Trend signal
    slope = float(row.get('trend_slope', 0.0))
    if slope > 0.08:
        parts.append('↑ Trending up')
    elif slope < -0.08:
        parts.append('↓ Trending down')

    if not parts:
        return 'Based on historical average'
    return ' | '.join(parts)


# ── Main prediction entry point ────────────────────────────────────────────────

def predict_for_date(target_date: str, conn: Optional[object] = None) -> List[dict]:
    """
    Generate order quantity predictions for all active items on target_date.

    Args:
        target_date: 'YYYY-MM-DD'
        conn: optional existing DB connection (will create one if None)

    Returns:
        List of dicts: {item_name, category, recommended_qty, reason, base_avg, model_used}
    """
    if conn is None:
        with get_db_connection() as own_conn:
            return predict_for_date(target_date, own_conn)

    try:
        # ── Weather ────────────────────────────────────────────────────────────
        weather = get_weather_for_date(target_date)
        if weather is None:
            weather = fetch_and_store_weather(target_date)
        if weather is None:
            weather = {'max_temp': 38.0, 'min_temp': 28.0, 'rainfall_mm': 0.0, 'condition': 'Sunny'}

        # ── Festival ───────────────────────────────────────────────────────────
        festival_mult, festival_name = get_festival_multiplier(target_date)
        upcoming = get_upcoming_festivals(target_date, lookahead_days=7)

        # ── Day info ───────────────────────────────────────────────────────────
        target_dt = datetime.strptime(target_date, '%Y-%m-%d')
        dow       = target_dt.weekday()
        dow_name  = DAY_NAMES[dow]

        # ── Try ML model ───────────────────────────────────────────────────────
        model, fb = load_or_train()

        if model is not None and fb is not None and PANDAS_AVAILABLE:
            pred_df = fb.build_prediction_features(target_date, conn)

            if not pred_df.empty:
                X = pred_df[FEATURE_COLS].values.astype(np.float32)
                raw_preds = model.predict(X)

                # Category lookup
                cat_map: dict = {}
                for r in conn.execute('SELECT item_name, category FROM menu_items').fetchall():
                    cat_map[r[0]] = r[1] or 'other'

                # Base avg lookup (14-day rolling for display)
                results: List[dict] = []
                for i, item_name in enumerate(pred_df.index):
                    raw_qty = float(raw_preds[i])
                    raw_qty = max(0.0, raw_qty)

                    # 14-day base avg for context
                    if hasattr(fb, '_history_cache') and item_name in fb._history_cache:
                        history = fb._history_cache[item_name]
                        vals14 = [qty for d, qty in history if (d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)) < target_date][::-1][:14]
                        base_avg = float(np.mean(vals14)) if vals14 else raw_qty
                    else:
                        avg_row = conn.execute(
                            'SELECT AVG(qty_sold) FROM ('
                            '  SELECT qty_sold FROM daily_sales '
                            '  WHERE item_name = ? AND date < ? '
                            '  ORDER BY date DESC LIMIT 14'
                            ')',
                            (item_name, target_date)
                        ).fetchone()
                        base_avg = float(avg_row[0]) if avg_row and avg_row[0] else raw_qty

                    # +10% safety buffer
                    buffered = raw_qty * 1.10
                    # Cap at base_avg * 1.8
                    cap      = base_avg * 1.8 if base_avg > 0 else buffered
                    final_qty = max(1, ceil(min(buffered, cap)))

                    category = cat_map.get(item_name, fb.item_cats.get(item_name, 'other'))

                    # Build reason from feature row
                    feat_row = pred_df.loc[item_name].to_dict()
                    reason   = _generate_reason(feat_row, dow_name, weather, festival_name, upcoming)

                    results.append({
                        'item_name':       item_name,
                        'category':        category,
                        'recommended_qty': final_qty,
                        'reason':          reason,
                        'base_avg':        round(base_avg, 1),
                        'model_used':      'lgbm',
                    })

                # Sort by category then recommended_qty desc
                results.sort(key=lambda x: (x['category'], -x['recommended_qty']))
                return results

        # ── Rule-based fallback ────────────────────────────────────────────────
        print('[ml_engine] Falling back to rule-based predictions.')
        menu_rows = conn.execute(
            'SELECT item_name, category FROM menu_items ORDER BY item_name'
        ).fetchall()
        fallback_results: List[dict] = []
        for r in menu_rows:
            item_name = r[0]
            category  = r[1] or 'other'
            rec = _rule_based_predict(
                item_name, category, target_date, conn, weather, festival_mult
            )
            fallback_results.append(rec)

        fallback_results.sort(key=lambda x: (x['category'], -x['recommended_qty']))
        return fallback_results

    finally:
        pass
