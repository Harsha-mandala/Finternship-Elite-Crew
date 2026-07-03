import os
import re
import sqlite3
from datetime import datetime, date

# Determine paths
_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('DB_PATH', os.path.join(_HERE, 'hotel_aditya.db'))

# Database URL and mode check
DATABASE_URL = os.environ.get('DATABASE_URL')
IS_POSTGRES = DATABASE_URL is not None and (DATABASE_URL.startswith('postgres://') or DATABASE_URL.startswith('postgresql://'))

# Load psycopg2 drivers only if Postgres is active
if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
import threading
def translate_sql(sql: str, is_postgres: bool) -> str:
    """
    Translate SQLite specific syntax to PostgreSQL compatibility.
    """
    if not is_postgres:
        return sql

    # 1. Safe parameter placeholder translation (? -> %s) outside quotes
    sql = re.sub(r"\?(?=(?:[^']*'[^']*')*[^']*$)", "%s", sql)

    # 2. Named placeholder translation (:name -> %(name)s) outside quotes
    sql = re.sub(r":([a-zA-Z0-9_]+)(?=(?:[^']*'[^']*')*[^']*$)", r"%(\1)s", sql)

    # 3. Date DOW pattern strftime('%w', date) -> EXTRACT(DOW FROM date)
    sql = sql.replace("CAST(strftime('%w', date) AS INTEGER)", "CAST(EXTRACT(DOW FROM date) AS INTEGER)")
    sql = sql.replace("strftime('%w', date)", "EXTRACT(DOW FROM date)")

    # 4. ROUND numeric cast translation
    sql = sql.replace("ROUND(SUM(gross_revenue), 2)", "ROUND(CAST(SUM(gross_revenue) AS NUMERIC), 2)")
    sql = sql.replace("ROUND(SUM(ds.gross_revenue), 2)", "ROUND(CAST(SUM(ds.gross_revenue) AS NUMERIC), 2)")

    # 5. INSERT OR REPLACE translations
    if "INSERT OR REPLACE INTO daily_sales" in sql:
        sql = sql.replace(
            "INSERT OR REPLACE INTO daily_sales",
            "INSERT INTO daily_sales"
        ) + " ON CONFLICT (date, item_name) DO UPDATE SET qty_sold = EXCLUDED.qty_sold, gross_revenue = EXCLUDED.gross_revenue, source = EXCLUDED.source"
    elif "INSERT OR REPLACE INTO weather_data" in sql:
        sql = sql.replace(
            "INSERT OR REPLACE INTO weather_data",
            "INSERT INTO weather_data"
        ) + " ON CONFLICT (date) DO UPDATE SET max_temp = EXCLUDED.max_temp, min_temp = EXCLUDED.min_temp, condition = EXCLUDED.condition, rainfall_mm = EXCLUDED.rainfall_mm"

    # 6. INSERT OR IGNORE translation
    sql = sql.replace("INSERT OR IGNORE", "INSERT")

    return sql

class WrappedCursor:
    def __init__(self, cursor, is_postgres):
        self.cursor = cursor
        self.is_postgres = is_postgres

    def execute(self, sql, params=None):
        sql_translated = translate_sql(sql, self.is_postgres)
        if params is None:
            self.cursor.execute(sql_translated)
        else:
            self.cursor.execute(sql_translated, params)
        return self

    def executemany(self, sql, params_list):
        sql_translated = translate_sql(sql, self.is_postgres)
        self.cursor.executemany(sql_translated, params_list)
        return self

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        return row

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def close(self):
        self.cursor.close()

class WrappedConnection:
    def __init__(self, conn, is_postgres):
        self.conn = conn
        self.is_postgres = is_postgres
        self.closed = False

    def cursor(self):
        if self.is_postgres:
            cursor = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        else:
            cursor = self.conn.cursor()
        return WrappedCursor(cursor, self.is_postgres)

    def execute(self, sql, params=None):
        cursor = self.cursor()
        cursor.execute(sql, params)
        return cursor

    def executemany(self, sql, params_list):
        cursor = self.cursor()
        cursor.executemany(sql, params_list)
        return cursor

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        if not self.closed:
            self.closed = True
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.conn.commit()
        self.close()

def get_db_connection() -> WrappedConnection:
    """
    Returns a unified database connection wrapper (PostgreSQL or SQLite fallback).
    """
    if IS_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        return WrappedConnection(conn, True)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA journal_mode=WAL')
        except Exception:
            pass
        return WrappedConnection(conn, False)

def initialize_database():
    """
    Checks if PostgreSQL tables exist. If they do not, creates them and
    idempotently seeds baseline historical data from the local SQLite database.
    """
    if not IS_POSTGRES:
        print("[database] Running on local SQLite DB — schema verified via migration logic.")
        return

    # PostgreSQL Schema initialization
    print("[database] Initializing PostgreSQL schema on Supabase...")
    with get_db_connection() as conn:
        # Create Tables
        conn.execute("""
            CREATE TABLE IF NOT EXISTS menu_items (
                id SERIAL PRIMARY KEY,
                item_name TEXT UNIQUE NOT NULL,
                category TEXT,
                avg_qty DOUBLE PRECISION DEFAULT 0.0,
                is_perishable INT DEFAULT 1,
                unit_price DOUBLE PRECISION DEFAULT 0.0
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_sales (
                id SERIAL PRIMARY KEY,
                date DATE NOT NULL,
                item_name TEXT NOT NULL,
                category TEXT,
                qty_sold INT NOT NULL,
                gross_revenue NUMERIC(10, 2) DEFAULT 0.0,
                day_of_week TEXT,
                dow_num INT,
                source TEXT DEFAULT 'manual',
                UNIQUE(date, item_name)
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weather_data (
                date DATE PRIMARY KEY,
                max_temp DOUBLE PRECISION,
                min_temp DOUBLE PRECISION,
                condition TEXT,
                rainfall_mm DOUBLE PRECISION DEFAULT 0.0
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS festivals (
                id SERIAL PRIMARY KEY,
                date DATE NOT NULL,
                name TEXT NOT NULL,
                type TEXT,
                demand_multiplier DOUBLE PRECISION DEFAULT 1.0,
                UNIQUE(date, name)
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recommendations (
                id SERIAL PRIMARY KEY,
                date DATE NOT NULL,
                item_name TEXT NOT NULL,
                category TEXT,
                recommended_qty INT,
                base_avg DOUBLE PRECISION,
                dow_factor DOUBLE PRECISION DEFAULT 1.0,
                weather_factor DOUBLE PRECISION DEFAULT 1.0,
                festival_factor DOUBLE PRECISION DEFAULT 1.0,
                trend_factor DOUBLE PRECISION DEFAULT 1.0,
                reason TEXT,
                merchant_override INT,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(date, item_name)
            );
        """)

    # Seeding Check
    print("[database] Checking if PostgreSQL is populated...")
    with get_db_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM menu_items")
        row = cursor.fetchone()
        menu_items_count = row[0] if row else 0

    if menu_items_count > 0:
        print("[database] PostgreSQL already populated — seeding skipped.")
        return

    # Seed data from local SQLite DB
    if not os.path.exists(DB_PATH):
        print(f"[database] WARNING: Seeding source SQLite DB not found at {DB_PATH}. Seeding skipped.")
        return

    print(f"[database] Seeding PostgreSQL from local SQLite DB ({DB_PATH})...")
    sqlite_conn = sqlite3.connect(DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    try:
        # Read from SQLite & Write to PostgreSQL
        with get_db_connection() as pg_conn:
            # Seed menu_items
            sqlite_menu = sqlite_conn.execute("SELECT item_name, category, avg_qty, is_perishable, unit_price FROM menu_items").fetchall()
            pg_conn.executemany(
                "INSERT INTO menu_items (item_name, category, avg_qty, is_perishable, unit_price) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (item_name) DO NOTHING",
                [(r['item_name'], r['category'], r['avg_qty'], r['is_perishable'], r['unit_price']) for r in sqlite_menu]
            )
            print(f"[database] Seeded {len(sqlite_menu)} menu_items.")

            # Seed daily_sales
            sqlite_sales = sqlite_conn.execute("SELECT date, item_name, category, qty_sold, gross_revenue, day_of_week, dow_num, source FROM daily_sales").fetchall()
            pg_conn.executemany(
                "INSERT INTO daily_sales (date, item_name, category, qty_sold, gross_revenue, day_of_week, dow_num, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (date, item_name) DO NOTHING",
                [(r['date'], r['item_name'], r['category'], r['qty_sold'], r['gross_revenue'], r['day_of_week'], r['dow_num'], r['source']) for r in sqlite_sales]
            )
            print(f"[database] Seeded {len(sqlite_sales)} daily_sales rows.")

            # Seed weather_data
            sqlite_weather = sqlite_conn.execute("SELECT date, max_temp, min_temp, condition, rainfall_mm FROM weather_data").fetchall()
            pg_conn.executemany(
                "INSERT INTO weather_data (date, max_temp, min_temp, condition, rainfall_mm) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (date) DO NOTHING",
                [(r['date'], r['max_temp'], r['min_temp'], r['condition'], r['rainfall_mm']) for r in sqlite_weather]
            )
            print(f"[database] Seeded {len(sqlite_weather)} weather_data rows.")

            # Seed festivals
            sqlite_fest = sqlite_conn.execute("SELECT date, name, type, demand_multiplier FROM festivals").fetchall()
            pg_conn.executemany(
                "INSERT INTO festivals (date, name, type, demand_multiplier) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (date, name) DO NOTHING",
                [(r['date'], r['name'], r['type'], r['demand_multiplier']) for r in sqlite_fest]
            )
            print(f"[database] Seeded {len(sqlite_fest)} festivals.")

            # Seed recommendations if any exist
            sqlite_recs = sqlite_conn.execute("SELECT date, item_name, category, recommended_qty, base_avg, dow_factor, weather_factor, festival_factor, trend_factor, reason, merchant_override FROM recommendations").fetchall()
            if sqlite_recs:
                pg_conn.executemany(
                    "INSERT INTO recommendations (date, item_name, category, recommended_qty, base_avg, dow_factor, weather_factor, festival_factor, trend_factor, reason, merchant_override) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (date, item_name) DO NOTHING",
                    [(r['date'], r['item_name'], r['category'], r['recommended_qty'], r['base_avg'], r['dow_factor'], r['weather_factor'], r['festival_factor'], r['trend_factor'], r['reason'], r['merchant_override']) for r in sqlite_recs]
                )
                print(f"[database] Seeded {len(sqlite_recs)} recommendations.")

        print("[database] Idempotent seeding completed successfully!")
    except Exception as e:
        print(f"[database] ERROR during seeding: {e}")
    finally:
        sqlite_conn.close()
