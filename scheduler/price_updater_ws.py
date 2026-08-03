"""
price_updater_ws.py — KIS 웹소켓 실시간 시세 업데이트
config.json 의 realtime_quote = true 일 때 동작.

구조:
  - KR/US 종목: KIS 웹소켓 (H0STCNT0 / HDFSCNT0) push 수신
  - FX/INDEX/CRYPTO: Yahoo Finance REST 폴링 (별도 asyncio task)
  - 주간거래(KST 10:00~18:00): US 웹소켓 구독 안 함 (closed 처리)

[테스트 모드]
    TEST_FX_OFFSET_MODE = 0  정상 운영
        TEST_FX_OFFSET_MODE = 1  실제 조회는 그대로 두고, USDKRW=X만 실조회값 기준 ±1원 오프셋
                                후 신호 발행 (DOM patch 테스트)
"""

# ---------------------------------------------------------------------------
# 테스트 모드
# ---------------------------------------------------------------------------
TEST_FX_OFFSET_MODE = 0  # 0: 정상, 1: USDKRW=X 실조회값 기준 ±1원 오프셋
TEST_FX_OFFSET = 1.0

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

# DB market → HDFSCNT0 tr_key prefix (야간/정규/애프터 무료시세)
US_MARKET_PREFIX = {
    "NAS": "DNAS",
    "NYS": "DNYS",
    "AMS": "DAMS",
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
UPBIT_POLL_INTERVAL = 10

# 웹소켓 재연결 대기 (초)
WS_RECONNECT_DELAY = 10


# ---------------------------------------------------------------------------
# tr_key 생성
# ---------------------------------------------------------------------------
def make_us_tr_key(ticker: str, market: str) -> str:
    prefix = US_MARKET_PREFIX.get(market, "DNAS")
    return f"{prefix}{ticker}"


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
        kor_time   = fields[7]  # ⭐ 한국 체결시각 (HHMMSS) 추가
        
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
        log.info(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
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
        log.info("price_updated 신호 발행 (Redis)")
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
    DB에서 전체 tickers 조회 후 시장 상태에 따라 분류.
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
        status = get_market_status(market)

        market_info = common.config.get("market_map", {}).get(market, {})
        market_time = market_info.get("market_time", "24h")

        if market_time == "KR":
            if status == "open":
                kr_tickers.append(ticker)
        elif market_time == "US":
            if status in ("open", "pre", "after"):
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
async def yahoo_poll_task(yahoo_rows: list):
    """INDEX/COM/ETC 등 순수 Yahoo 대상 종목을 주기적으로 Yahoo REST로 폴링."""
    if not yahoo_rows:
        return

    while True:
        for r in yahoo_rows:
            ticker = r["ticker"]
            try:
                price, change_pct, data_time = get_yahoo_price(ticker)
                if price == 0:
                    log.warning(f"[{ticker}] Yahoo 가격 0 — 건너뜀")
                    continue
                price, change_pct = _apply_test_fx_offset(ticker, price, change_pct)
                update_price_cache(ticker, price, change_pct, data_time)
                log.info(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
            except Exception as e:
                log.error(f"[{ticker}] Yahoo 폴링 실패: {e}")

        # 매 폴링 사이클마다 항상 알림 발행.
        # (recalc_today_row 내부 락이 WS와의 동시 호출 중복을 막아주므로
        #  여기서 별도로 "최근에 WS가 알림 보냈는지" 따질 필요 없음)
        _notify()
        await asyncio.sleep(YAHOO_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Upbit 폴링 태스크 (asyncio) — FX(USDKRW=X)/CRYPTO 전용
# ---------------------------------------------------------------------------
async def upbit_poll_task(upbit_rows: list):
    """FX(USDKRW=X)/CRYPTO 종목을 주기적으로 Upbit REST로 폴링. Yahoo 루프와 독립적인 주기 사용."""
    if not upbit_rows:
        return

    while True:
        for r in upbit_rows:
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
                log.info(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
            except Exception as e:
                log.error(f"[{ticker}] Upbit 폴링 실패: {e}")

        _notify()
        await asyncio.sleep(UPBIT_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# KIS 웹소켓 메인 루프 (asyncio)
# ---------------------------------------------------------------------------
async def kis_ws_task(kr_tickers: list, us_rows: list):
    """
    KIS 웹소켓 연결 → KR/US 종목 구독 → 수신 루프.
    연결 끊기면 WS_RECONNECT_DELAY 후 재연결.

    approval_key는 매 연결(최초 연결 포함) 시도 시마다 kis_auth.get_kis_approval_key()로
    새로 조회한다. 캐시가 유효하면(만료까지 5분 이상) Redis/발급 비용 없이 즉시 반환되므로,
    24시간 이상 프로세스가 살아있어도 만료된 키를 계속 재사용하는 문제가 해결된다.
    """
    us_ticker_set = {r["ticker"] for r in us_rows}
    # tr_key → ticker 역매핑 (US)
    tr_key_map = {make_us_tr_key(r["ticker"], r["market"]): r["ticker"] for r in us_rows}

    while True:
        try:
            approval_key = get_kis_approval_key()
            log.info(f"KIS 웹소켓 연결 시도: {WS_URL}")
            async with websockets.connect(WS_URL, ping_interval=None) as ws:
                log.info("KIS 웹소켓 연결됨")

                # KR 구독
                for ticker in kr_tickers:
                    await ws.send(_sub_msg(approval_key, "H0STCNT0", ticker, True))
                    log.info(f"[KR] 구독: {ticker}")
                    await asyncio.sleep(0.05)

                # US 구독
                for r in us_rows:
                    tr_key = make_us_tr_key(r["ticker"], r["market"])
                    await ws.send(_sub_msg(approval_key, "HDFSCNT0", tr_key, True))
                    log.info(f"[US] 구독: {tr_key}")
                    await asyncio.sleep(0.05)

                last_notify = time.time()

                async for raw_msg in ws:
                    # PINGPONG 처리
                    if raw_msg == "PINGPONG":
                        await ws.send("PINGPONG")
                        continue

                    # JSON 응답 (구독 결과 등) 처리
                    if raw_msg.startswith("{"):
                        try:
                            j = json.loads(raw_msg)
                            msg1 = j.get("body", {}).get("msg1", "")
                            if msg1:
                                log.info(f"WS 응답 [{j.get('header', {}).get('tr_id', '')}]: {msg1}")
                        except Exception:
                            pass
                        continue

                    # 실시간 데이터: |로 구분된 문자열
                    # 형식: tr_id|tr_key_cnt|data_cnt|data1^data2^...
                    # 실시간 데이터: |로 구분된 문자열
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

                        log.info(f"HDFSCNT0 RAW={data_str}")

                        result = parse_us(data_str)
                        if result:
                            # kor_time을 추가로 받습니다.
                            symb, price, change_pct, kor_time = result
                            
                            # SYMB → DB ticker 변환
                            db_ticker = tr_key_map.get(symb)
                            if db_ticker is None:
                                db_ticker = _us_tr_key_to_ticker(symb, us_ticker_set)
                                
                            if db_ticker:
                                _save_price(db_ticker, price, change_pct)
                                
                                # 가독성을 위해 HHMMSS -> HH:MM:SS 변환
                                if len(kor_time) == 6:
                                    formatted_time = f"{kor_time[0:2]}:{kor_time[2:4]}:{kor_time[4:6]}"
                                else:
                                    formatted_time = kor_time
                                
                                # ⭐ 실시간 KST 로그 출력
                                log.info(f"[US] {db_ticker} 현재가: {price} ({change_pct}%) | 체결시각(KST): {formatted_time}")
                            else:
                                log.warning(f"[US] 매핑 실패: {symb}")

                    # 일정 주기마다 price_updated 신호 발행 (Redis pub/sub)
                    now = time.time()
                    # if now - last_notify >= YAHOO_POLL_INTERVAL:
                    if now - last_notify >= 0.2:
                        _notify()
                        last_notify = now

        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"KIS 웹소켓 연결 종료: {e} — {WS_RECONNECT_DELAY}초 후 재연결")
        except Exception as e:
            log.error(f"KIS 웹소켓 오류: {e} — {WS_RECONNECT_DELAY}초 후 재연결")

        await asyncio.sleep(WS_RECONNECT_DELAY)


# ---------------------------------------------------------------------------
# KR 종가 1회성 확정 조회 task (15:40 + buf 이후 하루 1회)
# ---------------------------------------------------------------------------
async def kr_final_close_task():
    while True:
        await asyncio.sleep(60)
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
# 구독 갱신 감시 태스크 (이벤트 기반 — 5분 polling 대체)
# ---------------------------------------------------------------------------
async def ticker_watcher_task(prev_kr_set: set, prev_us_set: set, prev_yahoo_set: set, prev_upbit_set: set):
    """
    ticker_changed 이벤트 수신 시에만 구독 대상을 재조회하고, 실제로 바뀐 경우에만
    웹소켓 재연결(프로세스 재시작)을 수행한다. sleep 기반 polling이 없다.

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

        if kr_set != prev_kr_set or us_set != prev_us_set or yahoo_set != prev_yahoo_set or upbit_set != prev_upbit_set:
            log.info("ticker_changed 이벤트 — 구독 대상 변경 감지, 웹소켓 재연결 필요 (프로세스 재시작으로 처리)")
            # 현재는 프로세스 재시작으로 처리 (systemd Restart=always 활용)
            # 추후 동적 구독/해제 로직으로 개선 가능
            import os, sys
            os.execv(sys.executable, [sys.executable] + sys.argv)

        prev_kr_set    = kr_set
        prev_us_set    = us_set
        prev_yahoo_set = yahoo_set
        prev_upbit_set = upbit_set


# ---------------------------------------------------------------------------
# 시장 상태 변경 감시 태스크 (DB/Redis 접근 없이 순수 계산만 수행)
# ---------------------------------------------------------------------------
MARKET_STATUS_POLL_INTERVAL = 10  # 초


async def market_status_watcher_task(kr_tickers: list, us_rows: list):
    """
    현재 구독 중인 market 코드(KR, NAS, NYS, AMS 등)의 상태(open/pre/after/closed)만
    주기적으로 재계산해서 변경 여부를 감시한다.

    get_market_status()는 순수 함수(DB/Redis 접근 없음)이므로, 이 태스크는 평상시
    DB/Redis를 전혀 건드리지 않는다. 상태가 실제로 바뀐 경우에만 프로세스를 재시작하고,
    재시작된 프로세스의 main()이 get_subscribe_targets()로 DB를 다시 조회해 구독 목록을
    새로 계산한다.

    ticker_watcher_task(종목 추가/삭제/변경 감시)와는 완전히 독립적으로 동작하며 서로의
    상태를 참조하지 않는다. 두 태스크 모두 변경 감지 시 os.execv()로 프로세스 이미지
    자체를 교체하므로, asyncio 싱글스레드 특성상 두 execv가 동시에 실행될 수 없어
    충돌 가능성이 없다.
    """
    markets = set()
    if kr_tickers:
        markets.add("KR")
    for r in us_rows:
        markets.add(r["market"])

    if not markets:
        return

    prev_status = {m: get_market_status(m) for m in markets}

    while True:
        await asyncio.sleep(MARKET_STATUS_POLL_INTERVAL)

        curr_status = {m: get_market_status(m) for m in markets}

        if curr_status != prev_status:
            log.info(
                f"[market_status_watcher] 시장상태 변경 감지 {prev_status} → {curr_status} "
                "— 웹소켓 재연결 필요 (프로세스 재시작으로 처리)"
            )
            import os, sys
            os.execv(sys.executable, [sys.executable] + sys.argv)

        prev_status = curr_status


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def main():
    load_config()

    if TEST_FX_OFFSET_MODE == 1:
        log.info("price_updater_ws (TEST_FX_OFFSET_MODE=1) 시작 — 기존 시세조회 유지, USDKRW=X만 실조회값 기준 ±1원 오프셋")

    log.info("price_updater (웹소켓 모드) 시작")

    holiday_cache.refresh_if_needed()

    # 구독 대상 조회
    kr_tickers, us_rows, yahoo_rows, upbit_rows = get_subscribe_targets()
    log.info(
        f"구독 대상 — KR: {len(kr_tickers)}개, US: {len(us_rows)}개, "
        f"Yahoo: {len(yahoo_rows)}개, Upbit: {len(upbit_rows)}개"
    )

    if not kr_tickers and not us_rows and not yahoo_rows and not upbit_rows:
        log.warning("현재 구독 대상 없음. 5분 후 재시작.")
        time.sleep(300)
        import os, sys
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # asyncio 이벤트 루프
    async def run():
        tasks = []

        if kr_tickers or us_rows:
            tasks.append(asyncio.create_task(
                kis_ws_task(kr_tickers, us_rows)
            ))

        if yahoo_rows:
            tasks.append(asyncio.create_task(
                yahoo_poll_task(yahoo_rows)
            ))

        if upbit_rows:
            tasks.append(asyncio.create_task(
                upbit_poll_task(upbit_rows)
            ))

        tasks.append(asyncio.create_task(
            ticker_watcher_task(
                set(kr_tickers),
                {(r["ticker"], r["market"]) for r in us_rows},
                {(r["ticker"], r["market"]) for r in yahoo_rows},
                {(r["ticker"], r["market"]) for r in upbit_rows},
            )
        ))

        tasks.append(asyncio.create_task(
            market_status_watcher_task(kr_tickers, us_rows)
        ))

        tasks.append(asyncio.create_task(
            kr_final_close_task()
        ))

        tasks.append(asyncio.create_task(
            holiday_cache_refresh_task()
        ))

        await asyncio.gather(*tasks)

    asyncio.run(run())


if __name__ == "__main__":
    main()