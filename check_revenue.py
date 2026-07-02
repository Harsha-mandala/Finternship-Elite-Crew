import sqlite3
import datetime

conn = sqlite3.connect('backend/hotel_aditya.db')
conn.row_factory = sqlite3.Row

print('=== DATE RANGE IN DB ===')
row = conn.execute('SELECT MIN(date), MAX(date), COUNT(*) FROM daily_sales').fetchone()
print(f'Min date: {row[0]}, Max date: {row[1]}, Total rows: {row[2]}')

print()
print('=== REVENUE BY DATE (last 30 days, most recent first) ===')
rows = conn.execute('''
    SELECT date, ROUND(SUM(gross_revenue),2) as rev, SUM(qty_sold) as qty, COUNT(*) as items
    FROM daily_sales
    GROUP BY date
    ORDER BY date DESC
    LIMIT 30
''').fetchall()
for r in rows:
    print(f'{r[0]} | revenue={r[1]} | qty={r[2]} | items={r[3]}')

print()
today = datetime.date.today()
print(f'=== TODAY (local machine) === {today}')

cutoff_14 = (today - datetime.timedelta(days=14)).isoformat()
cutoff_30 = (today - datetime.timedelta(days=30)).isoformat()
print(f'14-day cutoff: {cutoff_14}')
print(f'30-day cutoff: {cutoff_30}')

print()
print('=== ROWS IN LAST 14 DAYS ===')
rows_14 = conn.execute(
    'SELECT date, ROUND(SUM(gross_revenue),2) as rev FROM daily_sales WHERE date >= ? GROUP BY date ORDER BY date DESC',
    (cutoff_14,)
).fetchall()
print(f'Found {len(rows_14)} days with data in last 14 days')
for r in rows_14:
    print(f'  {r[0]} | rev={r[1]}')

print()
print('=== ROWS WITH ZERO OR NULL REVENUE (last 10) ===')
zero_rows = conn.execute('''
    SELECT date, item_name, qty_sold, gross_revenue
    FROM daily_sales
    WHERE gross_revenue IS NULL OR gross_revenue = 0
    ORDER BY date DESC LIMIT 10
''').fetchall()
zero_count = conn.execute('SELECT COUNT(*) FROM daily_sales WHERE gross_revenue IS NULL OR gross_revenue = 0').fetchone()[0]
print(f'Total zero/null revenue rows: {zero_count}')
for r in zero_rows:
    print(f'  {r[0]} | {r[1]} | qty={r[2]} | rev={r[3]}')

conn.close()
