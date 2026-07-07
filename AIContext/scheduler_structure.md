# 스케줄러 구조 문서

---

## 1. 파일 구성

| 파일 | 역할 |
|------|------|
| `scheduler/price_updater.py` | 시세 수집 런처. `config.json.interval` 값에 따라 REST / WS 모드 분기 |
| `scheduler/price_updater_common.py` | 공통 설정/시장 상태/공휴일/시세 조회/Redis write 함수 |
| `scheduler/price_updater_rest.py` | REST 폴링 모드 (`interval > 0`) |
| `scheduler/price_updater_ws.py` | 웹소켓 모드 (`interval = 0`) |
| `scheduler/daily_inserter.py` | 일간 누적 데이터 자동 삽입 / 백필 |
| `app/utils/daily_snapshot.py` | 단일 날짜 스냅샷 계산 (daily_summary 1행 생성용) |
| `app/utils/snap.py` | 날짜 범위 스냅샷 계산 (백필용) |
| `scheduler/gen_daily_data.py` | 누락 기간 수동 보정용 스탠드얼론 스크립트 |
| `scheduler/config.json` | 공통 설정값 |

---

## 2. config.json 키 목록

| 키 | 설명 | 예시 |
|----|------|------|
| `kis_app_key` | KIS API 앱키 | |
| `kis_app_secret` | KIS API 시크릿 | |
| `db_password` | PostgreSQL 비밀번호 | |
| `interval` | 시세 수집 주기(분). `0` = 웹소켓, `>0` = REST 폴링 | `1` |
| `data_go_kr_key` | 한국 공휴일 API 키 | |
| `finnhub_api_key` | 미국 공휴일 API 키 | |
| `retirement_date` | 은퇴 목표일(YYYYMMDD) | `"20351231"` |
| `daily_insert_time` | 일간 누적 삽입 시각(HH:MM, KST) | `"07:10"` |
| `login_id` | 앱 로그인 ID | |
| `jwt_secret` | JWT 서명 시크릿 | |

---

## 3. daily_snapshot.py

### 역할
특정일 기준의 가장 최근 종가를 KIS/Yahoo API에서 조회해서 `daily_summary` 1행분 데이터를 계산한다. DB INSERT 는 하지 않는다.

### 공개 함수

#### `get_daily_snapshot(target_date: datetime.date, calc_account_totals: bool = False) → dict`

**동작 순서:**
1. Yahoo Finance에서 `USDKRW=X`, `^NDX` 조회
2. `positions + tickers + accounts` 를 읽어 종목별 종가 조회
3. 시장에 따라 KIS 또는 Yahoo API 사용
4. `calculate_exposure_and_ratios()` 로 총자산 및 비중 계산
5. 전날 `daily_summary` 행을 읽어 TWR 계산
6. dict 반환

**반환 dict 키:**

| 키 | 설명 |
|----|------|
| `date` | 조회 날짜 |
| `total_asset` | 총자산(원화) |
| `usd_krw` | USD/KRW 환율 |
| `ndx100` | NDX100 지수 절대값 |
| `exposure` | Exposure 비중 (0~1) |
| `cash_ratio` | 현금 비중 (0~1) |
| `x1_ratio` | x1 비중 (0~1) |
| `x2_ratio` | x2 비중 (0~1) |
| `x3_ratio` | x3 비중 (0~1) |
| `twr_asset` | TWR 계산용 보조값 |
| `account_totals` | 계좌별 합산값(옵션) |

### 종가 조회 방식

| market | API |
|--------|-----|
| `KR` | KIS 국내주식 기간별시세 |
| `NAS` / `AMS` / `ARC` | Yahoo Finance |
| `FX` / `INDEX` / `CRYPTO` | Yahoo Finance |
| `KRW` | 1.0 고정 |
| `USD` | USDKRW=X 환율값 |

### Fallback

- 해당 날짜 데이터가 없으면 target_date 이전 가장 최근값을 사용한다.
- 공휴일 스킵 없이 항상 가장 최근 종가를 사용한다.
- KR 종목 0 반환도 그대로 계산한다.

---

## 4. snap.py

### 역할
날짜 범위를 지정하면 캐시를 채워두고 날짜별 스냅샷을 계산한다. `daily_inserter.py` 의 백필 경로에서 사용한다.

### 주요 함수

| 함수 | 설명 |
|------|------|
| `set_batch_range(start_date_str, end_date_str)` | 배치 범위와 캐시 리셋을 함께 설정 |
| `get_kis_access_token()` | KIS API 토큰 발급 |
| `fetch_positions()` | DB에서 포지션 목록 조회 |
| `fetch_db_row(date)` | 특정 날짜 `daily_summary` 행 조회 |
| `fetch_cash_flows(start, end)` | 기간 내 입출금 내역 조회 |
| `date_range(start, end)` | 주말 제외 날짜 리스트 반환 |
| `fetch_snapshot(target_date, position_rows, token)` | 단일 날짜 스냅샷 계산 |

### 전역 캐시

| 변수 | 설명 |
|------|------|
| `_GLOBAL_START_DATE_STR` | 캐시 조회 시작일 |
| `_GLOBAL_END_DATE_STR` | 캐시 조회 종료일 |
| `_KR_CACHE` | 국내주식 종가 캐시 |
| `_YAHOO_CACHE` | Yahoo Finance 캐시 |

---

## 5. daily_inserter.py

### 동작 방식

- `threading.Timer` 기반으로 동작한다.
- 서비스 시작 시 `backfill_checkpoint` 를 읽고 누락 날짜가 있으면 즉시 보정한다.
- 이후 `daily_insert_time` 까지 타이머를 등록한다.
- 타이머 도달 시 `get_market_status("NAS")` 를 보고 정상 마감 경로를 실행한다.

### 핵심 흐름

#### `_upsert(snapshot, use_redis_cash_flow=False)`
- `daily_summary` 에 UPSERT 한다.
- `use_redis_cash_flow=True` 인 경우에만 Redis `today_cash_flow` / `today_cash_flow_note` 를 읽어 반영하고, insert 후 Redis 값을 리셋한다.

#### `_backfill(dates)`
- 누락 날짜들을 순서대로 채운다.
- 실패한 날짜는 스킵하고 다음 날짜를 계속 처리한다.
- 마지막 성공 날짜 기준으로 `accounts.prev_total_asset` 를 갱신한다.

#### `_insert_daily_close(target_date)`
- 정상 마감 전용 경로.
- `daily_snapshot.get_daily_snapshot()` 결과를 UPSERT 하고 Redis 오늘 입출금을 반영한다.

#### `_run_daily_cycle(start_date, end_date)`
- start~end 범위에서 구멍을 찾고 백필 + 정상 마감을 분리 처리한다.
- 성공/실패에 따라 checkpoint 를 전진시킨다.

### systemd 서비스

- 서비스명: `daily_inserter`
- 서비스 파일: `scheduler/daily_inserter.service`
- VM 재부팅 시 자동 시작, 크래시 시 10초 후 자동 재시작

---

## 6. price_updater.py 요약

### 런처

```text
interval == 0 → price_updater_ws.main()
interval  > 0 → price_updater_rest.main()
--force 옵션은 REST 모드에서만 의미 있음
```

### price_updater_common.py

- `load_config()` 로 config.json 로드
- `get_db_conn()` 으로 PostgreSQL 접속
- `HolidayCache` 로 한국/미국 공휴일 캐시
- `get_market_status()` 로 시장 상태 판단
- `get_yahoo_price()` 로 FX/INDEX/CRYPTO 시세 조회
- `update_price_cache()` 로 Redis `prices` hash 갱신
- `recalc_today_row()` 는 Redis 시세/입출금/캐시를 읽어 `today_row` 생성

### price_updater_rest.py

- `run_update_cycle()` 에서 tickers 전체를 읽는다.
- 상태가 `open` / `pre` / `after` 인 종목만 업데이트한다.
- 종목별 스레드를 병렬로 돌린 뒤 `recalc_today_row()` 와 `publish_price_updated()` 를 호출한다.
- `should_run_kr_final_close()` / `run_kr_final_close_update()` 로 KR 종가 확정 경로를 별도 처리한다.

### price_updater_ws.py

- KR/US 는 KIS 웹소켓, FX/INDEX/CRYPTO 는 Yahoo 폴링으로 처리한다.
- `yahoo_poll_task()` 는 10초 주기다.
- `_notify()` 는 `recalc_today_row()` + `publish_price_updated()` 를 수행한다.

---

## 7. 주의사항

- 시세는 DB `tickers.current_price` 가 아니라 Redis `prices` 를 봐야 한다.
- `daily_inserter.py` 의 `today_cash_flow` 는 Redis 에 먼저 있고, 정상 마감 시 DB로 확정된다.
- `snap.py` 는 백필용이고 `daily_snapshot.py` 는 단일 날짜 계산용이다.