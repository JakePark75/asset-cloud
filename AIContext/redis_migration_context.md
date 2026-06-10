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
│   ├── app.py
│   ├── db.py                    # get_usd_krw() → Redis 읽음 (DB 아님)
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
│   │   ├── metrics.py
│   │   ├── daily_snapshot.py    # daily_inserter 전용 — KIS/Yahoo API 직접 호출
│   │   └── snap.py              # 누락 보정용 standalone (_backfill에서 사용)
│   └── modules/
│       ├── dashboard.py
│       ├── portfolio.py
│       ├── accounts.py
│       ├── accounts_DAL.py
│       ├── accounts_components.py
│       ├── accounts_modals.py
│       ├── components.py
│       ├── history.py
│       ├── history_DAL.py       # load_history()에서 today_row Redis 읽어 append
│       ├── history_charts.py
│       ├── history_table.py
│       ├── history_utils.py
│       └── settings.py
├── common/                      # ★ 신규 — app/scheduler 공용 모듈
│   └── redis_store.py
└── scheduler/
    ├── price_updater.py
    ├── price_updater_common.py  # update_ticker_in_db() → DB write 주석처리, Redis write만 실행
    ├── price_updater_rest.py    # 사이클 완료 후 recalc_today_row() → NOTIFY 순서
    ├── price_updater_ws.py      # yahoo_poll_task() — recalc 미호출 (미완)
    ├── daily_inserter.py        # common.redis_store import, API 직접 호출로 스냅샷 계산
    ├── gen_daily_data.py
    └── config.json
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
| current_price | NUMERIC | **price_updater의 DB write가 주석처리되어 있어 갱신되지 않음. 시세는 Redis에서만 읽는다.** |
| change_pct | NUMERIC | 동일 — DB 갱신 안 됨 |
| updated_at | TIMESTAMP | 마지막 업데이트 (DB write 주석처리로 갱신 안 됨) |
| is_manual | BOOLEAN | 수동 추가 여부 |
| data_time | TIMESTAMP | 실제 데이터 시각 (DB write 주석처리로 갱신 안 됨) |

> **주의**: `update_ticker_in_db()`의 UPDATE 쿼리는 통째로 주석처리(TODO 주석)되어 있다.
> tickers 테이블에서 current_price 등을 직접 읽는 코드가 있다면 stale 값을 읽게 된다.
> 시세는 반드시 Redis `prices` hash에서 읽어야 한다.

> **daily_snapshot.py는 tickers를 읽지 않는다.** KIS/Yahoo API를 직접 호출하여
> 종가를 조회하므로 Redis와 무관하다. daily_inserter와 gen_daily_data.py 전용 모듈이다.

---

## 4. Redis Key 전체 현황

| Key | 타입 | 내용 | 쓰는 곳 | 읽는 곳 |
|-----|------|------|---------|--------|
| `prices` | hash | `ticker → json{price, change_pct}` | `price_updater_common.py` `update_ticker_in_db()` 내 `write_price()` | 각 화면 모듈 `get_all_prices()` |
| `usd_krw` | string (float) | 현재 환율 — USDKRW=X 수신 시 자동 갱신 | `write_price()` 내부, ticker==USDKRW=X 조건 | `db.py` `get_usd_krw()` |
| `today_cash_flow` | string (int) | 오늘 입출금액 | `history.py` `_save_cash_flow()` | `history.py`, `dashboard.py`, `daily_inserter.py` `_upsert()` |
| `today_cash_flow_note` | string | 오늘 입출금 사유 | `history.py` `_save_cash_flow()` | `history.py`, `daily_inserter.py` `_upsert()` |
| `today_row` | string (JSON) | 오늘치 실적 스냅샷 | `common/redis_store.py` `recalc_today_row()` | `history_DAL.py` `load_history()` |

### daily_inserter와 today_cash_flow 관계
`_upsert()` 실행 시 Redis에서 `today_cash_flow`를 읽어 DB INSERT에 포함한다.
INSERT 완료 후 Redis의 `today_cash_flow`를 0으로 리셋하고 `today_cash_flow_note`를 삭제한다.
즉 오늘 입출금은 Redis에만 존재하다가 daily_inserter가 실행되는 시점에 DB로 확정 저장된다.

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

## 5. common/redis_store.py 공개 API

```python
get_redis()                            # Redis 연결 싱글턴 (실패 시 None)
write_price(ticker, price, change_pct) # prices hash 기록 + USDKRW=X면 usd_krw도 갱신
get_price(ticker) → dict | None        # {price, change_pct} 또는 None
get_all_prices() → dict                # {ticker: {price, change_pct}, ...} — 실패 시 빈 dict
recalc_today_row()                     # today_row 계산 + Redis 저장 (threading.Lock 보호)
```

### recalc_today_row() 계산 흐름
1. `get_all_prices()` — Redis `prices` hash 전체 조회
2. `usd_krw` — `prices['USDKRW=X']['price']` 우선, 없으면 Redis `usd_krw` key, 최종 fallback 1350.0
3. `ndx100` — `prices['^NDX']['price']`, 없으면 0.0
4. DB: `positions` + `accounts(is_watch=false 또는 NULL)` JOIN 조회
5. DB: `daily_summary` 마지막 행 (prev total_asset, twr_asset)
6. Redis: `today_cash_flow`
7. `calculate_exposure_and_ratios()` → total_asset, 비중
8. `twr_asset = prev_twr × (total_asset - cash_flow) / prev_total`
9. `today_row` JSON → Redis `set('today_row', ...)`

`threading.Lock(blocking=False)` 보호 — 이미 실행 중이면 스킵.
실패해도 예외를 밖으로 내보내지 않음.

### recalc_today_row() 호출 시점

| 호출 위치 | 트리거 |
|----------|--------|
| `price_updater_rest.py` `run_update_cycle()` | 전체 종목 스레드 join 완료 → recalc → NOTIFY 순서 |
| `accounts.py` `_notify_price_updated()` | CRUD(계좌/포지션/현금 추가·수정·삭제) 완료 후 NOTIFY와 함께 호출 |
| `history.py` `_save_cash_flow()` | 오늘 날짜 입출금을 Redis에 저장한 직후 |
| `price_updater_ws.py` `yahoo_poll_task()` | **미구현** — NOTIFY만 있고 recalc 없음 |

---

## 6. 각 화면의 시세 읽기 현황 (전환 완료)

**원칙: current_price, change_pct만 Redis로. name/market/leverage 등 메타데이터는 DB 유지.**

| 파일 | 변경 내용 |
|------|---------|
| `db.py` `get_usd_krw()` | Redis `usd_krw` key + `prices['USDKRW=X']` 읽기. Redis 미연결 또는 키 없으면 `RuntimeError` 발생 |
| `dashboard.py` `_load_summary_data()` | `get_all_prices()` → usd_krw, ndx100, 종목별 price 매핑. DB는 positions/accounts/daily_summary 메타/이력만 조회 |
| `dashboard.py` `_load_position_data()` | `get_all_prices()` → price 매핑. DB는 ticker/name/market/leverage/quantity만 조회 |
| `portfolio.py` `load_portfolio()` | `get_all_prices()` → price, change_pct 매핑. DB는 메타+수량. `get_usd_krw()`로 환율 |
| `accounts_DAL.py` `fetch_accounts_summary()` | SQL 서브쿼리 제거 → Python에서 prices 매핑 후 평가액 직접 계산. `get_usd_krw()` 경유 |
| `accounts_DAL.py` `fetch_account_details()` | prices 매핑 + Python 정렬 (SQL ORDER BY 대체). 평가액 기준 정렬 로직 Python으로 재구현 |
| `settings.py` `ticker_list` | `get_all_prices()` → price, change_pct 매핑. 메타데이터는 DB |
| `history_DAL.py` `load_history()` | DB에서 daily_summary 전체 조회 후, Redis `today_row`를 마지막 행으로 append. DB 마지막 날짜가 오늘이면 중복 방지로 스킵 |
| `history.py` `_save_cash_flow()` | 오늘 날짜: Redis에만 저장 후 `recalc_today_row()`. 과거 날짜: DB UPDATE 후 TWR 재계산 (기존 로직) |

---

## 7. import 경로 규칙

`common/` 폴더는 프로젝트 루트에 위치.

- `app/` 진입: Shiny가 `/home/ubuntu/asset-cloud/`에서 실행되므로 `common.redis_store` 자동 접근 가능
- `scheduler/` 진입: `price_updater_common.py` 상단에 sys.path 패치 적용
  ```python
  _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
  if _PROJECT_ROOT not in sys.path:
      sys.path.insert(0, _PROJECT_ROOT)
  ```
- `common/redis_store.py` 자체에도 동일 패치 적용 — `recalc_today_row()` 내부에서 `from app.db import ...`, `from app.utils.metrics import ...` 호출 시 필요
- `daily_inserter.py`도 동일 패턴 (PROJECT_ROOT → sys.path)

**새 scheduler 파일에서 common을 import할 경우 동일한 sys.path 패치가 필요하다.**

---

## 8. 미완료 / 후속 작업

### WS 모드 recalc 미호출
`price_updater_ws.py`의 `yahoo_poll_task()`는 `_notify()` 호출만 있고 `recalc_today_row()`가 없다.
WS 모드에서는 `today_row`가 갱신되지 않아 히스토리 화면의 오늘 행이 stale해진다.
REST 모드 안정화 후 별도 작업 예정.

추가 위치: `yahoo_poll_task()` 내 `_notify()` 직전
```python
try:
    from common.redis_store import recalc_today_row
    recalc_today_row()
except Exception as e:
    log.warning(f"recalc_today_row 실패 (무시): {e}")
```

### Phase 2 (미착수)
- `_send_history_table`이 `@reactive.effect`로 탭 숨겨져도 실행됨 → `@reactive.event` 전환 검토
- 대시보드/history가 동일한 positions 데이터를 독립적으로 중복 조회 → 공유 reactive.calc 구조 검토
- `settings.py` `invalidate_later(60)` — 예측 불가한 타이밍의 화면 갱신 유발