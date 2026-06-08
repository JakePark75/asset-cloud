import datetime
import sys
import os
import json
import requests
import time
import calendar

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
# 설정 상수
# ---------------------------------------------------------------------------
MAX_QUERY_DAYS = 1000  # 조회 가능한 최대 날짜 범위 (일). 이 값을 초과하면 조회를 거부한다.

# ---------------------------------------------------------------------------
# API 최적화를 위한 글로벌 캐시 저장소 (기존 로직 훼손 방지)
# ---------------------------------------------------------------------------
_GLOBAL_START_DATE_STR = None
_GLOBAL_END_DATE_STR = None
_KR_CACHE = {}
_US_CACHE = {}
_YAHOO_CACHE = {}

# ---------------------------------------------------------------------------
# KIS API (호출 횟수를 줄이기 위한 내부 캐싱 로직만 추가됨)
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
    """KIS 국내주식 기간별시세"""
    global _KR_CACHE, _GLOBAL_START_DATE_STR, _GLOBAL_END_DATE_STR
    
    # 캐시에 없으면 100건씩 루프로 전체 기간 조회 (KIS KR API 1회 최대 100건 제한 대응)
    if ticker not in _KR_CACHE:
        _KR_CACHE[ticker] = []
        url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        headers = {
            "authorization": f"Bearer {token}",
            "appkey":        config_data["kis_app_key"],
            "appsecret":     config_data["kis_app_secret"],
            "tr_id":         "FHKST03010100",
            "custtype":      "P",
        }
        # 시작일보다 30일 더 넉넉하게 가져와서 휴장일 fallback 보장
        global_start_dt = datetime.datetime.strptime(_GLOBAL_START_DATE_STR or target_date_str, "%Y%m%d") - datetime.timedelta(days=30)
        current_end_dt = datetime.datetime.strptime(_GLOBAL_END_DATE_STR or target_date_str, "%Y%m%d")
        current_end = current_end_dt.strftime("%Y%m%d")

        total_days = (current_end_dt - global_start_dt).days
        if total_days > MAX_QUERY_DAYS:
            print(f"❌ 조회 범위({total_days}일)가 최대치({MAX_QUERY_DAYS}일)를 초과합니다. 범위를 줄여주세요.")
            return 0.0

        loop_count = (total_days // 100) + 2  # 100건씩, 여유 2회 추가

        for _ in range(loop_count):
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         ticker,
                "FID_ORG_ADJ_PRC":        "1",
                "FID_PERIOD_DIV_CODE":    "D",
                "FID_INPUT_DATE_1":       global_start_dt.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2":       current_end,
            }
            try:
                res = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
                rows = res.json().get("output2", [])
                if not rows:
                    break
                _KR_CACHE[ticker].extend(rows)
                # 가장 오래된 날짜(마지막 row)보다 하루 앞이 이미 start 이전이면 종료
                oldest_date_str = rows[-1].get("stck_bsop_date", "")
                if not oldest_date_str:
                    break
                oldest_dt = datetime.datetime.strptime(oldest_date_str, "%Y%m%d")
                if oldest_dt <= global_start_dt:
                    break
                # 다음 루프: 가장 오래된 날짜 하루 전을 end로 설정
                current_end = (oldest_dt - datetime.timedelta(days=1)).strftime("%Y%m%d")
            except Exception as e:
                print(f"⚠️ [{ticker}] KIS 국내 과거 시세 조회 실패: {e}")
                break

    rows = _KR_CACHE[ticker]
    
    # --- [사용자님 원본 로직 유지 구역] ---
    for row in rows:
        if row.get("stck_bsop_date") == target_date_str:
            return float(row.get("stck_clpr", 0))
    for row in rows:
        if row.get("stck_bsop_date", "") <= target_date_str:
            return float(row.get("stck_clpr", 0))
    return 0.0

def get_historical_us_price(ticker: str, excd: str, target_date_str: str, token: str) -> float:
    """KIS 해외주식 기간별시세"""
    global _US_CACHE, _GLOBAL_END_DATE_STR
    
    if ticker not in _US_CACHE:
        _US_CACHE[ticker] = []
        url = "https://openapi.koreainvestment.com:9443/uapi/overseas-price/v1/quotations/dailyprice"
        headers = {
            "authorization": f"Bearer {token}",
            "appkey":        config_data["kis_app_key"],
            "appsecret":     config_data["kis_app_secret"],
            "tr_id":         "HHDFS76240000",
            "custtype":      "P",
        }
        current_end = _GLOBAL_END_DATE_STR or target_date_str
        
        # 해외 주식은 한 번에 100일씩 주므로, 넉넉히 3번(약 1년치)을 긁어와 캐시에 저장
        for _ in range(3):
            params = {"AUTH": "", "EXCD": excd, "SYMB": ticker, "GUBN": "0", "BYMD": current_end, "MODP": "1"}
            try:
                res = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
                rows = res.json().get("output2", [])
                if not rows: break
                _US_CACHE[ticker].extend(rows)
                # 다음 루프를 위해 가장 오래된 날짜 기준 하루 전으로 설정
                dt = datetime.datetime.strptime(rows[-1].get("xymd"), "%Y%m%d") - datetime.timedelta(days=1)
                current_end = dt.strftime("%Y%m%d")
            except:
                break

    rows = _US_CACHE[ticker]
    
    # --- [사용자님 원본 로직 유지 구역] ---
    for row in rows:
        if row.get("xymd") == target_date_str:
            return float(row.get("clos", 0))
    for row in rows:
        if row.get("xymd", "") <= target_date_str:
            return float(row.get("clos", 0))
    return 0.0

def get_historical_yahoo_index(ticker: str, target_date: datetime.date) -> float:
    """FX/INDEX/CRYPTO 야후 파이낸스 과거 종가"""
    global _YAHOO_CACHE, _GLOBAL_START_DATE_STR, _GLOBAL_END_DATE_STR
    
    if ticker not in _YAHOO_CACHE:
        try:
            # [수정] Naive 객체에 명확한 UTC 타임존 정보를 강제 주입하여 실행 환경(KST/UTC)에 따른 타임스탬프 틀어짐 원천 차단
            start_dt = datetime.datetime.strptime(_GLOBAL_START_DATE_STR or target_date.strftime("%Y%m%d"), "%Y%m%d").replace(tzinfo=datetime.timezone.utc)
            end_dt = datetime.datetime.strptime(_GLOBAL_END_DATE_STR or target_date.strftime("%Y%m%d"), "%Y%m%d").replace(tzinfo=datetime.timezone.utc)
            
            start_ts = int(start_dt.timestamp()) - (86400 * 10)
            end_ts = int(end_dt.timestamp()) + (86400 * 10)
            
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={start_ts}&period2={end_ts}&interval=1d"
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10, verify=False).json()
            result = res.get("chart", {}).get("result")
            
            cache_list = []
            if result and result[0].get("indicators", {}).get("quote"):
                ts_list = result[0].get("timestamp", [])
                closes = result[0]["indicators"]["quote"][0].get("close", [])
                for ts, c in zip(ts_list, closes):
                    if c is not None:
                        cache_list.append((ts, float(c)))
            _YAHOO_CACHE[ticker] = cache_list
        except Exception as e:
            print(f"⚠️ [{ticker}] 지수/환율 조회 실패: {e}")
            _YAHOO_CACHE[ticker] = []

    cache_list = _YAHOO_CACHE.get(ticker, [])
    
    target_str = target_date.strftime("%Y-%m-%d")
    
    # [수정] 리스트 컴프리헨션 내부의 fromtimestamp에 tz=datetime.timezone.utc를 명시하여 야후 날짜 경계선 뒤틀림 완벽 방지
    matched = [
        (ts, c) for ts, c in cache_list 
        if datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d") <= target_str
    ]
    
    if matched:
        return matched[-1][1]
    return 0.0

# ---------------------------------------------------------------------------
# 날짜 범위 생성 (기존 로직 100% 동일)
# ---------------------------------------------------------------------------
def date_range(start: datetime.date, end: datetime.date) -> list:
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 7:
            days.append(cur)
        cur += datetime.timedelta(days=1)
    return days

# ---------------------------------------------------------------------------
# 단일 날짜 스냅샷 계산 (기존 로직 100% 동일)
# ---------------------------------------------------------------------------
def fetch_snapshot(target_date: datetime.date, position_rows: list, token: str):
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
# DB 조회 헬퍼 (기존 로직 100% 동일)
# ---------------------------------------------------------------------------
def fetch_db_row(date: datetime.date):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, total_asset, twr_asset, ndx100, cash_flow, cash_flow_note,
                       exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio
                FROM daily_summary WHERE date = %s
            """, (date,))
            return cur.fetchone()

def fetch_cash_flows(start: datetime.date, end: datetime.date) -> dict:
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
                LEFT JOIN accounts a ON p.account_id = a.id
                WHERE a.is_watch = false
            """)
            return cur.fetchall()

# ---------------------------------------------------------------------------
# 출력 행 포맷 (기존 로직 100% 동일)
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
        0.0,
        ratios,
        int(cash_flow or 0),
        float(twr_asset or 0),
    )

# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def main():
    global _GLOBAL_START_DATE_STR, _GLOBAL_END_DATE_STR  # 글로벌 변수 추가
    
    print("==================================================")
    print("🕒 [타임머신] 과거 일자 자산 스냅샷 복원기")
    print("==================================================")
    print("입력 형식: YYYYMMDD (단일) 또는 YYYYMMDD-YYYYMMDD (범위)")

    raw = input("👉 날짜 입력: ").strip()

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

    # [수정된 부분] 함수 안에서 쓸 수 있도록 전체 조회 구간 날짜를 저장해 둡니다.
    _GLOBAL_START_DATE_STR = start_date.strftime("%Y%m%d")
    _GLOBAL_END_DATE_STR = end_date.strftime("%Y%m%d")

    # [이하 사용자님 원본 로직과 100% 동일]
    prev_date = start_date - datetime.timedelta(days=1)
    prev_db_row = fetch_db_row(prev_date)

    if prev_db_row:
        prev_twr_asset  = float(prev_db_row[2] or 0)
        prev_total_asset = float(prev_db_row[1] or 0)
    else:
        prev_twr_asset  = None
        prev_total_asset = None

    cash_flow_map = fetch_cash_flows(start_date, end_date)
    position_rows = fetch_positions()

    token = get_kis_access_token()
    weekdays = date_range(start_date, end_date)

    print(f"\n📡 {start_date} ~ {end_date} | 평일 {len(weekdays)}일 조회 시작...\n")

    results = []

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

        if prev_twr_asset is None:
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

    output_lines = []
    output_lines.append("=" * len(HEADER))
    output_lines.append(f"📅 [스냅샷 복원 결과]  {start_date} ~ {end_date}  |  총 {len(results)}일")
    output_lines.append("=" * len(HEADER))
    output_lines.append(HEADER)
    output_lines.append(SEP)

    for row in reversed(results):
        date_str, total_asset, ndx100, usd_krw, ratios, cash_flow, twr_asset = row
        output_lines.append(fmt_row(date_str, total_asset, ndx100, usd_krw, ratios, cash_flow, twr_asset))

    if prev_db_row:
        output_lines.append(SEP)
        output_lines.append(f"[DB 참조행 — {prev_date}]")
        output_lines.append(fmt_db_row(prev_db_row))

    output_lines.append("=" * len(HEADER))

    final_output = "\n".join(output_lines)
    print("\n" + final_output)

    file_name = f"result_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.txt"
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(final_output)

    print(f"\n💾 저장 완료: {file_name}")

if __name__ == "__main__":
    main()