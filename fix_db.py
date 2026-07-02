import sqlite3, shutil

# Check the real DB
path = "D:/Finternship - wk5/Finternship/backend/hotel_aditya.db"
conn = sqlite3.connect(path)
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print('Tables:', [x[0] for x in tables])
try:
    r = conn.execute('SELECT COUNT(*) FROM daily_sales').fetchone()
    print('Sales rows:', r[0])
    d = conn.execute('SELECT MIN(date), MAX(date) FROM daily_sales').fetchone()
    print('Date range:', d[0], '->', d[1])
    m = conn.execute('SELECT COUNT(*) FROM menu_items').fetchone()
    print('Menu items:', m[0])
except Exception as e:
    print('Error:', e)
conn.close()

print('\nNow copying to backend/hotel_aditya.db ...')
shutil.copy2(path, 'backend/hotel_aditya.db')
print('Done. New size:', __import__('os').path.getsize('backend/hotel_aditya.db'), 'bytes')
