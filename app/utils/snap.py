import datetime
import sys
import os
import json
import requests
import time
import calendar

import urllib3

from common.notify import notify_telegram_alert as _notify_telegram_alert
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 패키지 경로 설정
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))  # app/utils
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))  # 프로젝트 루트
sys.path.append(PROJECT_ROOT)

from app.db import get_db
from app.utils.metrics import calculate_exposure_and_ratios, to_f
from common.kis_auth import get_kis_access_token, KISAuthError as KISTokenError

CONFIG_FILE = os.path.join(PROJECT_ROOT, "scheduler", "config.json")
with open(CONFIG_FILE, encoding="utf-8") as f:
    config_data = json.load(f)

# ---------------------------------------------------------------------------
# 설정 상수
# ---------------------------------------------------------------------------
MAX_QUERY_DAYS = 1000  # 조회 가능한 최대 날짜 범위 (일). 이 값을 초과하면 조회를 거부한다.

# ---------------------------------------------------------------------------
# 커스텀 예외
# ---------------------------------------------------------------------------
# KISTokenError는 common/kis_auth.py의 KISAuthError를 그대로 재노출한 것이다.
# (daily_snapshot.py 등 다른 파일이 `from app.utils.snap import KISTokenError`로
#  이 이름을 가져다 쓰고 있어, 토큰 발급 로직을 kis_auth로 옮기면서도 기존
#  import 경로/except 절이 깨지지 않도록 이름만 유지한다. 실제 예외 객체는 동일하다.)

class KRPriceFetchError(Exception):
    """
    KIS 국내 시세 API 자체가 실패한 경우 (네트워크 오류 등).
    "정말 가격이 0/데이터 없음"과 "API 호출 실패"를 구분하기 위함.
    있어선 안 되는 이상상황이므로 조용히 넘어가지 않고 상위로 전파한다.
    """
    pass

class YahooFetchError(Exception):
    """
    Yahoo Finance API 호출 자체가 실패한 경우 (네트워크/응답 오류 등).
    "해당 티커가 그 시점에 데이터가 없는 경우(상장 전 등)"와 "API 호출 실패"를
    구분하기 위함. 있어선 안 되는 이상상황이므로 조용히 넘어가지 않고 상위로 전파한다.
    """
    pass

# ---------------------------------------------------------------------------
# API 최적화를 위한 글로벌 캐시 저장소 (기존 로직 훼손 방지)
# ---------------------------------------------------------------------------
_GLOBAL_START_DATE_STR = None
_GLOBAL_END_DATE_STR = None
_KR_CACHE = {}
_YAHOO_CACHE = {}

# ---------------------------------------------------------------------------
# 배치 범위 설정 (+ 캐시 리셋을 한 덩어리로 강제)
# ---------------------------------------------------------------------------
def set_batch_range(start_date_str: str, end_date_str: str) -> None:
    """
    새 배치(날짜 범위)를 시작할 때 반드시 호출해야 한다.
    날짜 범위 설정과 캐시 초기화를 하나로 묶어, 호출하는 쪽이 캐시 리셋을
    깜빡할 수 없게 한다.

    (버그 배경: daily_inserter.py는 하나의 프로세스가 계속 살아있으면서
    _backfill()을 여러 번 호출한다. 캐시 초기화 없이 날짜 범위만 갱신하면,
    이전 배치에서 캐시된 티커는 재조회되지 않고 그대로 재사용되어 — 가격
    조회의 "target 이전 가장 최근값" fallback 로직 때문에 — 새 배치의
    날짜에 대해 예전 배치의(더 과거) 가격이 조용히 반환될 수 있다.)
    """
    global _GLOBAL_START_DATE_STR, _GLOBAL_END_DATE_STR, _KR_CACHE, _YAHOO_CACHE
    _GLOBAL_START_DATE_STR = start_date_str
    _GLOBAL_END_DATE_STR   = end_date_str
    _KR_CACHE = {}
    _YAHOO_CACHE = {}

# ---------------------------------------------------------------------------
# KIS API
# get_kis_access_token()은 common/kis_auth.py에서 import (Redis 캐시 + 락로 통합).
# 이 파일 자체 캐싱(_KR_CACHE 등)은 시세 데이터용이라 토큰과는 별개로 그대로 둔다.
# ---------------------------------------------------------------------------
def get_historical_kr_price(ticker: str, target_date_str: str, token: str) -> float:
    """KIS 국내주식 기간별시세"""
    global _KR_CACHE, _GLOBAL_START_DATE_STR, _GLOBAL_END_DATE_STR
    
    # 캐시에 없으면 100건씩 루프로 전체 기간 조회 (KIS KR API 1회 최대 100건 제한 대응)
    if ticker not in _KR_CACHE:
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
            raise KRPriceFetchError(
                f"[{ticker}] 조회 범위({total_days}일)가 최대치({MAX_QUERY_DAYS}일)를 초과합니다. "
                f"체크포인트/날짜 범위를 확인하세요."
            )
        loop_count = (total_days // 100) + 2  # 100건씩, 여유 2회 추가

        fetched_rows: list = []
        fetch_failed = False
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
                res_json = res.json()
                rt_cd = res_json.get("rt_cd")
                if rt_cd is not None and rt_cd != "0":
                    # HTTP 자체는 성공했지만 KIS API가 에러코드를 응답한 경우
                    # (예: 인증 실패, 점검중). output2가 비어서 그냥 "데이터 없음"으로
                    # 오인되지 않도록 여기서 명시적으로 실패 처리한다.
                    raise KRPriceFetchError(
                        f"[{ticker}] KIS API 오류 응답 (rt_cd={rt_cd}, msg={res_json.get('msg1')})"
                    )
                rows = res_json.get("output2", [])
                if not rows:
                    break
                fetched_rows.extend(rows)
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
                fetch_failed = True
                break

        # API 호출 자체가 실패했고, 그 결과 데이터를 하나도 못 채운 경우
        # → "정말 가격이 없는 상황"이 아니라 "장애/이상 상황"이므로
        #   0.0으로 조용히 넘어가지 않고 예외를 던져 상위(backfill)에서 처리하도록 한다.
        #   이때 캐시에는 아무 것도 등록하지 않는다 — 빈 상자를 남기면, 같은 배치 안에서
        #   이 티커를 다시 조회할 때 "이미 조회했다"고 오인해 재시도도, 재예외도 없이
        #   조용히 0.0을 반환하게 되므로(캐시 오염) 절대 남기지 않는다.
        if fetch_failed and not fetched_rows:
            raise KRPriceFetchError(
                f"[{ticker}] KIS 국내 시세 API 호출 실패 (target={target_date_str})"
            )

        # 성공(또는 부분 성공)한 경우에만 캐시에 등록한다.
        _KR_CACHE[ticker] = fetched_rows

    rows = _KR_CACHE[ticker]
    
    # --- [사용자님 원본 로직 유지 구역] ---
    for row in rows:
        if row.get("stck_bsop_date") == target_date_str:
            return float(row.get("stck_clpr", 0))
    for row in rows:
        if row.get("stck_bsop_date", "") <= target_date_str:
            return float(row.get("stck_clpr", 0))
    return 0.0



def get_historical_yahoo_index(ticker: str, target_date: datetime.date) -> float:
    """FX/INDEX/CRYPTO 야후 파이낸스 과거 종가"""
    global _YAHOO_CACHE, _GLOBAL_START_DATE_STR, _GLOBAL_END_DATE_STR
    
    if ticker not in _YAHOO_CACHE:
        fetch_failed = False
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
            cache_list.sort(key=lambda x: x[0])
            _YAHOO_CACHE[ticker] = cache_list
        except Exception as e:
            print(f"⚠️ [{ticker}] 지수/환율 조회 실패: {e}")
            fetch_failed = True
            _YAHOO_CACHE[ticker] = []

        # API 호출 자체가 실패한 경우(네트워크/파싱 오류)만 예외로 전파한다.
        # 호출은 성공했지만 그 시점에 데이터가 없는 경우(상장 전 등)는 정상적인
        # "매칭 없음"이므로 아래 fallback(0.0)을 그대로 신뢰한다.
        if fetch_failed:
            raise YahooFetchError(
                f"[{ticker}] Yahoo Finance API 호출 실패 (target={target_date})"
            )

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
    """
    매일 기록하는 구조이므로 '휴장일 스킵' 개념은 없다.
    가격이 0이면 daily_snapshot.py와 동일하게 fallback(최근 종가) 로직을 그대로 신뢰하고,
    API 호출 자체가 실패한 경우에만 KRPriceFetchError가 던져져 상위로 전파된다.
    """
    date_str = target_date.strftime("%Y%m%d")

    usd_krw = get_historical_yahoo_index('USDKRW=X', target_date)
    ndx100  = get_historical_yahoo_index('^NDX', target_date)
    
    if usd_krw == 0:
        usd_krw = 9999.0
        # 디버깅이 필요하도록 알림 발송
        _notify_telegram_alert(f"⚠️ {target_date} 환율 데이터 없음. 폴백 9999원 적용")

    final_db_rows = []

    for ticker, qty, leverage, market in position_rows:
        qty = to_f(qty)
        market_str = market.upper() if market else "KR"

        if ticker == "KRW":
            past_price = 1.0
        elif ticker == "USD":
            past_price = usd_krw
        elif market_str == "KR":
            past_price = get_historical_kr_price(ticker, date_str, token)
        else:
            past_price = get_historical_yahoo_index(ticker, target_date)

        final_db_rows.append((ticker, qty, past_price, leverage, market))

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

    # 전체 조회 구간 날짜 설정 + 캐시 리셋 (한 번의 호출로 묶어서 처리)
    set_batch_range(start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d"))

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