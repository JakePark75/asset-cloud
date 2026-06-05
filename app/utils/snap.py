import datetime
import sys
import os
import json
import requests
import time
import calendar

# 패키지 경로 설정
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))  # app/utils
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))  # 프로젝트 루트
sys.path.append(PROJECT_ROOT)

from app.db import get_db
from app.utils.metrics import calculate_exposure_and_ratios, to_f

CONFIG_FILE = os.path.join(PROJECT_ROOT, "scheduler", "config.json")
with open(CONFIG_FILE, encoding="utf-8") as f:
    config_data = json.load(f)

# ---------------------------------------------------------------------------
# KIS API
# ---------------------------------------------------------------------------
def get_kis_access_token():
    url  = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey":     config_data["kis_app_key"],
        "appsecret":  config_data["kis_app_secret"],
    }
    res = requests.post(url, json=body, timeout=10, verify=False)
    return res.json().get("access_token")

def get_historical_kr_price(ticker: str, target_date_str: str, token: str) -> float:
    """KIS 국내주식 기간별시세 — 날짜 제한 없음, 1회 최대 100건
    반환값: 해당일 종가 (휴장일이면 직전 거래일 종가). 데이터 없으면 0.0
    """
    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey":        config_data["kis_app_key"],
        "appsecret":     config_data["kis_app_secret"],
        "tr_id":         "FHKST03010100",
        "custtype":      "P",
    }
    target_date = datetime.datetime.strptime(target_date_str, "%Y%m%d").date()
    start_date_str = (target_date - datetime.timedelta(days=10)).strftime("%Y%m%d")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD":         ticker,
        "FID_ORG_ADJ_PRC":        "1",
        "FID_PERIOD_DIV_CODE":    "D",
        "FID_INPUT_DATE_1":       start_date_str,
        "FID_INPUT_DATE_2":       target_date_str,
    }
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
        rows = res.json().get("output2", [])
        # 해당일 우선
        for row in rows:
            if row.get("stck_bsop_date") == target_date_str:
                return float(row.get("stck_clpr", 0))
        # 휴장일이면 직전 거래일
        for row in rows:
            if row.get("stck_bsop_date", "") <= target_date_str:
                return float(row.get("stck_clpr", 0))
    except Exception as e:
        print(f"⚠️ [{ticker}] KIS 국내 과거 시세 조회 실패: {e}")
    return 0.0

def get_historical_us_price(ticker: str, excd: str, target_date_str: str, token: str) -> float:
    """KIS 해외주식 기간별시세 (TR: HHDFS76240000)"""
    url = "https://openapi.koreainvestment.com:9443/uapi/overseas-price/v1/quotations/dailyprice"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey":        config_data["kis_app_key"],
        "appsecret":     config_data["kis_app_secret"],
        "tr_id":         "HHDFS76240000",
        "custtype":      "P",
    }
    params = {
        "AUTH": "",
        "EXCD": excd,
        "SYMB": ticker,
        "GUBN": "0",
        "BYMD": target_date_str,
        "MODP": "1",
    }
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
        rows = res.json().get("output2", [])
        for row in rows:
            if row.get("xymd") == target_date_str:
                return float(row.get("clos", 0))
        for row in rows:
            if row.get("xymd", "") <= target_date_str:
                return float(row.get("clos", 0))
    except Exception as e:
        print(f"⚠️ [{ticker}] KIS 해외 과거 시세 조회 실패: {e}")
    return 0.0

def get_historical_yahoo_index(ticker: str, target_date: datetime.date) -> float:
    """FX/INDEX/CRYPTO 야후 파이낸스 과거 종가"""
    start_ts = calendar.timegm(target_date.timetuple())
    end_ts = start_ts + (86400 * 5)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={start_ts}&period2={end_ts}&interval=1d"
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10, verify=False).json()
        result = res.get("chart", {}).get("result")
        if result and result[0].get("indicators", {}).get("quote"):
            closes = result[0]["indicators"]["quote"][0].get("close", [])
            for c in closes:
                if c is not None: return float(c)
    except Exception as e:
        print(f"⚠️ [{ticker}] 지수/환율 조회 실패: {e}")
    return 0.0

# ---------------------------------------------------------------------------
# 날짜 범위 생성 (주말 제외 — 공휴일은 시세 0으로 판단 후 스킵)
# ---------------------------------------------------------------------------
def date_range(start: datetime.date, end: datetime.date) -> list:
    """start ~ end 사이 평일(월~금) 목록 반환 (오름차순)"""
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # 0=월 ~ 4=금
            days.append(cur)
        cur += datetime.timedelta(days=1)
    return days

# ---------------------------------------------------------------------------
# 단일 날짜 스냅샷 계산
# ---------------------------------------------------------------------------
def fetch_snapshot(target_date: datetime.date, position_rows: list, token: str):
    """
    반환: (ratios_dict, ndx100, usd_krw, final_db_rows) 또는
          시세 없는 날(휴장일)이면 None
    """
    date_str = target_date.strftime("%Y%m%d")

    usd_krw = get_historical_yahoo_index('USDKRW=X', target_date)
    ndx100  = get_historical_yahoo_index('^NDX', target_date)
    if usd_krw == 0:
        usd_krw = 1350.0

    final_db_rows = []
    is_holiday = False

    for ticker, qty, leverage, market in position_rows:
        qty = to_f(qty)
        market_str = market.upper() if market else "KR"

        if ticker == "KRW":
            past_price = 1.0
        elif ticker == "USD":
            past_price = usd_krw
        elif market_str == "KR":
            past_price = get_historical_kr_price(ticker, date_str, token)
            if past_price == 0.0:
                is_holiday = True
                break
        elif market_str in ("NAS", "AMS", "ARC"):
            past_price = get_historical_us_price(ticker, market_str, date_str, token)
        elif market_str in ("FX", "INDEX", "CRYPTO"):
            past_price = get_historical_yahoo_index(ticker, target_date)
        else:
            print(f"⚠️ [{ticker}] 알 수 없는 market 값: {market_str} — 시세 0으로 처리")
            past_price = 0.0

        final_db_rows.append((ticker, qty, past_price, leverage, market))

    if is_holiday:
        return None

    ratios = calculate_exposure_and_ratios(final_db_rows, usd_krw)
    return ratios, ndx100, usd_krw, final_db_rows

# ---------------------------------------------------------------------------
# DB 조회 헬퍼
# ---------------------------------------------------------------------------
def fetch_db_row(date: datetime.date):
    """daily_summary에서 특정 날짜 행 조회. 없으면 None"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, total_asset, twr_asset, ndx100, cash_flow, cash_flow_note,
                       exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio
                FROM daily_summary WHERE date = %s
            """, (date,))
            return cur.fetchone()

def fetch_cash_flows(start: datetime.date, end: datetime.date) -> dict:
    """범위 내 cash_flow 있는 날짜 dict로 반환 {date: (cash_flow, note)}"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, cash_flow, cash_flow_note
                FROM daily_summary
                WHERE date BETWEEN %s AND %s AND cash_flow != 0
            """, (start, end))
            return {row[0]: (int(row[1] or 0), row[2] or "") for row in cur.fetchall()}

def fetch_positions():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.ticker, p.quantity, t.leverage, t.market
                FROM positions p LEFT JOIN tickers t ON p.ticker = t.ticker
            """)
            return cur.fetchall()

# ---------------------------------------------------------------------------
# 출력 행 포맷
# ---------------------------------------------------------------------------
HEADER = (
    f"{'날짜':<10} | {'총자산(원)':>14} | {'NDX100':>10} | {'환율':>8} | "
    f"{'Exposure':>8} | {'현금':>7} | {'X3':>7} | {'X2':>7} | {'X1':>7} | "
    f"{'입출금':>12} | {'TWR자산':>14}"
)
SEP = "-" * len(HEADER)

def fmt_row(date_str, total_asset, ndx100, usd_krw, ratios, cash_flow, twr_asset):
    return (
        f"{date_str:<10} | "
        f"{total_asset:>14,.0f} | "
        f"{ndx100:>10,.2f} | "
        f"{usd_krw:>8,.2f} | "
        f"{ratios['exposure']:>8.2%} | "
        f"{ratios['cash_ratio']:>7.2%} | "
        f"{ratios['x3_ratio']:>7.2%} | "
        f"{ratios['x2_ratio']:>7.2%} | "
        f"{ratios['x1_ratio']:>7.2%} | "
        f"{cash_flow:>12,.0f} | "
        f"{twr_asset:>14,.0f}"
    )

def fmt_db_row(row):
    """DB에서 읽은 raw row를 출력 포맷으로 변환"""
    date, total_asset, twr_asset, ndx100, cash_flow, cash_flow_note, \
        exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio = row
    ratios = {
        "exposure": float(exposure or 0),
        "cash_ratio": float(cash_ratio or 0),
        "x1_ratio": float(x1_ratio or 0),
        "x2_ratio": float(x2_ratio or 0),
        "x3_ratio": float(x3_ratio or 0),
    }
    return fmt_row(
        date.strftime("%Y%m%d"),
        float(total_asset or 0),
        float(ndx100 or 0),
        0.0,  # DB에 환율 컬럼 없음
        ratios,
        int(cash_flow or 0),
        float(twr_asset or 0),
    )

# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def main():
    print("==================================================")
    print("🕒 [타임머신] 과거 일자 자산 스냅샷 복원기")
    print("==================================================")
    print("입력 형식: YYYYMMDD (단일) 또는 YYYYMMDD-YYYYMMDD (범위)")

    raw = input("👉 날짜 입력: ").strip()

    # 날짜 파싱
    try:
        if "-" in raw:
            parts = raw.split("-")
            start_date = datetime.datetime.strptime(parts[0], "%Y%m%d").date()
            end_date   = datetime.datetime.strptime(parts[1], "%Y%m%d").date()
        else:
            start_date = end_date = datetime.datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        print("❌ 날짜 형식이 올바르지 않습니다.")
        return

    if start_date > end_date:
        print("❌ 시작일이 종료일보다 늦습니다.")
        return

    # 시작일 -1일: DB에서 직전 행 읽기 (TWR 초기값용)
    prev_date = start_date - datetime.timedelta(days=1)
    prev_db_row = fetch_db_row(prev_date)

    if prev_db_row:
        prev_twr_asset  = float(prev_db_row[2] or 0)
        prev_total_asset = float(prev_db_row[1] or 0)
    else:
        prev_twr_asset  = None  # 첫날 total_asset으로 초기화
        prev_total_asset = None

    # 범위 내 cash_flow DB에서 미리 읽기
    cash_flow_map = fetch_cash_flows(start_date, end_date)

    # 포지션 조회 (현재 포지션 기준 — 과거 시점 시세만 교체)
    position_rows = fetch_positions()

    token = get_kis_access_token()
    weekdays = date_range(start_date, end_date)

    print(f"\n📡 {start_date} ~ {end_date} | 평일 {len(weekdays)}일 조회 시작...\n")

    # 날짜별 계산 (오름차순으로 계산해야 TWR 연산 가능)
    results = []  # [(date_str, total_asset, ndx100, usd_krw, ratios, cash_flow, twr_asset)]

    for target_date in weekdays:
        date_str = target_date.strftime("%Y%m%d")
        print(f"  ⏳ {date_str} 조회 중...", end=" ", flush=True)

        snap = fetch_snapshot(target_date, position_rows, token)
        if snap is None:
            print("⏭️  휴장일 스킵")
            continue

        ratios, ndx100, usd_krw, _ = snap
        total_asset = ratios["total_asset"]
        cash_flow, _ = cash_flow_map.get(target_date, (0, ""))

        # TWR 계산
        if prev_twr_asset is None:
            # DB에 이전 행 없음 → 첫날 twr_asset = total_asset (기준점)
            twr_asset = total_asset
        else:
            denom = prev_total_asset
            if denom and denom != 0:
                twr_asset = prev_twr_asset * ((total_asset - cash_flow) / denom)
            else:
                twr_asset = prev_twr_asset

        prev_twr_asset   = twr_asset
        prev_total_asset = total_asset

        results.append((date_str, total_asset, ndx100, usd_krw, ratios, cash_flow, twr_asset))
        print(f"✅ 총자산: {total_asset:,.0f} 원")

    if not results:
        print("\n❌ 조회된 데이터가 없습니다.")
        return

    # ---------------------------------------------------------------------------
    # 출력 (최신순 — 위가 최신)
    # ---------------------------------------------------------------------------
    output_lines = []
    output_lines.append("=" * len(HEADER))
    output_lines.append(f"📅 [스냅샷 복원 결과]  {start_date} ~ {end_date}  |  총 {len(results)}일")
    output_lines.append("=" * len(HEADER))
    output_lines.append(HEADER)
    output_lines.append(SEP)

    for row in reversed(results):
        date_str, total_asset, ndx100, usd_krw, ratios, cash_flow, twr_asset = row
        output_lines.append(fmt_row(date_str, total_asset, ndx100, usd_krw, ratios, cash_flow, twr_asset))

    # 시작일 -1일 DB 행
    if prev_db_row:
        output_lines.append(SEP)
        output_lines.append(f"[DB 참조행 — {prev_date}]")
        output_lines.append(fmt_db_row(prev_db_row))

    output_lines.append("=" * len(HEADER))

    final_output = "\n".join(output_lines)
    print("\n" + final_output)

    # 파일 저장
    file_name = f"result_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.txt"
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(final_output)

    print(f"\n💾 저장 완료: {file_name}")

if __name__ == "__main__":
    main()