import json
import psycopg2
from psycopg2 import pool as pg_pool
from pathlib import Path
from contextlib import contextmanager

CONFIG_PATH = Path(__file__).parent.parent / "scheduler" / "config.json"

CASH_KEY_PREFIX = "__CASH_"
CASH_KEY_SUFFIX = "__"


def cash_key(currency: str) -> str:
    """Return the internal position key for a cash currency."""
    normalized = str(currency or "").strip().upper()
    if not normalized or not normalized.isalpha():
        raise ValueError(f"Invalid cash currency: {currency!r}")
    return f"{CASH_KEY_PREFIX}{normalized}{CASH_KEY_SUFFIX}"


def cash_currency(ticker: str) -> str | None:
    """Return a cash key's currency; keep legacy KRW rows readable."""
    value = str(ticker or "").strip().upper()
    if value == "KRW":
        return "KRW"
    if value.startswith(CASH_KEY_PREFIX) and value.endswith(CASH_KEY_SUFFIX):
        currency = value[len(CASH_KEY_PREFIX):-len(CASH_KEY_SUFFIX)]
        return currency or None
    return None


def is_cash_ticker(ticker: str) -> bool:
    return cash_currency(ticker) is not None


CASH_KRW = cash_key("KRW")
CASH_USD = cash_key("USD")

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


def _get_live_conn(p: "pg_pool.ThreadedConnectionPool"):
    """
    풀에서 커넥션을 받아오되, 살아있는지 SELECT 1로 확인(pre-ping)한 뒤 반환한다.
    죽은 커넥션이면 풀에서 폐기(close=True)하고 다시 받아온다 (최대 3회 재시도).

    이 함수를 추가한 계기: 2026-08-22 Ubuntu unattended-upgrades가 postgresql-14를
    자동 업그레이드하면서 서비스가 재시작됐고, 그때 살아있던 풀의 커넥션 2개가
    "administrator command"로 서버 쪽에서 강제 종료됨. 풀 객체는 이를 모른 채
    다음 getconn()에서 죽은 커넥션을 그대로 내어줬고, daily_inserter가
    "connection already closed" 에러로 실패함 (postgresql 로그로 원인 확정).

    표준 해법(SQLAlchemy pool_pre_ping과 동일한 패턴): 체크아웃 시 트리비얼
    쿼리로 살아있는지 확인 후, 죽었으면 버리고 새로 발급받는다.
    """
    last_err = None
    for _ in range(3):
        conn = p.getconn()
        if conn.closed:
            last_err = "conn.closed flag set"
            p.putconn(conn, close=True)
            continue
        try:
            with conn.cursor() as probe:
                probe.execute("SELECT 1")
            # SELECT 1은 read-only라 커밋 불필요하지만, 트랜잭션이 열린 채로
            # 반환되지 않도록 정리해둔다.
            if conn.status == psycopg2.extensions.STATUS_IN_TRANSACTION:
                conn.rollback()
        except Exception as e:
            last_err = e
            try:
                conn.close()
            except Exception:
                pass
            p.putconn(conn, close=True)
            continue
        return conn
    raise RuntimeError(
        f"DB 커넥션 풀에서 살아있는 커넥션을 받아오지 못했습니다 (3회 재시도 실패): {last_err}"
    )


@contextmanager
def get_db():
    """
    커넥션 풀에서 빌리고 반납하는 컨텍스트 매니저.
    기존 인터페이스(`with get_db() as conn:`)는 그대로 유지 — 호출부 수정 불필요.

    - 빌려주기 전에 SELECT 1로 살아있는지 확인(pre-ping)하고, 죽어있으면 폐기 후
      재발급한다 (_get_live_conn 참고 — postgres 재시작 등으로 인한 stale
      connection 문제 대응).
    - 정상 종료됐지만 트랜잭션이 열린 채로 남아있으면(commit() 안 부르는 SELECT 전용
      호출부 다수 존재) 반납 전에 rollback() 하여 다음 사용자가 깨끗한 상태로 받게 함.
      (psycopg2 공식문서: 트랜잭션이 열린 채로 커넥션을 닫거나 반납하면 문제가 될 수
      있으니 commit()/rollback()으로 트랜잭션을 끝내라고 명시)
    - 예외 발생 시 rollback() 후 그대로 재발생(re-raise) — 예외를 삼키지 않는 기존 동작 유지
    """
    p = _get_pool()
    conn = _get_live_conn(p)
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


def get_cash_currencies() -> list[str]:
    """Configured currencies available in the cash input UI."""
    currencies = get_config().get("cash_currencies", ["KRW", "USD"])
    return [str(currency).strip().upper() for currency in currencies if str(currency).strip()]


def get_cash_fx_map() -> dict[str, str]:
    """Configured currency -> KRW FX ticker map for future rate support."""
    return {
        str(currency).strip().upper(): str(ticker).strip()
        for currency, ticker in get_config().get("cash_fx", {}).items()
        if str(currency).strip() and str(ticker).strip()
    }

def is_us_market(market: str) -> bool:
    """USD 통화 마켓 여부."""
    return get_market_currency(market) == "USD"

def get_supported_markets() -> list[str]:
    """market_map에 정의된 전체 마켓 코드 목록."""
    return list(get_market_map().keys())