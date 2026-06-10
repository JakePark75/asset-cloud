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

# ---------------------------------------------------------------------------
# 환율 조회 — Step 6-1: DB(tickers) → Redis 전환
#   Redis `usd_krw` key : write_price()가 USDKRW=X 수신 시 자동 갱신
#   Redis `prices` hash : {ticker: {"price": float, "change_pct": float}}
#   Redis 미연결·키 없음 → None, None 반환 (호출처에서 fallback 처리)
# ---------------------------------------------------------------------------
def get_usd_krw():
    from common.redis_store import get_redis
    import json

    r = get_redis()
    if not r:
        raise RuntimeError("Redis connection unavailable")

    raw_price = r.get("usd_krw")
    if raw_price is None:
        raise RuntimeError("Redis key 'usd_krw' not found")

    raw_chg = 0.0

    raw_prices = r.hget("prices", "USDKRW=X")
    if raw_prices:
        data = json.loads(raw_prices)
        raw_chg = float(data.get("change_pct", 0.0))

    return float(raw_price), raw_chg

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