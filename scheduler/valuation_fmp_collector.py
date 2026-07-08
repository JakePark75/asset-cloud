"""
FMP 데이터 수집기
- 대상: fmp_symbols 테이블의 is_active=True 종목
- 수집: income-statement / balance-sheet-statement / cash-flow-statement / analyst-estimates / quote
- 저장: fmp_financials, fmp_estimates, fmp_metrics (가격/시총/EV만)
- 실행: python3 scheduler/valuation_fmp_collector.py

(34차 세션 수정)
- upsert_metrics_quote: price/market_cap/timestamp 중 하나라도 null이면 upsert 자체를 스킵하고
  경고 로그만 남김 (기존 정상값을 NULL로 덮어쓰는 것을 방지).
- calculated_at: 배치가 실행된 서버 로컬 날짜(date.today()) 대신, FMP가 응답으로 주는
  timestamp(Unix epoch, quote가 실제로 찍힌 시점)를 미국 동부시간(America/New_York, DST 자동 반영)
  기준 날짜로 변환해서 사용. 서버 실행 시각과 실제 거래일이 어긋나는 문제(예: 자정 근처 실행,
  타임존 차이로 인한 하루 밀림)를 근본적으로 방지.
"""

import json
import logging
import os
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg2
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
BASE_URL = "https://financialmodelingprep.com/stable"
LIMIT = 5  # 연간 5년치
US_MARKET_TZ = ZoneInfo("America/New_York")


# ── 설정 로드 ─────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ── DB 연결 ───────────────────────────────────────────────────────────────────

def get_conn(cfg: dict):
    return psycopg2.connect(
        host="localhost",
        dbname="assetdb",
        user="jake",
        password=cfg["db_password"],
    )


# ── FMP API 호출 ──────────────────────────────────────────────────────────────

def fmp_get(endpoint: str, api_key: str, params: dict) -> list:
    params["apikey"] = api_key
    url = f"{BASE_URL}/{endpoint}"
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "Error Message" in data:
        raise RuntimeError(f"FMP 오류: {data['Error Message']}")
    return data if isinstance(data, list) else []


# ── 종목 목록 조회 ─────────────────────────────────────────────────────────────

def get_active_symbols(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM fmp_symbols WHERE is_active = TRUE ORDER BY symbol")
        return [row[0] for row in cur.fetchall()]


# ── fmp_financials 저장 ────────────────────────────────────────────────────────

def upsert_financials(conn, symbol: str, income: list, balance: list, cashflow: list):
    # fiscal_year 기준으로 3개 소스 병합
    income_map  = {r["fiscalYear"]: r for r in income}
    balance_map = {r["fiscalYear"]: r for r in balance}
    cf_map      = {r["fiscalYear"]: r for r in cashflow}

    fiscal_years = sorted(set(income_map) | set(balance_map) | set(cf_map), reverse=True)

    rows = []
    for fy in fiscal_years:
        i = income_map.get(fy, {})
        b = balance_map.get(fy, {})
        c = cf_map.get(fy, {})

        # total_debt: shortTermDebt + longTermDebt
        total_debt = b.get("totalDebt")

        rows.append((
            symbol,
            int(fy),
            i.get("revenue"),
            i.get("grossProfit"),
            i.get("operatingIncome"),
            i.get("ebitda"),
            i.get("netIncome"),
            i.get("epsDiluted"),
            b.get("totalAssets"),
            b.get("totalLiabilities"),
            total_debt,
            b.get("totalStockholdersEquity"),
            b.get("cashAndShortTermInvestments"),
            c.get("operatingCashFlow"),
            c.get("capitalExpenditure"),
            c.get("freeCashFlow"),
            datetime.now(),
        ))

    sql = """
        INSERT INTO fmp_financials (
            symbol, fiscal_year,
            revenue, gross_profit, operating_income, ebitda, net_income, eps,
            total_assets, total_liabilities, total_debt, total_equity, cash,
            operating_cash_flow, capex, free_cash_flow,
            collected_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (symbol, fiscal_year) DO UPDATE SET
            revenue             = EXCLUDED.revenue,
            gross_profit        = EXCLUDED.gross_profit,
            operating_income    = EXCLUDED.operating_income,
            ebitda              = EXCLUDED.ebitda,
            net_income          = EXCLUDED.net_income,
            eps                 = EXCLUDED.eps,
            total_assets        = EXCLUDED.total_assets,
            total_liabilities   = EXCLUDED.total_liabilities,
            total_debt          = EXCLUDED.total_debt,
            total_equity        = EXCLUDED.total_equity,
            cash                = EXCLUDED.cash,
            operating_cash_flow = EXCLUDED.operating_cash_flow,
            capex               = EXCLUDED.capex,
            free_cash_flow      = EXCLUDED.free_cash_flow,
            collected_at        = EXCLUDED.collected_at
    """
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    log.info(f"  [{symbol}] fmp_financials {len(rows)}건 upsert")


# ── fmp_estimates 저장 ─────────────────────────────────────────────────────────

def upsert_estimates(conn, symbol: str, estimates: list):
    rows = []
    for e in estimates:
        # date 필드에서 연도 추출 (예: "2026-09-27" → 2026)
        try:
            est_year = int(e["date"][:4])
        except (KeyError, ValueError):
            continue

        rows.append((
            symbol,
            est_year,
            e.get("revenueAvg"),
            e.get("ebitdaAvg"),
            e.get("netIncomeAvg"),
            e.get("epsAvg"),
            datetime.now(),
        ))

    if not rows:
        return

    sql = """
        INSERT INTO fmp_estimates (
            symbol, estimate_year,
            revenue_avg, ebitda_avg, net_income_avg, eps_avg,
            collected_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (symbol, estimate_year) DO UPDATE SET
            revenue_avg    = EXCLUDED.revenue_avg,
            ebitda_avg     = EXCLUDED.ebitda_avg,
            net_income_avg = EXCLUDED.net_income_avg,
            eps_avg        = EXCLUDED.eps_avg,
            collected_at   = EXCLUDED.collected_at
    """
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    log.info(f"  [{symbol}] fmp_estimates {len(rows)}건 upsert")


# ── fmp_metrics 가격/시총/EV 저장 ──────────────────────────────────────────────
# 계산 지표는 별도 계산 엔진에서 채움. 여기서는 price/market_cap/enterprise_value만 저장.

def upsert_metrics_quote(conn, symbol: str, quote: dict):
    price      = quote.get("price")
    market_cap = quote.get("marketCap")
    ts         = quote.get("timestamp")

    # price/market_cap/timestamp 중 하나라도 없으면 upsert 자체를 하지 않는다.
    # (기존에 정상적으로 들어간 값을 NULL로 덮어쓰는 사고를 막기 위함. 그날의 행이
    #  아예 안 생기는 것 뿐이며, 다른 날짜/다른 종목에는 영향 없음.)
    if price is None or market_cap is None or ts is None:
        log.warning(
            f"  [{symbol}] quote 응답에 price/market_cap/timestamp 누락 "
            f"(price={price}, market_cap={market_cap}, timestamp={ts}) — upsert 스킵, 기존 값 보존"
        )
        return

    # FMP timestamp는 Unix epoch(초). 서버 실행 시각(date.today())이 아니라,
    # 이 quote가 실제로 찍힌 시점을 미국 동부시간(DST 자동 반영) 기준 날짜로 환산해서 사용한다.
    # 이유: 서버 로컬 날짜를 쓰면 실행 시각과 타임존 차이로 인해 실제 거래일과 하루 어긋날 수 있음
    # (예: 자정 근처 실행, 또는 서버 타임존과 미국 동부시간 간의 날짜 경계 차이).
    quote_date = datetime.fromtimestamp(ts, tz=US_MARKET_TZ).date()

    # EV = market_cap + total_debt - cash (최근 연도 재무 데이터 활용)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT total_debt, cash FROM fmp_financials
            WHERE symbol = %s ORDER BY fiscal_year DESC LIMIT 1
        """, (symbol,))
        row = cur.fetchone()

    ev = None
    if row and price and market_cap:
        total_debt, cash = row
        if total_debt is not None and cash is not None:
            ev = float(market_cap) + float(total_debt) - float(cash)

    sql = """
        INSERT INTO fmp_metrics (symbol, calculated_at, price, market_cap, enterprise_value)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (symbol, calculated_at) DO UPDATE SET
            price            = EXCLUDED.price,
            market_cap       = EXCLUDED.market_cap,
            enterprise_value = EXCLUDED.enterprise_value
    """
    with conn.cursor() as cur:
        cur.execute(sql, (symbol, quote_date, price, market_cap, ev))
    conn.commit()
    log.info(f"  [{symbol}] fmp_metrics quote/EV upsert (date={quote_date}, price={price}, EV={ev})")


# ── 종목별 수집 ───────────────────────────────────────────────────────────────

def collect_symbol(symbol: str, api_key: str, conn):
    log.info(f"[{symbol}] 수집 시작")

    try:
        income   = fmp_get("income-statement",        api_key, {"symbol": symbol, "period": "annual", "limit": LIMIT})
        time.sleep(0.3)
        balance  = fmp_get("balance-sheet-statement", api_key, {"symbol": symbol, "period": "annual", "limit": LIMIT})
        time.sleep(0.3)
        cashflow = fmp_get("cash-flow-statement",     api_key, {"symbol": symbol, "period": "annual", "limit": LIMIT})
        time.sleep(0.3)
        estimates = fmp_get("analyst-estimates",      api_key, {"symbol": symbol, "period": "annual", "limit": LIMIT})
        time.sleep(0.3)
        quotes   = fmp_get("quote",                   api_key, {"symbol": symbol})
        time.sleep(0.3)
    except Exception as e:
        log.error(f"  [{symbol}] API 호출 실패: {e}")
        return

    if income or balance or cashflow:
        upsert_financials(conn, symbol, income, balance, cashflow)

    if estimates:
        upsert_estimates(conn, symbol, estimates)

    if quotes:
        upsert_metrics_quote(conn, symbol, quotes[0])


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    cfg     = load_config()
    api_key = cfg.get("fmp_api_key")
    if not api_key:
        log.error("config.json에 fmp_api_key 없음")
        sys.exit(1)

    conn    = get_conn(cfg)
    symbols = get_active_symbols(conn)

    if not symbols:
        log.warning("fmp_symbols 테이블에 is_active=TRUE 종목 없음")
        conn.close()
        return

    log.info(f"수집 대상 {len(symbols)}개: {symbols}")

    for symbol in symbols:
        collect_symbol(symbol, api_key, conn)

    conn.close()
    log.info("수집 완료")


if __name__ == "__main__":
    main()