import json
import psycopg2
from pathlib import Path
from contextlib import contextmanager

CONFIG_PATH = Path(__file__).parent.parent / "scheduler" / "config.json"

def get_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

def get_connection():
    config = get_config()
    return psycopg2.connect(
        host="localhost",
        port=5432,
        dbname="assetdb",
        user="jake",
        password=config["db_password"]
    )

@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

def get_usd_krw():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT current_price, change_pct 
            FROM tickers 
            WHERE ticker = 'USDKRW=X'
        """)
        row = cur.fetchone()
        cur.close()
    if row:
        return float(row[0]), float(row[1])
    return None, None

def save_config(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)