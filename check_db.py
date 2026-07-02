import sqlite3
conn = sqlite3.connect('backend/hotel_aditya.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print('Tables:', tables)
try:
    rows = conn.execute('SELECT COUNT(*) FROM daily_sales').fetchone()
    print('Sales rows:', rows[0])
    dates = conn.execute('SELECT MIN(date), MAX(date) FROM daily_sales').fetchone()
    print('Date range:', dates[0], '->', dates[1])
    items = conn.execute('SELECT COUNT(*) FROM menu_items').fetchone()
    print('Menu items:', items[0])
except Exception as e:
    print('Error:', e)
conn.close()
