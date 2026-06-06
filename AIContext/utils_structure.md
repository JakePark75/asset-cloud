# app/utils — 유틸리티 모듈 구조

---

## 파일 구성

| 파일 | 역할 |
|------|------|
| `app/utils/metrics.py` | 순수 계산 함수 (IRR, 알파, 베타, Exposure 등) — DB/API 호출 없음 |
| `app/utils/daily_snapshot.py` | 특정일 기준 가장 최근 종가 조회 + daily_summary 1행분 계산 → dict 반환 |
| `app/utils/snap.py` | 날짜 범위 지정 스냅샷 계산 (캐시 최적화, 수동 보정용) |

---

## metrics.py

### 특징
- 순수 계산 함수만 포함. DB/API 호출 없음.
- `dashboard.py`, `daily_snapshot.py`, `daily_inserter.py` 등 여러 곳에서 공용 사용.
- psycopg2 NUMERIC → float 변환용 `to_f()` 포함.

### 함수 목록

#### `to_f(val) → float`
- None 또는 Decimal 등 → float 변환
- psycopg2로 조회한 NUMERIC 컬럼에 항상 사용

#### `calculate_exposure_and_ratios(db_rows, usd_krw) → dict`
- 포지션 목록으로 총자산 및 비중 지표 계산
- `db_rows` 형식: `[(ticker, quantity, current_price, leverage, market), ...]`
- 미국주식(NAS/AMS/ARC): `qty × price × usd_krw` 원화 환산
- KRW/USD/FX/INDEX는 현금으로 분류 (Exposure 제외)
- 반환 dict:

| 키 | 설명 |
|----|------|
| `total_asset` | 총자산 (원화) |
| `exposure` | 레버리지 가중 익스포저 비중 (0~1) |
| `cash_ratio` | 현금 비중 (0~1) |
| `x1_ratio` | x1 종목 비중 (0~1) |
| `x2_ratio` | x2 종목 비중 (0~1) |
| `x3_ratio` | x3 종목 비중 (0~1) |

#### `calculate_xirr(cash_flows) → float`
- XIRR 기반 연환산 IRR 계산
- `cash_flows`: `[(date, amount), ...]` — 입출금 음수, 현재 자산 양수 마지막 항목
- scipy.optimize.newton 사용, 수렴 실패 시 0.0 반환

#### `calculate_monthly_irr(cash_flows) → float`
- `calculate_xirr()` 결과를 월환산으로 변환
- 반환값: 월 수익률 (예: 0.02 = 2%)

#### `calculate_alpha(start_row, end_row) → float`
- 포트폴리오 알파 계산 (vs NDX100)
- `start_row`, `end_row`: `(total_asset, ndx100)` 튜플
- 반환: 내 수익률 - NDX100 수익률

#### `calculate_beta(rows) → float`
- 포트폴리오 베타 계산 (vs NDX100)
- `rows`: `[(total_asset, ndx100), ...]` 날짜 오름차순
- 일별 수익률 기반 공분산 / NDX100 분산
- 최소 3개 행 필요, 부족 시 0.0 반환

#### `calculate_daily_profit(today_asset, today_cash_flow, yesterday_asset) → float`
- 금일 순수 운용 수익 (입출금 제외)
- `= (오늘 총자산 - 오늘 입출금) - 어제 총자산`

#### `calculate_retirement_asset(total_asset, monthly_irr, retirement_date) → float`
- 은퇴 시점 예상 자산액
- 현재 총자산에 월평균 IRR 복리 적용
- `retirement_date`: `datetime.date`

---

## daily_snapshot.py

### 역할
특정일 기준 "가장 최근 종가"를 KIS/Yahoo API로 조회하여 `daily_summary` 1행분 데이터를 계산해 dict로 반환. DB INSERT는 하지 않음.

> 상세 내용은 `scheduler_structure.md` 섹션 3 참조

### 주요 함수

#### `get_daily_snapshot(target_date) → dict`
- 단일 날짜 스냅샷 계산 (daily_inserter에서 매일 호출)
- 반환: date, total_asset, usd_krw, ndx100, exposure, cash_ratio, x1~x3_ratio, twr_asset

### 내부 함수 (직접 호출 불필요)

| 함수 | 설명 |
|------|------|
| `_get_token()` | KIS API 토큰 발급 (캐시) |
| `_get_kr_price(ticker, date_str, token)` | KIS 국내주식 과거 종가 |
| `_get_us_price(ticker, excd, date_str, token)` | KIS 해외주식 과거 종가 (현재 미사용 — Yahoo로 대체) |
| `_get_yahoo_price(ticker, target_date)` | Yahoo Finance 과거 종가 (FX/INDEX/CRYPTO/미국주식) |
| `_fetch_positions()` | DB에서 포지션 목록 조회 |
| `_fetch_prev_summary(date)` | DB에서 전날 (total_asset, twr_asset) 조회 |

### 종가 조회 방식 (market별)

| market | API |
|--------|-----|
| `KR` | KIS 국내주식 기간별시세 |
| `NAS` / `AMS` / `ARC` | Yahoo Finance (KIS에서 대체) |
| `FX` / `INDEX` / `CRYPTO` | Yahoo Finance |
| `KRW` | 1.0 고정 |
| `USD` | USDKRW=X 환율값 |

---

## snap.py

### 역할
날짜 범위를 지정하면 전체 기간의 API 데이터를 한 번에 캐싱 후 날짜별로 스냅샷 계산. `daily_inserter.py`의 누락 날짜 보정(`_backfill`)에서 import하여 사용.

### 특징
- `_GLOBAL_START_DATE_STR`, `_GLOBAL_END_DATE_STR` 전역변수로 캐시 범위를 미리 세팅 → API 호출 최소화
- KR: 전체 기간을 100건씩 루프로 한 번에 조회 후 캐시
- Yahoo: 전체 기간을 한 번에 조회 후 캐시
- `fetch_snapshot()` 반환 None이면 휴장일 (KR 종목 가격 0인 날)

### 주요 함수

#### `fetch_snapshot(target_date, position_rows, token) → tuple | None`
- 단일 날짜 스냅샷 계산
- 반환: `(ratios_dict, ndx100, usd_krw, db_rows)` 또는 휴장일이면 `None`
- `ratios_dict`: `calculate_exposure_and_ratios()` 결과

#### `fetch_positions() → list`
- DB에서 `(ticker, quantity, leverage, market)` 목록 조회

#### `fetch_db_row(date) → tuple | None`
- DB에서 특정 날짜 `daily_summary` 행 조회

#### `fetch_cash_flows(start, end) → dict`
- 기간 내 입출금 내역 조회
- 반환: `{date: (cash_flow, note), ...}`

#### `get_kis_access_token() → str`
- KIS API 토큰 발급

#### `date_range(start, end) → list`
- 평일만 포함한 날짜 리스트 반환 (주말 제외)

### 전역변수 (캐시 제어)

| 변수 | 설명 |
|------|------|
| `_GLOBAL_START_DATE_STR` | 캐시 조회 시작일 (YYYYMMDD) — 외부에서 세팅 필요 |
| `_GLOBAL_END_DATE_STR` | 캐시 조회 종료일 (YYYYMMDD) — 외부에서 세팅 필요 |
| `_KR_CACHE` | 국내주식 종가 캐시 |
| `_US_CACHE` | 해외주식 종가 캐시 (현재 미사용) |
| `_YAHOO_CACHE` | Yahoo Finance 캐시 |

### 사용 예시 (daily_inserter._backfill에서)
```python
import app.utils.snap as snap

snap._GLOBAL_START_DATE_STR = start_date.strftime("%Y%m%d")
snap._GLOBAL_END_DATE_STR   = end_date.strftime("%Y%m%d")

token         = snap.get_kis_access_token()
position_rows = snap.fetch_positions()
weekdays      = snap.date_range(start_date, end_date)

for target_date in weekdays:
    result = snap.fetch_snapshot(target_date, position_rows, token)
    if result is None:
        continue  # 휴장일
    ratios, ndx100, usd_krw, _ = result
```