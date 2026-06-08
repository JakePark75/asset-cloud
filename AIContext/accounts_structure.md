# accounts.py — 구조 요약

### Reactive State

| 변수 | 타입 | 설명 |
|------|------|------|
| `selected_account` | `reactive.value(None)` | 선택된 계좌 ID. None이면 목록, 값 있으면 상세 화면 |
| `refresh` | `reactive.value(0)` | 증가시키면 main_view 강제 재렌더 |
| `show_modal` | `reactive.value(False)` | 계좌 추가 모달 표시 여부 |
| `show_modal_position` | `reactive.value(False)` | 종목 추가 모달 표시 여부 |
| `show_modal_cash` | `reactive.value(False)` | 현금 추가 모달 표시 여부 |
| `show_modal_edit_position` | `reactive.value(False)` | 종목 수정 모달 표시 여부 |
| `edit_position_id` | `reactive.value(None)` | 수정 중인 position id (종목) |
| `show_modal_edit_cash` | `reactive.value(False)` | 현금 수정 모달 표시 여부 |
| `edit_cash_id` | `reactive.value(None)` | 수정 중인 position id (현금) |

### DB 조회 함수

DB 조회 함수는 `accounts_DAL.py`로 분리됨.

#### `fetch_accounts_summary()` (accounts_DAL.py)
- 계좌 목록 + 총자산/현금/당일손익 집계
- accounts LEFT JOIN positions LEFT JOIN tickers
- 반환: `[(id, name, alias, total, cash, pnl, is_watch), ...]`
- cash: KRW + USD×환율 원화 합산
- pnl: 종목별 change_pct 기반, 미국주식 환율 반영
- is_watch: True면 감시 계좌 (총자산 합계에서 제외)

#### `fetch_account_details(account_id)` (accounts_DAL.py)
- 계좌명/별명/is_watch, 포지션 목록, USDKRW 환율 조회
- 반환: `(acc, positions, usd_rate)`
  - `acc`: `(name, alias, is_watch)`
  - `positions`: `[(id, ticker, quantity, name, current_price, change_pct, market, leverage), ...]`
  - `usd_rate`: float (Decimal → float 변환 적용)
- 정렬: 종목 먼저/현금 하단 → 시장별(KR→미국→CRYPTO→나머지) → 레버리지 내림차순 → 평가액 내림차순 → 티커 알파벳순

#### `get_usd_krw()` (db.py)
- USDKRW=X 티커의 current_price, change_pct 조회
- 반환: `(usd_rate: float, usd_chg: float)` 또는 `(None, None)`
- 다른 화면에서도 재사용 가능

### UI 렌더러 (output_ui)

#### `main_view`
- `price_signal.get()` 호출로 시세 갱신 시 자동 재실행
- `selected_account()`가 None이면 계좌 목록, 값 있으면 계좌 상세
- **계좌 목록**: `fetch_accounts_summary()` → `render_asset_card()` 반복, onclick으로 `selected_id` input 세팅
  - 카드 목록 상단: 전체 총자산/일간손익 요약 (total-summary), **감시 계좌 제외**하고 합산
  - 일간손익 표시: ▲/▼ + 금액 + 수익률(%), 투자금(총자산-현금) 대비
  - **일반 계좌 섹션** + **"감시 계좌" 섹션** 분리 표시 (감시 계좌 있을 때만 섹션 노출)
- **계좌 상세**: `fetch_account_details()` → `render_ticker_row()` 반복, 현금/종목 분기 렌더
  - 상단 타이틀바: ‹ 아이콘(좌측, `btn_back`) + 계좌명(중앙), `detail-titlebar` 클래스
  - 타이틀바 아래: 해당 계좌 총자산/일간손익 요약 (`total-summary`), positions 루프 돌며 Python에서 합산
  - 중단: positions 루프 → `.ticker-row` (ticker-name / ticker-qty / ticker-amount / ticker-change), onclick으로 `edit_pos_id` input 세팅
  - 하단: 종목추가(`btn_add_position`), 현금추가(`btn_add_cash`), 계좌삭제(`btn_delete_account`, `btn-account-delete-bottom` 클래스) 버튼
- 계좌 목록 상단 총자산 요약(total-summary)에 USD/KRW 환율 및 등락률 표시
  - `get_usd_krw()`로 환율/등락률 조회
  - 일간손익과 같은 줄 우측 정렬, 11px
  - "USD/KRW" 레이블은 #888888, 숫자/등락률은 positive/negative 색상

#### `modal_add_account`
- `show_modal()` True일 때 렌더
- `modal_add_account_ui(ns)` 호출 (accounts_modals.py)
- input: `new_account_name`, `new_account_alias`, `new_account_is_watch` (체크박스: "감시 계좌 (내 자산 아님)")
- 닫기: `modal_close`

#### `modal_add_position`
- `show_modal_position()` True일 때 렌더
- `modal_add_position_ui(ns)` 호출 (accounts_modals.py)
- input: `new_position_name`, `new_position_ticker`, `new_position_market`, `new_position_leverage`, `new_position_qty`
- 닫기: `modal_position_close`

#### `modal_add_cash`
- `show_modal_cash()` True일 때 렌더
- `modal_add_cash_ui(ns)` 호출 (accounts_modals.py)
- input: `new_cash_type`, `new_cash_amount`
- 닫기: `modal_cash_close`

#### `modal_edit_position`
- `show_modal_edit_position()` True일 때 렌더
- `fetch_account_details()`로 positions 조회 후 해당 pos_id 행에서 기존값 추출
- `modal_edit_position_ui(ns, ticker, name, market, leverage, qty)` 호출 (accounts_modals.py)
- 티커는 읽기전용 표시
- input: `edit_position_name`, `edit_position_market`, `edit_position_leverage`, `edit_position_qty`
- 저장: `btn_confirm_edit_position`, 삭제: `confirm_delete_position` (JS confirm 후 세팅)
- 닫기: `modal_edit_position_close`
- 삭제 버튼: modal-box 안 하단, `btn-modal-delete-bottom` 클래스

#### `modal_edit_cash`
- `show_modal_edit_cash()` True일 때 렌더
- `fetch_account_details()`로 positions 조회 후 해당 pos_id 행에서 기존값 추출
- `modal_edit_cash_ui(ns, ticker, amount)` 호출 (accounts_modals.py)
- input: `edit_cash_type`, `edit_cash_amount`
- 저장: `btn_confirm_edit_cash`, 삭제: `confirm_delete_cash` (JS confirm 후 세팅)
- 닫기: `modal_edit_cash_close`
- 삭제 버튼: modal-box 안 하단, `btn-modal-delete-bottom` 클래스

### 이벤트 핸들러

| 핸들러 함수 | 트리거 | 동작 |
|---|---|---|
| `handle_card_click` | `input.selected_id` | `selected_account` 세팅 |
| `open_modal` | `btn_add_account` | `show_modal = True` |
| `close_modal` | `modal_close` | `show_modal = False` |
| `add_account` | `btn_confirm_add` | accounts INSERT (name/alias/is_watch), 모달 닫기, refresh |
| `go_back` | `btn_back` | `selected_account = None`, refresh |
| `open_modal_position` | `btn_add_position` | `show_modal_position = True` |
| `close_modal_position` | `modal_position_close` | `show_modal_position = False` |
| `open_modal_cash` | `btn_add_cash` | `show_modal_cash = True` |
| `close_modal_cash` | `modal_cash_close` | `show_modal_cash = False` |
| `add_position` | `btn_confirm_add_position` | tickers 없으면 INSERT (market/leverage 반영), positions INSERT, refresh |
| `add_cash` | `btn_confirm_add_cash` | positions INSERT (ticker=KRW/USD), refresh |
| `delete_account` | `confirm_delete_account` | accounts DELETE (CASCADE로 positions 자동 삭제), `selected_account = None`, refresh |
| `handle_edit_pos_click` | `input.edit_pos_id` | ticker가 KRW/USD면 `edit_cash_id` + `show_modal_edit_cash`, 아니면 `edit_position_id` + `show_modal_edit_position` |
| `close_modal_edit_position` | `modal_edit_position_close` | `show_modal_edit_position = False` |
| `edit_position` | `btn_confirm_edit_position` | positions UPDATE (quantity), tickers UPDATE (name/market/leverage), refresh |
| `delete_position` | `confirm_delete_position` | positions DELETE, refresh |
| `close_modal_edit_cash` | `modal_edit_cash_close` | `show_modal_edit_cash = False` |
| `edit_cash` | `btn_confirm_edit_cash` | positions UPDATE (ticker/quantity), refresh |
| `delete_cash` | `confirm_delete_cash` | positions DELETE, refresh |

### 비고
- 계좌/종목/현금 삭제 시 JS `confirm()`으로 확인 후 Shiny input 세팅하는 방식 사용
- tickers.ticker는 PK라 티커 변경 불가 — 수정 모달에서 읽기전용 표시만
- `start_signal_listener(db_password)`를 server 함수 진입부에서 호출 (`price_signal.py` 연동)
- DB NUMERIC 컬럼(current_price, change_pct, quantity 등)은 psycopg2가 Decimal로 반환 — float() 변환 후 사용
- 모달 UI 함수들은 `accounts_modals.py`로 분리 (`modal_add_account_ui`, `modal_add_position_ui`, `modal_add_cash_ui`, `modal_edit_position_ui`, `modal_edit_cash_ui`)
- 포맷 유틸 및 공통 헤더는 `components.py`로 분리 (`fmt_krw`, `fmt_usd`, `fmt_pct`, `fmt_pnl`, `fmt_change`, `render_summary_header`)
  - `fmt_change(price, chg_pct, currency)`: 현재가 + 등락률 + CSS 클래스 반환 → `render_ticker_row`에서 사용
  - `render_summary_header()`: 계좌목록/계좌상세/포트폴리오 공통 상단 요약 헤더
  - `render_ticker_row`: 종목 행 우측 하단에 현재가 + 등락률 표시
    - 현재가는 거래 화폐단위 그대로 표시 (KRW종목→원화, 미국주식→USD)
    - 현재가 색상은 등락률과 동일 (positive/negative)
    - 현금(KRW/USD)은 시장 상태 배지 표시 안 함
    - 종목은 `get_market_status()`로 4종류 배지: 장중(녹)/프리(보라)/애프터(주황)/휴장(회색)
---