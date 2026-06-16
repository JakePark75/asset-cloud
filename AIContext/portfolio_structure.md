# portfolio.py — 구조 요약

## 파일 위치
`app/modules/portfolio.py`

## 관련 파일
- `app/modules/components.py` — `build_ticker_row_skeleton`, `build_ticker_row_values`, 포맷 유틸 공유
- `app/db.py` — `get_usd_krw()`, `get_config()`, `get_market_currency()`
- `app/price_signal.py` — `price_signal`, `position_signal`, `ticker_signal`
- `app/utils/display_diff.py` — `diff_display`
- `scheduler/price_updater_common.py` — `get_market_status()`
- `common/redis_store.py` — `get_all_prices()`

---

## 역할
- 전체 계좌 통합 종목 뷰 (`is_watch = false` 계좌만)
- 종목 클릭 시 드릴다운 뷰 — 해당 종목 보유 계좌 목록 표시
- 종목별 평가액, 현재가, 등락률, 시장 상태 배지, 평단가, 수익률 표시
- 강제 시세 조회 버튼 (`↺`) — `interval != 0`일 때만 표시
- `render.ui` 없음 — 커스텀 메시지로 DOM 직접 패치

---

## DAL 함수 (모듈 레벨)

### `load_portfolio(db_rows, yesterday_total)`
- Redis `get_all_prices()`로 시세 주입
- `db_rows`: `_db_portfolio_rows()` 결과 (8-tuple: ticker, qty, name, market, leverage, avg_price)
- 반환: `(rows, yesterday_total)`
  - rows: `[(ticker, qty, name, price, change_pct, market, leverage, avg_price), ...]`

### `load_ticker_accounts(ticker, db_rows, usd_rate)`
- 특정 ticker 보유 계좌 목록에 Redis 시세 주입
- `db_rows`: `_db_ticker_accounts()` 결과
- 반환: `(acc_rows, price, chg_pct)`
  - acc_rows: `[(acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, price, chg_pct, ticker_name), ...]` ← 11-tuple

---

## 헬퍼 함수 (모듈 레벨)

| 함수 | 설명 |
|------|------|
| `_ticker_to_id(ticker)` | 하이픈/캐럿 → 언더스코어 (DOM id 안전화) |
| `_calc_amount(ticker, qty_f, price_f, market, usd_rate)` | KRW/USD/미국주식/국내주식 분기 평가액 계산 |
| `_sort_rows(rows, usd_rate)` | 현금(KRW/USD) 하단, 나머지 평가액 내림차순 |
| `_build_pf_row_skeleton(ticker, qty, name, market, leverage, avg_price)` | 포트폴리오 종목 행 골격 HTML |
| `_build_pf_tick_values(ticker, qty, name, price, chg_pct, market, leverage, usd_rate, avg_price)` | 포트폴리오 종목 tick 값 dict |
| `_build_drilldown_row_skeleton(acc_id, ticker, acc_name, alias, is_watch, qty, avg_price, market, leverage)` | 드릴다운 계좌 행 골격 HTML |
| `_build_drilldown_row_values(acc_id, ticker, qty, avg_price, price, chg_pct, market, leverage, usd_rate)` | 드릴다운 계좌 행 tick 값 dict |

### `_build_pf_row_skeleton()` 구조
- `build_ticker_row_skeleton(ticker=ticker, ...)` 호출 — `is_cash`는 내부에서 ticker 기반 판단
- 현금(KRW): qty 영역 없음
- 현금(USD): qty 영역에 `fmt_usd(qty_f)` 고정 표시
- 종목: qty_fixed=None → span 3개 (`pf-qty`, `pf-avgprice`, `pf-pnlpct`) tick에서 채움

### `_build_drilldown_row_skeleton()` 구조
- `build_ticker_row_skeleton(ticker=ticker, ...)` 호출 — ticker 원천값 기반 is_cash 판단
- 드릴다운은 항상 일반 종목 (현금 포지션 없음)
- qty_fixed: 수량 고정 표시, avgprice/pnlpct는 span으로 tick에서 채움

### tick 값 dict 공통 키

| key | 설명 |
|-----|------|
| `id` | DOM id용 식별자 (포트폴리오: `_ticker_to_id(ticker)`, 드릴다운: `str(acc_id)`) |
| `amount` | 원화 평가액 |
| `qty` | 수량 문자열 (포트폴리오만, 드릴다운은 골격 고정) |
| `price` | 현재가 문자열 |
| `chg` | 등락률 문자열 |
| `chg_css` | `positive` / `negative` |
| `avgprice` | 평단가 문자열 (avg_price > 0, price > 0 조건) |
| `pnlpct` | 수익률 문자열 |
| `pnlpct_css` | `positive` / `negative` |
| `status_dot` / `status_txt` / `status_cls` | 시장 상태 배지 |

---

## UI (`portfolio_ui()`)

### 뷰 구조
- **포트폴리오 목록 뷰** (`#pf-list-view`): 종목 목록
- **드릴다운 뷰** (`#pf-drilldown-view`): 종목별 보유 계좌 목록, 기본 `display:none`
- 공통 헤더: `#pf-total-asset`, `#pf-pnl`, `#pf-usd-wrap`, 뒤로가기 버튼 `#pf-back-btn`

### JS 커스텀 메시지 핸들러

| 핸들러 | 동작 |
|--------|------|
| `pf_init` | summary 반영, `pf-ticker-list` 골격 교체, force 버튼 제어, tick 값 반영 |
| `pf_tick` | summary + 변경된 ticker만 DOM 패치 |
| `pfd_init` | 드릴다운 뷰 표시, 계좌 목록 골격 교체, tick 값 반영 |
| `pfd_tick` | summary + 변경된 계좌 행만 DOM 패치 |

### JS 전역 함수

| 함수 | 설명 |
|------|------|
| `pfOpenTickerDrilldown(ticker, name)` | 종목 클릭 시 `portfolio-ticker_clicked` 이벤트 발송 |
| `pfGoBack()` | 뒤로가기 — 목록 뷰 복원, `portfolio-go_back` 이벤트 발송 |

### `_applyOneTicker(t)` 패치 DOM id

| id 패턴 | 내용 |
|---------|------|
| `pf-amount-{tid}` | 원화 평가액 |
| `pf-qty-{tid}` | 수량 |
| `pf-price-{tid}` | 현재가 |
| `pf-chg-{tid}` | 등락률 |
| `pf-avgprice-{tid}` | 평단가 |
| `pf-pnlpct-{tid}` | 수익률 |
| `pf-status-{tid}` | 시장 상태 배지 |

### `_applyOneDrilldownRow(r)` 패치 DOM id

| id 패턴 | 내용 |
|---------|------|
| `pfd-amount-{acc_id}` | 원화 평가액 |
| `pfd-price-{acc_id}` | 현재가 |
| `pfd-chg-{acc_id}` | 등락률 |
| `pfd-avgprice-{acc_id}` | 평단가 |
| `pfd-pnlpct-{acc_id}` | 수익률 |
| `pfd-status-{acc_id}` | 시장 상태 배지 |

---

## Server (`portfolio_server()`)

### Reactive State

| 변수 | 타입 | 설명 |
|------|------|------|
| `initialized` | `reactive.value(False)` | 첫 렌더 완료 여부 |
| `selected_ticker` | `reactive.value(None)` | None: 목록 뷰, str: 드릴다운 뷰 |
| `_last_tickers` | list | 이전 종목 목록 (구성 변경 감지) |
| `_last_display` | dict | 이전 표시값 (diff 기준) |
| `_last_dd_accounts` | list | 이전 드릴다운 계좌 목록 |
| `_last_dd_display` | dict | 이전 드릴다운 표시값 |

### DB 캐시 (`@reactive.calc`)

| calc | 구독 신호 | 설명 |
|------|-----------|------|
| `_db_portfolio_rows()` | `position_signal`, `ticker_signal` | positions + tickers JOIN |
| `_db_yesterday_total()` | `daily_insert_signal` | daily_summary 최신 1행 |
| `_db_ticker_accounts()` | `position_signal`, `ticker_signal`, `selected_ticker` | 선택 ticker 보유 계좌 목록 (t.name 포함) |

### `_send_update` (`@reactive.effect`, async)
- `price_signal`, `position_signal`, `ticker_signal` 구독
- `initialized` 후 비활성 탭이면 스킵
- `get_usd_krw()` — Redis 조회 (매 tick)
- 목록 뷰: 구성 변경 → `pf_init`, 동일 → `diff_display` → `pf_tick`
- 드릴다운 뷰: 구성 변경 → `pfd_init`, 동일 → `diff_display` → `pfd_tick`

### 이벤트 핸들러

| 핸들러 | 트리거 | 동작 |
|--------|--------|------|
| `_handle_ticker_click` | `ticker_clicked` | `selected_ticker` 세팅, 드릴다운 상태 초기화 |
| `_handle_go_back` | `go_back` | `selected_ticker = None`, 목록 상태 초기화 |
| `_show_force_modal` | `force_update` | 확인/취소 모달 표시 |
| `_do_force_update` | `force_confirm` | `price_updater.py --force` 실행 |
| `_cancel_force_update` | `force_cancel` | 모달 닫기 |

---

## components.py — 공통 컴포넌트

### `build_ticker_row_skeleton(ticker, display_name, market, leverage, id_prefix, row_id, qty_fixed, onclick_attr, data_attrs)`
- `is_cash = ticker in ('KRW', 'USD')` — 내부에서 판단 (호출자가 전달하지 않음)
- `qty_fixed=None`: qty/avgprice/pnlpct 모두 span (portfolio)
- `qty_fixed=str`: qty 고정, avgprice/pnlpct는 span (accounts/드릴다운)
- `qty_fixed=""`: 수량 영역 없음 (KRW 현금)

### `build_ticker_row_values(ticker, amount, qty, price, chg_pct, market, avg_price, id_prefix, row_id, get_market_currency_fn, get_market_status_fn, qty_in_values)`
- `is_cash = ticker in ('KRW', 'USD')` — 내부에서 판단
- `get_market_currency_fn(market)` — 함수 상단에서 1회만 호출
- `qty_in_values=True`: 수량 값 포함 (portfolio), `False`: 제외 (accounts/드릴다운)