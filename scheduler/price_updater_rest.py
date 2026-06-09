"""
price_updater_rest.py — REST API 폴링 방식 시세 업데이트
config.json 의 realtime_quote = false 일 때 동작.
기존 price_updater.py 의 동작을 100% 보존한다.
"""
import threading
import time
from datetime import datetime

import requests
import pytz

import price_updater_common as common
from price_updater_common import (
    log,
    load_config, get_db_conn, get_access_token,
    holiday_cache, get_market_status,
    get_yahoo_price, update_ticker_in_db,
    _is_close_confirmed, _set_close_confirmed,
)

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ---------------------------------------------------------------------------
# KR 현재가
# ---------------------------------------------------------------------------
def get_kr_price(ticker):
    token = get_access_token()
    url   = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey":        common.config["kis_app_key"],
        "appsecret":     common.config["kis_app_secret"],
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
# 미국주식 현재가
# ---------------------------------------------------------------------------
def get_us_price(ticker, excd):
    token = get_access_token()
    url   = "https://openapi.koreainvestment.com:9443/uapi/overseas-price/v1/quotations/price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey":        common.config["kis_app_key"],
        "appsecret":     common.config["kis_app_secret"],
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


# ---------------------------------------------------------------------------
# KR 종가 확정 조회
# ---------------------------------------------------------------------------
def get_confirmed_close_kr(ticker: str):
    """
    output2에 오늘 날짜 데이터가 있으면 (종가, change_pct) 반환.
    미확정이면 None 반환.
    """
    token    = get_access_token()
    today    = datetime.now(pytz.timezone("Asia/Seoul")).strftime("%Y%m%d")
    url      = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-daily-price"
    headers  = {
        "authorization": f"Bearer {token}",
        "appkey":        common.config["kis_app_key"],
        "appsecret":     common.config["kis_app_secret"],
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
        body  = res.json()
        rows  = body.get("output2", [])
        if not rows:
            return None
        row = rows[0]
        if row.get("stck_bsop_date") != today:
            return None
        price     = float(row.get("stck_clpr", 0))
        prdy_clpr = float(body.get("output1", {}).get("stck_prdy_clpr", 0) or 0)
        change_pct = round((price - prdy_clpr) / prdy_clpr * 100, 2) if prdy_clpr else 0.0
        return price, change_pct
    except Exception as e:
        log.error(f"[{ticker}] KR 종가 확정 조회 실패: {e}")
        return None


# ---------------------------------------------------------------------------
# 종목별 업데이트 워커
# ---------------------------------------------------------------------------
def update_worker(row):
    ticker    = row["ticker"]
    market    = row["market"]
    data_time = None
    try:
        market_info = common.config.get("market_map", {}).get(market, {})
        market_time = market_info.get("market_time", "24h")

        if market_time == "KR":
            price, change_pct = get_kr_price(ticker)
        elif market_time == "US":
            price, change_pct = get_us_price(ticker, market)
        elif market_time == "24h":
            price, change_pct, data_time = get_yahoo_price(ticker)
        else:
            log.warning(f"[{ticker}] 알 수 없는 market_time: {market_time}")
            return

        if price == 0:
            log.warning(f"[{ticker}] 가격 0 수신 — DB 업데이트 건너뜀")
            return

        conn = get_db_conn()
        try:
            update_ticker_in_db(
                conn, ticker, price, change_pct,
                data_time if market_time == "24h" else None
            )
            log.info(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
        finally:
            conn.close()

    except Exception as e:
        log.error(f"[{ticker}] 업데이트 실패: {e}")


# ---------------------------------------------------------------------------
# 종가 확정 워커
# ---------------------------------------------------------------------------
def close_confirm_worker(row: dict):
    ticker = row["ticker"]
    market = row["market"]

    try:
        if market != "KR":
            return

        result = get_confirmed_close_kr(ticker)
        if result is None:
            return

        price, change_pct = result
        if price == 0:
            return

        conn = get_db_conn()
        try:
            update_ticker_in_db(conn, ticker, price, change_pct, None)
            log.info(f"[{ticker}] 종가 확정 업데이트: {price:,.4f} ({change_pct:+.2f}%)")
        finally:
            conn.close()

        _set_close_confirmed("KR")

    except Exception as e:
        log.error(f"[{ticker}] 종가 확정 워커 실패: {e}")


# ---------------------------------------------------------------------------
# 전체 종목 업데이트 사이클
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
            status = get_market_status(market)

            if status in ("open", "pre", "after"):
                targets.append(r)
            elif status == "closing":
                if _is_close_confirmed("KR"):
                    pass
                else:
                    close_targets.append(r)
            # closed → 스킵

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
    log.info("price_updater (REST 모드) 시작")

    while True:
        load_config()
        interval_sec = common.config["interval"] * 60

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