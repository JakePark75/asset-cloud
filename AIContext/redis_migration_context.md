# asset-cloud Redis 전환 작업 컨텍스트

---

## 1. 프로젝트 개요

Oracle Cloud Ubuntu VM에서 운영 중인 개인 자산관리 시스템.

| 항목 | 내용 |
|------|------|
| VM | Oracle Cloud 도쿄 리전 |
| OS | Ubuntu |
| 프레임워크 | Shiny for Python 1.6.2 |
| DB | PostgreSQL (assetdb / user: jake) |
| 런타임 | Python 3.10.12 |

### 서비스 구조

| 서비스 | 파일 | WorkingDirectory |
|--------|------|-----------------|
| myassets (Shiny 앱) | `app/app.py` | `/home/ubuntu/asset-cloud/` |
| price_updater (시세수집) | `scheduler/price_updater.py` | `/home/ubuntu/asset-cloud/scheduler/` |
| daily_inserter (일간스냅샷) | `scheduler/daily_inserter.py` | `/home/ubuntu/asset-cloud/scheduler/` |

---

## 2. 프로젝트 디렉토리 구조

```
/home/ubuntu/asset-cloud/
├── app/
│   ├── app.py                   # Shiny 진입점
│   ├── db.py                    # DB 연결, 마켓 헬퍼
│   ├── redis_client.py          # Redis 연결 (현재 app/ 전용 → 삭제 예정)
│   ├── price_signal.py          # asyncpg LISTEN/NOTIFY → reactive.Value
│   ├── auth.py
│   ├── context_api.py
│   ├── static/
│   │   ├── base.css
│   │   ├── dashboard.css
│   │   ├── portfolio.css
│   │   ├── accounts.css
│   │   └── history.css
│   ├── utils/
│   │   ├── metrics.py           # 순수 계산 함수 (XIRR, TWR, alpha, beta 등)
│   │   ├── daily_snapshot.py    # 단일 날짜 스냅샷 계산 (daily_inserter 전용)
│   │   └── snap.py              # 날짜범위 스냅샷 (누락 보정용 standalone)
│   └── modules/
│       ├── dashboard.py
│       ├── portfolio.py
│       ├── accounts.py
│       ├── accounts_DAL.py
│       ├── accounts_components.py
│       ├── accounts_modals.py
│       ├── components.py
│       ├── history.py
│       ├── history_DAL.py
│       ├── history_charts.py
│       ├── history_table.py
│       ├── history_utils.py
│       └── settings.py
└── scheduler/
    ├── price_updater.py         # 런처 (interval=0 → WS모드, >0 → REST모드)
    ├── price_updater_common.py  # 공통 (설정/DB/시장상태/Yahoo/공휴일)
    ├── price_updater_rest.py    # REST 폴링 모드
    ├── price_updater_ws.py      # WebSocket 실시간 모드
    ├── daily_inserter.py        # 일간 스냅샷 자동 삽입
    ├── gen_daily_data.py        # 수동 보정용 standalone
    └── config.json              # 공통 설정
```

---

## 3. DB 스키마

### tickers
| 컬럼 | 타입 | 설명 |
|------|------|------|
| ticker | TEXT PK | 종목 티커 |
| name | TEXT | 종목명 |
| market | TEXT | KR / NAS / NYS / AMS / ARC / FX / INDEX / CRYPTO / COM |
| leverage | INT | 1 / 2 / 3 |
| current_price | NUMERIC | 현재가 |
| change_pct | NUMERIC | 등락률 |
| updated_at | TIMESTAMP | 마지막 업데이트 |
| is_manual | BOOLEAN | 수동 추가 여부 |
| data_time | TIMESTAMP | 실제 데이터 시각 (Yahoo만 해당) |

### accounts
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | SERIAL PK | |
| name | TEXT | 계좌이름 |
| alias | TEXT | 계좌별명 |
| is_watch | BOOLEAN | 감시계좌 여부 (총자산 합계 제외) |
| prev_total_asset | NUMERIC | 전일 총자산 (일간손익 계산용) |

### positions
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | SERIAL PK | |
| account_id | INT FK → accounts.id CASCADE DELETE | |
| ticker | TEXT | 종목 티커 또는 "KRW" / "USD" |
| quantity | NUMERIC | 수량 (현금인 경우 금액) |

### daily_summary
| 컬럼 | 타입 | 설명 |
|------|------|------|
| date | DATE PK | |
| total_asset | NUMERIC | 총자산 (원화) |
| cash_flow | BIGINT | 입출금 합산 (+입금 / -출금) |
| cash_flow_note | TEXT | 입출금 사유 |
| ndx100 | NUMERIC | NDX100 지수 절대값 |
| exposure | NUMERIC | 익스포저 비중 (0~1) |
| cash_ratio | NUMERIC | 현금비중 (0~1) |
| x1_ratio | NUMERIC | x1 비중 (0~1) |
| x2_ratio | NUMERIC | x2 비중 (0~1) |
| x3_ratio | NUMERIC | x3 비중 (0~1) |
| twr_asset | NUMERIC | TWR 계산용 보조값 |
| usd_krw | NUMERIC(10,2) | 해당일 USD/KRW 환율 |

---

## 4. 현재 시세 수집 구조

### 모드 분기
`config.json`의 `interval` 값으로 분기:
- `interval = 0` → WebSocket 모드 (`price_updater_ws.py`)
- `interval > 0` → REST 폴링 모드 (`price_updater_rest.py`), N분 주기

**Redis 전환 작업은 REST 모드 기준으로 진행한다. WS 모드는 REST 완료 후 별도 작업.**

### REST 모드 흐름
```
price_updater_rest.py
  run_update_cycle()
    ├── DB에서 tickers 전체 조회
    ├── 시장 상태별 필터링 (get_market_status)
    ├── 종목별 스레드로 update_worker() 병렬 실행
    │     └── KIS REST API or Yahoo Finance
    │           └── update_ticker_in_db() → tickers 테이블 UPDATE
    └── 전체 완료 후 NOTIFY price_updated
```

### WebSocket 모드 흐름 (참고용)
```
price_updater_ws.py
  kis_ws_task()         ← KR/US 실시간 push → _save_price() → update_ticker_in_db()
  yahoo_poll_task()     ← FX/INDEX/CRYPTO 60초 폴링 → update_ticker_in_db()
  ※ NOTIFY는 60초마다 1회 (YAHOO_POLL_INTERVAL 기준)
```

### 공통
- `update_ticker_in_db()` — tickers 테이블 UPDATE (`price_updater_common.py`)
- 업데이트 완료 후 PostgreSQL `NOTIFY price_updated` 발송
- `price_signal.py`가 asyncpg로 LISTEN 대기 → reactive.Value 카운터 증가 → 화면 갱신

---

## 5. 현재 화면 갱신 구조

```
price_updater → NOTIFY price_updated
  → price_signal.py (asyncpg LISTEN)
  → reactive.Value(_counter) 증가
  → 구독 중인 화면 자동 재렌더링 → 각 화면이 DB 재조회
```

| 화면 | 트리거 | 메커니즘 |
|------|--------|---------|
| 포트폴리오 | price_signal | `price_signal.get()` 구독 → `portfolio_content` 전체 재실행 |
| 대시보드 | price_signal | `data()`, `position_data()` calc 구독 → 하위 렌더러 재실행 |
| 실적(history) | price_signal + today_cf_trigger | `history_data()` calc → `_calc_and_store_today_row()` + `load_history()` |
| 설정 | reactive.invalidate_later(60) | price_signal 무관, 60초 자체 타이머 |
| 계좌관리 | 사용자 액션 전용 | `refresh = reactive.value(0)`, CRUD 시 refresh.set() |

---

## 6. 현재 DB Read/Write 전체 현황

### 6-1. tickers 테이블

#### WRITE
| 파일 | 함수 | 내용 |
|------|------|------|
| `price_updater_common.py` | `update_ticker_in_db()` | current_price, change_pct, updated_at, data_time UPDATE |
| `accounts.py` | `add_position()` | 신규 종목 INSERT (tickers에 없을 경우) |
| `accounts.py` | `edit_position()` | name, market, leverage UPDATE |
| `settings.py` | `btn_confirm_add_ticker` | 수동 티커 INSERT (ON CONFLICT DO UPDATE) |
| `settings.py` | `confirm_delete_ticker` | 수동 티커 DELETE |

#### READ (current_price, change_pct 포함 — Redis 전환 대상)
| 파일 | 함수 | 읽는 컬럼 | 전환 방향 |
|------|------|---------|---------|
| `price_updater_rest.py` | `run_update_cycle()` | ticker, market | DB 유지 (메타데이터) |
| `price_updater_ws.py` | `get_subscribe_targets()` | ticker, market | DB 유지 (메타데이터) |
| `dashboard.py` | `_load_summary_data()` | current_price (USDKRW=X, ^NDX) | **Redis 전환** |
| `dashboard.py` | `_load_position_data()` | current_price, market, leverage | **Redis 전환** (current_price만) |
| `portfolio.py` | `load_portfolio()` | current_price, change_pct, market, leverage | **Redis 전환** (시세만) |
| `accounts_DAL.py` | `fetch_accounts_summary()` | current_price (SQL 내 서브쿼리 포함) | **Redis 전환** |
| `accounts_DAL.py` | `fetch_account_details()` | current_price, change_pct, market, leverage | **Redis 전환** (시세만) |
| `settings.py` | `ticker_list` render | current_price, change_pct | **Redis 전환** |
| `history.py` | `_calc_and_store_today_row()` | current_price (USDKRW=X, ^NDX, 전종목) | **Redis 전환** |
| `db.py` | `get_usd_krw()` | current_price, change_pct (USDKRW=X) | **Redis 전환** |

#### READ (메타데이터만 — DB 유지)
| 파일 | 함수 | 읽는 컬럼 |
|------|------|---------|
| `price_updater_rest.py` | `run_update_cycle()` | ticker, market |
| `price_updater_ws.py` | `get_subscribe_targets()` | ticker, market |
| `settings.py` | `ticker_list` render | ticker, name, market, leverage, is_manual |
| `accounts_DAL.py` | `fetch_account_details()` | name, market, leverage |

### 6-2. positions 테이블

#### WRITE
| 파일 | 함수 | 내용 |
|------|------|------|
| `accounts.py` | `add_position()` | INSERT |
| `accounts.py` | `add_cash()` | INSERT |
| `accounts.py` | `edit_position()` | quantity UPDATE |
| `accounts.py` | `edit_cash()` | ticker, quantity UPDATE |
| `accounts.py` | `delete_position()` | DELETE |
| `accounts.py` | `delete_cash()` | DELETE |

#### READ
| 파일 | 함수 | 내용 | 전환 방향 |
|------|------|------|---------|
| `dashboard.py` | `_load_summary_data()` | ticker, quantity, leverage, market JOIN tickers | DB 유지 |
| `dashboard.py` | `_load_position_data()` | ticker, quantity, leverage, market JOIN tickers | DB 유지 |
| `portfolio.py` | `load_portfolio()` | ticker, quantity JOIN tickers | DB 유지 |
| `accounts_DAL.py` | `fetch_accounts_summary()` | quantity JOIN tickers | DB 유지 |
| `accounts_DAL.py` | `fetch_account_details()` | id, ticker, quantity JOIN tickers | DB 유지 |
| `history.py` | `_calc_and_store_today_row()` | ticker, quantity, market, leverage | DB 유지 |
| `daily_snapshot.py` | `_fetch_positions()` | ticker, quantity, leverage, market | DB 유지 |

### 6-3. accounts 테이블

#### WRITE
| 파일 | 함수 | 내용 |
|------|------|------|
| `accounts.py` | `add_account()` | INSERT |
| `accounts.py` | `delete_account()` | DELETE (CASCADE → positions 자동 삭제) |
| `daily_inserter.py` | `_update_account_prev_totals()` | prev_total_asset UPDATE |

#### READ
| 파일 | 함수 | 내용 | 전환 방향 |
|------|------|------|---------|
| `accounts_DAL.py` | `fetch_accounts_summary()` | id, name, alias, is_watch, prev_total_asset | DB 유지 |
| `accounts_DAL.py` | `fetch_account_details()` | name, alias, is_watch, prev_total_asset | DB 유지 |
| `dashboard.py` | `_load_summary_data()` | is_watch (JOIN 필터) | DB 유지 |
| `portfolio.py` | `load_portfolio()` | is_watch (JOIN 필터) | DB 유지 |
| `history.py` | `_calc_and_store_today_row()` | is_watch (JOIN 필터) | DB 유지 |

### 6-4. daily_summary 테이블

#### WRITE
| 파일 | 함수 | 내용 |
|------|------|------|
| `daily_inserter.py` | `_upsert()` | 전일 스냅샷 INSERT (ON CONFLICT DO UPDATE) |
| `history_DAL.py` | `save_cash_flow()` | cash_flow, cash_flow_note UPDATE + twr_asset 재계산 UPDATE |

#### READ
| 파일 | 함수 | 내용 | 전환 방향 |
|------|------|------|---------|
| `dashboard.py` | `_load_summary_data()` | 전체 이력 (date, total_asset, cash_flow 등) | DB 유지 |
| `history_DAL.py` | `load_history()` | 전체 이력 | DB 유지 |
| `history_DAL.py` | `save_cash_flow()` | 특정 날짜 이후 rows (TWR 재계산용) | DB 유지 |
| `history.py` | `_calc_and_store_today_row()` | 마지막 row (prev total_asset, twr_asset) | DB 유지 |
| `portfolio.py` | `load_portfolio()` | 마지막 row (yesterday_total) | DB 유지 |
| `daily_snapshot.py` | `_fetch_prev_summary()` | 전날 total_asset, twr_asset | DB 유지 |
| `daily_inserter.py` | `_fetch_last_snapshot_date()` | MAX(date) | DB 유지 |
| `daily_inserter.py` | `_fetch_prev_summary()` | 전날 total_asset, twr_asset | DB 유지 |

---

## 7. 현재 Redis 사용 현황

### 연결 파일
`app/redis_client.py` — 싱글턴 패턴, ping으로 연결 확인, 실패 시 None 반환.

### 현재 저장 중인 Key

| Key | 타입 | 내용 | 쓰는 곳 | 읽는 곳 |
|-----|------|------|---------|--------|
| `today_cash_flow` | string (int) | 오늘 입출금액 | `history.py` `_save_cash_flow()` | `history.py`, `dashboard.py`, `daily_inserter.py` |
| `today_cash_flow_note` | string | 오늘 입출금 사유 | `history.py` `_save_cash_flow()` | `history.py`, `daily_inserter.py` |
| `today_row` | string (JSON) | 오늘치 실적 스냅샷 | `history.py` `_calc_and_store_today_row()` | `history_DAL.py` `load_history()` |

### today_row JSON 구조
```json
{
  "date": "2026-06-10",
  "total_asset": 123456789,
  "twr_asset": 98765432,
  "ndx100": 19500.0,
  "cash_flow": 0,
  "cash_flow_note": null,
  "exposure": 0.85,
  "cash_ratio": 0.15,
  "x1_ratio": 0.30,
  "x2_ratio": 0.40,
  "x3_ratio": 0.15,
  "usd_krw": 1380.50
}
```

---

## 8. 현재 구조의 문제점

1. ~~계좌 CRUD 후 NOTIFY 안 날림~~ → **완료**: accounts.py, settings.py에 `_notify_price_updated()` 추가됨

2. **`_send_history_table`이 `@reactive.effect`** — 실적 탭이 숨겨져 있어도 실행됨 → 불필요한 DB 조회 + 계산 (Phase 2에서 해결)

3. **대시보드/history가 동일한 값을 독립적으로 중복 계산** — positions + tickers를 각각 별도 DB 조회 (Phase 2에서 해결)

4. **settings.py `invalidate_later(60)` 부작용** — 예측 불가한 타이밍에 화면 갱신 유발 (Phase 2에서 해결)

5. **price_updater가 시세를 DB에만 기록** — 각 화면이 매번 DB 직접 조회. Redis 캐시 없음. → **Phase 1에서 해결**

6. **today_row 재계산 트리거 불완전** — accounts에서 positions 변경 시 today_row가 즉시 재계산되지 않음. → **Phase 1에서 해결**

---

## 9. Redis 전환 설계 (확정)

### 9-1. 핵심 원칙

- **수시로 변하는 값, DB에 확정 저장할 필요 없는 값은 Redis에 존재**
- **Redis read는 어디서든 자유롭게**
- **today_row 재계산은 명시적 함수 호출로만** — `recalc_today_row()`
- **price_updater는 시세만 Redis에 기록**, today_row 재계산은 사이클 완료 후 1회 호출
- **동시 호출 보호**: `recalc_today_row()` 내부에 `threading.Lock` — `acquire(blocking=False)`로 이미 실행 중이면 스킵

### 9-2. Redis에 추가할 Key

| Key | 타입 | 내용 | 쓰는 곳 |
|-----|------|------|---------|
| `prices` | hash | `ticker → json{price, change_pct}` | `redis_store.py` `write_price()` |
| `usd_krw` | string (float) | 현재 환율 (USDKRW=X 별도 편의 key) | `redis_store.py` `write_price()` 내부에서 자동 갱신 |

기존 key (`today_cash_flow`, `today_cash_flow_note`, `today_row`) 유지.

### 9-3. 공용 모듈 신규 생성

**`{공용폴더}/redis_store.py`** (폴더명 미결정)

루트(`/home/ubuntu/asset-cloud/`) 하위 폴더에 생성.

포함 내용:
```
get_redis()                          # 연결 (기존 redis_client.py와 동일)
write_price(ticker, price, change_pct)  # prices hash + usd_krw 갱신
get_price(ticker) → dict             # prices hash에서 단일 조회
get_all_prices() → dict              # prices hash 전체 조회
recalc_today_row()                   # today_row 재계산 + Redis 저장 (Lock 보호)
```

**`scheduler/price_updater_common.py`** 상단에 추가:
```python
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
```
(daily_inserter.py에는 이미 동일 패턴 존재)

### 9-4. recalc_today_row() 상세

```python
_recalc_lock = threading.Lock()

def recalc_today_row():
    if not _recalc_lock.acquire(blocking=False):
        return  # 이미 계산 중이면 스킵
    try:
        # 1. DB: positions + accounts(is_watch 필터) 조회
        # 2. DB: daily_summary 마지막 row (prev total_asset, twr_asset)
        # 3. Redis: get_all_prices() → 종목별 현재가
        # 4. Redis: get('usd_krw') → 환율
        # 5. Redis: get('today_cash_flow') → 오늘 입출금
        # 6. calculate_exposure_and_ratios() 로 total_asset, 비중 계산
        # 7. twr_asset 계산
        # 8. Redis: set('today_row', json.dumps(result))
    finally:
        _recalc_lock.release()
```

### 9-5. recalc_today_row() 호출 시점

| 호출 위치 | 트리거 | 비고 |
|----------|--------|------|
| `price_updater_rest.py` `run_update_cycle()` | 전체 사이클 완료 후 NOTIFY 직전 1회 | 종목별 스레드 완료 후 |
| `price_updater_ws.py` `yahoo_poll_task()` | Yahoo 폴링 완료 후 NOTIFY 직전 | kis_ws_task에서는 호출 안 함 |
| `accounts.py` | 종목/현금 추가·수정·삭제 완료 후 | `_notify_price_updated()` 호출과 같은 위치 |
| `history.py` `_save_cash_flow()` | 오늘 입출금 Redis 저장 직후 | 현재 `_calc_and_store_today_row()` 호출 위치 교체 |

### 9-6. 각 화면의 시세 읽기 전환

**전환 대상: current_price, change_pct 만. 메타데이터(name, market, leverage 등)는 DB 유지.**

| 파일 | 현재 | 전환 후 |
|------|------|--------|
| `dashboard.py` `_load_summary_data()` | DB tickers에서 USDKRW=X, ^NDX 조회 | Redis `get_price('USDKRW=X')` 등 |
| `dashboard.py` `_load_position_data()` | DB tickers JOIN으로 current_price 조회 | Redis `get_all_prices()` 후 매핑 |
| `portfolio.py` `load_portfolio()` | DB tickers JOIN으로 current_price, change_pct | Redis `get_all_prices()` 후 매핑 |
| `accounts_DAL.py` `fetch_accounts_summary()` | SQL 내 서브쿼리로 current_price 조회 | Python에서 Redis 읽어 계산으로 변경 |
| `accounts_DAL.py` `fetch_account_details()` | DB tickers JOIN으로 current_price, change_pct | Redis `get_all_prices()` 후 매핑 |
| `settings.py` `ticker_list` | DB tickers에서 current_price, change_pct | Redis `get_all_prices()` 후 매핑 |
| `db.py` `get_usd_krw()` | DB tickers WHERE ticker='USDKRW=X' | Redis `get('usd_krw')` |
| `history.py` `_calc_and_store_today_row()` | DB tickers에서 현재가 조회 | recalc_today_row()로 완전 대체 |

**DB 유지 대상:**
- tickers: ticker, name, market, leverage, is_manual (메타데이터 전체)
- positions, accounts, daily_summary: 전부 DB 유지

---

## 10. 작업 순서 (Phase 1)

**원칙: 기존 DB 기반 동작은 건드리지 않고 Redis write를 추가. 각 단계별로 검증 후 진행.**

### Step 1. `{공용폴더}/redis_store.py` 신규 생성
- 서비스 무중단. 파일만 생성.
- `get_redis()`, `write_price()`, `get_all_prices()`, `recalc_today_row()` 구현

### Step 2. `price_updater_common.py` 수정
- 상단 sys.path 추가
- `update_ticker_in_db()` 내부에 Redis write 추가 (try/except로 감싸서 실패해도 DB write 영향 없음)
- **price_updater 서비스 재시작 1회**
- 검증: Redis `prices` hash에 값이 쌓이는지 확인
  ```bash
  redis-cli hgetall prices
  redis-cli get usd_krw
  ```

### Step 3. `price_updater_rest.py` 수정
- `run_update_cycle()` 완료 후 NOTIFY 직전 `recalc_today_row()` 호출
- **price_updater 서비스 재시작 1회**
- 검증: Redis `today_row` 값이 갱신되는지 확인
  ```bash
  redis-cli get today_row
  ```

### Step 4. `accounts.py` 수정
- 종목/현금 추가·수정·삭제 완료 후 `recalc_today_row()` 호출 추가
- **myassets 서비스 재시작 1회**
- 검증: 계좌에서 수량 변경 후 `today_row` 즉시 갱신 확인

### Step 5. `history.py` 수정
- `_save_cash_flow()`에서 `_calc_and_store_today_row()` 호출 부분을 `recalc_today_row()`로 교체
- `history_data()` calc에서 `_calc_and_store_today_row()` 호출 제거 (recalc는 이미 다른 트리거에서 호출되므로)
- `_calc_and_store_today_row()` 함수 삭제
- **myassets 서비스 재시작 1회**

### Step 6. 각 화면 시세 읽기 전환 (DB → Redis)
- `db.py` `get_usd_krw()` → Redis 읽기로 교체
- `dashboard.py`, `portfolio.py`, `accounts_DAL.py`, `settings.py` → `get_all_prices()` 읽기로 교체
- **myassets 서비스 재시작 1회**
- 검증: 각 화면 정상 동작 확인

### Step 7. 정리
- `app/redis_client.py` 삭제
- 기존 `from app.redis_client import get_redis` import 경로 전부 교체
- `daily_inserter.py` import 경로 수정
- **전체 서비스 재시작**

---

## 11. 변경 영향 범위 요약

| 파일 | 변경 내용 | 단계 |
|------|---------|------|
| `{공용폴더}/redis_store.py` | **신규 생성** | Step 1 |
| `scheduler/price_updater_common.py` | sys.path 추가, update_ticker_in_db에 Redis write 추가 | Step 2 |
| `scheduler/price_updater_rest.py` | run_update_cycle 끝에 recalc_today_row 호출 | Step 3 |
| `app/modules/accounts.py` | CRUD 완료 시 recalc_today_row 호출 추가 | Step 4 |
| `app/modules/history.py` | _calc_and_store_today_row → recalc_today_row 교체 후 삭제 | Step 5 |
| `app/db.py` | get_usd_krw() Redis 읽기로 교체 | Step 6 |
| `app/modules/dashboard.py` | 시세 조회 Redis로 교체 | Step 6 |
| `app/modules/portfolio.py` | 시세 조회 Redis로 교체 | Step 6 |
| `app/modules/accounts_DAL.py` | 시세 조회 Redis로 교체 (SQL→Python 재작성) | Step 6 |
| `app/modules/settings.py` | 시세 조회 Redis로 교체 | Step 6 |
| `app/modules/history_DAL.py` | import 경로 수정 | Step 7 |
| `scheduler/daily_inserter.py` | import 경로 수정 | Step 7 |
| `app/redis_client.py` | **삭제** | Step 7 |

---

## 12. 미결 사항

- 공용 폴더명 미결정
- Phase 2 (DOM 패치 구조 재설계) 상세 설계 미완
