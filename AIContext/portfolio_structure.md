# portfolio.py — 구조 요약

## 파일 위치
`app/modules/portfolio.py`

## 관련 파일
- `app/modules/components.py` — `fmt_krw`, `fmt_usd`, `fmt_pct`, `fmt_change` 포맷 유틸 공유
- `app/db.py` — `get_usd_krw()`, `get_config()`, `get_market_currency()`
- `app/price_signal.py` — `price_signal`
- `app/utils/display_diff.py` — `diff_display`
- `scheduler/price_updater_common.py` — `get_market_status()`

---

## 역할
- 전체 계좌 통합 종목 뷰 (계좌 구분 없음, `is_watch = false` 계좌만)
- 종목별 평가액, 현재가, 등락률, 시장 상태 배지 표시
- **평단가 · 수익률 표시** — `positions.avg_price` 기반, 평단 미입력 종목은 미표시
- 강제 시세 조회 버튼 (`↺`) — `interval != 0`일 때만 표시
- `render.ui` 없음 — `pf_init` / `pf_tick` 커스텀 메시지로 DOM 직접 패치

---

## 데이터 로드

### `load_portfolio()`
- `get_all_prices()` (Redis)로 시세 조회
- `get_usd_krw()`로 USD/KRW 환율 조회
- positions을 ticker 기준 GROUP BY 집계, tickers LEFT JOIN으로 name, market, leverage 조회
- `avg_price`: 계좌별 수량 가중평균 → `SUM(p.quantity * p.avg_price) / NULLIF(SUM(p.quantity), 0)`
  - 일부 포지션만 avg_price 있어도 NULL 포함분은 무시하고 계산됨
- `is_watch = false` 계좌만 포함
- `daily_summary` 최근 1건 조회 → `yesterday_total` (전일 총자산, 손익 계산용)
- 반환: `(rows, usd_rate, usd_chg, yesterday_total)`
  - rows: `[(ticker, qty, name, price, change_pct, market, leverage, avg_price), ...]` ← 8-tuple

---

## 헬퍼 함수 (모듈 레벨)

| 함수 | 설명 |
|------|------|
| `_ticker_to_id(ticker)` | 하이픈/캐럿 → 언더스코어 변환 (DOM id 안전화) |
| `_calc_amount(ticker, qty_f, price_f, market, usd_rate)` | KRW/USD/미국주식/국내주식 분기 평가액 계산 |
| `_sort_rows(rows, usd_rate)` | 현금(KRW/USD) 하단, 나머지 평가액 내림차순 |
| `_fmt_amount_short(amount)` | 포트폴리오 전용 축약 포맷 (1억+ → "X.X억원", 100만+ → "X만원") |
| `_build_row_skeleton(ticker, qty, name, market, leverage, usd_rate, avg_price)` | 골격 HTML. 종목 구성 변경 시 1회 전송 |
| `_build_tick_values(ticker, qty, name, price, chg_pct, market, leverage, usd_rate, avg_price)` | 시세 갱신 시 전송할 값 dict |

### `_build_row_skeleton()` 구조
- 현금(KRW): qty 영역 없음
- 현금(USD): qty 영역에 `fmt_usd(qty_f)` 고정 표시
- 종목: qty 영역에 span 3개 — `pf-qty-{tid}` · `pf-avgprice-{tid}` · `pf-pnlpct-{tid}` (구분자 `·`)

### `_build_tick_values()` 반환 dict

| key | 설명 |
|-----|------|
| `id` | `_ticker_to_id(ticker)` |
| `amount` | 원화 평가액 (`fmt_krw`) |
| `qty` | 수량 문자열 (소수점 있으면 `≈X.XX주`, 없으면 `Xg주`) |
| `price` | 현재가 문자열 |
| `chg` | 등락률 문자열 |
| `chg_css` | `positive` / `negative` |
| `avgprice` | 평단가 문자열 (avg_price > 0 이고 price > 0 일 때만, 아니면 `""`) |
| `pnlpct` | 수익률 문자열 (`+X.XX%` / `-X.XX%`, 조건 동일) |
| `pnlpct_css` | `positive` / `negative` |
| `status_dot` | `●` / `○` |
| `status_txt` | `Open` / `Pre` / `After` / `Closed` |
| `status_cls` | `status-open` / `status-pre` / `status-after` / `status-closed` |

---

## UI

### `portfolio_ui()`
- JS 커스텀 메시지 핸들러 인라인 포함
- `@render.ui` 없음 — `<div id="pf-ticker-list">` 정적 골격만 선언

### JS 커스텀 메시지 핸들러

| 핸들러 | 동작 |
|--------|------|
| `pf_init` | summary 반영, `pf-ticker-list` 골격 HTML 교체, `show_force_btn` 표시 제어, tick 값 즉시 반영 |
| `pf_tick` | summary + 변경된 ticker key만 DOM 패치 |

### `_applyOneTicker(t)` JS 패치 대상 DOM id

| id 패턴 | 내용 |
|---------|------|
| `pf-amount-{tid}` | 원화 평가액 |
| `pf-qty-{tid}` | 수량 |
| `pf-price-{tid}` | 현재가 |
| `pf-chg-{tid}` | 등락률 |
| `pf-avgprice-{tid}` | 평단가 |
| `pf-pnlpct-{tid}` | 수익률 (className도 갱신) |
| `pf-status-{tid}` | 시장 상태 배지 |

---

## Server

### `portfolio_server()`

#### 세션 상태
- `initialized: reactive.value(False)` — 첫 렌더 완료 여부
- `_last_tickers: list` — 이전 ticker 목록 (종목 구성 변경 감지용)
- `_last_display: dict` — 이전 표시값 (`diff_display` 기준)

#### `_send_update` (`@reactive.effect`, async)
- `price_signal.get()` 구독 → 시세 갱신 시 자동 실행
- `initialized` 후 비활성 탭이면 스킵
- `load_portfolio()` → 총 평가액 합산, 손익 계산, USD 환율 HTML 구성
- 종목 구성 변경 감지 (`current_tickers != _last_tickers`):
  - 변경됨 → `pf_init` 전송 (골격 + tick 값 동시 포함)
  - 동일 → `diff_display(current, _last_display)` → `pf_tick` 전송

#### `_show_force_modal` / `_do_force_update` / `_cancel_force_update`
- `input.force_update` → 확인/취소 모달 표시
- `input.force_confirm` → `subprocess.Popen`으로 `scheduler/price_updater.py --force` 실행
- `input.force_cancel` → 모달 닫기

---

## components.py — 포맷 유틸

### 파일 위치
`app/modules/components.py`

### 포맷 유틸 함수

| 함수 | 설명 | 반환 예시 |
|------|------|-----------|
| `fmt_krw(amount)` | 원화 포맷 | `"1,234,567원"` |
| `fmt_usd(amount)` | 달러 포맷 | `"$1,234.56"` |
| `fmt_pct(pct)` | 등락률 포맷 | `"+1.23%"` / `"-1.23%"` |
| `fmt_pnl(amount, pct)` | 손익 텍스트 + CSS 클래스 | `("+1,234원 (+1.23%)", "positive")` |
| `fmt_change(price, chg_pct, currency)` | 현재가 + 등락률 + CSS | `("1,234원", "+1.23%", "positive")` |