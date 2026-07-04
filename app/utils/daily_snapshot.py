"""
daily_snapshot.py
특정일 기준 "가장 최근 종가"를 KIS/Yahoo API로 조회하여
daily_summary 1행분 데이터를 계산해 dict로 반환한다.

반환 dict 키:
    date, total_asset, usd_krw, ndx100,
    exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio, twr_asset
    cash_flow, cash_flow_note 는 포함하지 않음 (사용자 수동 입력)
"""

import datetime
import calendar
import json
import requests
import urllib3
from pathlib import Path

from app.utils.snap import KISTokenError, KRPriceFetchError, YahooFetchError
from common.notify import notify_telegram_alert as _notify_telegram_alert

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from app.db import get_db, get_config, get_market_currency
from app.utils.metrics import calculate_exposure_and_ratios, to_f

# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
CONFIG = get_config()

# ---------------------------------------------------------------------------
# API 캐시 (프로세스 수명 동안 유지)
# ---------------------------------------------------------------------------
_KR_CACHE: dict = {}
_US_CACHE: dict = {}
_YAHOO_CACHE: dict = {}
_TOKEN: str | None = None

# ---------------------------------------------------------------------------
# KIS 토큰
# ---------------------------------------------------------------------------
def _get_token() -> str:
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    try:
        res = requests.post(
            "https://openapi.koreainvestment.com:9443/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey":     CONFIG["kis_app_key"],
                "appsecret":  CONFIG["kis_app_secret"],
            },
            timeout=10,
            verify=False,
        )
        token = res.json().get("access_token")
    except Exception as e:
        raise KISTokenError(f"토큰 요청 실패: {e}")

    if not token:
        raise KISTokenError(f"토큰 응답에 access_token 없음: {res.text[:200]}")

    _TOKEN = token
    return _TOKEN

# ---------------------------------------------------------------------------
# KIS 국내주식 과거 종가 (100건 루프, fallback: 가장 최근 종가)
# ---------------------------------------------------------------------------
def _get_kr_price(ticker: str, target_date_str: str, token: str) -> float:
    if ticker not in _KR_CACHE:
        _KR_CACHE[ticker] = []
        url = (
            "https://openapi.koreainvestment.com:9443"
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        )
        headers = {
            "authorization": f"Bearer {token}",
            "appkey":        CONFIG["kis_app_key"],
            "appsecret":     CONFIG["kis_app_secret"],
            "tr_id":         "FHKST03010100",
            "custtype":      "P",
        }
        # 넉넉히 30일 전부터 조회
        end_dt = datetime.datetime.strptime(target_date_str, "%Y%m%d")
        start_dt = end_dt - datetime.timedelta(days=30)
        current_end = end_dt.strftime("%Y%m%d")

        fetch_failed = False
        for _ in range(3):
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         ticker,
                "FID_ORG_ADJ_PRC":        "1",
                "FID_PERIOD_DIV_CODE":    "D",
                "FID_INPUT_DATE_1":       start_dt.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2":       current_end,
            }
            try:
                res_json = requests.get(
                    url, headers=headers, params=params, timeout=10, verify=False
                ).json()
                rt_cd = res_json.get("rt_cd")
                if rt_cd is not None and rt_cd != "0":
                    raise KRPriceFetchError(
                        f"[{ticker}] KIS API 오류 응답 (rt_cd={rt_cd}, msg={res_json.get('msg1')})"
                    )
                rows = res_json.get("output2", [])
                if not rows:
                    break
                _KR_CACHE[ticker].extend(rows)
                oldest_str = rows[-1].get("stck_bsop_date", "")
                if not oldest_str:
                    break
                oldest_dt = datetime.datetime.strptime(oldest_str, "%Y%m%d")
                if oldest_dt <= start_dt:
                    break
                current_end = (oldest_dt - datetime.timedelta(days=1)).strftime("%Y%m%d")
            except Exception as e:
                print(f"⚠️ [{ticker}] KR 시세 조회 실패: {e}")
                fetch_failed = True
                break

        # API 호출 자체가 실패했고, 그 결과 캐시에 아무 데이터도 못 채운 경우
        # → "정말 가격이 없는 상황"이 아니라 "장애로 못 가져온 상황"이므로
        #   0.0으로 조용히 넘어가지 않고 예외를 던져 상위에서 INSERT를 막는다.
        if fetch_failed and not _KR_CACHE[ticker]:
            raise KRPriceFetchError(f"[{ticker}] KIS 국내 시세 API 호출 실패 (target={target_date_str})")

    rows = _KR_CACHE[ticker]
    # 정확히 일치하는 날짜 우선, 없으면 target_date 이전 가장 최근값
    for row in rows:
        if row.get("stck_bsop_date") == target_date_str:
            return float(row.get("stck_clpr", 0))
    for row in rows:
        if row.get("stck_bsop_date", "") <= target_date_str:
            return float(row.get("stck_clpr", 0))
    return 0.0

# ---------------------------------------------------------------------------
# KIS 해외주식 과거 종가 (fallback: 가장 최근 종가)
# NOTE: 2026-07 기준 get_daily_snapshot()에서는 해외 종목 조회에 이 함수 대신
#       _get_yahoo_price()를 사용 중이라 실제로는 호출되지 않는 코드다(dead code).
#       그래도 API 실패 시 조용히 0.0으로 넘어가면 나중에 이 함수가 다시
#       연결됐을 때 조용한 데이터 오염으로 이어질 수 있어 방어 로직은 유지한다.
#       실제로 안 쓰는 게 확실하면 삭제 후보.
# ---------------------------------------------------------------------------
def _get_us_price(ticker: str, excd: str, target_date_str: str, token: str) -> float:
    if ticker not in _US_CACHE:
        _US_CACHE[ticker] = []
        url = (
            "https://openapi.koreainvestment.com:9443"
            "/uapi/overseas-price/v1/quotations/dailyprice"
        )
        headers = {
            "authorization": f"Bearer {token}",
            "appkey":        CONFIG["kis_app_key"],
            "appsecret":     CONFIG["kis_app_secret"],
            "tr_id":         "HHDFS76240000",
            "custtype":      "P",
        }
        current_end = target_date_str
        fetch_failed = False
        for _ in range(3):
            params = {
                "AUTH": "", "EXCD": excd, "SYMB": ticker,
                "GUBN": "0", "BYMD": current_end, "MODP": "1",
            }
            try:
                rows = requests.get(
                    url, headers=headers, params=params, timeout=10, verify=False
                ).json().get("output2", [])
                if not rows:
                    break
                # raw 응답 첫 번째 행 전체 필드 출력 (디버깅용)
                if not _US_CACHE[ticker]:
                    print(f"  [{ticker}] raw 응답 첫행: {rows[0]}")
                _US_CACHE[ticker].extend(rows)
                dt = datetime.datetime.strptime(
                    rows[-1].get("xymd"), "%Y%m%d"
                ) - datetime.timedelta(days=1)
                current_end = dt.strftime("%Y%m%d")
            except Exception as e:
                print(f"⚠️ [{ticker}] US 시세 조회 실패: {e}")
                fetch_failed = True
                break

        # API 호출 자체가 실패했고 캐시에 아무 데이터도 못 채운 경우
        # → KR/Yahoo와 동일하게 0.0으로 조용히 넘어가지 않고 예외를 던진다.
        if fetch_failed and not _US_CACHE[ticker]:
            raise KRPriceFetchError(
                f"[{ticker}] KIS 해외 시세 API 호출 실패 (target={target_date_str})"
            )

    rows = _US_CACHE[ticker]
    # 조회된 날짜 목록 로그
    dates = [r.get("xymd") for r in rows[:5]]
    print(f"  [{ticker}] API 반환 날짜(최근5): {dates}, target: {target_date_str}")
    for row in rows:
        if row.get("xymd") == target_date_str:
            price = float(row.get("clos", 0))
            print(f"  [{ticker}] 정확히 일치 → {target_date_str} 종가: {price}")
            return price
    for row in rows:
        if row.get("xymd", "") <= target_date_str:
            price = float(row.get("clos", 0))
            print(f"  [{ticker}] fallback → {row.get('xymd')} 종가: {price}")
            return price
    return 0.0

# ---------------------------------------------------------------------------
# Yahoo Finance 과거 종가 (FX/INDEX/CRYPTO, fallback: 가장 최근 종가)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Yahoo Finance 과거 종가 (FX/INDEX/CRYPTO, fallback: 가장 최근 종가)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Yahoo Finance 과거 종가 (FX/INDEX/CRYPTO, fallback: 가장 최근 종가)
# ---------------------------------------------------------------------------
def _get_yahoo_price(ticker: str, target_date: datetime.date) -> float:
    from datetime import datetime as dt_cls, timezone, timedelta

    if ticker not in _YAHOO_CACHE:
        fetch_failed = False
        try:
            end_dt = dt_cls(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
            start_dt = end_dt - timedelta(days=15)

            start_ts = int(start_dt.timestamp())
            end_ts   = int(end_dt.timestamp()) + 86400 * 5

            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                f"?period1={start_ts}&period2={end_ts}&interval=1d"
            )
            res = requests.get(
                url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10, verify=False
            ).json()
            result = res.get("chart", {}).get("result")

            cache_list = []
            if result and result[0].get("indicators", {}).get("quote"):
                ts_list = result[0].get("timestamp", [])
                closes  = result[0]["indicators"]["quote"][0].get("close", [])
                for ts, c in zip(ts_list, closes):
                    if c is not None:
                        cache_list.append((ts, float(c)))
                        
            cache_list.sort(key=lambda x: x[0])
            _YAHOO_CACHE[ticker] = cache_list
        except Exception as e:
            print(f"⚠️ [{ticker}] Yahoo 시세 조회 실패: {e}")
            fetch_failed = True
            _YAHOO_CACHE[ticker] = []

        # API 호출 자체가 실패한 경우(네트워크/파싱 오류)만 예외로 전파한다.
        # 호출은 성공했지만 그 시점에 데이터가 없는 경우(상장 전 등)는 정상적인
        # "매칭 없음"이므로 아래 fallback(0.0)을 그대로 신뢰한다.
        if fetch_failed:
            raise YahooFetchError(
                f"[{ticker}] Yahoo Finance API 호출 실패 (target={target_date})"
            )

    cache_list = _YAHOO_CACHE[ticker]
    target_str = target_date.strftime("%Y-%m-%d")

    matched = []
    for ts, c in cache_list:
        dt_utc = dt_cls.fromtimestamp(ts, tz=timezone.utc)
        dt_utc_str = dt_utc.strftime("%Y-%m-%d")
        
        if dt_utc_str <= target_str:
            # 로그 출력을 위해 날짜와 시각 문자열을 모두 보관
            dt_full_str = dt_utc.strftime("%Y-%m-%d %H:%M:%S")
            matched.append((dt_utc_str, dt_full_str, c))

    if matched:
        # 로그에 [YYYY-MM-DD HH:MM:SS UTC] 형태로 상세 출력하도록 변경
        print(f"  [{ticker}] Yahoo(UTC 매칭) → {matched[-1][1]} UTC | 가격: {matched[-1][2]}")
        return matched[-1][2]
        
    print(f"  [{ticker}] Yahoo → 매칭 없음")
    return 0.0

# ---------------------------------------------------------------------------
# DB 헬퍼
# ---------------------------------------------------------------------------
def _fetch_positions() -> list:
    """(ticker, quantity, leverage, market, account_id, is_watch) 목록"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.ticker, p.quantity, t.leverage, t.market,
                       p.account_id, a.is_watch
                FROM positions p
                LEFT JOIN tickers t ON p.ticker = t.ticker
                LEFT JOIN accounts a ON p.account_id = a.id
            """)
            return cur.fetchall()

def _fetch_prev_summary(date: datetime.date) -> tuple | None:
    """전날 (total_asset, twr_asset) 반환"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT total_asset, twr_asset
                FROM daily_summary
                WHERE date = %s
            """, (date,))
            return cur.fetchone()

# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def get_daily_snapshot(target_date: datetime.date, calc_account_totals: bool = False) -> dict:
    """
    target_date 기준 가장 최근 종가로 daily_summary 1행분 데이터를 계산한다.
    cash_flow / cash_flow_note 는 포함하지 않는다.
    """
    # 상시 실행 프로세스(daily_inserter)에서 호출 시 전날 캐시가 남아있으면
    # target_date 종가 대신 전날 종가로 계산되는 버그 방지 → 매 호출마다 초기화
    global _KR_CACHE, _US_CACHE, _YAHOO_CACHE
    _KR_CACHE = {}
    _US_CACHE = {}
    _YAHOO_CACHE = {}

    date_str = target_date.strftime("%Y%m%d")
    token = _get_token()

    # 환율 / NDX100
    usd_krw = _get_yahoo_price("USDKRW=X", target_date)
    if usd_krw is None:
        usd_krw = 9999.0
        # 디버깅이 필요하도록 알림 발송
        _notify_telegram_alert(f"⚠️ {target_date} 환율 데이터 없음. 폴백 9999원 적용")
        
    ndx100  = _get_yahoo_price("^NDX",     target_date)

    # 포지션별 시세 조회
    position_rows = _fetch_positions()
    db_rows = []          # is_watch=false 전체 (total_asset 계산용)
    account_rows = {}     # {account_id: [(ticker, qty, price, leverage, market), ...]}

    for ticker, qty, leverage, market, account_id, is_watch in position_rows:
        market_str = (market or "KR").upper()

        if ticker == "KRW":
            price = 1.0
        elif ticker == "USD":
            price = usd_krw
        elif market_str == "KR":
            price = _get_kr_price(ticker, date_str, token)
        else:
            price = _get_yahoo_price(ticker, target_date)

        # is_watch=false만 전체 합산용에 추가
        if not is_watch:
            db_rows.append((ticker, qty, price, leverage, market))

        # 계좌별은 전체 추가
        if account_id not in account_rows:
            account_rows[account_id] = []
        account_rows[account_id].append((ticker, qty, price, leverage, market))

    ratios = calculate_exposure_and_ratios(db_rows, usd_krw)
    total_asset = ratios["total_asset"]

    # ── 스냅샷 상세 로그 ──────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"📅 스냅샷 날짜   : {target_date}")
    print(f"💱 환율 (USD/KRW): {usd_krw:,.2f}")
    print(f"{'─'*55}")

    cash_krw = sum(to_f(qty) for ticker, qty, price, lev, mkt in db_rows if ticker == "KRW")
    cash_usd = sum(to_f(qty) for ticker, qty, price, lev, mkt in db_rows if ticker == "USD")
    print(f"💵 현금 KRW      : {cash_krw:>20,.0f} 원")
    print(f"💵 현금 USD      : {cash_usd:>20,.2f} USD  ({cash_usd * usd_krw:>15,.0f} 원)")
    print(f"{'─'*55}")
    print(f"{'종목':<10} {'수량':>12} {'종가':>14} {'평가액(원)':>18}")
    print(f"{'─'*55}")
    for ticker, qty, price, lev, mkt in db_rows:
        if ticker in ("KRW", "USD"):
            continue
        qty_f = to_f(qty)
        price_f = to_f(price)
        if get_market_currency((mkt or "").upper()) == "USD":
            eval_krw = qty_f * price_f * usd_krw
        else:
            eval_krw = qty_f * price_f
        print(f"{ticker:<10} {qty_f:>12,.4f} {price_f:>14,.4f} {eval_krw:>18,.0f}")
    print(f"{'─'*55}")
    print(f"{'총자산':<10} {'':>12} {'':>14} {total_asset:>18,.0f} 원")
    print(f"{'='*55}\n")

    # TWR 계산
    prev = _fetch_prev_summary(target_date - datetime.timedelta(days=1))
    if prev is None:
        twr_asset = total_asset
    else:
        prev_total = to_f(prev[0])
        prev_twr   = to_f(prev[1])
        # cash_flow 는 이 시점에 알 수 없으므로 0으로 계산
        # inserter가 INSERT 후 cash_flow 가 입력되면 history 화면의 twr 재계산 로직이 보정
        twr_asset = prev_twr * ((total_asset / prev_total) if prev_total else 1.0)

    # 계좌별 총자산 계산 (감시 계좌 포함) — 필요할 때만 수행
    account_totals = {}
    if calc_account_totals:
        for acc_id, rows in account_rows.items():
            acc_total = 0.0
            for t, q, p, lev, mkt in rows:
                qty_f   = to_f(q)
                price_f = to_f(p)
                mkt_str = (mkt or "").upper()
                if t == "KRW":
                    acc_total += qty_f
                elif t == "USD":
                    acc_total += qty_f * usd_krw
                elif get_market_currency(mkt_str) == "USD":
                    acc_total += qty_f * price_f * usd_krw
                else:
                    acc_total += qty_f * price_f
            account_totals[acc_id] = acc_total

    return {
        "date":        target_date,
        "total_asset": total_asset,
        "usd_krw":     usd_krw,
        "ndx100":      ndx100,
        "exposure":    ratios["exposure"],
        "cash_ratio":  ratios["cash_ratio"],
        "x1_ratio":    ratios["x1_ratio"],
        "x2_ratio":    ratios["x2_ratio"],
        "x3_ratio":    ratios["x3_ratio"],
        "twr_asset":   twr_asset,
        "account_totals": account_totals,
    }