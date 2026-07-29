# settings 구조 문서

---

## 1. 역할

`app/modules/settings.py` 는 설정 탭의 화면과 서버 로직을 함께 담당한다. 현재 구현에서는 티커 설정, 뉴스 설정, 시장 상태 표시, 로그인 관련 상태를 한 모듈에서 처리한다.

---

## 2. 주요 구성

| 영역 | 내용 |
|------|------|
| 티커 관리 | 계좌/포트폴리오에서 사용할 ticker 메타 정보 관리. 티커 입력 시 🔍 버튼으로 `common/kis_lookup.py`의 `resolve_ticker_info()`를 호출해 종목명/시장/레버리지 자동조회 (accounts.py와 동일 공통함수 사용, 2026-07 추가) |
| 뉴스 키워드 | `news_feed` 관련 키워드 변경/저장/재폴링 트리거 |
| 뉴스 소스 | 뉴스 수집 소스 목록 저장 및 반영 |
| 시장 상태 | KIS/Yahoo 기반의 현재 시장 상태 표시 |
| 로그인 설정 | config 기반 JWT / 로그인 정보 처리 |

---

## 3. 현재 화면 구조

### 설정 탭 내부 구성

- 기본 설정 영역
- ticker 목록 영역
- 뉴스 설정 영역
- 상태 메시지 / alert 영역

### 뉴스 영역

뉴스 관련 UI는 별도 `news.py` 모듈이 아니라 settings 화면 안에서 렌더링된다.

주요 동작:

- 뉴스 키워드를 수정하면 Redis pub/sub 신호를 발행한다.
- 뉴스 소스를 수정하면 즉시 재폴링이 유도된다.
- 현재 피드는 Redis `news:feed` 캐시를 읽는다.

### 티커 자동조회 (2026-07 추가)

"티커 추가" 모달의 티커 입력란 옆 🔍 버튼으로 트리거한다.

```
[🔍 클릭] stLookupTicker() (settings_js.py)
  └─ Shiny.setInputValue('settings-lookup_ticker', {ticker})
       └─ 서버 _lookup_ticker (settings.py) — common.kis_lookup.resolve_ticker_info(ticker) 호출
            └─ session.send_custom_message('st_ticker_lookup_result', {ticker, name, market, leverage?})
                 └─ 결과 핸들러가 name/market/leverage 입력 필드 자동 채움
```

- `resolve_ticker_info()`는 `accounts.py`와 완전히 동일한 공통함수를 사용 — KIS 국내/해외 조회 실패 시 yfinance로 폴백
- `leverage`는 KIS가 구조화된 필드로 명확히 판단 가능하거나, yfinance 폴백에서 longName에 "3X" 등 명확한 배수 표기가 있을 때만 payload에 포함됨 (없으면 필드 자체가 안 옴 → 프론트가 기존 값을 건드리지 않음)
- accounts.py는 `window._acNs`(계좌별 동적 네임스페이스)를 쓰지만, settings는 모듈 인스턴스가 하나뿐이라 `'settings-'` 고정 접두사를 그대로 사용

### 티커 정렬 순서 (`_MARKET_ORDER`, settings.py)

목록 표시 시 market 그룹별 정렬 순서를 정의한다.

| 그룹 | market | 순서 |
|------|--------|------|
| 국내 | KR | 0 |
| 미국 | NAS, NYS, AMS | 1 |
| 가상화폐 | CRYPTO | 2 |
| 원자재 | COM | 3 |
| 해외기타(Yahoo) | ETC | 4 |
| 환율·지수 | FX, INDEX | 5 |

> `ARC`(NYSE Arca)는 KIS 미지원으로 마켓 자체가 폐지되어 이 표에서 제거됨 (2026-07). `ETC`는 이때 신설된 그룹.

---

## 4. 주요 의존성

| 파일 | 역할 |
|------|------|
| `app/modules/settings.py` | settings 화면/서버 |
| `app/modules/settings_js.py` | settings 화면에서 사용하는 client-side helper |
| `common/kis_lookup.py` | `resolve_ticker_info()` — 티커 자동조회 공통함수 (accounts.py와 공유, KIS 국내/해외 조회 + yfinance 폴백) |
| `common/redis_store.py` | `news_feed_updated`, `news_keyword_changed`, `news_source_changed` 발행 및 캐시 조회 |
| `scheduler/news_fetcher.py` | 키워드/소스 변경 후 뉴스 재수집 |
| `scheduler/price_updater_common.py` | 현재 시장 상태 판단 |

---

## 5. 시장 상태 표시

`settings.py` 는 현재 시장 상태를 직접 계산하지 않고 공용 helper 를 통해 읽는다.

주요 상태값은 다음과 같이 해석한다.

| 값 | 의미 |
|----|------|
| `open` | 정규장 거래 가능 |
| `pre` | 장전 |
| `after` | 장후 |
| `closed` | 휴장 또는 장 종료 |

화면에서는 이 상태를 배지/문구로 표시하고, 일부 입력값 활성화 여부를 제어한다.

---

## 6. Redis 연동

### 읽기

- `get_all_prices()` 로 현재가 표시를 갱신한다.
- `get_news_feed_cache()` 로 뉴스 피드를 렌더링한다.

### 쓰기

- 티커 변경 후 `publish_ticker_changed()`
- 뉴스 키워드 변경 후 `publish_news_keyword_changed()`
- 뉴스 소스 변경 후 `publish_news_source_changed()`
- 뉴스 피드 갱신 후 `publish_news_feed_updated()`

---

## 7. 주의사항

- 뉴스 기능은 독립 모듈이 아니라 settings 화면에 묶여 있다.
- 화면 갱신은 DB 재조회보다 Redis 신호와 캐시를 우선한다.
- 시장 상태 문구는 코드의 실제 반환값과 맞춰 유지해야 한다.
- 티커 자동조회 로직(`resolve_ticker_info`)은 `common/kis_lookup.py`에 있으며 `accounts.py`와 공유한다 — 마켓 분류 규칙(KIS 우선, yfinance 폴백, exchange_map)을 바꿀 때는 이 파일 하나만 고치면 양쪽에 반영된다.
- 새 24h 카테고리(FX/INDEX/CRYPTO/COM/ETC와 동일한 성격)를 추가할 때는 `scheduler/config.json`의 `market_map`만 추가하면 되고, `price_updater_*.py` 코드 수정은 필요 없다 (`price_updater_structure.md` 참고). 다만 `settings.py`의 `_MARKET_ORDER`와 `common/kis_lookup.py`의 exchange_map은 화면 표시/자동조회 목적이라 별도로 챙겨야 한다.