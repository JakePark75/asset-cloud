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

# ── 마켓 헬퍼 ──────────────────────────────────────────────
def get_market_map() -> dict:
    """config.json market_map 반환. 없으면 빈 dict."""
    return get_config().get("market_map", {})

def get_market_currency(market: str) -> str:
    """마켓 코드 -> 통화 코드. 정의 안 된 마켓은 'KRW' 기본값."""
    return get_market_map().get(market, {}).get("currency", "KRW")

def get_market_label(market: str) -> str:
    """마켓 코드 -> 표시 레이블. 정의 안 된 마켓은 마켓 코드 그대로 반환."""
    return get_market_map().get(market, {}).get("label", market)

def is_us_market(market: str) -> bool:
    """USD 통화 마켓 여부."""
    return get_market_currency(market) == "USD"

def get_supported_markets() -> list[str]:
    """market_map에 정의된 전체 마켓 코드 목록."""
    return list(get_market_map().keys())