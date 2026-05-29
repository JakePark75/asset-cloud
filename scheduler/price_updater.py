import json
import logging
import os
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

import psycopg2
import requests

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
# 시세 수집 함수 (기존 stock_updater.py 재활용)
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


def get_index_price(ticker):
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
    try:
        if market == "KR":
            price, change_pct = get_kr_price(ticker)
        elif market in ("NAS", "NYS", "AMS", "ARC"):
            price, change_pct = get_us_price(ticker, market)
        elif market == "IDX":
            price, change_pct, data_time = get_index_price(ticker)
        else:
            log.warning(f"[{ticker}] 알 수 없는 market: {market}")
            return

        if price == 0:
            log.warning(f"[{ticker}] 가격 0 수신 — DB 업데이트 건너뜀")
            return

        conn = get_db_conn()
        try:
            update_ticker_in_db(conn, ticker, price, change_pct, data_time if market == "IDX" else None)
            log.info(f"[{ticker}] {price:,.4f} ({change_pct:+.2f}%)")
        finally:
            conn.close()

    except Exception as e:
        log.error(f"[{ticker}] 업데이트 실패: {e}")


# ---------------------------------------------------------------------------
# 전체 종목 조회 후 스레드 실행
# ---------------------------------------------------------------------------
def run_update_cycle():
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

    log.info(f"업데이트 시작 — 총 {len(rows)}개 종목")
    threads = []
    for row in rows:
        t = threading.Thread(target=update_worker, args=(row,), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    log.info("업데이트 완료")

def run_update_cycle():
    # ... 기존 코드 ...
    for t in threads:
        t.join()

    log.info("업데이트 완료")

    # NOTIFY 추가
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
        start = time.time()
        run_update_cycle()
        elapsed = time.time() - start
        sleep_sec = max(0, interval_sec - elapsed)
        log.info(f"다음 업데이트까지 {sleep_sec:.1f}초 대기")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()