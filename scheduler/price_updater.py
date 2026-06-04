import json
import logging
import os
import threading
import time
from datetime import datetime, date, time as dt_time  # time 모듈과 이름 충돌 방지
from logging.handlers import RotatingFileHandler

import psycopg2
import requests
import pytz

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# 경로 설정
# ---------------------------------------------------------------------------
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_FILE    = os.path.join(BASE_DIR, "price_updater.log")

# ---------------------------------------------------------------------------
# 로깅
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=1*1024*1024, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 전역 상태
# ---------------------------------------------------------------------------
config        = {}
access_token  = None
token_expires = 0
token_lock    = threading.Lock()  # 멀티스레드 환경에서 토큰 공유


# ---------------------------------------------------------------------------
# 설정 로드
# ---------------------------------------------------------------------------
def load_config():
    global config
    if not os.path.exists(CONFIG_FILE):
        log.error(f"config.json 없음: {CONFIG_FILE}")
        raise FileNotFoundError(CONFIG_FILE)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        config = json.load(f)
    config.setdefault("interval", 1)
    log.info("config.json 로드 완료")


# ---------------------------------------------------------------------------
# DB 연결
# ---------------------------------------------------------------------------
def get_db_conn():
    return psycopg2.connect(
        host="localhost",
        dbname="assetdb",
        user="jake",
        password=config["db_password"],
    )

# ---------------------------------------------------------------------------
# 공휴일 캐시
# ---------------------------------------------------------------------------
class HolidayCache:
    """
    매일 08:00 KST에 한국/미국 공휴일을 조회하여 캐싱.
    - 한국: 공공데이터포털 특일 API
    - 미국: Finnhub market-holiday API
    당일 날짜가 공휴일이면 해당 시장을 휴장으로 판단.
    """

    def __init__(self):
        self._lock         = threading.Lock()
        self._fetched_date = None          # 마지막으로 조회한 날짜 (date 객체)
        self._kr_holidays  = set()         # {date, ...}
        self._us_holidays  = set()         # {date, ...}
        self._last_fetch_hour = None       # 마지막 조회 시각(시)

    # ------------------------------------------------------------------
    # 한국 특일 API 조회
    # ------------------------------------------------------------------
    def _fetch_kr_holidays(self, year: int, month: int) -> set:
        """공공데이터포털 특일 API → isHoliday=Y 인 날짜 집합 반환"""
        key = config.get("data_go_kr_key", "")
        if not key:
            log.warning("data_go_kr_key 미설정 — 한국 공휴일 조회 건너뜀")
            return set()

        url = "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getHoliDeInfo"
        params = {
            "serviceKey": key,
            "solYear":    year,
            "solMonth":   f"{month:02d}",
            "numOfRows":  50,
            "_type":      "json",
        }
        try:
            res  = requests.get(url, params=params, timeout=10)
            data = res.json()
            items = data["response"]["body"]["items"]
            # 항목이 없으면 빈 dict 또는 "" 반환됨
            if not items or items == "":
                return set()
            item_list = items["item"]
            if isinstance(item_list, dict):   # 항목이 1개면 dict로 옴
                item_list = [item_list]
            holidays = set()
            for item in item_list:
                if item.get("isHoliday") == "Y":
                    d = str(item["locdate"])  # 예: "20250127"
                    holidays.add(date(int(d[:4]), int(d[4:6]), int(d[6:])))
            return holidays
        except Exception as e:
            log.error(f"한국 공휴일 조회 실패: {e}")
            return set()

    # ------------------------------------------------------------------
    # 미국 Finnhub market-holiday 조회
    # ------------------------------------------------------------------
    def _fetch_us_holidays(self) -> set:
        """Finnhub market-holiday API → tradingHour='' (완전 휴장) 날짜 집합 반환"""
        key = config.get("finnhub_api_key", "")
        if not key:
            log.warning("finnhub_api_key 미설정 — 미국 공휴일 조회 건너뜀")
            return set()

        url = f"https://finnhub.io/api/v1/stock/market-holiday?exchange=US&token={key}"
        try:
            res  = requests.get(url, timeout=10)
            data = res.json()
            holidays = set()
            for item in data.get("data", []):
                # tradingHour가 빈 문자열이면 완전 휴장
                if item.get("tradingHour", "") == "":
                    d = item["atDate"]  # "YYYY-MM-DD"
                    holidays.add(date.fromisoformat(d))
            return holidays
        except Exception as e:
            log.error(f"미국 공휴일 조회 실패: {e}")
            return set()

    # ------------------------------------------------------------------
    # 캐시 갱신 (매일 08:00 KST 기준)
    # ------------------------------------------------------------------
    def refresh_if_needed(self):
        kst = pytz.timezone("Asia/Seoul")
        now_kst = datetime.now(kst)
        today   = now_kst.date()

        with self._lock:
            # 오늘 날짜로 이미 조회했으면 스킵
            if self._fetched_date == today:
                return

            # 08:00 이전이면 아직 조회하지 않음 (당일 첫 실행 전)
            if now_kst.hour < 8:
                return

            log.info(f"공휴일 캐시 갱신 시작 — {today}")

            kr = self._fetch_kr_holidays(today.year, today.month)
            us = self._fetch_us_holidays()

            self._kr_holidays  = kr
            self._us_holidays  = us
            self._fetched_date = today

            log.info(f"한국 공휴일: {sorted(kr)}")
            log.info(f"미국 공휴일: {sorted(us)}")

    # ------------------------------------------------------------------
    # 공휴일 여부 조회
    # ------------------------------------------------------------------
    def is_kr_holiday(self, d: date = None) -> bool:
        d = d or date.today()
        with self._lock:
            return d in self._kr_holidays

    def is_us_holiday(self, d: date = None) -> bool:
        d = d or date.today()
        with self._lock:
            return d in self._us_holidays


# 전역 캐시 인스턴스
holiday_cache = HolidayCache()


# ---------------------------------------------------------------------------
# 공통 함수: 시장 상태 판단
# 반환값: "open" | "pre" | "after" | "closed"
# ---------------------------------------------------------------------------
def get_market_status(market: str) -> str:
    """
    시장 상태를 반환한다.
    - open  : 정규장 중
    - pre   : 프리마켓 (미국만 해당)
    - after : 애프터마켓 (미국만 해당) 또는 한국 장 마감 후 (종가 미확정 구간)
    - closed: 완전 휴장 (장 전 조기시간 / 주말 / 공휴일 / 야간)
    FX/CRYPTO/INDEX는 항상 "open" 반환 (24시간)
    """
    if market in ("FX", "CRYPTO", "INDEX"):
        return "open"

    if market == "KR":
        tz = pytz.timezone("Asia/Seoul")
        now_local   = datetime.now(tz)
        today_local = now_local.date()

        if now_local.weekday() >= 5:
            return "closed"
        if holiday_cache.is_kr_holiday(today_local):
            return "closed"

        now_min = now_local.hour * 60 + now_local.minute
        # 정규장: 09:00 ~ 15:30
        if dt_time(9, 0) <= now_local.time() <= dt_time(15, 30):
            return "open"
        # after: 15:30 초과 ~ 18:00 (종가 확정 대기 구간)
        if dt_time(15, 30) < now_local.time() <= dt_time(18, 0):
            return "after"
        return "closed"

    if market in ("NAS", "NYS", "AMS", "ARC"):
        tz = pytz.timezone("America/New_York")
        now_local   = datetime.now(tz)
        today_local = now_local.date()

        if now_local.weekday() >= 5:
            return "closed"
        if holiday_cache.is_us_holiday(today_local):
            return "closed"

        # 프리마켓: 04:00 ~ 09:30
        if dt_time(4, 0) <= now_local.time() < dt_time(9, 30):
            return "pre"
        # 정규장: 09:30 ~ 16:00
        if dt_time(9, 30) <= now_local.time() <= dt_time(16, 0):
            return "open"
        # 애프터마켓: 16:00 초과 ~ 20:00
        if dt_time(16, 0) < now_local.time() <= dt_time(20, 0):
            return "after"
        return "closed"

    # 알 수 없는 market
    return "open"


def is_market_open(market: str) -> bool:
    """하위 호환용. get_market_status() == 'open' 과 동일."""
    return get_market_status(market) == "open"


# ---------------------------------------------------------------------------
# 종가 확정 조회 함수
# ---------------------------------------------------------------------------
def get_confirmed_close_kr(ticker: str):
    """
    KR 당일 종가 확정 여부 확인.
    output2에 오늘 날짜 데이터가 있으면 (종가, change_pct) 반환.
    미확정이면 None 반환.
    """
    token    = get_access_token()
    today    = datetime.now(pytz.timezone("Asia/Seoul")).strftime("%Y%m%d")
    url      = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-daily-price"
    headers  = {
        "authorization": f"Bearer {token}",
        "appkey":        config["kis_app_key"],
        "appsecret":     config["kis_app_secret"],
        "tr_id":         "FHKST03010100",
        "custtype":      "P",
    }
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd":         ticker,
        "fid_org_adj_prc":        "1",
        "fid_period_div_code":    "D",
        "fid_input_date_1":       today,
        "fid_input_date_2":       today,
    }
    try:
        res   = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
        rows  = res.json().get("output2", [])
        if not rows:
            return None
        row = rows[0]
        if row.get("stck_bsop_date") != today:
            return None
        price      = float(row.get("stck_clpr", 0))
        change_pct = float(row.get("prdy_vrss_sign", 0))
        # prdy_vrss_sign은 부호코드(1~5), 실제 등락률은 output1의 prdy_ctrt 사용
        # 여기서는 output2에 등락률이 없으므로 prdy_vrss로 계산
        prdy_clpr  = float(res.json().get("output1", {}).get("stck_prdy_clpr", 0) or 0)
        if prdy_clpr:
            change_pct = round((price - prdy_clpr) / prdy_clpr * 100, 2)
        else:
            change_pct = 0.0
        return price, change_pct
    except Exception as e:
        log.error(f"[{ticker}] KR 종가 확정 조회 실패: {e}")
        return None


def get_confirmed_close_us(ticker: str, excd: str):
    """
    US 당일 종가 확정 여부 확인.
    output2 첫 번째 행이 오늘 날짜이고 tvol != "0" 이면 (종가, change_pct) 반환.
    미확정이면 None 반환.
    """
    token   = get_access_token()
    today   = datetime.now(pytz.timezone("America/New_York")).strftime("%Y%m%d")
    url     = "https://openapi.koreainvestment.com:9443/uapi/overseas-price/v1/quotations/dailyprice"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey":        config["kis_app_key"],
        "appsecret":     config["kis_app_secret"],
        "tr_id":         "HHDFS76240000",
        "custtype":      "P",
    }
    params = {
        "AUTH": "",
        "EXCD": excd,
        "SYMB": ticker,
        "GUBN": "0",
        "BYMD": today,
        "MODP": "0",
    }
    try:
        res  = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
        rows = res.json().get("output2", [])
        if not rows:
            return None
        row = rows[0]
        if row.get("xymd") != today:
            return None
        if row.get("tvol", "0") == "0":
            return None
        price      = float(row.get("clos", 0))
        change_pct = float(row.get("rate", 0))
        return price, change_pct
    except Exception as e:
        log.error(f"[{ticker}] US 종가 확정 조회 실패: {e}")
        return None


# ---------------------------------------------------------------------------
# KIS API 토큰
# ---------------------------------------------------------------------------
def get_access_token():
    global access_token, token_expires
    with token_lock:
        if access_token and time.time() < token_expires:
            return access_token
        url  = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey":     config["kis_app_key"],
            "appsecret":  config["kis_app_secret"],
        }
        res = requests.post(url, json=body, timeout=10, verify=False)
        data = res.json()
        access_token  = data["access_token"]
        token_expires = time.time() + int(data.get("expires_in", 86400)) - 60
        log.info("KIS 토큰 발급 완료")
        return access_token


# ---------------------------------------------------------------------------
# 시세 수집 함수
# ---------------------------------------------------------------------------
def get_kr_price(ticker):
    token = get_access_token()
    url   = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey":        config["kis_app_key"],
        "appsecret":     config["kis_app_secret"],
        "tr_id":         "FHKST01010100",
        "custtype":      "P",
    }
    params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker}
    res    = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
    out    = res.json().get("output", {})
    curr_price = float(out.get("stck_prpr", 0))
    price      = curr_price if curr_price != 0 else float(out.get("prdy_clpr", 0))
    return price, float(out.get("prdy_ctrt", 0))


def get_us_price(ticker, excd):
    token = get_access_token()
    url   = "https://openapi.koreainvestment.com:9443/uapi/overseas-price/v1/quotations/price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey":        config["kis_app_key"],
        "appsecret":     config["kis_app_secret"],
        "tr_id":         "HHDFS00000300",
        "custtype":      "P",
    }
    params = {"AUTH": "", "EXCD": excd, "SYMB": ticker}
    res    = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
    out    = res.json().get("output", {})

    def safe_float(v):
        try:
            return float(v) if v not in ("", None) else 0
        except Exception:
            return 0

    price = safe_float(out.get("last")) if safe_float(out.get("last")) != 0 else safe_float(out.get("base"))
    return price, safe_float(out.get("rate"))


def get_yahoo_price(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10, verify=False).json()
    meta = res["chart"]["result"][0]["meta"]
    price = float(meta.get("regularMarketPrice", 0))
    prev_close = float(meta.get("previousClose", 0) or meta.get("chartPreviousClose", 0))
    change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
    data_time = datetime.fromtimestamp(meta.get("regularMarketTime", 0))
    return price, change_pct, data_time


# ---------------------------------------------------------------------------
# DB 업데이트
# ---------------------------------------------------------------------------
def update_ticker_in_db(conn, ticker, price, change_pct, data_time=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tickers
               SET current_price = %s,
                   change_pct    = %s,
                   updated_at    = %s,
                   data_time     = %s
             WHERE ticker = %s
            """,
            (price, change_pct, datetime.now(), data_time, ticker),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# 종목별 업데이트 워커 (스레드 1개당 종목 1개)
# ---------------------------------------------------------------------------
def update_worker(row):
    ticker = row["ticker"]
    market = row["market"]
    data_time = None
    try:
        if market == "KR":
            price, change_pct = get_kr_price(ticker)
        elif market in ("NAS", "NYS", "AMS", "ARC"):
            price, change_pct = get_us_price(ticker, market)
        elif market in ("FX", "INDEX", "CRYPTO"):
            price, change_pct, data_time = get_yahoo_price(ticker)
        else:
            log.warning(f"[{ticker}] 알 수 없는 market: {market}")
            return

        if price == 0:
            log.warning(f"[{ticker}] 가격 0 수신 — DB 업데이트 건너뜀")
            return

        conn = get_db_conn()
        try:
            update_ticker_in_db(conn, ticker, price, change_pct, data_time if market in ("FX", "INDEX", "CRYPTO") else None)
            log.info(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
        finally:
            conn.close()

    except Exception as e:
        log.error(f"[{ticker}] 업데이트 실패: {e}")


# ---------------------------------------------------------------------------
# 종가 확정 후 스킵할 시장 관리
# {market_group: date} 형태로 당일 종가 확정된 시장 기록
# market_group: "KR" 또는 "US"
# ---------------------------------------------------------------------------
_close_confirmed: dict = {}   # {"KR": date(2026,6,4), "US": date(2026,6,4)}
_close_lock = threading.Lock()


def _is_close_confirmed(market_group: str) -> bool:
    """오늘 날짜로 종가 확정 여부 확인"""
    with _close_lock:
        return _close_confirmed.get(market_group) == date.today()


def _set_close_confirmed(market_group: str):
    """종가 확정 기록"""
    with _close_lock:
        _close_confirmed[market_group] = date.today()
        log.info(f"[{market_group}] 종가 확정 — 오늘 조회 중단")


def _market_group(market: str) -> str:
    """market → market_group 매핑"""
    if market == "KR":
        return "KR"
    if market in ("NAS", "NYS", "AMS", "ARC"):
        return "US"
    return market


# ---------------------------------------------------------------------------
# 종가 확정 워커 (after 상태에서 종가 API 호출)
# ---------------------------------------------------------------------------
def close_confirm_worker(row: dict):
    ticker = row["ticker"]
    market = row["market"]
    group  = _market_group(market)

    try:
        if market == "KR":
            result = get_confirmed_close_kr(ticker)
        elif market in ("NAS", "NYS", "AMS", "ARC"):
            excd   = market
            result = get_confirmed_close_us(ticker, excd)
        else:
            return

        if result is None:
            return  # 아직 미확정

        price, change_pct = result
        if price == 0:
            return

        conn = get_db_conn()
        try:
            update_ticker_in_db(conn, ticker, price, change_pct, None)
            log.info(f"[{ticker}] 종가 확정 업데이트: {price:,.4f} ({change_pct:+.2f}%)")
        finally:
            conn.close()

        # 종가 확정 — 해당 그룹 당일 조회 중단 플래그 세팅
        _set_close_confirmed(group)

    except Exception as e:
        log.error(f"[{ticker}] 종가 확정 워커 실패: {e}")


# ---------------------------------------------------------------------------
# 전체 종목 조회 후 스레드 실행
# ---------------------------------------------------------------------------
def run_update_cycle(force=False):
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT ticker, market FROM tickers ORDER BY market, ticker")
            rows = [{"ticker": r[0], "market": r[1]} for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        log.error(f"tickers 조회 실패: {e}")
        return

    if not rows:
        log.warning("tickers 테이블에 종목 없음")
        return

    if force:
        targets       = rows
        close_targets = []
    else:
        targets       = []
        close_targets = []

        for r in rows:
            market = r["market"]
            group  = _market_group(market)
            status = get_market_status(market)

            if status == "open":
                targets.append(r)
            elif status == "after":
                if _is_close_confirmed(group):
                    pass  # 종가 확정됐으면 완전 스킵
                else:
                    targets.append(r)        # 실시간 시세도 계속 조회
                    close_targets.append(r)  # 종가 확정 병행 시도
            # pre / closed → 스킵

    if not targets and not close_targets:
        log.info("현재 업데이트 대상 종목이 없습니다.")
        return

    log.info(f"업데이트 시작 — 실시간 {len(targets)}개 / 종가확정 {len(close_targets)}개 / 전체 {len(rows)}개")

    threads = []
    for row in targets:
        t = threading.Thread(target=update_worker, args=(row,), daemon=True)
        threads.append(t)
        t.start()

    for row in close_targets:
        t = threading.Thread(target=close_confirm_worker, args=(row,), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # 종가 확정은 close_confirm_worker 내에서 직접 _set_close_confirmed() 호출

    log.info("업데이트 완료")

    try:
        conn = get_db_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("NOTIFY price_updated")
        conn.close()
        log.info("NOTIFY price_updated 전송")
    except Exception as e:
        log.error(f"NOTIFY 실패: {e}")


# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------
def main():
    load_config()
    log.info("price_updater 시작")

    while True:
        load_config()
        interval_sec = config["interval"] * 60

        # 공휴일 캐시 갱신 (매일 08:00 KST 이후 첫 루프에서 1회 실행)
        holiday_cache.refresh_if_needed()

        start = time.time()
        run_update_cycle()
        elapsed = time.time() - start
        sleep_sec = max(0, interval_sec - elapsed)
        log.info(f"다음 업데이트까지 {sleep_sec:.1f}초 대기")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.force:
        load_config()
        log.info("강제 업데이트 실행 (--force)")
        run_update_cycle(force=True)
    else:
        main()