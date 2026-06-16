# accounts — 구조 요약

## 파일 구성

| 파일 | 역할 |
|------|------|
| `app/modules/accounts.py` | module UI/server 뼈대, JS 핸들러, 이벤트 핸들러 |
| `app/modules/accounts_helpers.py` | skeleton/values 빌더 함수 |
| `app/modules/accounts_modals.py` | 종목 수정 모달 HTML + JS |
| `app/modules/accounts_DAL.py` | DB 조회/수정 함수 (fetch, execute_buy, execute_sell) |
| `app/static/accounts.css` | accounts 전용 스타일 |

---

## DB 스키마 변경 이력
- `positions.avg_price NUMERIC DEFAULT NULL` 컬럼 추가 (평단가 관리)

---

## accounts_helpers.py

### 함수 목록

| 함수 | 반환 | 설명 |
|------|------|------|
| `_ticker_to_id(ticker)` | str | 하이픈/캐럿/등호 → 언더스코어 (DOM id 안전화) |
| `_build_account_card_skeleton(acc, ns_str)` | str (HTML) | 계좌 카드 골격. 구성 변경 시 1회 전송 |
| `_build_account_card_values(acc)` | dict | 계좌 카드 가변값 (total, pnl_text, pnl_class, cash) |
| `_build_position_row_skeleton(pos, ns_str)` | str (HTML) | 종목 행 골격. `data-avg-price` 속성 포함. `build_ticker_row_skeleton(ticker=ticker, ...)` 호출 |
| `_build_position_row_values(pos, usd_rate)` | dict | 종목 행 가변값. `build_ticker_row_values(ticker=ticker, ...)` 호출 |
| `_build_summary_html(label, total, pnl, pnl_pct, usd_rate, usd_chg)` | dict | summary 헤더 값 dict |

### 튜플 구조
- `acc` 튜플 (7): `(id, name, alias, total, cash, is_watch, prev_total)`
- `pos` 튜플 (9): `(pos_id, ticker, qty, name, price, change_pct, market, leverage, avg_price)`

### is_cash 판단
- `build_ticker_row_skeleton`, `build_ticker_row_values` 모두 `ticker` 파라미터를 받아 내부에서 `is_cash = ticker in ('KRW', 'USD')` 판단
- 호출자(`_build_position_row_skeleton` 등)는 `is_cash`를 전달하지 않음

---

## accounts_modals.py

### 함수

| 함수 | 반환 | 설명 |
|------|------|------|
| `modal_edit_position_html(market_options)` | `ui.Tag` | 종목 수정 모달 HTML (탭 3개: 정보/매수/매도) |
| `modal_edit_position_js()` | str | 모달 전용 JS 문자열 |

### 모달 구조
- 탭 3개: **정보** / **매수** / **매도**
- 정보 탭: 종목명 / 시장 / 레버리지 / 수량 / 평균단가 직접 입력 (미입력 시 기존값 유지) / 저장 / 삭제
- 매수 탭: 수량 + 단가 입력 → 실시간 미리보기 (매수 후 평단, 수량, 현금 차감액)
- 매도 탭: 수량 + 단가 입력 → 실시간 미리보기 (매도 후 수량, 현금 가산액, 실현손익)

### JS 전역 함수

| 함수 | 설명 |
|------|------|
| `acOpenEditPositionModal(el)` | 종목 row 클릭 시 `data-*` 읽어 필드 채움, 정보 탭으로 초기화 |
| `acSwitchTab(tab)` | 탭 전환 + 입력값/미리보기 초기화 |
| `acUpdateBuyPreview()` | 매수 수량/단가 입력 시 실시간 미리보기 갱신 |
| `acUpdateSellPreview()` | 매도 수량/단가 입력 시 실시간 미리보기 갱신 |
| `acTriggerEditPositionSave()` | 정보 탭 저장 이벤트 |
| `acTriggerBuy()` | 매수 확인 이벤트 |
| `acTriggerSell()` | 매도 확인 이벤트 |
| `acTriggerPositionDelete()` | 종목 삭제 confirm + 이벤트 |

---

## accounts_DAL.py

### fetch 함수

#### `fetch_accounts_summary()`
- 반환: `[(id, name, alias, total, cash, is_watch, prev_total), ...]`
- Redis `get_all_prices()` + DB 조회 → Python에서 평가액 계산

#### `fetch_account_details(account_id)`
- 반환: `(acc, positions, usd_rate)`
  - `acc`: `(name, alias, is_watch, prev_total_asset)`
  - `positions`: `[(pos_id, ticker, qty, name, price, change_pct, market, leverage, avg_price), ...]` ← 9-tuple
  - `usd_rate`: float
- 정렬: 현금 후순위 → 마켓 순서(KR→USD마켓→CRYPTO→나머지) → leverage DESC → 평가액 DESC → ticker ASC

### 매수/매도 함수

#### `execute_buy(pos_id, qty_delta, trade_price, usd_markets)`
- 가중평균 평단 재계산
- `positions.quantity += qty_delta`, `positions.avg_price = new_avg`
- 마켓 통화 기준 현금 차감

#### `execute_sell(pos_id, qty_delta, trade_price, usd_markets)`
- 평단 변동 없음
- `positions.quantity -= qty_delta`, 현금 가산
- 보유 수량 초과 시 `ValueError`

---

## accounts.py

### Reactive State

| 변수 | 타입 | 설명 |
|------|------|------|
| `initialized` | `reactive.value(False)` | 첫 렌더 완료 여부 |
| `selected_account` | `reactive.value(None)` | None: 목록, 값 있으면 상세 |
| `refresh` | `reactive.value(0)` | 증가 시 DB 캐시 무효화 + `_send_update` 재실행 |

### DB 캐시 (`@reactive.calc`)

| calc | 구독 신호 | 설명 |
|------|-----------|------|
| `_db_accounts()` | `refresh` | `fetch_accounts_summary()` 캐시 |
| `_db_detail()` | `refresh`, `selected_account` | `fetch_account_details(acc_id)` 캐시. acc_id=None이면 None 반환 |

- `price_signal`/`daily_insert_signal`에는 반응하지 않음 → 시세 갱신 시 DB 재조회 없음
- `refresh.set()`은 이벤트 핸들러(매수/매도/추가/삭제 등)에서만 호출

### UI 구조 (`accounts_ui()`)
- `@render.ui` 없음 — 모든 갱신은 커스텀 메시지로 DOM 직접 패치
- 모달 전체가 정적 DOM으로 상주 (JS `acShowModal()` / `acHideModal()`로 display 제어)

### JS 커스텀 메시지 핸들러

| 핸들러 | 동작 |
|--------|------|
| `ac_list_init` | summary 반영, 계좌 목록 골격 HTML 교체, 카드 값 반영, 상세 숨김 |
| `ac_list_tick` | summary + 변경된 카드 값만 DOM 패치 |
| `ac_detail_init` | summary 반영, 종목 목록 골격 HTML 교체, 종목 값 반영, 목록 숨김 |
| `ac_detail_tick` | summary + 변경된 종목 값만 DOM 패치 |
| `ac_ticker_lookup_result` | 티커 자동조회 결과 → 종목명/시장 필드 자동 채움 |

### `_applyOnePosition()` 패치 DOM id

| id 패턴 | 내용 |
|---------|------|
| `ac-amount-{pos_id}` | 원화 평가액 |
| `ac-price-{pos_id}` | 현재가 |
| `ac-chg-{pos_id}` | 등락률 |
| `ac-avgprice-{pos_id}` | 평단가 |
| `ac-pnlpct-{pos_id}` | 수익률 |
| `ac-status-{pos_id}` | 시장 상태 배지 |
- data-* 속성 갱신: `amountEl.closest('[data-pos-id]')` 로 wrapper 탐색 후 `data-avg-price`, `data-amount` 갱신

### `_send_update` (`@reactive.effect`, async)
- `price_signal`, `daily_insert_signal` 구독
- `initialized` 후 비활성 탭이면 스킵
- `get_usd_krw()` — Redis 조회 (매 tick)
- 계좌 목록 화면: `_db_accounts()` 캐시 사용 → 구성 변경 시 `ac_list_init`, 동일 시 `diff_display` → `ac_list_tick`
- 계좌 상세 화면: `_db_detail()` 캐시 사용 → 구성 변경 시 `ac_detail_init`, 동일 시 `diff_display` → `ac_detail_tick`

### 이벤트 핸들러

| 핸들러 | 트리거 | 동작 |
|--------|--------|------|
| `_handle_card_click` | `card_clicked` | `selected_account` 세팅 |
| `_go_back` | `btn_back` | `selected_account = None` |
| `_lookup_ticker` | `lookup_ticker` | yfinance로 종목명/시장 조회 → `ac_ticker_lookup_result` |
| `_add_account` | `btn_confirm_add` | accounts INSERT |
| `_delete_account` | `confirm_delete_account` | accounts DELETE (CASCADE) |
| `_add_position` | `btn_confirm_add_position` | tickers INSERT (없을 때), positions INSERT |
| `_add_cash` | `btn_confirm_add_cash` | positions INSERT (KRW/USD) |
| `_edit_position` | `btn_confirm_edit_position` | positions UPDATE (qty, avg_price 선택적), tickers UPDATE |
| `_buy` | `btn_confirm_buy` | `execute_buy()` 호출 |
| `_sell` | `btn_confirm_sell` | `execute_sell()` 호출 |
| `_delete_position` | `confirm_delete_position` | positions DELETE, 미사용 tickers 정리 |
| `_edit_cash` | `btn_confirm_edit_cash` | positions UPDATE |
| `_delete_cash` | `confirm_delete_cash` | positions DELETE |

### JS 전역 함수 (accounts.py 내 `<script>`)

| 함수 | 설명 |
|------|------|
| `acShowModal(id)` / `acHideModal(id)` | 모달 display 제어 |
| `acOpenEditCashModal(el)` | 현금 row 클릭 시 data-* 읽어 현금 수정 모달 채움 |
| `acTriggerEditCashSave()` | 현금 저장 이벤트 |
| `acTriggerCashDelete()` | 현금 삭제 confirm + 이벤트 |
| `acLookupTicker()` | 티커 자동조회 이벤트 |

### 비고
- 평단가 수정: 정보 탭에서 직접 입력 시 UPDATE, 미입력(null) 시 기존값 유지
- 매수/매도 후 `_notify_position_changed()` 호출 → Redis recalc + 시세 신호 발행
- `ns_str`: `session.ns("_")[:-1]` → `"accounts-"` 접두사