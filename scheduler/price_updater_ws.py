"""
price_updater_ws.py — KIS 웹소켓 실시간 시세 업데이트
config.json 의 realtime_quote = true 일 때 동작.

구조:
  - KR/US 종목: KIS 웹소켓 (H0STCNT0 / HDFSCNT0) push 수신
  - FX/INDEX/CRYPTO: Yahoo Finance REST 폴링 (별도 asyncio task)
  - 주간거래(KST 10:00~18:00): US 웹소켓 구독 안 함 (closed 처리)
"""

import asyncio
import json
import time
import threading
from datetime import datetime, date

import requests
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
    update_ticker_in_db,
    _is_close_confirmed,
    _set_close_confirmed,
)

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
    "ARC": "DARC",  # NYSE Arca — prefix 미검증, 동작 확인 필요
}

# H0STCNT0 수신 필드 인덱스
KR_IDX_PRICE      = 2
KR_IDX_CHANGE_PCT = 5

# HDFSCNT0 수신 필드 인덱스
US_IDX_PRICE      = 10
US_IDX_CHANGE_PCT = 13

# Yahoo 폴링 주기 (초)
YAHOO_POLL_INTERVAL = 60

# 웹소켓 재연결 대기 (초)
WS_RECONNECT_DELAY = 10


# ---------------------------------------------------------------------------
# KIS 웹소켓 접속키 발급 (REST 토큰과 별개)
# ---------------------------------------------------------------------------
def get_approval_key() -> str:
    url = "https://openapi.koreainvestment.com:9443/oauth2/Approval"
    body = {
        "grant_type": "client_credentials",
        "appkey":     common.config["kis_app_key"],
        "secretkey":  common.config["kis_app_secret"],
    }
    res  = requests.post(url, json=body, timeout=10, verify=False)
    key  = res.json().get("approval_key", "")
    if not key:
        raise RuntimeError(f"approval_key 발급 실패: {res.text}")
    log.info("KIS 웹소켓 approval_key 발급 완료")
    return key


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


def parse_us(raw: str):
    """HDFSCNT0 수신 데이터 → (ticker, price, change_pct)"""
    fields = raw.split("^")
    try:
        # SYMB 필드에는 prefix 포함 (예: DNASTQQQ) → 순수 ticker 추출 불가
        # DB 조회 시 prefix 제거해서 매칭하므로 SYMB 그대로 반환
        symb       = fields[0]
        price      = float(fields[US_IDX_PRICE])
        change_pct = float(fields[US_IDX_CHANGE_PCT])
        return symb, price, change_pct
    except (IndexError, ValueError) as e:
        log.error(f"US 데이터 파싱 실패: {e} / raw={raw[:80]}")
        return None


# ---------------------------------------------------------------------------
# 웹소켓 수신 데이터 → DB 업데이트
# ---------------------------------------------------------------------------
def _save_price(ticker: str, price: float, change_pct: float):
    if price == 0:
        log.warning(f"[{ticker}] 가격 0 수신 — DB 업데이트 건너뜀")
        return
    try:
        conn = get_db_conn()
        try:
            update_ticker_in_db(conn, ticker, price, change_pct, None)
            log.info(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
        finally:
            conn.close()
    except Exception as e:
        log.error(f"[{ticker}] DB 저장 실패: {e}")


def _notify():
    try:
        conn = get_db_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("NOTIFY price_updated")
        conn.close()
    except Exception as e:
        log.error(f"NOTIFY 실패: {e}")


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
    반환: (kr_tickers, us_rows, yahoo_rows)
      kr_tickers : ['005930', ...]
      us_rows    : [{'ticker': 'TQQQ', 'market': 'NAS'}, ...]
      yahoo_rows : [{'ticker': 'USDKRW=X', 'market': 'FX'}, ...]
    """
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT ticker, market FROM tickers ORDER BY market, ticker")
            rows = [{"ticker": r[0], "market": r[1]} for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        log.error(f"tickers 조회 실패: {e}")
        return [], [], []

    kr_tickers = []
    us_rows    = []
    yahoo_rows = []

    for r in rows:
        market = r["market"]
        status = get_market_status(market)

        if market == "KR":
            if status in ("open", "pre", "after", "closing"):
                kr_tickers.append(r["ticker"])
        elif market in ("NAS", "NYS", "AMS", "ARC"):
            if status in ("open", "pre", "after"):
                us_rows.append(r)
        elif market in ("FX", "INDEX", "CRYPTO"):
            yahoo_rows.append(r)

    return kr_tickers, us_rows, yahoo_rows


# ---------------------------------------------------------------------------
# Yahoo 폴링 태스크 (asyncio)
# ---------------------------------------------------------------------------
async def yahoo_poll_task(yahoo_rows: list):
    """FX/INDEX/CRYPTO 종목을 주기적으로 Yahoo REST로 폴링."""
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
                conn = get_db_conn()
                try:
                    update_ticker_in_db(conn, ticker, price, change_pct, data_time)
                    log.info(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
                finally:
                    conn.close()
            except Exception as e:
                log.error(f"[{ticker}] Yahoo 폴링 실패: {e}")

        _notify()
        await asyncio.sleep(YAHOO_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# KIS 웹소켓 메인 루프 (asyncio)
# ---------------------------------------------------------------------------
async def kis_ws_task(approval_key: str, kr_tickers: list, us_rows: list):
    """
    KIS 웹소켓 연결 → KR/US 종목 구독 → 수신 루프.
    연결 끊기면 WS_RECONNECT_DELAY 후 재연결.
    """
    us_ticker_set = {r["ticker"] for r in us_rows}
    # tr_key → ticker 역매핑 (US)
    tr_key_map = {make_us_tr_key(r["ticker"], r["market"]): r["ticker"] for r in us_rows}

    while True:
        try:
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
                            rt_cd = j.get("header", {}).get("tr_id", "")
                            body  = j.get("body", {})
                            msg1  = body.get("msg1", "")
                            if msg1:
                                log.info(f"WS 응답 [{rt_cd}]: {msg1}")
                        except Exception:
                            pass
                        continue

                    # 실시간 데이터: |로 구분된 문자열
                    # 형식: tr_id|tr_key_cnt|data_cnt|data1^data2^...
                    parts = raw_msg.split("|")
                    if len(parts) < 4:
                        continue

                    tr_id    = parts[0]
                    data_str = parts[3]

                    if tr_id == "H0STCNT0":
                        result = parse_kr(data_str)
                        if result:
                            ticker, price, change_pct = result
                            _save_price(ticker, price, change_pct)

                    elif tr_id == "HDFSCNT0":
                        result = parse_us(data_str)
                        if result:
                            symb, price, change_pct = result
                            # SYMB → DB ticker 변환
                            db_ticker = tr_key_map.get(symb)
                            if db_ticker is None:
                                # prefix 4자리 제거 후 재시도
                                db_ticker = _us_tr_key_to_ticker(symb, us_ticker_set)
                            if db_ticker:
                                _save_price(db_ticker, price, change_pct)
                            else:
                                log.warning(f"[US] 매핑 실패: {symb}")

                    # 일정 주기마다 NOTIFY
                    now = time.time()
                    if now - last_notify >= YAHOO_POLL_INTERVAL:
                        _notify()
                        last_notify = now

        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"KIS 웹소켓 연결 종료: {e} — {WS_RECONNECT_DELAY}초 후 재연결")
        except Exception as e:
            log.error(f"KIS 웹소켓 오류: {e} — {WS_RECONNECT_DELAY}초 후 재연결")

        await asyncio.sleep(WS_RECONNECT_DELAY)


# ---------------------------------------------------------------------------
# 구독 갱신 감시 태스크
# ---------------------------------------------------------------------------
async def subscription_refresh_task(approval_key_holder: list):
    """
    매시간 장 상태를 재확인하고 구독 대상이 바뀌면 ws 태스크를 재시작한다.
    approval_key_holder: [approval_key] — 갱신 시 새 키로 교체 가능하도록 리스트로 전달.
    """
    # 초기 상태 저장
    prev_kr, prev_us, prev_yahoo = get_subscribe_targets()
    prev_kr_set  = set(prev_kr)
    prev_us_set  = {(r["ticker"], r["market"]) for r in prev_us}

    while True:
        await asyncio.sleep(300)  # 5분마다 체크

        holiday_cache.refresh_if_needed()
        kr, us, yahoo = get_subscribe_targets()
        kr_set  = set(kr)
        us_set  = {(r["ticker"], r["market"]) for r in us}

        if kr_set != prev_kr_set or us_set != prev_us_set:
            log.info("구독 대상 변경 감지 — 웹소켓 재연결 필요 (프로세스 재시작으로 처리)")
            # 현재는 프로세스 재시작으로 처리 (systemd Restart=always 활용)
            # 추후 동적 구독/해제 로직으로 개선 가능
            import os, sys
            os.execv(sys.executable, [sys.executable] + sys.argv)

        prev_kr_set = kr_set
        prev_us_set = us_set


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def main():
    load_config()
    log.info("price_updater (웹소켓 모드) 시작")

    holiday_cache.refresh_if_needed()

    # 구독 대상 조회
    kr_tickers, us_rows, yahoo_rows = get_subscribe_targets()
    log.info(f"구독 대상 — KR: {len(kr_tickers)}개, US: {len(us_rows)}개, Yahoo: {len(yahoo_rows)}개")

    if not kr_tickers and not us_rows and not yahoo_rows:
        log.warning("현재 구독 대상 없음. 5분 후 재시작.")
        time.sleep(300)
        import os, sys
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # KIS 웹소켓 접속키 발급 (KR/US 종목이 있을 때만)
    approval_key = None
    if kr_tickers or us_rows:
        approval_key = get_approval_key()

    approval_key_holder = [approval_key]

    # asyncio 이벤트 루프
    async def run():
        tasks = []

        if approval_key and (kr_tickers or us_rows):
            tasks.append(asyncio.create_task(
                kis_ws_task(approval_key, kr_tickers, us_rows)
            ))

        if yahoo_rows:
            tasks.append(asyncio.create_task(
                yahoo_poll_task(yahoo_rows)
            ))

        tasks.append(asyncio.create_task(
            subscription_refresh_task(approval_key_holder)
        ))

        await asyncio.gather(*tasks)

    asyncio.run(run())


if __name__ == "__main__":
    main()
