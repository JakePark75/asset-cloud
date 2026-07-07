# settings 구조 문서

---

## 1. 역할

`app/modules/settings.py` 는 설정 탭의 화면과 서버 로직을 함께 담당한다. 현재 구현에서는 티커 설정, 뉴스 설정, 시장 상태 표시, 로그인 관련 상태를 한 모듈에서 처리한다.

---

## 2. 주요 구성

| 영역 | 내용 |
|------|------|
| 티커 관리 | 계좌/포트폴리오에서 사용할 ticker 메타 정보 관리 |
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

---

## 4. 주요 의존성

| 파일 | 역할 |
|------|------|
| `app/modules/settings.py` | settings 화면/서버 |
| `app/modules/settings_js.py` | settings 화면에서 사용하는 client-side helper |
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
