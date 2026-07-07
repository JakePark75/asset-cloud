# asset-cloud Redis 전환 작업 컨텍스트

---

## 1. 프로젝트 개요

Oracle Cloud Ubuntu VM에서 운영 중인 개인 자산관리 시스템.

| 항목 | 내용 |
|------|------|
| VM | Oracle Cloud 도쿄 리전 |
| OS | Ubuntu |
| 프레임워크 | Shiny for Python 1.6.2 |
| DB | PostgreSQL `assetdb` |
| 런타임 | Python 3.10.12 |

### 서비스 구조

| 서비스 | 파일 |
|------|------|
| myassets | `app/app.py` |
| price_updater | `scheduler/price_updater.py` |
| daily_inserter | `scheduler/daily_inserter.py` |

---

## 2. Redis 중심 전환 상태

현재 실시간 시세와 오늘치 파생 값은 Redis가 중심이다.

### 핵심 원칙

- 시세는 `common/redis_store.py` 의 `prices` hash 에 저장한다.
- DB `tickers.current_price` 는 신뢰 소스가 아니다.
- 화면은 DB 메타데이터 + Redis 시세를 조합해 렌더링한다.
- `today_row` 는 Redis 기반 파생 스냅샷이다.

### 주요 Redis 키

| Key | 내용 | 주 용도 |
|-----|------|--------|
| `prices` | `ticker -> {price, change_pct}` | 실시간 시세 |
| `usd_krw` | USD/KRW 환율 | 환산 계산 |
| `today_cash_flow` | 오늘 입출금액 | `today_row` 계산 |
| `today_cash_flow_note` | 오늘 입출금 사유 | `today_row` 계산 |
| `today_row` | 오늘치 실적 스냅샷 | history 화면 |
| `news:feed` | 뉴스 피드 캐시 | settings 뉴스 패널 |
| `news:translated:*` | 뉴스 번역 캐시 | 뉴스 재사용 |

---

## 3. 공용 Redis API

### 시세 / 신호

- `write_price()`
- `get_price()`
- `get_all_prices()`
- `publish_price_updated()`
- `publish_position_changed()`
- `publish_ticker_changed()`
- `publish_daily_inserted()`

### 뉴스

- `publish_news_keyword_changed()`
- `publish_news_source_changed()`
- `publish_news_feed_updated()`
- `get_news_feed_cache()`
- `set_news_feed_cache()`

### 캐시 재구성

- `refresh_position_cache()`
- `refresh_daily_summary_cache()`
- `recalc_today_row()`

---

## 4. `recalc_today_row()` 동작

1. Redis `prices` 전체를 읽는다.
2. `USDKRW=X` 또는 Redis `usd_krw` 를 환율로 쓴다.
3. `^NDX` 값을 읽는다.
4. `position_cache` 와 `daily_summary_cache` 를 확인하고 없으면 DB에서 다시 채운다.
5. `today_cash_flow` 와 `today_cash_flow_note` 를 읽는다.
6. `calculate_exposure_and_ratios()` 로 비중과 총자산을 계산한다.
7. `today_row` JSON 을 Redis 에 저장한다.

동시 실행은 lock 으로 막고, 실패해도 예외를 밖으로 올리지 않는다.

---

## 5. 호출 지점

| 호출자 | 트리거 |
|------|------|
| `scheduler/price_updater_rest.py` | 업데이트 사이클 완료 후 `recalc_today_row()` |
| `scheduler/price_updater_ws.py` | `_notify()` 내부 |
| `app/modules/accounts.py` | 계좌/포지션 CRUD 완료 후 |
| `app/modules/history.py` | 오늘 입출금 저장 후 |
| `scheduler/daily_inserter.py` | 정상 마감 UPSERT 경로 |

---

## 6. 화면별 영향

| 화면 | Redis 사용 |
|------|-----------|
| dashboard | 현재가와 환율 읽기 |
| portfolio | 현재가와 환율 읽기 |
| accounts | 평가액 계산 |
| settings | 티커 현재가, 뉴스 피드, 시장 상태 |
| history | 오늘 행은 `today_row`, 과거 행은 DB |

---

## 7. 주의사항

- DB `tickers.current_price` 를 직접 읽는 코드가 있으면 stale 값일 수 있다.
- `daily_snapshot.py` 는 단일 날짜 계산용, `snap.py` 는 범위 백필용이다.
- `news_fetcher.py` 는 폴링과 pub/sub 즉시 재폴링을 함께 쓴다.
