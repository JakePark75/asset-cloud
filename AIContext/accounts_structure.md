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

#### `load_accounts()`
- 계좌 목록 + 총자산/현금/당일손익 집계
- accounts LEFT JOIN positions LEFT JOIN tickers
- 반환: `[(id, name, alias, total_asset, cash, daily_pnl), ...]`

#### `load_positions(acc_id)`
- 계좌명/별명, 포지션 목록, USDKRW 환율 조회
- positions LEFT JOIN tickers, KRW/USD 현금은 상단 정렬
- 반환: `(acc, positions, usd_rate)`
  - `acc`: `(name, alias)`
  - `positions`: `[(id, ticker, quantity, name, current_price, change_pct, market), ...]`
  - `usd_rate`: float

### UI 렌더러 (output_ui)

#### `main_view`
- `price_signal.get()` 호출로 시세 갱신 시 자동 재실행
- `selected_account()`가 None이면 계좌 목록, 값 있으면 계좌 상세
- **계좌 목록**: `load_accounts()` → `.asset-card` div 반복, onclick으로 `selected_id` input 세팅
- **계좌 상세**: `load_positions()` → `.ticker-row` div 반복, 현금/종목 분기 렌더
  - 상단: 뒤로가기(`btn_back`), 계좌삭제(`btn_delete_account`), 계좌명
  - 중단: positions 루프 → `.ticker-row` (ticker-name / ticker-qty / ticker-amount / ticker-change), onclick으로 `edit_pos_id` input 세팅
  - 하단: 종목추가(`btn_add_position`), 현금추가(`btn_add_cash`) 버튼

#### `modal_add_account`
- `show_modal()` True일 때 렌더
- input: `new_account_name`, `new_account_alias`
- 닫기: `modal_close`

#### `modal_add_position`
- `show_modal_position()` True일 때 렌더
- input: `new_position_name`, `new_position_ticker`, `new_position_market`, `new_position_leverage`, `new_position_qty`
- 닫기: `modal_position_close`

#### `modal_add_cash`
- `show_modal_cash()` True일 때 렌더
- input: `new_cash_type`, `new_cash_amount`
- 닫기: `modal_cash_close`

#### `modal_edit_position`
- `show_modal_edit_position()` True일 때 렌더
- DB에서 해당 position 조회 후 기존값으로 초기화
- 티커는 읽기전용 표시
- input: `edit_position_name`, `edit_position_market`, `edit_position_leverage`, `edit_position_qty`
- 저장: `btn_confirm_edit_position`, 삭제: `confirm_delete_position` (JS confirm 후 세팅)
- 닫기: `modal_edit_position_close`

#### `modal_edit_cash`
- `show_modal_edit_cash()` True일 때 렌더
- DB에서 해당 position 조회 후 기존값으로 초기화
- input: `edit_cash_type`, `edit_cash_amount`
- 저장: `btn_confirm_edit_cash`, 삭제: `confirm_delete_cash` (JS confirm 후 세팅)
- 닫기: `modal_edit_cash_close`

### 이벤트 핸들러

| 핸들러 함수 | 트리거 | 동작 |
|---|---|---|
| `handle_card_click` | `input.selected_id` | `selected_account` 세팅 |
| `open_modal` | `btn_add_account` | `show_modal = True` |
| `close_modal` | `modal_close` | `show_modal = False` |
| `add_account` | `btn_confirm_add` | accounts INSERT, 모달 닫기, refresh |
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
- `start_signal_listener(db_password)` 를 server 함수 진입부에서 호출 (`price_signal.py` 연동)

---
