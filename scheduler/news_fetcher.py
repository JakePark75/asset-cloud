"""
news_fetcher.py
5분 주기로 활성화된 RSS 소스를 폴링하여, 등록된 키워드가 제목에
단어 단위로 매칭되는 기사만 번역 후 Redis에 캐시한다.

동작 방식:
    1. DB에서 enabled=true 인 news_sources(+lang), 전체 news_keywords(+lang) 로드
    2. 각 소스를 feedparser로 폴링, 소스 lang에 따라 적용 키워드 결정:
       - 소스 lang=en → en 키워드만 매칭
       - 소스 lang=ko → en + ko 키워드 모두 매칭
    3. 매칭된 기사만 제목 번역 (URL 해시 기준 Redis 캐시, TTL 1시간)
       - ko 소스 기사는 번역 없이 원문 그대로 표시 (이미 한국어)
       - en 소스 번역 실패 시 원문 제목 + "[번역실패]" 표시, 스킵하지 않고 진행
    4. 동일 제목 중복 제거 (제목 정규화 후 첫 번째 기사만 유지)
    5. 매칭 기사 리스트에 matched_keywords 필드 포함, 발행시각 내림차순 정렬 후
       Redis news:feed 에 저장 (TTL 5분)
    6. asyncio 기반 두 태스크 병렬 실행:
       - _poll_loop: 5분 주기 정기 폴링
       - _keyword_listener: news_keyword_changed 채널 구독, 수신 시 즉시 재폴링

날짜 처리:
    feedparser의 published_parsed/updated_parsed는 UTC 기준 struct_time으로
    정규화되어 있다. time.mktime()을 쓰면 로컬 시간대로 잘못 해석되므로
    calendar.timegm()으로 UTC epoch를 구한 뒤 timezone-aware datetime으로 변환한다.
    (기존 UTC/KST 버그 패턴 재발 방지 원칙에 따름)
"""

import asyncio
import calendar
import datetime
import hashlib
import re
import sys
import os

from zoneinfo import ZoneInfo
KST = ZoneInfo("Asia/Seoul")
UTC = datetime.timezone.utc

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import feedparser
import redis.asyncio as aioredis
from deep_translator import GoogleTranslator
from deep_translator.exceptions import (
    TooManyRequests,
    RequestError,
    TranslationNotFound,
    NotValidLength,
    NotValidPayload,
)

from app.db import get_db

POLL_INTERVAL_SECONDS = 300  # 5분


# ---------------------------------------------------------------------------
# DB 로드
# ---------------------------------------------------------------------------
def _fetch_enabled_sources() -> list[tuple[str, str, str]]:
    """enabled=true 인 (name, url, lang) 목록 반환."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name, url, lang FROM news_sources WHERE enabled = TRUE")
            return cur.fetchall()


def _fetch_keywords() -> list[tuple[str, str]]:
    """등록된 (keyword, lang) 목록 반환."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT keyword, lang FROM news_keywords")
            return cur.fetchall()


# ---------------------------------------------------------------------------
# 키워드 패턴 빌드 (단어 단위, 대소문자 무시)
# ---------------------------------------------------------------------------
def _build_pattern(keywords: list[str]) -> re.Pattern | None:
    """주어진 키워드 목록으로 단어 단위 매칭 패턴 생성."""
    if not keywords:
        return None
    escaped = [re.escape(kw) for kw in keywords if kw.strip()]
    if not escaped:
        return None
    pattern = r"\b(?:" + "|".join(escaped) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


def _find_matched_keywords(title: str, keywords: list[str]) -> list[str]:
    """title에서 매칭된 키워드 목록 반환 (중복 제거, 순서 유지)."""
    matched = []
    seen = set()
    for kw in keywords:
        if not kw.strip():
            continue
        pat = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        if pat.search(title) and kw not in seen:
            matched.append(kw)
            seen.add(kw)
    return matched


# ---------------------------------------------------------------------------
# 발행시각 파싱 (UTC, timezone-aware)
# ---------------------------------------------------------------------------
def _parse_published_utc(entry) -> datetime.datetime:
    struct_time = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if struct_time is None:
        return datetime.datetime.now(UTC)
    epoch = calendar.timegm(struct_time)
    return datetime.datetime.fromtimestamp(epoch, tz=UTC)


# ---------------------------------------------------------------------------
# 번역 (Redis 캐시 경유) — en 소스 전용
# ---------------------------------------------------------------------------
def _translate_title(title: str, cache_key: str) -> str:
    from common.redis_store import get_news_translation_cache, set_news_translation_cache

    url_hash = hashlib.md5(cache_key.encode("utf-8")).hexdigest()

    cached = get_news_translation_cache(url_hash)
    if cached:
        return cached

    try:
        translated = GoogleTranslator(source="en", target="ko").translate(title)
        if not translated:
            raise TranslationNotFound(title)
        set_news_translation_cache(url_hash, translated)
        return translated
    except (TooManyRequests, RequestError, TranslationNotFound,
            NotValidLength, NotValidPayload) as e:
        print(f"[news_fetcher] 번역 실패 ({e.__class__.__name__}): {title[:50]} | {e}")
        return f"{title} [번역실패]"
    except Exception as e:
        print(f"[news_fetcher] 번역 실패 (예상치 못한 오류): {title[:50]} | {e}")
        return f"{title} [번역실패]"


# ---------------------------------------------------------------------------
# 제목 정규화 (중복 제거용)
# ---------------------------------------------------------------------------
def _normalize_title(title: str) -> str:
    """대소문자·공백·특수문자 정규화 → 중복 판별 키."""
    return re.sub(r"[\s\W]+", "", title).lower()


# ---------------------------------------------------------------------------
# 폴링 + 매칭 + 캐시 저장 (동기, executor에서 실행)
# ---------------------------------------------------------------------------
def _fetch_and_cache() -> None:
    from common.redis_store import set_news_feed_cache, publish_news_feed_updated

    now_kst = datetime.datetime.now(KST)
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] 📰 뉴스 폴링 시작", flush=True)

    try:
        sources = _fetch_enabled_sources()
        keyword_rows = _fetch_keywords()
    except Exception as e:
        print(f"[news_fetcher] DB 조회 실패: {e}", flush=True)
        return

    # 언어별 키워드 분리
    en_keywords = [kw for kw, lang in keyword_rows if lang == "en"]
    ko_keywords = [kw for kw, lang in keyword_rows if lang == "ko"]
    all_keywords = en_keywords + ko_keywords  # ko 소스용

    if not keyword_rows:
        print("[news_fetcher] 등록된 키워드 없음 → 매칭 기사 없음, 캐시 비움", flush=True)
        set_news_feed_cache([])
        publish_news_feed_updated()
        return

    # 소스 lang별 패턴 빌드
    en_pattern = _build_pattern(en_keywords)
    ko_pattern = _build_pattern(all_keywords)  # ko 소스는 en+ko 모두

    matched_items = []
    seen_titles: set[str] = set()  # 중복 제목 추적

    for source_name, source_url, source_lang in sources:
        # 이 소스에 적용할 패턴과 키워드 목록 결정
        if source_lang == "ko":
            pattern = ko_pattern
            applicable_keywords = all_keywords
        else:
            pattern = en_pattern
            applicable_keywords = en_keywords

        if pattern is None:
            continue

        try:
            feed = feedparser.parse(source_url)
        except Exception as e:
            print(f"[news_fetcher] 소스 폴링 실패 ({source_name}): {e}", flush=True)
            continue

        if getattr(feed, "bozo", False):
            print(f"[news_fetcher] 소스 파싱 경고 ({source_name}): {feed.get('bozo_exception')}", flush=True)

        for entry in feed.entries:
            title = getattr(entry, "title", None)
            link = getattr(entry, "link", None)
            if not title or not link:
                continue

            # 키워드 매칭
            matched_kws = _find_matched_keywords(title, applicable_keywords)
            if not matched_kws:
                continue

            # 중복 제목 제거
            title_key = _normalize_title(title)
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)

            published_utc = _parse_published_utc(entry)

            # ko 소스는 번역 불필요 (이미 한국어)
            if source_lang == "ko":
                translated_title = title
            else:
                translated_title = _translate_title(title, link)

            summary = getattr(entry, "summary", None) or ""
            if summary:
                if source_lang == "ko":
                    translated_summary = summary
                else:
                    translated_summary = _translate_title(summary, link + ":summary")
            else:
                translated_summary = ""

            matched_items.append({
                "title": title,
                "translated_title": translated_title,
                "summary": summary,
                "translated_summary": translated_summary,
                "link": link,
                "source": source_name,
                "source_lang": source_lang,
                "published_at": published_utc.isoformat(),
                "matched_keywords": matched_kws,
            })

    matched_items.sort(key=lambda x: x["published_at"], reverse=True)

    set_news_feed_cache(matched_items)
    publish_news_feed_updated()

    now_kst = datetime.datetime.now(KST)
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"✅ 뉴스 폴링 완료 | 매칭 기사: {len(matched_items)}건", flush=True)


# ---------------------------------------------------------------------------
# asyncio 태스크 1: 5분 주기 정기 폴링
# ---------------------------------------------------------------------------
async def _poll_loop() -> None:
    loop = asyncio.get_running_loop()
    while True:
        await loop.run_in_executor(None, _fetch_and_cache)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# asyncio 태스크 2: news_keyword_changed / news_source_changed 구독 → 즉시 재폴링
# ---------------------------------------------------------------------------
async def _change_listener() -> None:
    """키워드·소스 변경 채널을 함께 구독, 수신 시 즉시 재폴링."""
    loop = asyncio.get_running_loop()
    r = aioredis.Redis(host="127.0.0.1", port=6379, db=0)

    _channel_labels = {
        "news_keyword_changed": "키워드",
        "news_source_changed":  "소스",
    }

    async with r.pubsub() as pubsub:
        await pubsub.subscribe("news_keyword_changed", "news_source_changed")
        now_kst = datetime.datetime.now(KST)
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"📡 news_keyword_changed / news_source_changed 채널 구독 시작", flush=True)

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            channel = message.get("channel", "")
            label = _channel_labels.get(channel, channel)
            now_kst = datetime.datetime.now(KST)
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"🔄 {label} 변경 감지 → 즉시 재폴링", flush=True)
            await loop.run_in_executor(None, _fetch_and_cache)


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
async def _main() -> None:
    now_kst = datetime.datetime.now(KST)
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] 📅 news_fetcher 시작 "
          f"(폴링 주기: {POLL_INTERVAL_SECONDS}초)", flush=True)

    await asyncio.gather(
        _poll_loop(),
        _change_listener(),
    )


if __name__ == "__main__":
    asyncio.run(_main())