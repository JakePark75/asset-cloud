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
- 강제 시세 조회 버튼 (`↺`) — `interval != 0`일 때만 표시
- `render.ui` 없음 — `pf_init` / `pf_tick` 커스텀 메시지로 DOM 직접 패치
- `render_summary_header()`, `render_ticker_row()` 미사용 (자체 골격 HTML 생성)

---

## 데이터 로드

### `load_portfolio()`
- `get_all_prices()` (Redis)로 시세 조회 — tickers DB의 current_price/change_pct 미사용
- `get_usd_krw()`로 USD/KRW 환율 조회
- positions을 ticker 기준 GROUP BY 집계, tickers LEFT JOIN으로 name, market, leverage 조회
- `is_watch = false` 계좌만 포함
- `daily_summary` 최근 1건 조회 → `yesterday_total` (전일 총자산, 손익 계산용)
- 반환: `(rows, usd_rate, usd_chg, yesterday_total)`
  - rows: `[(ticker, quantity, name, price, change_pct, market, leverage), ...]` (price/change_pct는 Redis 값)

---

## UI

### `portfolio_ui()`
- JS 커스텀 메시지 핸들러 인라인 포함:
  - `pf_init`: 종목 구성 변경 시 골격 HTML 통째 교체 + tick 값 즉시 반영
  - `pf_tick`: 변경된 key만 DOM 패치 (summary + ticker별)
- `@render.ui` 없음 — `<div id="pf-ticker-list">` 등 정적 골격만 선언, 내용은 서버 메시지로 주입
- `ui.output_ui` 없음

---

## Server

### `portfolio_server()`

#### 세션 상태
- `_last_tickers: list` — 이전 ticker 목록 (종목 구성 변경 감지용)
- `_last_display: dict` — 이전 표시값 (`diff_display` 기준)

#### `_show_force_modal` / `_do_force_update` / `_cancel_force_update`
- `input.force_update` → 확인/취소 모달 표시
- `input.force_confirm` → `subprocess.Popen`으로 `price_updater.py --force` 실행
- `input.force_cancel` → 모달 닫기

#### `_send_update` (`@reactive.effect`, async)
- `price_signal.get()` 구독 → 시세 갱신 시 자동 실행
- 탭 비활성 시 스킵
- `load_portfolio()` → 총 평가액 합산, 손익(`total_pnl = total_asset - yesterday_total`), USD 환율 HTML 구성
- **종목 구성 변경 감지** (`current_tickers != _last_tickers`):
  - 변경됨 → `_build_row_skeleton()` 으로 골격 HTML 생성 → `pf_init` 전송 (골격 + tick 값 동시 포함)
  - 동일 → `diff_display(current, _last_display)` → 변경된 key만 `pf_tick` 전송
- `show_force_btn`: `config.json`의 `interval != 0` 이면 표시

#### 헬퍼 함수 (모듈 레벨)
- `_ticker_to_id(ticker)` — 하이픈/캐럿 → 언더스코어 변환 (DOM id 안전화)
- `_calc_amount(ticker, qty_f, price_f, market, usd_rate)` — KRW/USD/미국주식/국내주식 분기 평가액 계산
- `_sort_rows(rows, usd_rate)` — 현금(KRW/USD) 하단, 나머지 평가액 내림차순
- `_build_row_skeleton(...)` → 골격 HTML (가변값은 id 달린 빈 span)
- `_build_tick_values(...)` → 시세 갱신 시 전송할 값 dict (`id, amount, price, chg, chg_css, status_dot, status_txt, status_cls`)

---

## components.py — 포맷 유틸 및 공통 헤더

### 파일 위치
`app/modules/components.py`

### 포맷 유틸 함수

| 함수 | 설명 | 반환 예시 |
|------|------|-----------|
| `fmt_krw(amount)` | 원화 포맷 | `"1,234,567원"` |
| `fmt_usd(amount)` | 달러 포맷 | `"$1,234.56"` |
| `fmt_pct(pct)` | 등락률 포맷 | `"+1.23%"` / `"-1.23%"` |
| `fmt_pnl(amount, pct, currency)` | 손익 텍스트 + CSS 클래스 | `("+1,234원 (+1.23%)", "positive")` |
| `fmt_change(price, chg_pct, currency)` | 현재가 + 등락률 + CSS | `("1,234원", "+1.23%", "positive")` |

### `render_summary_header(label, total_asset, pnl, pnl_pct, usd_rate, usd_chg)`
- 계좌/계좌상세 상단 공통 요약 헤더 (portfolio.py에서는 **미사용** — portfolio는 자체 HTML로 직접 구성)
- label: 헤더 레이블 (예: "총자산", 계좌명)
- total_asset: 총자산 (원화)
- pnl / pnl_pct: 손익 금액 / 손익률
- usd_rate / usd_chg: 환율 및 등락률 (None이면 미표시)
- USD/KRW 환율은 우측 정렬, 11px, positive/negative 색상