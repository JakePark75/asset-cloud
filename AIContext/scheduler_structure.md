# 스케줄러 구조 문서

---

## 1. 파일 구성

| 파일 | 역할 |
|------|------|
| `scheduler/price_updater.py` | 실시간 시세 수집 (KIS/Yahoo API) |
| `scheduler/daily_inserter.py` | 매일 지정 시각에 전날 일간 누적 데이터 자동 삽입 |
| `app/utils/daily_snapshot.py` | 특정일 기준 가장 최근 종가 조회 + 지표 계산 (순수 계산 모듈) |
| `scheduler/gen_daily_data.py` | 누락 기간 수동 보정용 스탠드얼론 스크립트 (서비스 아님) |
| `scheduler/config.json` | 공통 설정값 |

---

## 2. config.json 키 목록

| 키 | 설명 | 예시 |
|----|------|------|
| `kis_app_key` | KIS API 앱키 | |
| `kis_app_secret` | KIS API 시크릿 | |
| `db_password` | PostgreSQL 비밀번호 | |
| `interval` | 시세 수집 주기 (분) | `5` |
| `kr_holiday_api_key` | 한국 공휴일 API 키 | |
| `us_holiday_api_key` | 미국 공휴일 API 키 | |
| `retirement_date` | 은퇴 목표일 | `"2035-01-01"` |
| `daily_insert_time` | 일간 누적 삽입 실행 시각 (HH:MM, KST) | `"07:30"` |

---

## 3. daily_snapshot.py

### 역할
특정일 기준 "가장 최근 종가"를 KIS/Yahoo API로 조회하여 `daily_summary` 1행분 데이터를 계산해 dict로 반환한다. DB INSERT는 하지 않는다.

### 공개 함수

#### `get_daily_snapshot(target_date: datetime.date) → dict`

**동작 순서:**
1. Yahoo Finance에서 `USDKRW=X`, `^NDX` 조회
2. `positions` + `tickers` 테이블에서 보유 종목 목록 조회
3. 종목별 시장(market)에 따라 KIS 또는 Yahoo API로 종가 조회
4. `calculate_exposure_and_ratios()` 호출하여 총자산 및 비중 계산
5. 전날 `daily_summary` 행 조회하여 TWR 계산
6. dict 반환

**반환 dict 키:**

| 키 | 타입 | 설명 |
|----|------|------|
| `date` | `datetime.date` | 조회 날짜 |
| `total_asset` | `float` | 총자산 (원화) |
| `usd_krw` | `float` | USD/KRW 환율 |
| `ndx100` | `float` | NDX100 지수 절대값 |
| `exposure` | `float` | Exposure 비중 (0~1) |
| `cash_ratio` | `float` | 현금 비중 (0~1) |
| `x1_ratio` | `float` | x1 비중 (0~1) |
| `x2_ratio` | `float` | x2 비중 (0~1) |
| `x3_ratio` | `float` | x3 비중 (0~1) |
| `twr_asset` | `float` | TWR 계산용 보조값 |

> `cash_flow` / `cash_flow_note` 는 반환하지 않음. 사용자가 히스토리 화면에서 수동 입력.

### 종가 조회 방식 (market별)

| market | API | 비고 |
|--------|-----|------|
| `KR` | KIS 국내주식 기간별시세 | 100건 루프, 최대 60일 범위 조회 |
| `NAS` / `AMS` / `ARC` | KIS 해외주식 기간별시세 | 100일씩 3회 조회 |
| `FX` / `INDEX` / `CRYPTO` | Yahoo Finance v8 API | |
| `KRW` | 1.0 고정 | |
| `USD` | USDKRW=X 환율값 | |

### Fallback 방식
- 해당 날짜 데이터 없으면 **target_date 이전 가장 최근값** 반환
- 공휴일, 연휴 관계없이 항상 최근 종가 사용 (is_holiday 스킵 없음)
- KR 종목이 0 반환해도 스킵하지 않고 그대로 계산

### 캐시
- `_KR_CACHE`, `_US_CACHE`, `_YAHOO_CACHE` — 프로세스 수명 동안 유지
- `gen_daily_data.py`로 범위 조회 시 API 중복 호출 방지

### TWR 계산 주의사항
- `get_daily_snapshot()` 호출 시점에는 `cash_flow`를 알 수 없으므로 **0으로 처리**하여 TWR 계산
- 사용자가 이후 히스토리 화면에서 입출금을 입력하면, 기존 재계산 로직이 `twr_asset`을 보정함

---

## 4. daily_inserter.py

### 동작 방식
- 1분 간격 루프로 실행
- 매일 `config.json`의 `daily_insert_time` (KST) 이후, 당일 최초 1회 실행
- `get_daily_snapshot(어제)` 호출 → `daily_summary` UPSERT

### UPSERT 정책
- `ON CONFLICT (date) DO UPDATE` — 같은 날짜 재실행 시 덮어쓰기
- `cash_flow` / `cash_flow_note` 는 UPSERT 시 덮어쓰지 않음 (사용자 입력값 보호)

### 오류 처리
- 오류 발생 시 `last_run_date` 갱신하지 않음 → 1분 후 자동 재시도

### systemd 서비스
- 서비스명: `daily_inserter`
- 서비스 파일 원본: `scheduler/daily_inserter.service`
- VM 재부팅 시 자동 시작, 크래시 시 10초 후 자동 재시작

---

## 5. gen_daily_data.py

### 용도
`daily_inserter`가 동작하지 않아 누락된 기간의 일간 누적 데이터를 수동으로 채울 때 사용.
서비스로 등록하지 않으며, 콘솔에서 직접 실행한다.

### 사용법
```bash
cd /home/ubuntu/asset-cloud

# 단일 날짜
python3 scheduler/gen_daily_data.py 20260610

# 날짜 범위
python3 scheduler/gen_daily_data.py 20260610-20260615
```

### 주의사항
- UPSERT 방식이므로 중복 실행해도 안전
- `cash_flow` / `cash_flow_note` 는 0 / NULL로 삽입 (이후 히스토리 화면에서 수동 입력)
- KIS 토큰은 실행 시 1회만 발급

---

## 6. price_updater.py 주요 구조 (요약)

> 상세 내용은 별도 문서 필요 시 추가

- 시장별 `is_market_open()` / `get_market_status()` 필터링
- `HolidayCache`: 매일 08:00 KST 한국/미국 공휴일 조회 및 캐싱
- 종가 확정 로직: `after` 상태에서 KR/US 종가 API 병행 호출, 확정 시 `_close_confirmed` 플래그로 당일 조회 중단
- 업데이트 완료 후 `NOTIFY price_updated` 전송 (Shiny 앱 실시간 갱신 트리거)
- systemd 서비스: `price_updater`, VM 재부팅 시 자동 시작