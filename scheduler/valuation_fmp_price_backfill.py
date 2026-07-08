"""
FMP 가치평가 서비스 — 주가 히스토리 backfill
- 대상: fmp_symbols 테이블의 is_active=True 종목 + 벤치마크(^NDX)
- 수집: yfinance 일별 종가, period="10y"
- 저장: fmp_price_history (symbol, date, close_price)
- 실행: python3 scheduler/valuation_fmp_price_backfill.py

주의:
- 1회성 backfill 스크립트입니다. 매일 배치(valuation_fmp_collector.py)와는 별도로 수동 실행합니다.
- yfinance는 Yahoo 비공식 엔드포인트를 호출하므로, 종목 호출 사이 delay를 둡니다.
"""

import json
import logging
import os
import sys
import time

import psycopg2
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
START_DATE = "2016-01-01"  # 연도 경계 정렬을 위해 고정 시작일 사용 (rolling 10y 대신)
REQUEST_DELAY_SEC = 1.5

BENCHMARK_YF_SYMBOL = "^NDX"   # yfinance 호출용
BENCHMARK_DB_SYMBOL = "NDX"    # DB 저장용 (특수문자 제거)


# ── 설정 로드 ─────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ── DB 연결 (valuation_fmp_collector.py와 동일 패턴) ─────────────────────────────────────

def get_conn(cfg: dict):
    return psycopg2.connect(
        host="localhost",
        dbname="assetdb",
        user="jake",
        password=cfg["db_password"],
    )


# ── 종목 목록 조회 ─────────────────────────────────────────────────────────────

def get_active_symbols(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM fmp_symbols WHERE is_active = TRUE ORDER BY symbol")
        return [row[0] for row in cur.fetchall()]


# ── 주가 히스토리 저장 ──────────────────────────────────────────────────────────

def upsert_price_history(conn, db_symbol: str, hist) -> int:
    if hist is None or hist.empty:
        return 0

    rows = []
    for ts, row in hist.iterrows():
        close = row.get("Close")
        if close is None:
            continue
        rows.append((db_symbol, ts.date(), float(close)))

    if not rows:
        return 0

    sql = """
        INSERT INTO fmp_price_history (symbol, date, close_price)
        VALUES (%s, %s, %s)
        ON CONFLICT (symbol, date) DO UPDATE SET
            close_price = EXCLUDED.close_price
    """
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


# ── 종목별 backfill ────────────────────────────────────────────────────────────

def backfill_symbol(conn, yf_symbol: str, db_symbol: str):
    log.info(f"[{db_symbol}] 주가 backfill 시작 (yfinance symbol={yf_symbol}, start={START_DATE})")
    try:
        ticker = yf.Ticker(yf_symbol)
        hist = ticker.history(start=START_DATE, interval="1d", auto_adjust=False)
    except Exception as e:
        log.error(f"  [{db_symbol}] yfinance 호출 실패: {e}")
        return

    count = upsert_price_history(conn, db_symbol, hist)
    log.info(f"  [{db_symbol}] fmp_price_history {count}건 upsert")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    cfg = load_config()
    conn = get_conn(cfg)

    symbols = get_active_symbols(conn)
    if not symbols:
        log.warning("fmp_symbols 테이블에 is_active=TRUE 종목 없음")
        conn.close()
        return

    log.info(f"backfill 대상 {len(symbols)}개 종목 + 벤치마크({BENCHMARK_DB_SYMBOL})")

    # 종목
    for symbol in symbols:
        backfill_symbol(conn, symbol, symbol)
        time.sleep(REQUEST_DELAY_SEC)

    # 벤치마크 (^NDX → DB에는 'NDX'로 저장)
    backfill_symbol(conn, BENCHMARK_YF_SYMBOL, BENCHMARK_DB_SYMBOL)

    conn.close()
    log.info("backfill 완료")


if __name__ == "__main__":
    main()