# portfolio.py — 구조 요약

## 파일 위치
`app/modules/portfolio.py`

## 관련 파일
- `app/modules/accounts_components.py` — `render_ticker_row()` 공유
- `app/modules/components.py` — `render_summary_header()`, 포맷 유틸 공유
- `app/db.py` — `get_usd_krw()`
- `app/price_signal.py` — `price_signal`

---

## 역할
- 전체 계좌 통합 종목 뷰 (계좌 구분 없음)
- 종목별 평가액, 현재가, 등락률, 시장 상태 배지 표시
- 강제 시세 조회 버튼 (`↺`)

---

## 데이터 로드

### `load_portfolio()`
- `config.json`에서 db_password 로드 후 psycopg2 직접 접속
- positions 전체를 ticker 기준으로 GROUP BY 집계
- tickers JOIN으로 name, current_price, change_pct, market, leverage 조회
- `get_usd_krw()`로 USD/KRW 환율 조회
- 반환: `(rows, usd_rate, usd_chg)`
  - rows: `[(ticker, quantity, name, current_price, change_pct, market, leverage), ...]`

---

## UI

### `portfolio_ui()`
- 강제 시세 조회 confirm JS (`force-update-btn` 클릭 시)
- `ui.output_ui("portfolio_content")`

---

## Server

### `portfolio_server()`

#### `_do_force_update`
- `input.force_update` 이벤트
- `subprocess.Popen`으로 `price_updater.py --force` 실행

#### `portfolio_content` (render.ui)
- `price_signal.get()` 호출로 실시간 시세 갱신 시 자동 재실행
- `load_portfolio()` 호출
- 총자산 / 손익 / 손익률 계산 (Python에서 직접 합산)
  - KRW: quantity 그대로
  - USD: quantity × usd_rate
  - 미국주식(NAS/AMS/ARC): quantity × price × usd_rate
  - 국내주식: quantity × price
- 종목 정렬: 현금(KRW/USD) 하단, 나머지는 평가액 내림차순
- `render_summary_header()` — 상단 요약 헤더
- `render_ticker_row()` — 종목/현금 행 렌더링
  - pos_id=None으로 호출 (클릭 이벤트 없음)
  - 현금(KRW/USD)은 시장 상태 배지 없음
  - 종목은 현재가 + 등락률 표시, 색상 동일
- 우측 상단 강제 조회 버튼 (`force-update-btn`)

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
- 계좌/포트폴리오/계좌상세 상단 공통 요약 헤더
- label: 헤더 레이블 (예: "포트폴리오", "총자산", 계좌명)
- total_asset: 총자산 (원화)
- pnl / pnl_pct: 손익 금액 / 손익률
- usd_rate / usd_chg: 환율 및 등락률 (None이면 미표시)
- USD/KRW 환율은 우측 정렬, 11px, positive/negative 색상