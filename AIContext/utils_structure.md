# utils 구조 문서

---

## 1. 파일 구성

| 파일 | 역할 |
|------|------|
| `app/utils/metrics.py` | 자산 지표, 수익률, 비중 계산 helper |
| `app/utils/daily_snapshot.py` | 단일 날짜 `daily_summary` 스냅샷 계산 |
| `app/utils/snap.py` | 날짜 범위 백필용 스냅샷 계산 |
| `app/utils/display_diff.py` | 표시용 diff/변동 계산 helper |

---

## 2. metrics.py

`metrics.py` 는 화면과 스냅샷 계산에서 공통으로 쓰는 비율 계산을 담당한다.

### 주요 기능

- 총자산 대비 비중 계산
- 현금 / x1 / x2 / x3 비중 계산
- exposure 계산
- TWR 계산 보조값 계산

### 사용처

- `app/modules/dashboard.py`
- `app/modules/portfolio.py`
- `app/utils/daily_snapshot.py`
- `app/utils/snap.py`
- `common/redis_store.py` 의 `recalc_today_row()`

---

## 3. daily_snapshot.py

이 모듈은 하나의 날짜에 대해 현재 포지션과 가장 최근 종가를 기준으로 스냅샷을 만든다.

### 핵심 함수

| 함수 | 역할 |
|------|------|
| `get_daily_snapshot()` | 단일 날짜 스냅샷 생성 |
| `calculate_exposure_and_ratios()` | 총자산/비중 계산 |
| `get_last_price()` | 종목의 가장 최근 가격 조회 |

### 특징

- DB insert 를 하지 않는다.
- API 조회 결과를 이용해 `daily_summary` 1행에 들어갈 값을 만든다.
- 해당 날짜 데이터가 없으면 가장 최근 거래일 값을 사용한다.

---

## 4. snap.py

`snap.py` 는 백필과 범위 계산용이다.

### 핵심 함수

| 함수 | 역할 |
|------|------|
| `set_batch_range()` | 범위 캐시 초기화 |
| `fetch_positions()` | 포지션 조회 |
| `fetch_cash_flows()` | 입출금 내역 조회 |
| `fetch_snapshot()` | 날짜별 스냅샷 생성 |
| `date_range()` | 범위 내 날짜 생성 |

### 특징

- 날짜 범위 단위 처리에 최적화되어 있다.
- `daily_inserter.py` 의 백필 경로가 주 소비자다.

---

## 5. display_diff.py

표시용 변화량을 계산할 때 쓰는 작은 helper 모음이다.

### 역할 예시

- 전일 대비 변화량 포맷팅
- 수익률 문구 생성
- UI 표시용 보조 계산

---

## 6. 주의사항

- `daily_snapshot.py` 와 `snap.py` 는 용도가 다르다. 전자는 단일 날짜, 후자는 범위 백필이다.
- 이 디렉터리는 UI 자체가 아니라 계산 helper 중심이다.