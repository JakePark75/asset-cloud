"""
common/redis_store.py
공용 Redis 저장소 모듈.

- get_redis()          : Redis 연결 (실패 시 None)
- write_price()        : prices hash + usd_krw key 갱신
- get_price()          : 단일 종목 시세 조회
- get_all_prices()     : 전체 종목 시세 조회
- recalc_today_row()   : 오늘치 실적 row 계산 + Redis 저장 (Lock 보호)
- publish_price_updated()   : price_updated 채널에 신호 발행 (NOTIFY 대체)
- publish_daily_inserted()  : daily_inserted 채널에 신호 발행 (NOTIFY 대체)
- get_news_translation_cache() / set_news_translation_cache() : 뉴스 제목 번역 캐시 (TTL 1시간)
- get_news_feed_cache() / set_news_feed_cache()               : 매칭된 뉴스 피드 캐시 (TTL 5분)
"""

import json
import datetime
import threading
import sys
import os
from zoneinfo import ZoneInfo
KST = ZoneInfo("Asia/Seoul")

import redis

# ── PROJECT_ROOT를 sys.path에 추가 (scheduler에서 import 시 app 패키지 접근용) ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ── Redis 연결 ────────────────────────────────────────────────────────────────

_client: redis.Redis | None = None


def get_redis() -> redis.Redis | None:
    global _client
    if _client is not None:
        try:
            _client.ping()
            return _client
        except Exception:
            _client = None

    try:
        _client = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
        _client.ping()
        return _client
    except Exception as e:
        print(f"[redis_store] Redis 연결 실패: {e}")
        return None


# ── 시세 Read / Write ─────────────────────────────────────────────────────────

def write_price(ticker: str, price: float, change_pct: float) -> None:
    """
    prices hash에 ticker → json{price, change_pct} 기록.
    USDKRW=X인 경우 usd_krw key도 함께 갱신.
    실패해도 예외를 밖으로 내보내지 않는다.
    """
    try:
        r = get_redis()
        if not r:
            return
        payload = json.dumps({"price": price, "change_pct": change_pct})
        r.hset("prices", ticker, payload)
        if ticker == "USDKRW=X":
            r.set("usd_krw", price)
    except Exception as e:
        print(f"[redis_store] write_price 실패 ({ticker}): {e}")


# ── 갱신 신호 Pub/Sub (NOTIFY 대체) ────────────────────────────────────────────

def publish_price_updated() -> None:
    """
    price_updated 채널에 신호 발행.
    기존 PostgreSQL NOTIFY price_updated 대체.
    payload는 의미 없는 고정값("1") — 화면 쪽은 채널 수신 자체만 트리거로 사용.
    실패해도 예외를 밖으로 내보내지 않는다.
    """
    try:
        r = get_redis()
        if not r:
            return
        r.publish("price_updated", "1")
    except Exception as e:
        print(f"[redis_store] publish_price_updated 실패: {e}")


def publish_position_changed() -> None:
    """
    position_changed 채널에 신호 발행.
    accounts.py에서 포지션(종목/현금) CRUD 완료 후 호출.
    payload는 의미 없는 고정값("1") — 화면 쪽은 채널 수신 자체만 트리거로 사용.
    실패해도 예외를 밖으로 내보내지 않는다.
    """
    try:
        r = get_redis()
        if not r:
            return
        r.publish("position_changed", "1")
    except Exception as e:
        print(f"[redis_store] publish_position_changed 실패: {e}")


def publish_ticker_changed() -> None:
    """
    ticker_changed 채널에 신호 발행.
    settings.py에서 티커 CRUD 완료 후 호출.
    payload는 의미 없는 고정값("1") — 화면 쪽은 채널 수신 자체만 트리거로 사용.
    실패해도 예외를 밖으로 내보내지 않는다.
    """
    try:
        r = get_redis()
        if not r:
            return
        r.publish("ticker_changed", "1")
    except Exception as e:
        print(f"[redis_store] publish_ticker_changed 실패: {e}")


def publish_daily_inserted() -> None:
    """
    daily_inserted 채널에 신호 발행.
    기존 PostgreSQL NOTIFY daily_inserted 대체.
    payload는 의미 없는 고정값("1") — 화면 쪽은 채널 수신 자체만 트리거로 사용.
    실패해도 예외를 밖으로 내보내지 않는다.
    """
    try:
        r = get_redis()
        if not r:
            return
        r.publish("daily_inserted", "1")
    except Exception as e:
        print(f"[redis_store] publish_daily_inserted 실패: {e}")


def publish_news_keyword_changed() -> None:
    """
    news_keyword_changed 채널에 신호 발행.
    settings.py에서 키워드 추가/삭제 완료 후 호출.
    news_fetcher.py가 구독 중이며, 수신 즉시 재폴링을 실행한다.
    실패해도 예외를 밖으로 내보내지 않는다.
    """
    try:
        r = get_redis()
        if not r:
            return
        r.publish("news_keyword_changed", "1")
    except Exception as e:
        print(f"[redis_store] publish_news_keyword_changed 실패: {e}")


def publish_news_feed_updated() -> None:
    """
    news_feed_updated 채널에 신호 발행.
    news_fetcher.py가 Redis news:feed 캐시를 새로 쓴 직후 호출.
    payload는 의미 없는 고정값("1") — 화면 쪽은 채널 수신 자체만 트리거로 사용.
    실패해도 예외를 밖으로 내보내지 않는다.
    """
    try:
        r = get_redis()
        if not r:
            return
        r.publish("news_feed_updated", "1")
    except Exception as e:
        print(f"[redis_store] publish_news_feed_updated 실패: {e}")


def get_price(ticker: str) -> dict | None:
    """
    prices hash에서 단일 종목 조회.
    반환: {"price": float, "change_pct": float} 또는 None
    """
    try:
        r = get_redis()
        if not r:
            return None
        raw = r.hget("prices", ticker)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        print(f"[redis_store] get_price 실패 ({ticker}): {e}")
        return None


def get_all_prices() -> dict:
    """
    prices hash 전체 조회.
    반환: {ticker: {"price": float, "change_pct": float}, ...}
    Redis 장애 또는 데이터 없으면 빈 dict.
    """
    try:
        r = get_redis()
        if not r:
            return {}
        raw_map = r.hgetall("prices")
        result = {}
        for ticker, raw in raw_map.items():
            try:
                result[ticker] = json.loads(raw)
            except Exception:
                pass
        return result
    except Exception as e:
        print(f"[redis_store] get_all_prices 실패: {e}")
        return {}


# ── today_row 재계산 ──────────────────────────────────────────────────────────

_recalc_lock = threading.Lock()


def recalc_today_row() -> None:
    """
    현재 positions + Redis 시세로 오늘치 실적 row를 계산해 Redis today_row에 저장.
    이미 계산 중이면 스킵 (blocking=False).
    실패해도 예외를 밖으로 내보내지 않는다.
    """
    _t0 = datetime.datetime.now(KST)

    if not _recalc_lock.acquire(blocking=False):
        print(f"[DEBUG-RECALC] {_t0} SKIP (lock busy)")
        return  # 이미 계산 중

    try:
        # lazy import: app 패키지는 scheduler에서도 호출될 수 있으므로 내부에서 import
        from app.db import get_db
        from app.utils.metrics import calculate_exposure_and_ratios, to_f

        r = get_redis()

        # 1. Redis 시세 전체 조회
        prices = get_all_prices()

        # 2. usd_krw
        usd_krw = 1350.0
        try:
            if r:
                raw_usd = r.get("usd_krw")
                if raw_usd:
                    usd_krw = float(raw_usd)
        except Exception:
            pass
        # prices에 USDKRW=X가 있으면 우선 사용
        if "USDKRW=X" in prices:
            usd_krw = float(prices["USDKRW=X"]["price"])

        # 3. ndx100
        ndx100 = 0.0
        if "^NDX" in prices:
            ndx100 = float(prices["^NDX"]["price"])

        with get_db() as conn:
            cur = conn.cursor()

            # 4. positions (is_watch=false 계좌만)
            cur.execute("""
                SELECT p.ticker, p.quantity, t.leverage, t.market
                FROM positions p
                LEFT JOIN tickers t ON p.ticker = t.ticker
                LEFT JOIN accounts a ON p.account_id = a.id
                WHERE (a.is_watch = false OR a.is_watch IS NULL)
            """)
            position_rows = cur.fetchall()

            # 5. 전일 (total_asset, twr_asset)
            cur.execute("""
                SELECT total_asset, twr_asset FROM daily_summary
                ORDER BY date DESC LIMIT 1
            """)
            prev = cur.fetchone()
            cur.close()

        # 6. 시세 매핑 (Redis prices 우선, 없으면 0)
        db_rows = []
        for ticker, qty, leverage, market in position_rows:
            if ticker == "KRW":
                price = 1.0
            elif ticker == "USD":
                price = usd_krw
            else:
                p_data = prices.get(ticker)
                price = float(p_data["price"]) if p_data else 0.0
            db_rows.append((ticker, qty, price, leverage, market))

        # 7. 비중 계산
        ratios = calculate_exposure_and_ratios(db_rows, usd_krw)
        total_asset = ratios["total_asset"]

        # 8. 오늘 입출금
        cash_flow = 0
        cash_flow_note = None
        try:
            if r:
                cash_flow = int(r.get("today_cash_flow") or 0)
                cash_flow_note = r.get("today_cash_flow_note")
        except Exception:
            pass

        # 9. twr_asset 계산
        if prev is None:
            twr_asset = total_asset
        else:
            prev_total = to_f(prev[0])
            prev_twr   = to_f(prev[1])
            denom = prev_total + cash_flow
            twr_asset = prev_twr * ((total_asset - cash_flow) / denom) if denom else prev_twr

        # 10. Redis 저장
        
        today_row = {
            "date":           str(datetime.datetime.now(KST).date()),
            "total_asset":    total_asset,
            "twr_asset":      twr_asset,
            "ndx100":         ndx100,
            "cash_flow":      cash_flow,
            "cash_flow_note": cash_flow_note,
            "exposure":       ratios["exposure"],
            "cash_ratio":     ratios["cash_ratio"],
            "x1_ratio":       ratios["x1_ratio"],
            "x2_ratio":       ratios["x2_ratio"],
            "x3_ratio":       ratios["x3_ratio"],
            "usd_krw":        usd_krw,
        }

        if r:
            r.set("today_row", json.dumps(today_row))

        print(f"[DEBUG-RECALC] {_t0} DONE total_asset={total_asset} "
              f"elapsed={(datetime.datetime.now(KST) - _t0).total_seconds():.3f}s")

    except Exception as e:
        print(f"[DEBUG-RECALC] {_t0} FAILED: {e}")
        print(f"[redis_store] recalc_today_row 실패 (무시): {e}")
    finally:
        _recalc_lock.release()


# ── 뉴스 피드 캐시 ─────────────────────────────────────────────────────────────
#
# 키 구조:
#   news:translated:{url_hash}  → 번역된 제목 문자열 (TTL 1시간)
#   news:feed                   → 최신 매칭 기사 리스트 JSON (TTL 5분)
#
# url_hash는 호출하는 쪽(news_fetcher.py)에서 hashlib.md5(url).hexdigest()로 생성해
# 인자로 넘긴다. 이 모듈은 해시 생성 책임을 가지지 않고 Redis 접근만 담당한다.

def get_news_translation_cache(url_hash: str) -> str | None:
    """
    캐시된 번역 제목 조회. 없거나 실패 시 None.
    """
    try:
        r = get_redis()
        if not r:
            return None
        return r.get(f"news:translated:{url_hash}")
    except Exception as e:
        print(f"[redis_store] get_news_translation_cache 실패 ({url_hash}): {e}")
        return None


def set_news_translation_cache(url_hash: str, translated_title: str, ttl: int = 3600) -> None:
    """
    번역 제목 캐시 저장 (기본 TTL 1시간).
    실패해도 예외를 밖으로 내보내지 않는다.
    """
    try:
        r = get_redis()
        if not r:
            return
        r.set(f"news:translated:{url_hash}", translated_title, ex=ttl)
    except Exception as e:
        print(f"[redis_store] set_news_translation_cache 실패 ({url_hash}): {e}")


def get_news_feed_cache() -> list | None:
    """
    캐시된 뉴스 피드 리스트 조회.
    반환: [{...}, ...] 또는 None (캐시 없음/실패).
    """
    try:
        r = get_redis()
        if not r:
            return None
        raw = r.get("news:feed")
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        print(f"[redis_store] get_news_feed_cache 실패: {e}")
        return None


def set_news_feed_cache(items: list, ttl: int = 300) -> None:
    """
    매칭된 뉴스 기사 리스트를 JSON으로 캐시 저장 (기본 TTL 5분).
    각 item은 JSON 직렬화 가능한 dict여야 한다 (예: title, translated_title,
    link, source, published_at(ISO 문자열) 등).
    실패해도 예외를 밖으로 내보내지 않는다.
    """
    try:
        r = get_redis()
        if not r:
            return
        r.set("news:feed", json.dumps(items, ensure_ascii=False), ex=ttl)
    except Exception as e:
        print(f"[redis_store] set_news_feed_cache 실패: {e}")