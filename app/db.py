import json
import psycopg2
from psycopg2 import pool as pg_pool
from pathlib import Path
from contextlib import contextmanager

CONFIG_PATH = Path(__file__).parent.parent / "scheduler" / "config.json"

def get_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

def get_connection():
    """
    풀을 거치지 않는 단발성 커넥션. (기존 동작 그대로 유지 — 이 함수를 직접 쓰는
    다른 호출부가 있는지는 확인되지 않았으므로 삭제하지 않고 남겨둠)
    """
    config = get_config()
    return psycopg2.connect(
        host="localhost",
        port=5432,
        dbname="assetdb",
        user="jake",
        password=config["db_password"]
    )


# ── 커넥션 풀 ──────────────────────────────────────────────────────────────
# 프로세스(myassets, price_updater, daily_inserter)마다 독립된 풀 인스턴스를 가짐 —
# 풀은 프로세스 경계를 넘어 공유되지 않으므로 이 모듈이 import되는 프로세스마다 별도로 생성됨.
# ThreadedConnectionPool 선택 근거(psycopg.org/docs/pool.html 공식문서):
#   - SimpleConnectionPool은 "멀티스레드 애플리케이션엔 안전하지 않다"고 명시됨
#   - ThreadedConnectionPool은 내부적으로 threading.Lock으로 getconn/putconn을 보호해
#     멀티스레드에서 안전하다고 명시됨 (Shiny 비동기/멀티세션 환경에 적합)
_pool: "pg_pool.ThreadedConnectionPool | None" = None


def _get_pool() -> "pg_pool.ThreadedConnectionPool":
    global _pool
    if _pool is None:
        config = get_config()
        _pool = pg_pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,  # 확정: max_connections=100 확인 후 여유폭 고려해 결정 (2026-07-04)
            host="localhost",
            port=5432,
            dbname="assetdb",
            user="jake",
            password=config["db_password"],
        )
    return _pool


@contextmanager
def get_db():
    """
    커넥션 풀에서 빌리고 반납하는 컨텍스트 매니저.
    기존 인터페이스(`with get_db() as conn:`)는 그대로 유지 — 호출부 수정 불필요.

    - 정상 종료됐지만 트랜잭션이 열린 채로 남아있으면(commit() 안 부르는 SELECT 전용
      호출부 다수 존재) 반납 전에 rollback() 하여 다음 사용자가 깨끗한 상태로 받게 함.
      (psycopg2 공식문서: 트랜잭션이 열린 채로 커넥션을 닫거나 반납하면 문제가 될 수
      있으니 commit()/rollback()으로 트랜잭션을 끝내라고 명시)
    - 예외 발생 시 rollback() 후 그대로 재발생(re-raise) — 예외를 삼키지 않는 기존 동작 유지
    """
    p = _get_pool()
    conn = p.getconn()
    try:
        yield conn
        if conn.status == psycopg2.extensions.STATUS_IN_TRANSACTION:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)

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