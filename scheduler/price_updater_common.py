import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, date, timedelta, timezone
from logging.handlers import RotatingFileHandler

# common/ 패키지 접근을 위해 PROJECT_ROOT를 sys.path에 추가
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import psycopg2
import requests
import pytz

from common.kis_auth import get_kis_access_token

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# 경로 설정
# ---------------------------------------------------------------------------
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_FILE    = os.path.join(BASE_DIR, "price_updater.log")

# ---------------------------------------------------------------------------
# 시장 상태(open/pre/after) 판정 안전마진 (분)
#  - KR/US open/pre/after 경계에 적용
#  - 2026-08-07: 구독 결정에 더 이상 안 쓰이고 UI 표시용으로만 남아 0으로 낮춤.
#    나중에 다시 필요해지면 이 값만 올리면 됨(수식은 그대로 유지).
# ---------------------------------------------------------------------------
MARKET_CLOSE_BUFFER_MIN = 0

# ---------------------------------------------------------------------------
# KR 종가 확정 1회 조회 안전마진 (분) — 15:40(시간외종가매매 개시) + 이 값
#  - market status 버퍼와 용도가 달라 2026-08-07부터 별도 상수로 분리.
# ---------------------------------------------------------------------------
KR_FINAL_CLOSE_BUFFER_MIN = 5

# ---------------------------------------------------------------------------
# 한국투자증권 미국주식 거래시간 공식 안내 (KST 기준, "미국주식 주간거래 재개
# 안내" 및 한투 앱 내 안내문 원문 그대로. 2026-08-27 사용자 확인)
#
#   구분              표준시           서머타임(Summer Time)
#   주간거래(장전)     10:00 ~ 18:00    10:00 ~ 17:00
#   프리마켓(장전)     18:00 ~ 23:30    17:00 ~ 22:30
#   정규장             23:30 ~ 06:00    22:30 ~ 05:00
#   애프터마켓         06:00 ~ 07:00    05:00 ~ 07:00
#   애프터마켓 연장     07:00 ~ 09:00    07:00 ~ 09:00 (동일)
#
#   ※ 애프터마켓+연장 합산 종료는 표준시/서머타임 모두 09:00 KST로 고정.
#     주간거래 시작은 양쪽 모두 10:00 KST로 고정.
#     → 이 두 값 사이, 매일 KST 09:00~10:00은 어떤 세션에도 속하지 않아
#       시세 조회가 불가능한 공백 구간(휴장일 여부와 무관하게 항상 존재).
#
#   프리마켓/정규장은 NY 로컬시각 4:00~9:30/9:30~16:00 ET로 계산해도
#   표준시/서머타임 두 케이스 모두 위 표와 정확히 일치함(검증됨, 아래
#   get_market_status()의 pre/open 분기 참고). 반면 주간거래·애프터마켓은
#   ET 로컬시각 고정값으로 환산해도 표준시/서머타임 간 폭이 달라져 위 표와
#   맞지 않으므로, 아래처럼 KST 값 자체를 DST 여부에 따라 분기한다.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 실시간 크립토 소스 선택
#   "upbit" — Upbit 실시간 (기본값)
#   "yahoo" — 기존 Yahoo Finance (Upbit 장애 시 폴백용)
# ---------------------------------------------------------------------------
CRYPTO_SOURCE = "upbit"


# ---------------------------------------------------------------------------
# 실시간 환율(FX 마켓) 소스 선택
#   "upbit" — Upbit KRW-USDT 실시간 (기본값)
#   "yahoo" — 기존 Yahoo Finance (Upbit 장애 시 폴백용)
# ---------------------------------------------------------------------------
FX_SOURCE = "yahoo"


# ---------------------------------------------------------------------------
# 고빈도 로그 on/off
# ---------------------------------------------------------------------------
# True로 바꾸면 틱 단위로 발생하는 고빈도 로그(시세 원본/개별 저장/신호발행 등)까지
# 전부 출력된다. False(기본)면 해당 로그는 DEBUG 레벨로 남고, 아래 logging.basicConfig()의
# level=INFO보다 낮은 레벨이라 로거 단계에서 걸러져 콘솔/로그파일 어디에도 기록되지 않는다.
# (구독 대상/개별 구독/연결 상태/WS 서버 응답/에러·경고 등은 이 값과 무관하게 항상 출력됨)
VERBOSE_PRICE_LOG = False

# ---------------------------------------------------------------------------
# 로깅
# ---------------------------------------------------------------------------
# 서버 시스템 타임존이 UTC(Etc/UTC)이므로, logging.Formatter.converter를 교체해
# 로그 표시 시각만 KST로 변환한다. 서버 시스템 시간/DB/다른 서비스에는 영향 없음.
# (logging.Formatter.converter는 클래스 속성이라 이 프로세스 내 모든 로거에 적용됨)
_KST = pytz.timezone("Asia/Seoul")


def _kst_time_converter(*args):
    return datetime.now(_KST).timetuple()


logging.Formatter.converter = _kst_time_converter

logging.basicConfig(
    level=logging.DEBUG if VERBOSE_PRICE_LOG else logging.INFO,
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
# 미국 서머타임(EDT) 적용 여부 판정
#  - pytz의 America/New_York 타임존은 IANA tzdata를 기반으로 DST 전환일을
#    자동 계산하므로, 매년 전환일을 별도로 하드코딩할 필요가 없다.
#  - datetime.now(tz)로 얻은 aware datetime의 .dst()가 0이 아니면 서머타임
#    (EDT, UTC-4), 0이면 표준시(EST, UTC-5)로 판정한다.
#    (2026-08-27 웹검색으로 확인: pytz에서 naive datetime에 tzinfo=를 직접
#    물리는 방식은 잘못된 패턴이지만, datetime.now(tz)는 공식적으로 올바른
#    패턴 — 별도 localize() 불필요.)
# ---------------------------------------------------------------------------
def _is_us_dst_today() -> bool:
    tz = pytz.timezone("America/New_York")
    now_ny = datetime.now(tz)
    return now_ny.dst() != timedelta(0)


# ---------------------------------------------------------------------------
# 미국주간거래(Day Trading) 시간대 여부 판단
#  - 뉴욕 현지시간 기준 판단(get_market_status 본체)과는 독립적으로, KST
#    날짜/시각만으로 판단한다. NY 기준 요일/휴장 체크를 먼저 거치면 KST와
#    NY 날짜가 다른 경계(예: KST 월요일 오전 = NY 일요일 밤)에서 주간거래가
#    잘못 걸러지는 문제가 생기므로, get_market_status()의 NY 분기보다 먼저
#    독립적으로 확인해야 한다.
#  - 시작(10:00 KST)은 표준시/서머타임 동일, 종료만 한투 공식 안내 기준으로
#    DST에 따라 18:00(표준시)/17:00(서머타임)로 갈린다 — 상단 주석 참고.
# ---------------------------------------------------------------------------
US_DAY_TRADING_START_MIN     = 10 * 60  # 10:00 (표준시/서머타임 동일)
US_DAY_TRADING_END_MIN_STD   = 18 * 60  # 18:00 (표준시)
US_DAY_TRADING_END_MIN_DST   = 17 * 60  # 17:00 (서머타임)


def _is_us_day_trading_window() -> bool:
    tz = pytz.timezone("Asia/Seoul")
    now_local   = datetime.now(tz)
    today_local = now_local.date()

    if now_local.weekday() >= 5:
        return False
    if holiday_cache.is_us_holiday(today_local):
        return False

    now_min = now_local.hour * 60 + now_local.minute
    end_min = US_DAY_TRADING_END_MIN_DST if _is_us_dst_today() else US_DAY_TRADING_END_MIN_STD
    return US_DAY_TRADING_START_MIN <= now_min < end_min


# ---------------------------------------------------------------------------
# 미국 애프터마켓(정규장 종료 후 거래, 연장 포함) 시간대 여부 판단
#  - 시간 폭(05:00/06:00~09:00)은 한투 공식 안내 기준 KST 값을 그대로 쓴다.
#  - 요일/휴장 판정은 day 마켓과 달리 KST '오늘'이 아니라 NY 로컬시각 기준으로
#    한다. 이 구간(KST 05:00~09:00 서머/06:00~09:00 표준)을 ET로 환산하면
#    항상 자정을 걸치지 않는 "전일 16:00~20:00 ET" 단일 구간에 대응하므로
#    (day 마켓처럼 NY 자정을 걸치지 않음 — 확인됨), NY 로컬시각의 날짜/요일을
#    바로 써도 안전하다.
#    [2026-08-31 수정] 이전엔 day 마켓 함수를 그대로 본떠 KST '오늘' 요일로
#    체크했었는데, 이 구간은 KST 기준 항상 전날 밤 미국 정규장의 연장선이라
#    실제 미국 거래일과 KST 날짜가 어긋나는 경우(KST 월요일 새벽 = NY 일요일
#    오후, KST 토요일 새벽 = NY 금요일 오후)에 오판이 발생했다(실사용 중
#    2026-08-31 발견: KST 월요일 오전에 잘못 after로 표시됨). NY 로컬시각
#    기준으로 바꿔 근본 수정.
# ---------------------------------------------------------------------------
US_AFTER_MARKET_END_MIN        = 9 * 60  # 09:00 (표준시/서머타임 동일)
US_AFTER_MARKET_START_MIN_STD  = 6 * 60  # 06:00 (표준시)
US_AFTER_MARKET_START_MIN_DST  = 5 * 60  # 05:00 (서머타임)


def _is_us_after_market_window() -> bool:
    tz_kst  = pytz.timezone("Asia/Seoul")
    now_kst = datetime.now(tz_kst)
    now_min = now_kst.hour * 60 + now_kst.minute

    # 요일/휴장/DST 판정은 모두 같은 NY 로컬시각 스냅샷 하나로 처리
    # (America/New_York 타임존 조회를 여러 번 반복하지 않도록 재사용).
    tz_ny  = pytz.timezone("America/New_York")
    now_ny = datetime.now(tz_ny)

    if now_ny.weekday() >= 5:
        return False
    if holiday_cache.is_us_holiday(now_ny.date()):
        return False

    is_dst    = now_ny.dst() != timedelta(0)
    start_min = US_AFTER_MARKET_START_MIN_DST if is_dst else US_AFTER_MARKET_START_MIN_STD
    return start_min <= now_min < US_AFTER_MARKET_END_MIN


# ---------------------------------------------------------------------------
# 시장 상태 판단
# 반환값: "open" | "pre" | "after" | "closed" | "day"(US 전용, 주간거래)
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

    if market_time == "KR":
        tz = pytz.timezone("Asia/Seoul")
        now_local   = datetime.now(tz)
        today_local = now_local.date()

        if now_local.weekday() >= 5:
            return "closed"
        if holiday_cache.is_kr_holiday(today_local):
            return "closed"

        now_min = now_local.hour * 60 + now_local.minute
        if 9 * 60 - MARKET_CLOSE_BUFFER_MIN <= now_min <= 15 * 60 + 30 + MARKET_CLOSE_BUFFER_MIN:
            return "open"
        return "closed"

    if market_time == "US":
        # 주간거래(KST, DST별 10:00~18:00/17:00) — NY 시간대 판단보다 먼저,
        # 독립적으로 확인. 이 구간은 NY 기준으로는 항상 20:00~04:00(closed)
        # 구간 안에 완전히 포함되므로(확인됨), 아래 NY 분기 결과와 절대
        # 충돌하지 않는다.
        if _is_us_day_trading_window():
            return "day"

        # 애프터마켓(연장 포함, KST, DST별 06:00/05:00~09:00 고정 종료) —
        # 시간 폭은 KST 값, 요일/휴장 판정은 NY 로컬시각 기준(위 함수 주석
        # 참고). NY 기준으로는 16:00~20:00 ET 부근에 걸쳐 있어 아래 NY
        # 분기(pre/open)와 겹치지 않는다.
        if _is_us_after_market_window():
            return "after"

        tz = pytz.timezone("America/New_York")
        now_local   = datetime.now(tz)
        today_local = now_local.date()

        if now_local.weekday() >= 5:
            return "closed"
        if holiday_cache.is_us_holiday(today_local):
            return "closed"

        now_min = now_local.hour * 60 + now_local.minute
        if 4 * 60 - MARKET_CLOSE_BUFFER_MIN <= now_min < 9 * 60 + 30:
            return "pre"
        if 9 * 60 + 30 <= now_min <= 16 * 60:
            return "open"
        return "closed"

    return "open"


def is_market_open(market: str) -> bool:
    """하위 호환용. get_market_status() == 'open' 과 동일."""
    return get_market_status(market) == "open"


# ---------------------------------------------------------------------------
# KIS API 토큰
# ---------------------------------------------------------------------------
def get_access_token():
    """
    common/kis_auth.py 로 통합 (Redis 캐시 + 락으로 프로세스간 공유).
    기존 호출부(price_updater_rest.py, price_updater_ws.py, get_kr_price 등)와의
    호환을 위해 함수명/시그니처는 그대로 유지한다.
    """
    return get_kis_access_token()

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
# Upbit 실시간 원/달러 환율 (USDKRW=X 전용, USDT-KRW로 근사)
# ---------------------------------------------------------------------------
def get_upbit_krw_price():
    try:
        url = "https://api.upbit.com/v1/ticker?markets=KRW-USDT"
        res = requests.get(url, timeout=10).json()

        if not res or not isinstance(res, list):
            log.warning("⚠️ [USDKRW=X] Upbit 응답이 비어있습니다.")
            return 0.0, 0.0, datetime.now(timezone.utc)

        item = res[0]
        price      = float(item.get("trade_price", 0))
        change_pct = round(float(item.get("signed_change_rate", 0)) * 100, 2)

        # trade_timestamp는 ms 단위 UTC epoch
        ts_ms = item.get("trade_timestamp", 0)
        data_time = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

        return price, change_pct, data_time

    except Exception as e:
        log.error(f"❌ [USDKRW=X] Upbit 시세 파싱 중 예외 발생: {e}")
        return 0.0, 0.0, datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Upbit 실시간 크립토 시세 (CRYPTO 마켓 공통)
# 티커는 항상 Yahoo 포맷(BASE-QUOTE, 예: BTC-KRW)을 기준으로 받아
# 내부에서 Upbit 포맷(QUOTE-BASE, 예: KRW-BTC)으로 변환한다.
# ---------------------------------------------------------------------------
def get_upbit_crypto_price(ticker):
    try:
        base, quote = ticker.split("-")
        upbit_market = f"{quote}-{base}"

        url = f"https://api.upbit.com/v1/ticker?markets={upbit_market}"
        res = requests.get(url, timeout=10).json()

        if not res or not isinstance(res, list):
            log.warning(f"⚠️ [{ticker}] Upbit 응답이 비어있습니다. (market={upbit_market})")
            return 0.0, 0.0, datetime.now(timezone.utc)

        item = res[0]
        price      = float(item.get("trade_price", 0))
        change_pct = round(float(item.get("signed_change_rate", 0)) * 100, 2)

        # trade_timestamp는 ms 단위 UTC epoch
        ts_ms = item.get("trade_timestamp", 0)
        data_time = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

        return price, change_pct, data_time

    except Exception as e:
        log.error(f"❌ [{ticker}] Upbit 크립토 시세 파싱 중 예외 발생: {e}")
        return 0.0, 0.0, datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 종목별 "마지막으로 실제 가격이 바뀐 시각" 인메모리 추적
# (2026-08 추가 — 포트폴리오/계좌 화면의 "N초/분/시간/일 전" 표시용)
#
# Yahoo(10초)/Upbit(1초) 폴링은 가격이 안 바뀌어도 매 주기 update_price_cache()를
# 호출한다. Redis에 "쓴 시각"을 그대로 저장하면 안 바뀐 값도 매번 방금 갱신된 것처럼
# 보이므로, 이 프로세스 메모리에 종목별 마지막 가격을 들고 있다가 실제로 값이
# 달라졌을 때만 시각을 갱신한다. Redis 재조회 없이 처리되어 고빈도 경로에 추가
# Redis 호출이 발생하지 않는다.
#
# [알려진 한계] 이 프로세스(price_updater_ws.py)가 재시작되면 이 dict가 초기화되어,
# 재시작 직후 모든 종목이 "방금 갱신됨"으로 리셋된다. price_updater.py --force(강제
# 재조회)는 별도 프로세스이지만 사용 안 하는 기능으로 확인되어 동기화 문제 없음.
#
# [부동소수점 동등비교 회피 — 2026-08 코드리뷰 반영]
# 가격 변경 감지(아래 price_key)와 갱신시각(changed_at)은 모두 이후
# diff_display_split()에서 "==" 로 비교당하는 값들이므로, float 그대로 저장/비교하지
# 않는다.
#   - price_key: price를 정수로 스케일링(소수점 8자리까지, 크립토 저가코인 포함)해서
#     정수끼리 비교. 부동소수점은 같은 값이어도 소스가 준 문자열 표현에 따라 다른
#     비트로 파싱될 수 있어 "==" 비교가 항상 안전하다고 보장할 수 없다.
#   - changed_at: time.time()의 소수점(밀리초 이하)을 버리고 초 단위 int로 저장.
#     화면 표시 단위가 애초에 초(s) 이상이라 소수점 정보는 어차피 쓰이지 않으며,
#     Redis(JSON) 왕복 후 diff_display_split이 정수끼리 비교하므로 오차 여지가 없다.
# ---------------------------------------------------------------------------
_PRICE_COMPARE_SCALE = 10 ** 8  # 소수점 8자리까지 정수로 스케일링 후 비교

_last_known_price: dict = {}  # ticker -> price_key(int, 스케일링된 정수)
_last_changed_at:  dict = {}  # ticker -> changed_at(int, unix epoch 초)
_price_change_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Redis 시세 업데이트
# ---------------------------------------------------------------------------
def update_price_cache(ticker, price, change_pct, data_time=None):
    """
    Redis write (휘발성 시세 데이터).

    data_time(호출부가 넘기는 소스별 체결/조회 시각 — KR은 항상 None, Yahoo/Upbit는
    실제 값)은 의도적으로 사용하지 않는다. "마지막 갱신 후 경과시간" 표시는
    "실제 가격 값이 바뀐 시점" 기준으로 정의하기로 확정했고, 소스마다 의미가 다른
    data_time 대신 이 프로세스가 관찰한 이전 값과의 비교로 changed_at을 직접 결정한다.
    """
    price = float(price)
    price_key = round(price * _PRICE_COMPARE_SCALE)  # 정수 비교용 — float "==" 회피
    now = round(time.time())  # 초 단위 int — 이후 diff 비교(==)에서 float 회피

    with _price_change_lock:
        prev_price_key = _last_known_price.get(ticker)
        if prev_price_key is None or prev_price_key != price_key:
            _last_changed_at[ticker] = now
            _last_known_price[ticker] = price_key
        changed_at = _last_changed_at[ticker]

    try:
        from common.redis_store import write_price
        write_price(ticker, price, float(change_pct), changed_at)
    except Exception as e:
        log.warning(f"[redis] write_price 실패 ({ticker}): {e}")


# ---------------------------------------------------------------------------
# KR 현재가 조회 (inquire-price) — WS/REST 공통
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


# ---------------------------------------------------------------------------
# KR 종가 1회성 확정 조회
#
# 정책:
#   - 15:40(시간외종가매매 개시) + KR_FINAL_CLOSE_BUFFER_MIN 이후,
#     하루 1회만 KR 전 종목의 현재가(inquire-price)를 조회해 그날의 종가로 기록.
#   - 이 시각엔 시간외종가매매가 "당일 종가"로만 체결되므로 이 값이 곧 종가.
#   - WS/REST 어느 쪽이 떠 있어도 동일하게 호출 가능 (전역 플래그로 1일 1회 보장).
# ---------------------------------------------------------------------------
_kr_final_close_done: date | None = None
_kr_final_close_lock = threading.Lock()


def should_run_kr_final_close() -> bool:
    """지금이 KR 종가 1회성 조회 시각 이후이고, 오늘 아직 실행 안 했으면 True (호출 시 즉시 '실행함'으로 마킹)."""
    global _kr_final_close_done

    tz = pytz.timezone("Asia/Seoul")
    now_local   = datetime.now(tz)
    today_local = now_local.date()

    with _kr_final_close_lock:
        if _kr_final_close_done == today_local:
            return False
        if now_local.weekday() >= 5:
            return False
        if holiday_cache.is_kr_holiday(today_local):
            return False

        now_min    = now_local.hour * 60 + now_local.minute
        target_min = 15 * 60 + 40 + KR_FINAL_CLOSE_BUFFER_MIN
        if now_min < target_min:
            return False

        _kr_final_close_done = today_local
        return True


def run_kr_final_close_update():
    """KR 전 종목에 대해 inquire-price를 1회 조회하여 그날의 종가로 Redis에 반영."""
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM tickers WHERE market = 'KR'")
            tickers = [r[0] for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        log.error(f"KR 종가조회 대상 조회 실패: {e}")
        return

    if not tickers:
        return

    log.info(f"KR 종가 확정 조회 시작 — {len(tickers)}개")

    from common.redis_store import get_price

    def _worker(ticker):
        try:
            # 덮어쓰기 전 캐시값 = WS가 마지막으로 남긴 값 (비교용, 실패해도 종가 조회 자체는 진행)
            try:
                prev = get_price(ticker)
            except Exception as e:
                log.warning(f"[{ticker}] WS 캐시값 조회 실패(비교 건너뜀): {e}")
                prev = None

            price, change_pct = get_kr_price(ticker)
            if price == 0:
                log.warning(f"[{ticker}] KR 종가 가격 0 — 건너뜀")
                return

            if prev is not None and prev.get("price") is not None:
                diff = price - prev["price"]
                if abs(diff) > 0.0001:
                    log.warning(
                        f"[{ticker}] WS 캐시값과 REST 확정 종가 불일치: "
                        f"WS={prev['price']:,.4f} REST={price:,.4f} diff={diff:+.4f}"
                    )
                else:
                    log.info(f"[{ticker}] WS 캐시값과 REST 확정 종가 일치: {price:,.4f}")
            else:
                log.info(f"[{ticker}] WS 캐시값 없음(비교 불가) — REST 종가만 기록: {price:,.4f}")

            update_price_cache(ticker, price, change_pct, None)
            log.info(f"[{ticker}] KR 종가: {price:,.4f} ({change_pct:+.2f}%)")
        except Exception as e:
            log.error(f"[{ticker}] KR 종가 조회 실패: {e}")

    threads = [threading.Thread(target=_worker, args=(t,), daemon=True) for t in tickers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    try:
        from common.redis_store import recalc_today_row, publish_price_updated
        recalc_today_row()
        publish_price_updated()
        log.info("price_updated 신호 발행 (Redis, KR 종가)")
    except Exception as e:
        log.error(f"recalc_today_row/신호 발행 실패: {e}")

    log.info("KR 종가 확정 조회 완료")