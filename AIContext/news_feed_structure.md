# 뉴스 피드 기능 구조 요약

## 1. 현재 구현 위치

| 파일 | 역할 |
|------|------|
| `app/modules/settings.py` | 뉴스 소스/키워드/피드 UI와 서버 로직을 실제로 호출하는 상위 모듈 |
| `app/modules/news.py` | settings.py가 호출하는 뉴스 관련 UI 조각과 서버 로직 함수 모음 |
| `app/modules/news_js.py` | 뉴스 소스/키워드/피드용 클라이언트 JS |
| `scheduler/news_fetcher.py` | RSS 폴링 + 키워드 매칭 + 번역 + Redis 캐시 저장 |
| `common/redis_store.py` | 뉴스 번역/피드 캐시와 pub/sub 발행 |

> 이 기능은 별도 `@module.ui` / `@module.server` 모듈로 분리되어 있지 않고, `settings.py` 안에서 `news_script_ui()`, `news_ui_section()`, `news_modals_ui()`, `news_server_logic()`를 호출하는 형태로 붙어 있다.

---

## 2. DB 스키마

실제 사용 테이블은 `news_sources` 와 `news_keywords` 두 개이며, 둘 다 `lang` 컬럼을 포함한다.

| 테이블 | 주요 컬럼 |
|------|------|
| `news_sources` | `id`, `name`, `url`, `enabled`, `lang`, `created_at` |
| `news_keywords` | `id`, `keyword`, `lang`, `created_at` |

- `news_sources.lang` 는 소스 언어 구분용이다.
- `news_keywords.lang` 는 키워드 언어 구분용이다.
- 두 테이블 모두 기본값은 `en`이다.

---

## 3. `common/redis_store.py`

뉴스 기능은 Redis를 통해 캐시와 갱신 신호를 주고받는다.

### 캐시 함수

| 함수 | 역할 |
|------|------|
| `get_news_translation_cache(url_hash)` | 번역된 제목 캐시 조회 |
| `set_news_translation_cache(url_hash, translated_title, ttl=3600)` | 번역 제목 캐시 저장 |
| `get_news_feed_cache()` | 뉴스 피드 캐시 조회 |
| `set_news_feed_cache(items, ttl=300)` | 매칭된 뉴스 피드 저장 |

### pub/sub 함수

| 함수 | 채널 |
|------|------|
| `publish_news_keyword_changed()` | `news_keyword_changed` |
| `publish_news_source_changed()` | `news_source_changed` |
| `publish_news_feed_updated()` | `news_feed_updated` |

---

## 4. `scheduler/news_fetcher.py`

`news_fetcher.py`는 `asyncio.gather()`로 두 태스크를 병렬 실행한다.

### 실행 흐름

1. `news_sources` 에서 `enabled=true` 인 소스와 `news_keywords` 전체를 읽는다.
2. RSS를 `feedparser` 로 폴링한다.
3. 제목에 키워드가 단어 경계(`\b`) 기준으로 매칭되는 기사만 남긴다.
4. 소스 언어에 따라 번역 여부를 결정한다.
5. 매칭된 기사만 발행시각 내림차순으로 정렬해 Redis `news:feed` 에 저장한다.

### 언어 규칙

- `source_lang == "en"` 이면 `lang=en` 키워드만 매칭한다.
- `source_lang == "ko"` 이면 `lang=en` + `lang=ko` 키워드를 모두 매칭한다.
- `ko` 소스는 제목/요약을 번역하지 않고 원문을 그대로 사용한다.
- `en` 소스는 제목과 요약을 번역한다.

### 번역 규칙

- 번역은 `deep-translator` 의 `GoogleTranslator(source="en", target="ko")` 를 사용한다.
- 번역 결과는 URL 해시 기준 Redis 캐시에 저장한다.
- 번역 실패 시 기사 자체는 버리지 않고, 제목/요약에 `[번역실패]` 를 붙여 계속 포함한다.

### 날짜 처리

- `published_parsed` / `updated_parsed` 는 UTC 기준으로 해석한다.
- `calendar.timegm()` 으로 epoch 를 만든 뒤 timezone-aware `datetime` 으로 변환한다.

### 갱신 트리거

- `_poll_loop()` 는 300초 주기로 실행된다.
- `_change_listener()` 는 `news_keyword_changed` 와 `news_source_changed` 두 채널을 구독하고, 수신 즉시 재폴링한다.

---

## 5. `app/modules/settings.py` 쪽 UI

뉴스 UI는 설정 화면 안에 들어간다.

### 화면 구성

- 뉴스 소스 목록
- 뉴스 키워드 목록
- 뉴스 피드 목록
- 뉴스 소스 편집 모달
- 뉴스 키워드 편집 모달
- 뉴스 슬라이드업 패널(ko 소스용)

### 주요 서버 함수

`news_server_logic(input, output, session, active_tab)` 가 실제 상태를 관리한다.

### 커스텀 메시지

| 메시지 | 용도 |
|------|------|
| `st_news_sources` | 소스 목록/토글 갱신 |
| `st_news_keywords` | 키워드 칩 목록 갱신 |
| `st_news_feed` | 뉴스 피드 갱신 |
| `st_news_translated` | 키워드 번역 결과를 입력창에 반영 |

### 실제 동작

- 소스 추가/수정/삭제/토글은 `publish_news_source_changed()` 를 호출한다.
- 키워드 추가/삭제/수정은 `publish_news_keyword_changed()` 를 호출한다.
- 피드는 `news_feed_signal` 변화와 settings 탭 활성 상태를 기준으로 갱신한다.
- 피드 표시 주기는 `reactive.invalidate_later(60)` 이다.

---

## 6. `app/modules/news.py`

`news.py`는 독립 Shiny 모듈이 아니라 일반 함수 모음이다.

- `news_script_ui()` → `news_js()` 를 `<script>` 로 주입
- `news_ui_section()` → 설정 화면의 뉴스 섹션 UI 생성
- `news_modals_ui()` → 소스/키워드 편집 모달과 뉴스 패널 생성
- `news_server_logic()` → DB 캐시, 토글, 저장, 삭제, 번역, 피드 전송 처리

---

## 7. 현재 구조에서 중요한 점

- 키워드와 소스는 `lang` 을 함께 저장한다.
- 뉴스 피드는 번역 결과가 있으면 번역 제목을 보여주고, 없으면 원문 제목에 `[번역실패]` 를 붙인다.
- 피드 항목에는 `translated_title`, `summary`, `translated_summary`, `source_lang`, `published_at`, `matched_keywords` 가 포함된다.
- 뉴스 관련 Redis 발행은 모두 `common/redis_store.py` 를 통해 간접 호출한다.