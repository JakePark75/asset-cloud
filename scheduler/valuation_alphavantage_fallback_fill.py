"""
Alpha Vantage 3순위 폴백 - 1회성 결측 보강 스크립트 (v2, 33차 세션 통합본)

통합 배경 (33차 세션):
- 기존 av_bulk_fill.py(operating_cash_flow, revenue / 하드코딩 대상 리스트 / SQL 파일만 생성)와
  alphavantage_fallback_fill.py(capex, eps_diluted / DB NULL 자동탐지 / DRY_RUN 직접실행)
  두 스크립트를 통합.
- 통합 시 DB 재확인 결과, 두 스크립트가 겨냥했던 결측 건은 전부 해소된 상태였음(2025-07-07 확인).
  하드코딩된 종목/날짜 리스트는 그 시점 스냅샷일 뿐이므로 전량 폐기.
- 남긴 것: "DB에서 NULL인 분기를 직접 조회해 자동 탐지" 방식(fallback_fill.py의 설계) +
  4개 필드(capex, eps_diluted, operating_cash_flow, revenue) 전체 커버.
- 남긴 이유: valuation_sec_edgar_backfill.py(SEC 1순위) 문서에 AV가 "3순위 폴백"으로 명시된 아키텍처
  원칙 자체는 계속 유효하기 때문. 즉 "이번에 채웠던 특정 건"은 끝났어도 "앞으로 SEC/FMP
  양쪽 다 구조적으로 못 채우는 결측이 새로 발견되면 이 스크립트로 메운다"는 역할은 유지됨.

역할 (변경 없음, 기존 원칙 유지):
- 상시 배치 아님. 이미 확인된 결측 구간만 채우는 1회성 보강용 (9차 세션 방침 유지).
- 대상 종목/분기를 하드코딩하지 않음. `SELECT DISTINCT symbol FROM fmp_quarterly_financials`로
  전체 종목을 얻고, 필드별로 실제 NULL인 분기만 DB에서 직접 조회 (실제 DB 상태가 진실의 원천).
- 기존 SEC/FMP 값은 절대 덮어쓰지 않음 (UPDATE 시 "AND {field} IS NULL" 가드 필수, 변경 없음).
- _tag_used 컬럼에 source=alphavantage 명시 기록 (기존 원칙 유지).
- capex와 operating_cash_flow는 같은 AV CASH_FLOW 엔드포인트를 쓰므로, 한 종목에 대해
  이 둘이 동시에 NULL이면 API 호출을 1회로 합쳐서 사용 (무료 API 호출 한도 절약).

*** 안전장치 (기존과 동일, 변경 없음) ***
- DRY_RUN = True로 먼저 실행해서 매칭 결과/채워질 값을 눈으로 확인할 것.

1) capex 부호: AV CASH_FLOW의 capitalExpenditures가 음수(현금유출 표기)로 올 수 있음.
   우리 DB의 capex는 양수(지출액) 컨벤션. abs()로 정규화하되, dry-run 출력에서 인접 분기
   capex 값과 스케일/부호가 맞는지 눈으로 대조할 것.

2) eps_diluted 분할조정 여부: AV EARNINGS의 reportedEPS가 소급조정된 값인지 원본인지
   문서상 불명확. 기본은 APPLY_SPLIT_ADJUSTMENT = False(무조정, 원본 그대로) 유지.
   dry-run에서 참고치와 대조 후 이상하면 켜기 전에 반드시 재확인.

사용법:
    1. DRY_RUN = True 상태로 실행 -> 콘솔 출력 검토
    2. 값이 타당하면 DRY_RUN = False로 바꿔서 재실행 -> 실제 UPDATE 수행
    3. 결과는 alphavantage_fill_log.json에 기록됨
"""

import json
import logging
import os
import time
from datetime import date

import psycopg2
import requests

# ---- 설정 ----
DB_CONFIG = dict(
    dbname="assetdb",
    user="jake",
    password="qkrworb0!",
    host="localhost",
)

ALPHA_VANTAGE_API_KEY = "KFHUIIVZ11P5LNV3"
ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"

# 안전장치: True면 DB에 아무것도 쓰지 않고 매칭 결과만 출력한다.
DRY_RUN = True

# eps_diluted 채울 때 split_multiplier를 추가로 적용할지 여부. 기본 False.
APPLY_SPLIT_ADJUSTMENT = False

# 날짜 매칭 허용 오차 (SEC period_end와 AV fiscalDateEnding이 며칠 어긋나는 경우 대비)
# 기존 원칙 유지: 10일 (AV가 fiscalDateEnding을 월말로 정규화하는 케이스 확인된 바 있음)
DATE_MATCH_TOLERANCE_DAYS = 10

# API 호출 간 대기 (무료 플랜 속도 제한: 5 calls/min, 25 calls/day)
AV_CALL_INTERVAL_SEC = 15

# 필드별 -> (AV function, 리포트 리스트 키, AV 필드명)
FIELD_ENDPOINT_MAP = {
    "capex": ("CASH_FLOW", "quarterlyReports", "capitalExpenditures"),
    "operating_cash_flow": ("CASH_FLOW", "quarterlyReports", "operatingCashflow"),
    "revenue": ("INCOME_STATEMENT", "quarterlyReports", "totalRevenue"),
    "eps_diluted": ("EARNINGS", "quarterlyEarnings", "reportedEPS"),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("av_fallback_fill")

fill_log = []  # 검증/기록용
_av_cache = {}  # (symbol, function) -> {date: raw_report_dict} , 같은 종목/함수 중복 호출 방지


# ---- 파일 기반 응답 캐시 (당일 TTL, 36차 세션 추가) ----
# 목적: API 호출 자체를 아끼려는 게 아니라(원래 로직도 이미 응답 공유로 절약함),
# 디버깅/재검토 중 스크립트를 같은 날 여러 번 재실행할 때(오늘처럼) 매번 API를
# 재호출하지 않도록 함. 파일명에 날짜가 박혀있어 날짜가 바뀌면 자동으로 새 캐시로
# 시작됨(= 하루 TTL). AV가 과거 분기 값을 재조회 시 다른 값으로 줄 가능성(정정 신고 등)이
# 있으므로 무기한 캐시는 하지 않음 - 딱 하루까지만 재사용.
def _cache_file_path() -> str:
    return f"av_response_cache_{date.today().isoformat()}.json"


def _load_file_cache() -> None:
    path = _cache_file_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"AV 응답 캐시 파일 로드 실패, 무시하고 새로 시작함: {e}")
        return

    for key_str, reports in raw.items():
        symbol, function = key_str.split("|", 1)
        parsed = {}
        for d_str, report in reports.items():
            try:
                parsed[date.fromisoformat(d_str)] = report
            except ValueError:
                continue
        _av_cache[(symbol, function)] = parsed
    log.info(f"AV 응답 캐시 파일 로드: {path} ({len(_av_cache)}개 (symbol,function) 조합, API 재호출 절약)")


def _save_file_cache() -> None:
    serializable = {}
    for (symbol, function), reports in _av_cache.items():
        serializable[f"{symbol}|{function}"] = {
            d.isoformat(): report for d, report in reports.items()
        }
    with open(_cache_file_path(), "w") as f:
        json.dump(serializable, f, ensure_ascii=False)


# ---- Alpha Vantage 호출 ----

def fetch_av_raw(symbol: str, function: str, list_key: str) -> dict:
    """AV 응답 원본을 {fiscalDateEnding(date): report_dict}로 반환. (symbol, function) 단위로 캐시.

    36차 세션 수정: 기존에는 이 함수가 value_key까지 받아 값 하나만 뽑은 뒤
    (symbol, function)만으로 캐싱했음. 그런데 capex/operating_cash_flow처럼
    같은 function(CASH_FLOW)을 쓰지만 value_key가 다른 필드가 같은 종목에서
    연달아 조회되면, 두 번째 호출이 캐시를 히트해서 첫 번째 필드의 값을
    그대로 반환하는 버그가 있었음(예: TSLA 2010-03-31 capex/ocf가 동일값으로
    나온 원인). raw 응답 전체를 캐싱하고 값 추출은 fetch_av_function에서
    하도록 분리해 API 호출 공유는 유지하면서 값 혼동을 제거함.
    """
    cache_key = (symbol, function)
    if cache_key in _av_cache:
        return _av_cache[cache_key]

    params = {"function": function, "symbol": symbol, "apikey": ALPHA_VANTAGE_API_KEY}
    resp = requests.get(ALPHA_VANTAGE_BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if list_key not in data:
        log.error(f"[{symbol}] {function} 응답에 {list_key} 없음: {data}")
        _av_cache[cache_key] = {}
        _save_file_cache()
        time.sleep(AV_CALL_INTERVAL_SEC)
        return {}

    result = {}
    for report in data[list_key]:
        try:
            end = date.fromisoformat(report["fiscalDateEnding"])
        except (KeyError, ValueError):
            continue
        result[end] = report  # raw dict 전체 보관 (값 추출은 호출부에서)

    _av_cache[cache_key] = result
    _save_file_cache()
    time.sleep(AV_CALL_INTERVAL_SEC)
    return result


def fetch_av_function(symbol: str, function: str, list_key: str, value_key: str) -> dict:
    """AV 응답에서 특정 value_key만 {date: float}로 추출. raw 응답은 fetch_av_raw가 캐시 공유."""
    raw = fetch_av_raw(symbol, function, list_key)
    result = {}
    for end, report in raw.items():
        val = report.get(value_key)
        if val in (None, "None", ""):
            continue
        result[end] = float(val)
    return result


def _match_date(target: date, av_data: dict):
    """target과 정확히 일치하는 날짜가 없으면 허용오차 내 가장 가까운 날짜를 찾는다."""
    if target in av_data:
        return target, av_data[target]
    best, best_diff = None, None
    for d in av_data:
        diff = abs((d - target).days)
        if diff <= DATE_MATCH_TOLERANCE_DAYS and (best_diff is None or diff < best_diff):
            best, best_diff = d, diff
    if best is not None:
        return best, av_data[best]
    return None, None


def _nearest_ignoring_tolerance(target: date, av_data: dict):
    """진단용: 허용오차 무시하고 가장 가까운 날짜/차이일수 반환 (진짜 결측 vs 허용오차 부족 구분용)."""
    if not av_data:
        return None, None
    best = min(av_data.keys(), key=lambda d: abs((d - target).days))
    return best, (best - target).days


# ---- DB 조회/갱신 ----

def get_all_symbols(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT symbol FROM fmp_quarterly_financials ORDER BY symbol")
        return [row[0] for row in cur.fetchall()]


def get_null_quarters(conn, symbol: str, field: str):
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT fiscal_quarter_end FROM fmp_quarterly_financials "
            f"WHERE symbol = %s AND {field} IS NULL ORDER BY fiscal_quarter_end",
            (symbol,),
        )
        return [row[0] for row in cur.fetchall()]


def get_operating_cash_flow(conn, symbol: str, quarter_end: date):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT operating_cash_flow FROM fmp_quarterly_financials "
            "WHERE symbol = %s AND fiscal_quarter_end = %s",
            (symbol, quarter_end),
        )
        row = cur.fetchone()
        return row[0] if row else None


def apply_simple_fill(conn, symbol: str, quarter_end: date, field: str, val: float, tag: str):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE fmp_quarterly_financials
            SET {field} = %s,
                {field}_tag_used = %s,
                collected_at = now()
            WHERE symbol = %s AND fiscal_quarter_end = %s AND {field} IS NULL
            """,
            (val, tag, symbol, quarter_end),
        )
        return cur.rowcount


def apply_capex_fill(conn, symbol: str, quarter_end: date, capex_val: float, tag: str):
    """capex는 free_cash_flow 재계산이 딸려있어 별도 처리."""
    ocf = get_operating_cash_flow(conn, symbol, quarter_end)
    fcf = (float(ocf) - capex_val) if ocf is not None else None
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE fmp_quarterly_financials
            SET capex = %s,
                capex_tag_used = %s,
                free_cash_flow = %s,
                collected_at = now()
            WHERE symbol = %s AND fiscal_quarter_end = %s AND capex IS NULL
            """,
            (capex_val, tag, fcf, symbol, quarter_end),
        )
        return cur.rowcount


# ---- 메인 로직 (필드 공통 처리) ----

def fill_field(conn, symbol: str, field: str):
    null_quarters = get_null_quarters(conn, symbol, field)
    if not null_quarters:
        return  # 이 종목/필드는 대상 아님 - API 호출 자체를 안 함 (호출 한도 절약)

    function, list_key, value_key = FIELD_ENDPOINT_MAP[field]
    log.info(f"[{symbol}] {field} NULL {len(null_quarters)}개 분기 발견: {[q.isoformat() for q in null_quarters]}")

    av_data = fetch_av_function(symbol, function, list_key, value_key)
    log.info(f"[{symbol}] AV {function} {len(av_data)}개 분기 확보 (캐시 공유 가능)")

    matched, unmatched = 0, []
    for q in null_quarters:
        matched_date, raw_val = _match_date(q, av_data)
        if raw_val is None:
            unmatched.append(q)
            continue

        applied_val = raw_val
        if field == "capex":
            applied_val = abs(raw_val)  # 부호 불확실 -> 양수(지출액) 컨벤션 정규화
        if field == "eps_diluted" and APPLY_SPLIT_ADJUSTMENT:
            log.warning("APPLY_SPLIT_ADJUSTMENT=True 이지만 분할조정 로직 미구현 - 원본값 그대로 사용")

        tag = f"alphavantage_{function}|source=alphavantage"
        entry = {
            "symbol": symbol,
            "field": field,
            "fiscal_quarter_end": q.isoformat(),
            "av_matched_date": matched_date.isoformat(),
            "av_raw_val": raw_val,
            "applied_val": applied_val,
            "dry_run": DRY_RUN,
            "matched": True,
        }

        if DRY_RUN:
            log.info(f"  [DRY_RUN] {field} {q} <- AV({matched_date}) raw={raw_val} -> applied={applied_val}")
        else:
            if field == "capex":
                rc = apply_capex_fill(conn, symbol, q, applied_val, tag)
            else:
                rc = apply_simple_fill(conn, symbol, q, field, applied_val, tag)
            entry["rows_updated"] = rc
            log.info(f"  [APPLIED] {field} {q} <- AV({matched_date}) val={applied_val} (rows_updated={rc})")

        fill_log.append(entry)
        matched += 1

    if unmatched:
        log.warning(f"[{symbol}] {field} 매칭 실패 {len(unmatched)}건 - 진단 정보:")
        for q in unmatched:
            near_date, diff_days = _nearest_ignoring_tolerance(q, av_data)
            entry = {
                "symbol": symbol,
                "field": field,
                "fiscal_quarter_end": q.isoformat(),
                "matched": False,
                "dry_run": DRY_RUN,
            }
            if near_date is None:
                log.warning(f"  {q}: AV에 해당 종목 데이터 자체가 없음 (진짜 결측 가능성 높음)")
                entry["reason"] = "av_no_data_for_symbol"
            else:
                rescueable = abs(diff_days) <= 15
                log.warning(
                    f"  {q}: 허용오차({DATE_MATCH_TOLERANCE_DAYS}일) 밖 최근접 AV 날짜={near_date} "
                    f"(차이 {diff_days:+d}일, 값={av_data[near_date]}) "
                    f"-> {'허용오차 확대로 구제 가능' if rescueable else '거리가 멀어 진짜 결측일 가능성 높음'}"
                )
                entry["reason"] = "date_gap_too_large"
                entry["nearest_av_date"] = near_date.isoformat()
                entry["nearest_av_val"] = av_data[near_date]
                entry["diff_days"] = diff_days
                entry["rescueable_with_wider_tolerance"] = rescueable
            fill_log.append(entry)
    log.info(f"[{symbol}] {field} 매칭 완료: {matched}/{len(null_quarters)}")

    if not DRY_RUN and matched:
        conn.commit()


def main():
    if DRY_RUN:
        log.warning("=== DRY_RUN 모드: DB에 아무것도 쓰지 않고 매칭 결과만 출력함 ===")
    else:
        log.warning("=== 실제 적용 모드: DB UPDATE 수행함 ===")

    _load_file_cache()

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        symbols = get_all_symbols(conn)
        log.info(f"대상 종목 (DB 실제 보유 기준, 하드코딩 없음): {symbols}")

        for symbol in symbols:
            for field in FIELD_ENDPOINT_MAP:
                fill_field(conn, symbol, field)
    finally:
        conn.close()

    if fill_log:
        n_matched = sum(1 for e in fill_log if e.get("matched"))
        n_unmatched = sum(1 for e in fill_log if not e.get("matched"))
        with open("alphavantage_fill_log.json", "w") as f:
            json.dump(fill_log, f, indent=2, ensure_ascii=False)
        log.info(
            f"=== 결과 {len(fill_log)}건 alphavantage_fill_log.json 저장 "
            f"(매칭 성공 {n_matched}건 / 매칭 실패-미해결 {n_unmatched}건) ==="
        )
    else:
        log.info("=== 매칭된 결과 없음 (모든 필드/종목 NULL 없음 - 대상 자체가 없었음) ===")


if __name__ == "__main__":
    main()