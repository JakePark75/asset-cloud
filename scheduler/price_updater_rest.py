"""
price_updater_rest.py — REST API 폴링 방식 시세 업데이트
config.json 의 realtime_quote = false 일 때 동작.
기존 price_updater.py 의 동작을 100% 보존한다.
"""
import threading
import time

import requests

import price_updater_common as common
from price_updater_common import (
    log,
    load_config, get_db_conn, get_access_token,
    holiday_cache, get_market_status,
    get_yahoo_price, update_price_cache,
    get_kr_price,
    should_run_kr_final_close, run_kr_final_close_update,
)

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
            log.warning(f"[{ticker}] 가격 0 수신 — 업데이트 건너뜀")
            return

        try:
            update_price_cache(
                ticker, price, change_pct,
                data_time if market_time == "24h" else None
            )
            log.info(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
        except Exception as e:
            log.error(f"[{ticker}] 시세 업데이트 실패: {e}")

    except Exception as e:
        log.error(f"[{ticker}] 업데이트 실패: {e}")


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
        targets = rows
    else:
        targets = [r for r in rows if get_market_status(r["market"]) in ("open", "pre", "after")]

    if not targets:
        log.info("현재 업데이트 대상 종목이 없습니다.")
        return

    log.info(f"업데이트 시작 — {len(targets)}개 / 전체 {len(rows)}개")

    threads = []
    for row in targets:
        t = threading.Thread(target=update_worker, args=(row,), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    log.info("업데이트 완료")

    # Redis: today_row 재계산 (휘발성 실적 데이터 — Step 6 완료 후 각 화면이 Redis에서만 읽음)
    try:
        from common.redis_store import recalc_today_row
        recalc_today_row()
        log.info("today_row 재계산 완료")
    except Exception as e:
        log.error(f"recalc_today_row 실패: {e}")

    from common.redis_store import publish_price_updated
    publish_price_updated()
    log.info("price_updated 신호 발행 (Redis)")


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
        if should_run_kr_final_close():
            run_kr_final_close_update()
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