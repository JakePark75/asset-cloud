import json
import psycopg2
from pathlib import Path

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

def get_usd_krw():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT current_price, change_pct 
        FROM tickers 
        WHERE ticker = 'USDKRW=X'
    """)
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return float(row[0]), float(row[1])
    return None, None