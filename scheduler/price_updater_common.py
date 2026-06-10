import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, date, timezone
from logging.handlers import RotatingFileHandler

# common/ 패키지 접근을 위해 PROJECT_ROOT를 sys.path에 추가
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

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
token_lock    = threading.Lock()


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
        self._fetched_date = None
        self._kr_holidays  = set()
        self._us_holidays  = set()

    def _fetch_kr_holidays(self, year: int, month: int) -> set:
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
            if not items or items == "":
                return set()
            item_list = items["item"]
            if isinstance(item_list, dict):
                item_list = [item_list]
            holidays = set()
            for item in item_list:
                if item.get("isHoliday") == "Y":
                    d = str(item["locdate"])
                    holidays.add(date(int(d[:4]), int(d[4:6]), int(d[6:])))
            return holidays
        except Exception as e:
            log.error(f"한국 공휴일 조회 실패: {e}")
            return set()

    def _fetch_us_holidays(self) -> set:
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
                if item.get("tradingHour", "") == "":
                    d = item["atDate"]
                    holidays.add(date.fromisoformat(d))
            return holidays
        except Exception as e:
            log.error(f"미국 공휴일 조회 실패: {e}")
            return set()

    def refresh_if_needed(self):
        kst = pytz.timezone("Asia/Seoul")
        now_kst = datetime.now(kst)
        today   = now_kst.date()

        with self._lock:
            if self._fetched_date == today:
                return
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
# 시장 상태 판단
# 반환값: "open" | "pre" | "closing" | "after" | "closed"
# ---------------------------------------------------------------------------
def get_market_status(market: str) -> str:
    _config = config if config else {}
    if not _config.get("market_map"):
        # Shiny 앱 등 load_config()가 호출되지 않은 환경에서는 직접 읽음
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                _config = json.load(f)
        except Exception:
            _config = {}

    market_info = _config.get("market_map", {}).get(market, {})
    market_time = market_info.get("market_time", "24h")

    if market_time == "24h":
        return "open"

    buf = _config.get("interval", 1)

    if market_time == "KR":
        tz = pytz.timezone("Asia/Seoul")
        now_local   = datetime.now(tz)
        today_local = now_local.date()

        if now_local.weekday() >= 5:
            return "closed"
        if holiday_cache.is_kr_holiday(today_local):
            return "closed"

        now_min = now_local.hour * 60 + now_local.minute
        if 9 * 60 - buf <= now_min <= 15 * 60 + 30:
            return "open"
        if 15 * 60 + 30 < now_min <= 18 * 60:
            return "closing"
        return "closed"

    if market_time == "US":
        tz = pytz.timezone("America/New_York")
        now_local   = datetime.now(tz)
        today_local = now_local.date()

        if now_local.weekday() >= 5:
            return "closed"
        if holiday_cache.is_us_holiday(today_local):
            return "closed"

        now_min = now_local.hour * 60 + now_local.minute
        if 4 * 60 - buf <= now_min < 9 * 60 + 30:
            return "pre"
        if 9 * 60 + 30 <= now_min <= 16 * 60:
            return "open"
        if 16 * 60 < now_min <= 20 * 60 + buf:
            return "after"
        return "closed"

    return "open"


def is_market_open(market: str) -> bool:
    """하위 호환용. get_market_status() == 'open' 과 동일."""
    return get_market_status(market) == "open"


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
# Yahoo Finance 시세 (FX / INDEX / CRYPTO 공통)
# ---------------------------------------------------------------------------
def get_yahoo_price(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10, verify=False).json()
        
        # 야후 응답 결과 데이터가 비어있는지 안전 검사
        result = res.get("chart", {}).get("result")
        if not result or not result[0]:
            log.warning(f"⚠️ [{ticker}] 야후 파이낸스에 해당 티커 데이터가 존재하지 않습니다.")
            return 0.0, 0.0, datetime.now(timezone.utc)
            
        meta = result[0]["meta"]
        price = float(meta.get("regularMarketPrice", 0))
        prev_close = float(meta.get("previousClose", 0) or meta.get("chartPreviousClose", 0))
        change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
        
        # [핵심] 앞의 datetime. 을 떼고 상단에서 가져온 timezone.utc를 바로 사용합니다.
        data_time = datetime.fromtimestamp(meta.get("regularMarketTime", 0), tz=timezone.utc)
        return price, change_pct, data_time
        
    except Exception as e:
        log.error(f"❌ [{ticker}] 야후 시세 파싱 중 예외 발생: {e}")
        return 0.0, 0.0, datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# DB 업데이트
# ---------------------------------------------------------------------------
def update_ticker_in_db(conn, ticker, price, change_pct, data_time=None):
    # TODO: Redis 전환 완료(Step 6) 후 current_price, change_pct DB write 제거 대상.
    #       updated_at, data_time 은 메타데이터이므로 유지.
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

    # Redis write (휘발성 시세 데이터 — 각 화면은 Step 6 이후 Redis에서만 읽음)
    try:
        from common.redis_store import write_price
        write_price(ticker, float(price), float(change_pct))
    except Exception as e:
        log.warning(f"[redis] write_price 실패 ({ticker}): {e}")


# ---------------------------------------------------------------------------
# 종가 확정 플래그 관리
# ---------------------------------------------------------------------------
_close_confirmed: dict = {}
_close_lock = threading.Lock()


def _is_close_confirmed(market_group: str) -> bool:
    with _close_lock:
        return _close_confirmed.get(market_group) == date.today()


def _set_close_confirmed(market_group: str):
    with _close_lock:
        _close_confirmed[market_group] = date.today()
        log.info(f"[{market_group}] 종가 확정 — 오늘 조회 중단")


def _market_group(market: str) -> str:
    return "KR" if market == "KR" else market