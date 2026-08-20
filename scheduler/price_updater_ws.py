"""
price_updater_ws.py — KIS 웹소켓 실시간 시세 업데이트
config.json 의 realtime_quote = true 일 때 동작.

구조:
  - KR/US 종목: KIS 웹소켓 (H0STCNT0 / HDFSCNT0) push 수신
  - FX/INDEX/CRYPTO: Yahoo Finance REST 폴링 (별도 asyncio task)
  - 미국주간거래(KST 10:00~16:00): [2026-08-20 추가] 이 구간에는 US 종목의
    HDFSCNT0 tr_key 접두어를 기존 D(DNAS/DNYS/DAMS)에서 R(RBAQ/RBAY/RBAA)로
    전환해서 구독을 유지한다(추가구독이 아닌 "전환" — 41종목 세션 한도 보호,
    make_us_tr_key/_switch_us_day_trading_prefix 참고). 전환은
    market_status_watcher_task가 10초 폴링으로 경계 진입/이탈을 감지해서 수행.
    포트폴리오 종목은 장 상태와 무관하게 항상 구독 대상이라는 기존 원칙(아래
    get_subscribe_targets 참고)은 그대로 유지되며, 이 전환도 "구독 대상 여부"가
    아니라 "같은 종목의 tr_key 접두어"만 바꾸는 것이다.

[재시작 정책 — 2026-08-07 개편]
  os.execv()를 통한 프로세스 재시작은 아래 경우에만 발생한다:
    - 웹소켓 연결이 예외로 완전히 끊겨 kis_ws_task의 while True 루프가 재연결하는 것은
      "재연결"이지 "재시작"이 아니므로 여기 해당 안 됨(기존 로직 그대로 유지).
    - 코드 배포, 설정(appkey 등) 변경 등 의도적 재배포 — 이 파일 내부 로직으로는
      트리거되지 않고 외부(운영자/배포 파이프라인)가 수행.
  즉 아래 두 가지는 더 이상 재시작을 트리거하지 않는다:
    1) 포트폴리오 종목 추가/삭제(ticker_watcher_task) — 개별 tr_type=1/2 전송으로 처리.
       KR/US는 KIS 웹소켓에 개별 구독/해제, Yahoo/Upbit는 공유 컨테이너(SharedState)
       갱신으로 처리.
    2) 장 상태(활성/휴장) 전환(market_status_watcher_task) — 포트폴리오에 있는 종목은
       장 상태와 무관하게 항상 구독 대상이므로 이 이벤트 자체가 구독에 영향을 주지
       않음. 활성/휴장 전환은 이제도 관찰용 로그만 남긴다(KIS가 장마감 중 idle 세션을
       자연 종료하는지는 아직 미확인 — 이 로그로 추후 관찰).
       단, [2026-08-20 추가] 같은 task 안에서 미국주간거래(KST 10:00/16:00) 진입·이탈은
       예외로, 이 이벤트는 US 종목의 tr_key 접두어(D↔R) 전환을 실제로 트리거한다
       (재시작이 아니라 개별 UNSUB/SUB — 아래 _switch_us_day_trading_prefix 참고).

[테스트 모드]
    TEST_FX_OFFSET_MODE = 0  정상 운영
        TEST_FX_OFFSET_MODE = 1  실제 조회는 그대로 두고, USDKRW=X만 실조회값 기준 ±1원 오프셋
                                후 신호 발행 (DOM patch 테스트)
"""

# ---------------------------------------------------------------------------
# 옵션 상수
# ---------------------------------------------------------------------------
TEST_FX_OFFSET_MODE = 0  # 0: 정상, 1: USDKRW=X 실조회값 기준 ±1원 오프셋
TEST_FX_OFFSET = 1.0

# KR 종가 1회성 확정 조회(REST) 사용 여부.
# 2026-08-12 기준: WS 캐시값과 REST 확정값 비교 로그 2거래일 연속 전 종목 일치 확인,
# 그리고 최종 종가는 매일 오전 Yahoo 재조회로 다시 덮어써지므로 이 REST 조회가 없어도
# 최악의 경우 몇 시간 정도 오차가 다음날 아침 자동 정정되는 수준 — 제거해도 되는 상태.
# 다만 만약을 대비해 코드는 남겨두고 이 상수로 켜고 끌 수 있게 함. False로 바꾸면
# kr_final_close_task()가 매 루프 아무 것도 안 하고 그냥 sleep만 반복한다.
# 변경 시 서비스 재시작 필요(다른 상수들과 동일).
KR_FINAL_CLOSE_ENABLED = False

import asyncio
import json
import time
import threading
import queue
from datetime import datetime, date

import websockets
import pytz

import price_updater_common as common
from price_updater_common import (
    log,
    load_config,
    get_db_conn,
    get_access_token,
    holiday_cache,
    get_market_status,
    get_yahoo_price,
    update_price_cache,
    should_run_kr_final_close,
    run_kr_final_close_update,
)
from common.kis_auth import get_kis_approval_key

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
WS_URL = "ws://ops.koreainvestment.com:21000"

# DB market → HDFSCNT0 tr_key prefix (야간/정규/애프터 무료 지연체결가)
US_MARKET_PREFIX = {
    "NAS": "DNAS",
    "NYS": "DNYS",
    "AMS": "DAMS",
}

# DB market → HDFSCNT0 tr_key prefix (미국주간거래, KST 10:00~16:00 전용)
# tr_id는 D접두어와 동일하게 HDFSCNT0, tr_key 접두어만 R+거래소 코드로 다름
# (KIS 공식 API 문서 기준: 나스닥 BAQ / 뉴욕 BAY / 아멕스 BAA)
US_MARKET_DAY_PREFIX = {
    "NAS": "RBAQ",
    "NYS": "RBAY",
    "AMS": "RBAA",
}

# tr_key 접두어(4자리) → market 역매핑 (D/R 전환 시 기존 구독의 market을
# 별도 저장 없이 tr_key 자체에서 복원하기 위해 사용)
_US_PREFIX_TO_MARKET = {
    **{v: k for k, v in US_MARKET_PREFIX.items()},
    **{v: k for k, v in US_MARKET_DAY_PREFIX.items()},
}

# H0STCNT0 수신 필드 인덱스
KR_IDX_PRICE      = 2
KR_IDX_CHANGE_PCT = 5

# HDFSCNT0 수신 필드 인덱스
US_IDX_PRICE      = 11
US_IDX_CHANGE_PCT = 14

# Yahoo 폴링 주기 (초)
YAHOO_POLL_INTERVAL = 10

# Upbit 폴링 주기 (초) — FX(USDKRW=X)/CRYPTO 전용, Yahoo와 별개로 빠르게 설정 가능
UPBIT_POLL_INTERVAL = 1

# 웹소켓 재연결 대기 (초)
WS_RECONNECT_DELAY = 10


# ---------------------------------------------------------------------------
# task 간 공유 상태
# ---------------------------------------------------------------------------
class SharedState:
    """
    kis_ws_task(연결 소유) ↔ ticker_watcher_task(변경 감지) ↔
    yahoo_poll_task/upbit_poll_task(폴링 대상 소비) 사이에서 공유하는 상태.

    - ws / sub_queue: kis_ws_task가 연결될 때마다 새로 만들어 갱신한다.
      연결이 끊긴 동안(재연결 대기 중)에는 둘 다 None — 이 상태에서
      ticker_watcher_task가 구독 변경을 요청하면 건너뛰고 로그만 남긴다
      (다음 재연결 시 kis_ws_task가 DB 최신 상태로 전체 재구독하므로
      별도 재시도/보관 로직이 필요 없다).
    - us_ticker_set / tr_key_map: kis_ws_task의 수신 파싱에서 tr_key → DB ticker
      역매핑에 사용. 구독 추가/삭제 시 ticker_watcher_task가 함께 갱신한다.
    - yahoo_rows / upbit_rows: yahoo_poll_task/upbit_poll_task가 매 폴링 사이클마다
      읽는 리스트. ticker_watcher_task가 포트폴리오 변경 감지 시 통째로 교체한다.
    """
    def __init__(self):
        self.ws = None
        self.sub_queue: asyncio.Queue | None = None
        self.us_ticker_set: set = set()
        self.tr_key_map: dict = {}
        self.yahoo_rows: list = []
        self.upbit_rows: list = []


# ---------------------------------------------------------------------------
# tr_key 생성
# ---------------------------------------------------------------------------
def make_us_tr_key(ticker: str, market: str, day_trading: bool = False) -> str:
    """
    day_trading=True면 주간거래(R접두어) tr_key를, False면 기존 무료
    지연체결가(D접두어) tr_key를 생성한다.
    """
    table  = US_MARKET_DAY_PREFIX if day_trading else US_MARKET_PREFIX
    prefix = table.get(market, "RBAQ" if day_trading else "DNAS")
    return f"{prefix}{ticker}"


def _is_us_day_trading_now() -> bool:
    """
    현재 미국주간거래(KST 10:00~16:00) 시간대인지 여부.
    get_market_status()는 US 마켓별로 호출해야 하지만, 주간거래 판단 자체는
    NAS/NYS/AMS 어느 마켓으로 호출해도 동일한 결과가 나온다(순수 KST 시각/
    날짜 기준 계산이라 거래소별 차이가 없음) — 그래서 "NAS" 고정으로 호출한다.
    단, 이 가정은 market_map에 "NAS"가 market_time="US"로 정의되어 있다는
    전제에 의존한다(현재 config.json 기준 항상 존재).
    """
    return get_market_status("NAS") == "day"


# ---------------------------------------------------------------------------
# 구독 메시지 생성
# ---------------------------------------------------------------------------
def _sub_msg(approval_key: str, tr_id: str, tr_key: str, sub: bool) -> str:
    return json.dumps({
        "header": {
            "approval_key": approval_key,
            "custtype":     "P",
            "tr_type":      "1" if sub else "2",
            "content-type": "utf-8",
        },
        "body": {
            "input": {
                "tr_id":  tr_id,
                "tr_key": tr_key,
            }
        }
    })


# ---------------------------------------------------------------------------
# 수신 데이터 파싱
# ---------------------------------------------------------------------------
def parse_kr(raw: str):
    """H0STCNT0 수신 데이터 → (ticker, price, change_pct)"""
    fields = raw.split("^")
    try:
        ticker     = fields[0]
        price      = float(fields[KR_IDX_PRICE])
        change_pct = float(fields[KR_IDX_CHANGE_PCT])
        return ticker, price, change_pct
    except (IndexError, ValueError) as e:
        log.error(f"KR 데이터 파싱 실패: {e} / raw={raw[:80]}")
        return None


US_IDX_KOR_TIME = 7  # 한국시간 (KHMS) 상수 추가 (선택사항)

def parse_us(raw: str):
    """HDFSCNT0 수신 데이터 → (ticker, price, change_pct, kor_time)"""
    fields = raw.split("^")
    try:
        symb       = fields[0]
        price      = float(fields[US_IDX_PRICE])
        change_pct = float(fields[US_IDX_CHANGE_PCT])
        kor_time   = fields[US_IDX_KOR_TIME]  # 한국 체결시각 (HHMMSS)

        return symb, price, change_pct, kor_time
    except (IndexError, ValueError) as e:
        log.error(f"US 데이터 파싱 실패: {e} / raw={raw[:80]}")
        return None

# ---------------------------------------------------------------------------
# 웹소켓 수신 데이터 → Redis 업데이트
# ---------------------------------------------------------------------------
def _save_price(ticker: str, price: float, change_pct: float):
    if price == 0:
        log.warning(f"[{ticker}] 가격 0 수신 — 저장 건너뜀")
        return
    try:
        update_price_cache(ticker, price, change_pct, None)
        log.debug(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
    except Exception as e:
        log.error(f"[{ticker}] 저장 실패: {e}")


def _notify():
    """
    recalc_today_row() + publish_price_updated() 호출.
    recalc_today_row() 내부의 _recalc_lock이 동시 호출 시 중복 실행을 막아주므로,
    WS/Yahoo 어느 경로에서 호출되든 매번 그냥 호출한다 (별도 억제 로직 불필요).
    """
    try:
        from common.redis_store import recalc_today_row, publish_price_updated
        recalc_today_row()
        publish_price_updated()
        log.debug("price_updated 신호 발행 (Redis)")
    except Exception as e:
        log.error(f"신호 발행 실패: {e}")


def _apply_test_fx_offset(ticker: str, price: float, change_pct: float) -> tuple[float, float]:
    """
    TEST_FX_OFFSET_MODE=1일 때만 USDKRW=X를 실제 조회값 기준으로 +/- 1원 흔든다.
    다른 종목은 그대로 반환한다.
    """
    if TEST_FX_OFFSET_MODE != 1 or ticker != "USDKRW=X" or price == 0:
        return price, change_pct

    direction = 1 if int(time.time()) % 2 == 0 else -1
    adjusted_price = price + (TEST_FX_OFFSET * direction)

    try:
        prev_close = price / (1 + (change_pct / 100.0)) if change_pct else price
        if prev_close:
            adjusted_change_pct = round((adjusted_price - prev_close) / prev_close * 100, 2)
        else:
            adjusted_change_pct = change_pct
    except Exception:
        adjusted_change_pct = change_pct

    return adjusted_price, adjusted_change_pct

# ---------------------------------------------------------------------------
# US tr_key → DB ticker 역매핑 테이블
# (prefix 4자리 제거)
# ---------------------------------------------------------------------------
def _us_tr_key_to_ticker(tr_key: str, us_ticker_set: set) -> str | None:
    """
    HDFSCNT0 수신 SYMB(예: DNASTQQQ) → DB ticker(예: TQQQ)
    prefix 4자리(DNAS/DNYS/DAMS/DARC) 제거 후 us_ticker_set에서 확인.
    """
    candidate = tr_key[4:]  # prefix 4자리 제거
    return candidate if candidate in us_ticker_set else None


# ---------------------------------------------------------------------------
# 구독 대상 종목 조회
# ---------------------------------------------------------------------------
def get_subscribe_targets():
    """
    DB에서 전체 tickers 조회.

    [2026-08-07 변경] 장 상태(open/pre/after/closed)에 따른 필터링을 제거했다.
    포트폴리오(DB tickers 테이블)에 있는 종목은 장 상태와 무관하게 항상 구독
    대상에 포함한다. 이유: market_status_watcher_task가 더 이상 구독 여부를
    결정하지 않는 구조로 바뀌었고("장 상태 전환은 재시작/구독변경을 유발하지
    않음"), 구독 등록/해제를 결정하는 유일한 이벤트는 포트폴리오 변경뿐이어야
    한다는 게 이번 구조 변경의 원칙이기 때문이다. 실측(2026-08-07)으로 휴장
    상태에서도 KIS가 신규 구독 등록을 정상 허용함을 확인했다(OPSP0000).

    반환: (kr_tickers, us_rows, yahoo_rows, upbit_rows)
      kr_tickers : ['005930', ...]
      us_rows    : [{'ticker': 'TQQQ', 'market': 'NAS'}, ...]
      yahoo_rows : [{'ticker': '^NDX', 'market': 'INDEX'}, ...]   (Yahoo REST 폴링 대상)
      upbit_rows : [{'ticker': 'USDKRW=X', 'market': 'FX'}, ...]  (Upbit REST 폴링 대상)
    """
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT ticker, market FROM tickers ORDER BY market, ticker")
            rows = [{"ticker": r[0], "market": r[1]} for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        log.error(f"tickers 조회 실패: {e}")
        return [], [], [], []

    kr_tickers = []
    us_rows    = []
    yahoo_rows = []
    upbit_rows = []

    for r in rows:
        market = r["market"]
        ticker = r["ticker"]

        market_info = common.config.get("market_map", {}).get(market, {})
        market_time = market_info.get("market_time", "24h")

        if market_time == "KR":
            kr_tickers.append(ticker)
        elif market_time == "US":
            us_rows.append(r)
        else:  # 24h
            is_upbit_fx     = (ticker == "USDKRW=X" and common.FX_SOURCE == "upbit")
            is_upbit_crypto = (market == "CRYPTO" and common.CRYPTO_SOURCE == "upbit")
            if is_upbit_fx or is_upbit_crypto:
                upbit_rows.append(r)
            else:
                yahoo_rows.append(r)

    return kr_tickers, us_rows, yahoo_rows, upbit_rows


# ---------------------------------------------------------------------------
# Yahoo 폴링 태스크 (asyncio)
# ---------------------------------------------------------------------------
async def yahoo_poll_task(shared: SharedState):
    """
    INDEX/COM/ETC 등 순수 Yahoo 대상 종목을 주기적으로 Yahoo REST로 폴링.

    [2026-08-07 변경] 고정 리스트 인자 대신 shared.yahoo_rows를 매 폴링 사이클마다
    읽는다. ticker_watcher_task가 포트폴리오 변경 감지 시 shared.yahoo_rows를
    통째로 교체하므로, 재시작 없이 다음 폴링 사이클부터 바로 반영된다. 목록이
    비어 있어도 task 자체는 계속 돌며(다음에 채워질 수 있으므로), 비어있는
    사이클에는 폴링/알림 모두 건너뛴다.
    """
    while True:
        rows = shared.yahoo_rows
        if rows:
            for r in rows:
                ticker = r["ticker"]
                try:
                    price, change_pct, data_time = get_yahoo_price(ticker)
                    if price == 0:
                        log.warning(f"[{ticker}] Yahoo 가격 0 — 건너뜀")
                        continue
                    price, change_pct = _apply_test_fx_offset(ticker, price, change_pct)
                    update_price_cache(ticker, price, change_pct, data_time)
                    log.debug(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
                except Exception as e:
                    log.error(f"[{ticker}] Yahoo 폴링 실패: {e}")

            # 매 폴링 사이클마다 알림 발행.
            # (recalc_today_row 내부 락이 WS와의 동시 호출 중복을 막아주므로
            #  여기서 별도로 "최근에 WS가 알림 보냈는지" 따질 필요 없음)
            _notify()

        await asyncio.sleep(YAHOO_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Upbit 폴링 태스크 (asyncio) — FX(USDKRW=X)/CRYPTO 전용
# ---------------------------------------------------------------------------
async def upbit_poll_task(shared: SharedState):
    """
    FX(USDKRW=X)/CRYPTO 종목을 주기적으로 Upbit REST로 폴링. Yahoo 루프와 독립적인
    주기 사용. shared.upbit_rows를 매 사이클마다 읽는 것은 yahoo_poll_task와 동일.
    """
    while True:
        rows = shared.upbit_rows
        if rows:
            for r in rows:
                ticker = r["ticker"]
                market = r["market"]
                try:
                    if ticker == "USDKRW=X":
                        price, change_pct, data_time = common.get_upbit_krw_price()
                    else:  # market == "CRYPTO"
                        price, change_pct, data_time = common.get_upbit_crypto_price(ticker)

                    if price == 0:
                        log.warning(f"[{ticker}] Upbit 가격 0 — 건너뜀")
                        continue
                    price, change_pct = _apply_test_fx_offset(ticker, price, change_pct)
                    update_price_cache(ticker, price, change_pct, data_time)
                    log.debug(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
                except Exception as e:
                    log.error(f"[{ticker}] Upbit 폴링 실패: {e}")

            _notify()

        await asyncio.sleep(UPBIT_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# 웹소켓 JSON 응답(구독 결과 등) 로깅
# ---------------------------------------------------------------------------
def _log_ws_json_response(raw_msg: str):
    """
    구독/해제 응답 msg_cd를 구분해서 로깅한다 (KIS 공식 오류코드 문서 기준).
    OPSP0002/0003/0008 발생 시 "메모리 추적 상태"와 "서버 실제 상태"가 어긋난
    것일 수 있으므로 경고/에러로 남겨 조기에 발견한다.
    """
    try:
        j = json.loads(raw_msg)
        body = j.get("body", {})
        msg_cd = body.get("msg_cd", "")
        msg1 = body.get("msg1", "")
        tr_id = j.get("header", {}).get("tr_id", "")

        if msg_cd == "OPSP0000":
            log.info(f"WS 응답 [{tr_id}] 구독 성공: {msg1}")
        elif msg_cd == "OPSP0001":
            log.info(f"WS 응답 [{tr_id}] 해제 성공: {msg1}")
        elif msg_cd == "OPSP0002":
            log.warning(f"WS 응답 [{tr_id}] 이미 구독 중: {msg1}")
        elif msg_cd == "OPSP0003":
            log.warning(f"WS 응답 [{tr_id}] 해제 대상 없음: {msg1}")
        elif msg_cd == "OPSP0008":
            log.error(f"WS 응답 [{tr_id}] 구독 한도 초과: {msg1}")
        elif msg1:
            log.info(f"WS 응답 [{tr_id}] {msg_cd}: {msg1}")
    except Exception as e:
        log.warning(f"WS JSON 응답 파싱 실패: {e} / raw={raw_msg[:200]}")


# ---------------------------------------------------------------------------
# 구독/해제 전송 전용 코루틴 (send 루프)
# ---------------------------------------------------------------------------
async def _ws_send_loop(ws, sub_queue: "asyncio.Queue", approval_key: str):
    """
    sub_queue를 소비하며 ws.send()를 전담하는 코루틴. kis_ws_task의 recv 루프와는
    별도 코루틴에서 동작한다. 이 조합(recv 1곳 + send 1곳, 같은 ws 객체 공유)은
    websockets 공식 문서(consumer/producer 예제) 및 KIS 공식 챗봇 확인 기준
    안전한 패턴이다. 구독 추가/삭제를 요청하는 다른 task(ticker_watcher_task)는
    이 큐에 명령만 넣고, 실제 전송은 여기서만 일어나므로 send 지점이 하나로
    직렬화된다.
    """
    try:
        while True:
            action, tr_id, tr_key = await sub_queue.get()
            try:
                await ws.send(_sub_msg(approval_key, tr_id, tr_key, action == "SUB"))
                log.info(f"[{'구독' if action == 'SUB' else '해제'} 요청 전송] {tr_id} {tr_key}")
            except Exception as e:
                log.error(f"구독 요청 전송 실패 ({tr_id} {tr_key}): {e}")
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# KIS 웹소켓 메인 루프 (asyncio)
# ---------------------------------------------------------------------------
async def kis_ws_task(shared: SharedState):
    """
    KIS 웹소켓 연결 → (재)연결 시점 DB 최신 상태 기준 KR/US 종목 전체 구독 →
    수신 루프. 연결 끊기면 WS_RECONNECT_DELAY 후 재연결.

    approval_key는 매 연결(최초 연결 포함) 시도 시마다 kis_auth.get_kis_approval_key()로
    새로 조회한다. 캐시가 유효하면(만료까지 5분 이상) Redis/발급 비용 없이 즉시 반환되므로,
    24시간 이상 프로세스가 살아있어도 만료된 키를 계속 재사용하는 문제가 해결된다.

    [2026-08-07 변경]
      - 연결/재연결마다 get_subscribe_targets()로 DB 최신 상태를 다시 조회해서
        전체 재구독한다(포트폴리오가 그 사이 바뀌었어도 재연결 시 항상 최신 반영).
      - recv 루프(본 코루틴)와 별도로 send 전용 코루틴(_ws_send_loop)을 띄워
        shared.sub_queue를 소비하게 한다. ticker_watcher_task는 이 큐에 명령만
        넣고 직접 ws.send()를 호출하지 않는다.
      - 연결이 끊긴 동안에는 shared.ws/shared.sub_queue를 None으로 비워서,
        그 사이 ticker_watcher_task가 개별 구독 요청을 시도해도 "미연결" 상태임을
        알 수 있게 하고, 그 요청은 버려진다 — 재연결 시 전체 재구독이 대신 처리한다.
    """
    while True:
        try:
            approval_key = get_kis_approval_key()
            kr_tickers, us_rows, _, _ = get_subscribe_targets()
            log.info(
                f"KIS 웹소켓 연결 시도: {WS_URL} (KR {len(kr_tickers)}개, US {len(us_rows)}개)"
            )

            # [2026-08-07 실험] ping_interval=None(프로토콜 레벨 keepalive 비활성화)이던 것을
            # 되돌림. KIS 텍스트 "PINGPONG"(애플리케이션 레벨, 아래 497행대에서 별도 처리)과는
            # 다른 계층(WS 표준 ping/pong 프레임, opcode 0x9/0xA)이라 서로 간섭 없음
            # (websockets 공식 문서 확인). 20초마다 소켓에 트래픽이 흐르게 해서, 데이터가
            # 안 흐르는 구간에 "no close frame received or sent"로 재연결되던 문제
            # (약 100~103초 간격, 매우 규칙적)가 NAT/방화벽 등의 idle timeout 때문일 가능성을
            # 테스트한다. 효과 없으면 원인이 다른 곳(예: KIS 서버 측 정책)에 있다는 뜻이므로
            # ping_interval=None으로 되돌리고 다른 가설을 봐야 한다.
            async with websockets.connect(
                WS_URL, ping_interval=20, ping_timeout=20
            ) as ws:
                log.info("KIS 웹소켓 연결됨")

                day_trading = _is_us_day_trading_now()

                shared.us_ticker_set = {r["ticker"] for r in us_rows}
                shared.tr_key_map = {
                    make_us_tr_key(r["ticker"], r["market"], day_trading): r["ticker"]
                    for r in us_rows
                }
                shared.sub_queue = asyncio.Queue()
                shared.ws = ws

                # KR 구독 (DB 최신 상태 기준 전체)
                for ticker in kr_tickers:
                    await ws.send(_sub_msg(approval_key, "H0STCNT0", ticker, True))
                    log.info(f"[KR] 구독: {ticker}")
                    await asyncio.sleep(0.05)

                # US 구독 (DB 최신 상태 기준 전체, 현재 주간거래 여부에 맞는 접두어)
                for r in us_rows:
                    tr_key = make_us_tr_key(r["ticker"], r["market"], day_trading)
                    await ws.send(_sub_msg(approval_key, "HDFSCNT0", tr_key, True))
                    log.info(f"[US] 구독: {tr_key}")
                    await asyncio.sleep(0.05)

                send_task = asyncio.create_task(
                    _ws_send_loop(ws, shared.sub_queue, approval_key)
                )

                try:
                    last_notify = time.time()

                    async for raw_msg in ws:
                        # PINGPONG 처리
                        if raw_msg == "PINGPONG":
                            await ws.send("PINGPONG")
                            continue

                        # JSON 응답 (구독 결과 등) 처리
                        if raw_msg.startswith("{"):
                            _log_ws_json_response(raw_msg)
                            continue

                        # 실시간 데이터: |로 구분된 문자열
                        # 형식: tr_id|tr_key_cnt|data_cnt|data1^data2^...
                        parts = raw_msg.split("|")
                        if len(parts) < 4:
                            continue

                        tr_id    = parts[1]
                        data_str = parts[3]

                        if tr_id == "H0STCNT0":
                            result = parse_kr(data_str)
                            if result:
                                ticker, price, change_pct = result
                                _save_price(ticker, price, change_pct)

                        elif tr_id == "HDFSCNT0":

                            log.debug(f"HDFSCNT0 RAW={data_str}")

                            result = parse_us(data_str)
                            if result:
                                # kor_time을 추가로 받습니다.
                                symb, price, change_pct, kor_time = result

                                # SYMB → DB ticker 변환
                                db_ticker = shared.tr_key_map.get(symb)
                                if db_ticker is None:
                                    db_ticker = _us_tr_key_to_ticker(symb, shared.us_ticker_set)

                                if db_ticker:
                                    _save_price(db_ticker, price, change_pct)

                                    # 가독성을 위해 HHMMSS -> HH:MM:SS 변환
                                    if len(kor_time) == 6:
                                        formatted_time = f"{kor_time[0:2]}:{kor_time[2:4]}:{kor_time[4:6]}"
                                    else:
                                        formatted_time = kor_time

                                    # ⭐ 실시간 KST 로그 출력
                                    log.debug(f"[US] {db_ticker} 현재가: {price} ({change_pct}%) | 체결시각(KST): {formatted_time}")
                                else:
                                    log.warning(f"[US] 매핑 실패: {symb}")

                        # 일정 주기마다 price_updated 신호 발행 (Redis pub/sub)
                        now = time.time()
                        if now - last_notify >= 0.2:
                            _notify()
                            last_notify = now
                finally:
                    pending = shared.sub_queue.qsize() if shared.sub_queue else 0
                    if pending:
                        log.warning(
                            f"연결 종료로 미전송 구독/해제 명령 {pending}건 폐기 — "
                            f"재연결 시 DB 최신 상태로 전체 재구독됨"
                        )
                    send_task.cancel()
                    try:
                        await send_task
                    except asyncio.CancelledError:
                        pass

        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"KIS 웹소켓 연결 종료: {e} — {WS_RECONNECT_DELAY}초 후 재연결")
        except Exception as e:
            log.error(f"KIS 웹소켓 오류: {e} — {WS_RECONNECT_DELAY}초 후 재연결")

        shared.ws = None
        shared.sub_queue = None
        await asyncio.sleep(WS_RECONNECT_DELAY)


# ---------------------------------------------------------------------------
# KR 종가 1회성 확정 조회 task (15:40 + buf 이후 하루 1회)
# ---------------------------------------------------------------------------
async def kr_final_close_task():
    while True:
        await asyncio.sleep(60)
        if not KR_FINAL_CLOSE_ENABLED:
            continue
        if should_run_kr_final_close():
            run_kr_final_close_update()


# ---------------------------------------------------------------------------
# 공휴일 캐시 갱신 태스크
# ---------------------------------------------------------------------------
async def holiday_cache_refresh_task():
    """
    holiday_cache.refresh_if_needed()를 주기적으로 호출한다.
    refresh_if_needed() 내부에 08:00 KST 이후 하루 1회 가드가 있어 실제 외부 API 호출은
    하루 1번뿐이므로, 여기서는 그 가드를 통과시키기 위한 트리거 역할만 한다.
    요일/공휴일 판단(should_run_kr_final_close 등)과 무관하게 항상 동작해야 하므로
    별도 task로 분리한다.
    """
    while True:
        await asyncio.sleep(3600)
        holiday_cache.refresh_if_needed()


# ---------------------------------------------------------------------------
# ticker_changed 이벤트 리스너 (별도 스레드)
# ---------------------------------------------------------------------------
def _ticker_changed_listener(q: "queue.Queue"):
    """
    별도 OS 스레드에서 Redis `ticker_changed` 채널을 blocking pubsub.listen()으로 구독한다.

    settings.py의 _notify_ticker_changed() → publish_ticker_changed()가 티커 추가/삭제/변경
    직후 이 채널에 신호를 발행한다. 여기서는 polling 없이 메시지가 올 때까지 순수 대기하다가,
    수신 즉시 스레드 안전한 표준 queue.Queue에 넣어 메인 asyncio 루프(ticker_watcher_task)로
    전달한다. queue.Queue.put()/get()은 그 자체로 스레드 안전하므로 별도 락이 필요 없다.

    Redis 연결이 끊기면 예외 발생 시 5초 후 재구독을 시도한다 (짧은 순단 대비).
    """
    import redis as _redis
    while True:
        try:
            r = _redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe("ticker_changed")
            log.info("[ticker_watcher] ticker_changed 채널 구독 시작")
            for message in pubsub.listen():
                if message["type"] == "message":
                    q.put(1)
        except Exception as e:
            log.error(f"[ticker_watcher] Redis 구독 오류: {e} — 5초 후 재연결")
            time.sleep(5)


# ---------------------------------------------------------------------------
# 구독 요청 헬퍼 (KR/US 개별 등록/해제)
# ---------------------------------------------------------------------------
async def _request_subscribe(shared: SharedState, tr_id: str, tr_key: str, sub: bool):
    """
    shared.sub_queue에 구독/해제 명령을 넣는다. 실제 ws.send()는 kis_ws_task의
    _ws_send_loop가 전담한다(recv/send 분리 원칙, 41행대 주석 참고).

    연결이 끊긴 상태(shared.sub_queue가 None)라면 요청을 버리고 경고만 남긴다.
    별도 재시도/보관 로직을 두지 않는 이유: 재연결 시 kis_ws_task가 DB 최신
    상태로 전체 재구독하므로, 그 시점에 이 요청이 자동으로 반영되기 때문이다.
    """
    if shared.sub_queue is None:
        log.warning(
            f"웹소켓 미연결 상태 — {'구독' if sub else '해제'} 요청 건너뜀 "
            f"({tr_id} {tr_key}), 다음 재연결 시 DB 최신 상태로 반영됨"
        )
        return
    await shared.sub_queue.put(("SUB" if sub else "UNSUB", tr_id, tr_key))


# ---------------------------------------------------------------------------
# 미국주간거래(D↔R) 전환 — 이미 구독 중인 US 종목 전체의 tr_key 접두어를 바꾼다
# ---------------------------------------------------------------------------
async def _switch_us_day_trading_prefix(shared: SharedState, to_day: bool):
    """
    KST 10:00/16:00 경계에서 호출된다. US 종목 1개당 tr_key 1개만 유지하는
    "전환" 방식(41종목 세션 한도 보호, 2026-08-20 설계 확정)이므로, 이미
    구독 중인 종목 전체를 대상으로 기존 접두어를 UNSUB하고 새 접두어로 SUB한다.

    처리 순서는 전체 UNSUB → 전체 SUB (하루 2번뿐인 저빈도 이벤트라, 전환
    구간 동안 US 시세가 잠깐 비는 것을 감수하는 단순한 구조를 택함 — 종목별
    UNSUB/SUB pair 처리 대신).

    market은 별도로 저장해두지 않고, 기존 tr_key의 접두어 4자리를
    _US_PREFIX_TO_MARKET으로 역매핑해서 복원한다.

    [알려진 제약] 이 함수 실행 중(각 await 사이)에 ticker_watcher_task가
    동시에 같은 shared.tr_key_map을 변경하면 경합이 생길 수 있다. 하루 2번,
    수 초 내 끝나는 저빈도 이벤트라 실무적 위험은 낮다고 보고 별도 락 없이
    진행하며, 문제가 실측되면 그때 락을 추가한다.
    """
    old_map = dict(shared.tr_key_map)  # {old_tr_key: ticker} 스냅샷
    if not old_map:
        log.info(f"[US] 주간거래 {'진입' if to_day else '종료'} — 구독 중인 US 종목 없음, 전환 생략")
        return

    # 1) 기존 접두어 전체 UNSUB
    for old_tr_key in old_map:
        await _request_subscribe(shared, "HDFSCNT0", old_tr_key, False)

    # 2) 새 접두어로 전체 SUB (+ 역매핑 테이블 갱신)
    new_map = {}
    for old_tr_key, ticker in old_map.items():
        market = _US_PREFIX_TO_MARKET.get(old_tr_key[:4])
        if market is None:
            log.warning(f"[US] 주간거래 전환 중 market 판별 실패, 건너뜀: {old_tr_key}")
            continue
        new_tr_key = make_us_tr_key(ticker, market, to_day)
        await _request_subscribe(shared, "HDFSCNT0", new_tr_key, True)
        new_map[new_tr_key] = ticker

    shared.tr_key_map = new_map
    log.info(
        f"[US] 주간거래 {'진입(D→R)' if to_day else '종료(R→D)'} 전환 완료 — {len(new_map)}개 종목"
    )


# ---------------------------------------------------------------------------
# 구독 갱신 감시 태스크 (이벤트 기반 — 5분 polling 대체)
# ---------------------------------------------------------------------------
async def ticker_watcher_task(
    shared: SharedState,
    prev_kr_set: set,
    prev_us_set: set,
    prev_yahoo_set: set,
    prev_upbit_set: set,
):
    """
    ticker_changed 이벤트 수신 시에만 구독 대상을 재조회하고, 변경분을 재시작 없이
    반영한다. sleep 기반 polling이 없다.

    [2026-08-07 변경] os.execv() 프로세스 재시작을 제거했다.
      - KR/US: 추가/삭제된 종목만 개별 tr_type=1/2로 _request_subscribe() 호출.
        41건 세션 한도는 별도로 사전 체크하지 않는다 — 그냥 시도하고, 서버가
        OPSP0008(한도초과) 등으로 거부하면 _log_ws_json_response()가 에러 로그를
        남긴다(41건이라는 한도 자체가 문서 기준이라 실제와 다를 수 있어, 하드코딩
        체크보다 서버 응답을 그대로 신뢰하는 쪽을 택함).
      - Yahoo/Upbit: shared.yahoo_rows/shared.upbit_rows를 통째로 교체 — 다음
        폴링 사이클부터 자동 반영.

    prev_*_set: main()에서 최초 get_subscribe_targets() 결과로 시드한 현재 상태.
    """
    q: "queue.Queue" = queue.Queue()
    threading.Thread(target=_ticker_changed_listener, args=(q,), daemon=True).start()

    while True:
        await asyncio.to_thread(q.get)  # 이벤트 올 때까지 블로킹 대기 (polling 아님)

        # 짧은 시간 내 여러 changed 이벤트가 몰려도 한 번만 처리
        while not q.empty():
            try:
                q.get_nowait()
            except queue.Empty:
                break

        holiday_cache.refresh_if_needed()
        kr, us, yahoo, upbit = get_subscribe_targets()
        kr_set    = set(kr)
        us_set    = {(r["ticker"], r["market"]) for r in us}
        yahoo_set = {(r["ticker"], r["market"]) for r in yahoo}
        upbit_set = {(r["ticker"], r["market"]) for r in upbit}

        # --- KR: 개별 등록/해제 ---
        kr_added   = kr_set - prev_kr_set
        kr_removed = prev_kr_set - kr_set
        for ticker in kr_added:
            await _request_subscribe(shared, "H0STCNT0", ticker, True)
        for ticker in kr_removed:
            await _request_subscribe(shared, "H0STCNT0", ticker, False)
        if kr_added:
            log.info(f"[KR] 구독 추가: {sorted(kr_added)}")
        if kr_removed:
            log.info(f"[KR] 구독 제거: {sorted(kr_removed)}")

        # --- US: 개별 등록/해제 (+ 역매핑 테이블 갱신) ---
        # 현재 주간거래 여부에 맞는 접두어를 써야, market_status_watcher_task가
        # 관리하는 shared.tr_key_map의 접두어 상태와 어긋나지 않는다.
        day_trading = _is_us_day_trading_now()
        us_added    = us_set - prev_us_set
        us_removed  = prev_us_set - us_set
        for ticker, market in us_added:
            tr_key = make_us_tr_key(ticker, market, day_trading)
            await _request_subscribe(shared, "HDFSCNT0", tr_key, True)
            shared.us_ticker_set.add(ticker)
            shared.tr_key_map[tr_key] = ticker
        for ticker, market in us_removed:
            tr_key = make_us_tr_key(ticker, market, day_trading)
            await _request_subscribe(shared, "HDFSCNT0", tr_key, False)
            shared.us_ticker_set.discard(ticker)
            shared.tr_key_map.pop(tr_key, None)
        if us_added:
            log.info(f"[US] 구독 추가: {sorted(us_added)}")
        if us_removed:
            log.info(f"[US] 구독 제거: {sorted(us_removed)}")

        # --- Yahoo/Upbit: 공유 컨테이너 통째로 교체 ---
        if yahoo_set != prev_yahoo_set:
            shared.yahoo_rows = yahoo
            log.info(f"[Yahoo] 구독 목록 갱신: {len(yahoo)}개")
        if upbit_set != prev_upbit_set:
            shared.upbit_rows = upbit
            log.info(f"[Upbit] 구독 목록 갱신: {len(upbit)}개")

        prev_kr_set    = kr_set
        prev_us_set    = us_set
        prev_yahoo_set = yahoo_set
        prev_upbit_set = upbit_set


# ---------------------------------------------------------------------------
# 시장 상태 변경 감시 태스크
# (태스크 시작 시 1회만 DB 조회, 이후 폴링 루프는 DB/Redis 접근 없이 순수 계산만 수행)
# ---------------------------------------------------------------------------
MARKET_STATUS_POLL_INTERVAL = 10  # 초


async def market_status_watcher_task(shared: SharedState):
    """
    [2026-08-07 변경] KR/US "활성/휴장" 전환 자체는 구독 등록/해제에 관여하지
    않는다. 포트폴리오에 있는 종목은 장 상태와 무관하게 항상 구독 대상이기
    때문이다. 이 부분은 오직 관찰용 로그만 남긴다: KIS가 장마감 중 idle
    세션을 서버 측에서 자연 종료하는지(인계 문서 1.4, 공식 자료로 미확인)
    여부를, 실제 운영 로그로 나중에 확인하기 위한 참고 자료 용도다.

    [2026-08-20 변경] 단, 미국주간거래(KST 10:00/16:00) 진입·이탈은 예외다.
    이건 "장 상태 전환"이 아니라 "같은 종목의 tr_key 접두어(D↔R)를 바꿔야
    하는" 별도 이벤트라서, 이 task가 감지해서 _switch_us_day_trading_prefix()로
    실제 구독 해제/재등록을 수행한다. 그래서 더 이상 "구독 변경에 관여하지
    않는 task"가 아니다.

    DB 조회는 태스크 시작 시 1회만 수행한다(활성/휴장 관찰용 markets 집합
    산출에만 쓰임). 이후 10초 폴링 루프 안에서는 get_market_status()(순수
    함수, DB/Redis 접근 없음)만 호출한다.

    주간거래 전환 감지는 DB 스냅샷(markets)에 의존하지 않는다 — 포트폴리오에
    US 종목이 나중에 추가되는 경우까지 놓치지 않기 위해, 항상
    _is_us_day_trading_now()를 매 폴링마다 독립적으로 확인한다.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT market FROM tickers")
            existing_markets = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()

    markets = {
        m for m in existing_markets
        if common.config.get("market_map", {}).get(m, {}).get("market_time") in ("KR", "US")
    }

    def _active_snapshot() -> dict:
        # "closed 여부"만으로 정규화 — True(활성) / False(closed/day)
        return {m: (get_market_status(m) not in ("closed", "day")) for m in markets}

    prev_active = _active_snapshot() if markets else {}
    prev_is_day = _is_us_day_trading_now()

    while True:
        await asyncio.sleep(MARKET_STATUS_POLL_INTERVAL)

        if markets:
            curr_active = _active_snapshot()
            if curr_active != prev_active:
                log.info(
                    f"[market_status_watcher] 시장상태(활성/휴장) 변경 감지 "
                    f"{prev_active} → {curr_active} (관찰용 로그 — 재시작/구독변경 없음)"
                )
            prev_active = curr_active

        curr_is_day = _is_us_day_trading_now()
        if curr_is_day != prev_is_day:
            log.info(
                f"[market_status_watcher] 미국주간거래 {'진입' if curr_is_day else '종료'} 감지 "
                f"— US 종목 tr_key 전환 시작"
            )
            await _switch_us_day_trading_prefix(shared, curr_is_day)
        prev_is_day = curr_is_day


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def main():
    load_config()

    if TEST_FX_OFFSET_MODE == 1:
        log.info("price_updater_ws (TEST_FX_OFFSET_MODE=1) 시작 — 기존 시세조회 유지, USDKRW=X만 실조회값 기준 ±1원 오프셋")

    log.info("price_updater (웹소켓 모드) 시작")

    holiday_cache.refresh_if_needed()

    # 구독 대상 조회 (초기 시드값 — 실제 KR/US 구독은 kis_ws_task가 연결 시점에
    # 다시 조회해서 최신 상태로 건다. 여기서는 ticker_watcher_task의 diff 기준값과
    # Yahoo/Upbit 초기 목록으로만 사용)
    kr_tickers, us_rows, yahoo_rows, upbit_rows = get_subscribe_targets()
    log.info(
        f"구독 대상 — KR: {len(kr_tickers)}개, US: {len(us_rows)}개, "
        f"Yahoo: {len(yahoo_rows)}개, Upbit: {len(upbit_rows)}개"
    )

    shared = SharedState()
    shared.yahoo_rows = yahoo_rows
    shared.upbit_rows = upbit_rows

    # asyncio 이벤트 루프
    async def run():
        tasks = [
            asyncio.create_task(kis_ws_task(shared)),
            asyncio.create_task(yahoo_poll_task(shared)),
            asyncio.create_task(upbit_poll_task(shared)),
            asyncio.create_task(
                ticker_watcher_task(
                    shared,
                    set(kr_tickers),
                    {(r["ticker"], r["market"]) for r in us_rows},
                    {(r["ticker"], r["market"]) for r in yahoo_rows},
                    {(r["ticker"], r["market"]) for r in upbit_rows},
                )
            ),
            asyncio.create_task(market_status_watcher_task(shared)),
            asyncio.create_task(kr_final_close_task()),
            asyncio.create_task(holiday_cache_refresh_task()),
        ]

        await asyncio.gather(*tasks)

    asyncio.run(run())


if __name__ == "__main__":
    main()