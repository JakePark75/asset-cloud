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
| `_build_position_row_skeleton(pos, ns_str)` | str (HTML) | 종목 행 골격. `data-avg-price` 속성 포함. 구성 변경 시 1회 전송 |
| `_build_position_row_values(pos, usd_rate)` | dict | 종목 행 가변값 (amount, price, chg, chg_css, status_*) |
| `_build_summary_html(label, total, pnl, pnl_pct, usd_rate, usd_chg)` | dict | summary 헤더 값 dict |

### 튜플 구조
- `acc` 튜플 (7): `(id, name, alias, total, cash, is_watch, prev_total)`
- `pos` 튜플 (9): `(pos_id, ticker, qty, name, price, change_pct, market, leverage, avg_price)`

---

## accounts_modals.py

### 함수

| 함수 | 반환 | 설명 |
|------|------|------|
| `modal_edit_position_html(market_options)` | `ui.Tag` | 종목 수정 모달 HTML (탭 3개: 정보/매수/매도) |
| `modal_edit_position_js()` | str | 모달 전용 JS 문자열 (accounts.py `<script>` 블록에 포함) |

### 모달 구조
- 탭 3개: **정보** / **매수** / **매도**
- 정보 탭: 종목명 / 시장 / 레버리지 / 수량 / 평균단가 직접 입력 (미입력 시 기존값 유지) / 저장 / 삭제
- 매수 탭: 수량 + 단가 입력 → 실시간 미리보기 (매수 후 평단, 수량, 현금 차감액)
- 매도 탭: 수량 + 단가 입력 → 실시간 미리보기 (매도 후 수량, 현금 가산액, 실현손익)

### JS 전역 함수

| 함수 | 설명 |
|------|------|
| `acOpenEditPositionModal(el)` | 종목 row 클릭 시 `data-*` 읽어 필드 채움, 정보 탭으로 초기화 |
| `acSwitchTab(tab)` | 탭 전환 (`'info'`/`'buy'`/`'sell'`) + 입력값/미리보기 초기화 |
| `acUpdateBuyPreview()` | 매수 수량/단가 입력 시 실시간 미리보기 갱신 |
| `acUpdateSellPreview()` | 매도 수량/단가 입력 시 실시간 미리보기 갱신 |
| `acTriggerEditPositionSave()` | 정보 탭 저장 → `accounts-btn_confirm_edit_position` 이벤트 |
| `acTriggerBuy()` | 매수 확인 → `accounts-btn_confirm_buy` 이벤트 |
| `acTriggerSell()` | 매도 확인 → `accounts-btn_confirm_sell` 이벤트 |
| `acTriggerPositionDelete()` | 종목 삭제 confirm → `accounts-confirm_delete_position` 이벤트 |

### 모달 상태 JS 변수
- `_editPosId`: 현재 편집 중인 position id
- `_editCurQty`: 현재 보유 수량 (미리보기 계산용)
- `_editCurAvg`: 현재 평단가 (미리보기 계산용)
- `_editMarket`: 마켓 코드 (통화 판별용)

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
- 가중평균 평단 재계산: `new_avg = (cur_qty × cur_avg + qty_delta × trade_price) / new_qty`
- `positions.quantity += qty_delta`, `positions.avg_price = new_avg`
- 마켓 통화 기준 현금 차감: KR → KRW, USD마켓/CRYPTO → USD
- `trade_price`: 원천 통화 단가 (KR→KRW, US→USD)

#### `execute_sell(pos_id, qty_delta, trade_price, usd_markets)`
- 평단 변동 없음 (매도는 평단에 영향 없음)
- `positions.quantity -= qty_delta`
- 마켓 통화 기준 현금 가산
- 보유 수량 초과 시 `ValueError` 발생

---

## accounts.py

### Reactive State

| 변수 | 타입 | 설명 |
|------|------|------|
| `initialized` | `reactive.value(False)` | 첫 렌더 완료 여부 (탭 비활성 스킵 제어) |
| `selected_account` | `reactive.value(None)` | 선택된 계좌 ID. None이면 목록, 값 있으면 상세 |
| `refresh` | `reactive.value(0)` | 증가 시 `_send_update` 재실행 |

### UI 구조 (`accounts_ui()`)
- `@render.ui` 없음 — 모든 갱신은 커스텀 메시지로 DOM 직접 패치
- 모달 전체가 정적 DOM으로 상주 (JS `acShowModal()` / `acHideModal()`로 display 제어)
- 종목 수정 모달만 `accounts_modals.py`에서 임포트 (`modal_edit_position_html()`)

### JS 커스텀 메시지 핸들러

| 핸들러 | 동작 |
|--------|------|
| `ac_list_init` | summary 반영, 계좌 목록 골격 HTML 교체, 카드 값 반영, 상세 숨김 |
| `ac_list_tick` | summary + 변경된 카드 값만 DOM 패치 |
| `ac_detail_init` | summary 반영, 종목 목록 골격 HTML 교체, 종목 값 반영, 목록 숨김 |
| `ac_detail_tick` | summary + 변경된 종목 값만 DOM 패치 |
| `ac_ticker_lookup_result` | 티커 자동조회 결과 → 종목명/시장 필드 자동 채움 |

### `_send_update` (`@reactive.effect`, async)
- `price_signal`, `daily_insert_signal`, `refresh()` 구독
- `initialized` 후 비활성 탭이면 스킵
- 계좌 목록 화면: 구성 변경 → `ac_list_init`, 동일 → `diff_display` → `ac_list_tick`
- 계좌 상세 화면: 구성 변경 → `ac_detail_init`, 동일 → `diff_display` → `ac_detail_tick`

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
| `_edit_cash` | `btn_confirm_edit_cash` | positions UPDATE (ticker/quantity) |
| `_delete_cash` | `confirm_delete_cash` | positions DELETE |

### JS 전역 함수 (accounts.py 내 `<script>`)

| 함수 | 설명 |
|------|------|
| `acShowModal(id)` / `acHideModal(id)` | 모달 display 제어 |
| `acOpenEditCashModal(el)` | 현금 row 클릭 시 data-* 읽어 현금 수정 모달 채움 |
| `acTriggerEditCashSave()` | 현금 저장 이벤트 발송 |
| `acTriggerCashDelete()` | 현금 삭제 confirm + 이벤트 발송 |
| `acLookupTicker()` | 티커 자동조회 이벤트 발송 |

### 비고
- 평단가 수정: 정보 탭에서 직접 입력 시 UPDATE, 미입력(null) 시 기존값 유지
- 매수/매도 후 `_notify_price_updated()` 호출 → Redis recalc + 시세 신호 발행
- `_applyOnePosition()` JS 함수: tick 수신 시 `data-avg-price` 속성도 갱신 (모달 오픈 시 최신값 반영)
- `ns_str`: `session.ns("_")[:-1]` → `"accounts-"` 접두사 (JS에서 Shiny input id 조립용)