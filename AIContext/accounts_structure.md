# accounts.py — 구조 요약

## 파일 위치
`app/modules/accounts.py`

## 관련 파일
- `app/modules/asset.py` — 상위 모듈 (서브탭 관리)
- `app/modules/accounts_DAL.py` — `fetch_accounts_summary`, `calc_accounts_summary`, `fetch_account_details`, `calc_account_details`, `execute_buy`, `execute_sell`
- `app/modules/accounts_helpers.py` — `_build_account_card_skeleton`, `_build_account_card_values`, `_build_position_row_skeleton`, `_build_position_row_values`
- `app/modules/accounts_js.py` — `accounts_js()` 전체 화면 JS 주입
- `app/modules/accounts_modals.py` — `modal_edit_position_html`, `modal_edit_position_js`
- `app/db.py` — `get_db()`, `get_usd_krw()`, `get_market_map()`, `get_market_label()`, `get_market_currency()`
- `app/modules/components.py` — `fmt_krw`, `fmt_usd`, `fmt_pct`, `fmt_pnl`, `fmt_change`
- `app/price_signal.py` — `price_signal`, `daily_insert_signal`
- `app/utils/display_diff.py` — `diff_display`, `diff_display_split`
- `scheduler/price_updater_common.py` — `get_market_status()`
- `common/redis_store.py` — `get_all_prices()`, `refresh_position_cache()`, `recalc_today_row()`, `publish_position_changed()`, `publish_ticker_changed()`

---

## 역할
- 계좌 카드 목록 표시 (일반 계좌 + 감시 계좌 섹션 구분)
- 계좌 카드 클릭 시 인라인 아코디언으로 해당 계좌의 종목(positions) 목록 표시 (한 번에 하나만 열림)
- 계좌별 총평가액, 손익, 현금 표시
- 종목별 현재가, 등락률, 평단가, 평가액, 손익, 시장 상태 배지 표시
- 종목/현금 추가·수정·삭제, 계좌 추가·삭제, 매수·매도 처리
- 티커 자동조회 (yfinance) — 종목명·시장 자동 채움, 레버리지 배수 추론
- `render.ui` 없음 — 커스텀 메시지로 DOM 직접 패치

---

## 모듈 레벨 헬퍼 함수

### `_notify_position_changed()`
- `refresh_position_cache()` + `recalc_today_row()` + `publish_position_changed()` 호출
- positions 변경 후 Redis 신호 발행 (실패 시 무시)

### `_notify_ticker_changed()`
- `refresh_position_cache()` + `publish_ticker_changed()` 호출
- tickers 메타데이터 변경 후 Redis 신호 발행 (실패 시 무시)

---

## UI (`accounts_ui()`)

### 뷰 구조
- `#ac-account-list` — 계좌 카드 목록 컨테이너 (아코디언 포함)
- 감시 계좌 섹션은 `<h4 class="section-heading">감시 계좌</h4>` 구분선 후 동일 컨테이너에 삽입
- `+ 계좌 추가` 버튼 — `ac-modal-add-account` 모달 열기

### 모달 목록

| 모달 id | 용도 |
|---------|------|
| `ac-modal-add-account` | 계좌 추가 (계좌명, 별명, 감시 계좌 여부) |
| `ac-modal-add-position` | 종목 추가 (티커, 종목명, 시장, 레버리지, 수량, 평단가, 매수 preview) |
| `ac-modal-add-cash` | 현금 추가 (통화 KRW/USD, 금액) |
| `modal_edit_position_html(...)` | 종목 수정 (탭 구조: 정보·매수·매도 탭) — `accounts_modals.py` 위임 |
| `ac-modal-edit-cash` | 현금 수정 (통화, 금액, 현금 삭제) |

### JS 커스텀 메시지 핸들러

| 핸들러 | 동작 |
|--------|------|
| `ac_list_init` | `#ac-account-list` 골격 통째 교체 후 카드 값 반영. 열려있던 아코디언 ID 검증 |
| `ac_list_tick` | 변경된 카드 key만 DOM 패치 |
| `ac_acc_init` | 아코디언(`#ac-acc-{acc_id}`) 내용 통째 교체 후 표시, 종목 값 전체 반영 |
| `ac_acc_tick` | 변경된 종목 행 dynamic 필드만 DOM 패치 |
| `ac_acc_static_tick` | 변경된 종목 행 static 필드만 DOM 패치 |
| `ac_ticker_lookup_result` | 종목 추가 모달 — 자동조회 결과 채움 (name, market, leverage 추론) |
| `ac_ticker_lookup_result_edit` | 종목 수정 모달 — 자동조회 결과 채움 |

### JS 전역 함수

| 함수 | 설명 |
|------|------|
| `acGetEl(id)` | id로 DOM 요소 조회 헬퍼 |
| `acShowModal(id)` / `acHideModal(id)` | 모달 show/hide |
| `acToggleCard(acc_id)` | 아코디언 토글. 한 번에 하나만 열림. 닫을 때 `card_clicked: 0` 발송 |
| `window._acOpenId` | 현재 열려있는 아코디언 acc_id (null이면 닫힘) |
| `acLookupTicker()` | 종목 추가 모달 — 티커 자동조회 트리거 |
| `acLookupTickerEdit()` | 종목 수정 모달 — 티커 자동조회 트리거 |
| `acUpdateAddPreview()` | 종목 추가 모달 — 보유현금/매수금액/잔여현금 preview 갱신. NUM 마켓이면 숨김 |
| `acTriggerAddPosition()` | 종목 추가 확인 — 현금 초과 시 alert, `btn_confirm_add_position` 발송 |
| `acOpenEditPositionModal(el)` | 종목 수정 모달 열기 — row의 `data-*`에서 초기값 채움 |
| `acSwitchTab(tab)` | 종목 수정 모달 탭 전환 (정보·매수·매도) |
| `acUpdateBuyPreview()` | 매수 탭 preview 갱신 |
| `acUpdateSellPreview()` | 매도 탭 preview 갱신 |
| `acTriggerEditPositionSave()` | 종목 수정 저장 — `btn_confirm_edit_position` 발송 |
| `acTriggerBuy()` | 매수 확인 — 입력값 검증 후 `btn_confirm_buy` 발송 |
| `acTriggerSell()` | 매도 확인 — 입력값 검증 후 `btn_confirm_sell` 발송 |
| `acTriggerPositionDelete()` | 종목 삭제 확인 후 `confirm_delete_position` 발송 |
| `acOpenEditCashModal(el)` | 현금 수정 모달 열기 — `data-*`에서 초기값 채움 |
| `acTriggerEditCashSave()` | 현금 수정 저장 — `btn_confirm_edit_cash` 발송 |
| `acTriggerCashDelete()` | 현금 삭제 확인 후 `confirm_delete_cash` 발송 |
| `_inferLeverage(name)` | 종목명으로 레버리지 추론 (3X/UltraPro→3, 2X/Ultra→2) |
| `_fmtNum(val, cur)` / `_getCashAmount(cur)` | preview 포맷/현금 조회 — `modal_edit_position_js()`에서 정의 |

### `_applyOneCard(c)` 패치 DOM id

| id 패턴 | 내용 |
|---------|------|
| `ac-card-total-{acc_id}` | 계좌 총평가액 |
| `ac-card-pnl-{acc_id}` | 계좌 손익 (텍스트 + 클래스) |
| `ac-card-cash-{acc_id}` | 계좌 현금 |

### `_applyOnePosition(p)` 패치 DOM id — dynamic 필드

| id 패턴 | 내용 |
|---------|------|
| `ac-amount-{pos_id}` | 원화 평가액 |
| `ac-price-{pos_id}` | 현재가 (+ chg_css 클래스) |
| `ac-chg-{pos_id}` | 등락률 |
| `ac-pnl-{pos_id}` | 손익 |

### `_applyOnePositionStatic(p)` 패치 DOM id — static 필드

| id 패턴 | 내용 |
|---------|------|
| `ac-name-{pos_id}` | 종목명 |
| `ac-lev-{pos_id}` | 레버리지 배지 |
| `ac-qty-{pos_id}` | 수량 |
| `ac-avgprice-{pos_id}` | 평단가 |
| `ac-status-{pos_id}` | 시장 상태 배지 |
| `[data-pos-id]` 부모 요소 `data-*` | `data-avg-price`, `data-amount`, `data-name`, `data-market`, `data-leverage`, `data-currency`, `data-qty` 갱신 (모달 재오픈 시 최신값 반영용) |

---

## Server (`accounts_server()`)

### 파라미터
- `active_tab`: 상위(app.py)에서 주입
- `active_sub_tab`: asset_server에서 주입. `"accounts"` 여부로 갱신 스킵 판단

### Reactive State

| 변수 | 타입 | 설명 |
|------|------|------|
| `_initialized` | bool (nonlocal) | 자기-재트리거 방지 |
| `open_account` | `reactive.value(None)` | None: 아코디언 닫힘, int: 해당 acc_id 아코디언 열림 |
| `refresh` | `reactive.value(0)` | DB 재조회 트리거 (CRUD 후 +1) |
| `_last_accounts` | list | 이전 계좌 id 목록 (구조 변경 감지) |
| `_last_list_disp` | dict | 계좌 카드 이전 표시값 (diff 기준) |
| `_last_positions` | dict | `{acc_id: [pos_id, ...]}` — 아코디언 구조 변경 감지 |
| `_last_acc_disp` | dict | 아코디언 종목 이전 표시값 (diff 기준) |

### DB 캐시 (`@reactive.calc`)

| calc | 구독 신호 | 설명 |
|------|-----------|------|
| `_db_accounts()` | `refresh` | 계좌 목록 + 구조만 조회 (시세 없음). `fetch_accounts_summary()` |
| `_db_account_positions()` | `refresh`, `open_account` | 열린 아코디언 계좌의 positions 조회. `open_account=None`이면 `None` 반환. `fetch_account_details(acc_id)` |

> 시세는 `_send_update()` 내에서 `get_all_prices()`로 직접 주입 — calc가 price_signal을 구독하지 않음

### `_send_update()` (`@reactive.effect`, async)
- `price_signal`, `daily_insert_signal` 구독
- `open_account` 의존성도 탭 가드 전에 등록
- `active_sub_tab != "accounts"`이면 `_initialized` 후 스킵
- **계좌 목록 갱신**: 계좌 구성 변경(`current_accounts != _last_accounts`) → `ac_list_init`, 동일 → `diff_display` → `ac_list_tick`
- **아코디언 종목 갱신** (`acc_id is not None`일 때): 종목 구성 변경 → `ac_acc_init`, 동일 → `diff_display_split` → dynamic diff: `ac_acc_tick`, static diff: `ac_acc_static_tick`

### `_build_accordion_footer(acc_id)` (내부 함수)
- 아코디언 하단 버튼 HTML 반환: `+ 종목 추가`, `+ 현금 추가`, `계좌 삭제`
- `ac_acc_init` 전송 시 `position_list_html`에 append

### 이벤트 핸들러

| 핸들러 | 트리거 | 동작 |
|--------|--------|------|
| `_handle_card_click` | `card_clicked` | `open_account` 세팅. `acc_id=0`이면 아코디언 상태 초기화 |
| `_lookup_ticker` | `lookup_ticker` | yfinance로 티커 자동조회. KR 종목은 `.KS` → `.KQ` 순 시도. `source` 에 따라 채널 분기 |
| `_add_account` | `btn_confirm_add` | accounts 테이블 INSERT |
| `_delete_account` | `confirm_delete_account` | `open_account` 기준 accounts 테이블 DELETE |
| `_add_position` | `btn_confirm_add_position` | tickers 없으면 INSERT, positions INSERT |
| `_add_cash` | `btn_confirm_add_cash` | positions INSERT (ticker=KRW/USD) |
| `_edit_position` | `btn_confirm_edit_position` | positions UPDATE (qty, avg_price), tickers UPDATE (name, market, leverage). `_notify_ticker_changed()` 추가 호출 |
| `_edit_cash` | `btn_confirm_edit_cash` | positions UPDATE (ticker, quantity) |
| `_buy` | `btn_confirm_buy` | `execute_buy(pos_id, qty, price, usd_markets)` |
| `_sell` | `btn_confirm_sell` | `execute_sell(pos_id, qty, price, usd_markets)` |
| `_delete_position` | `confirm_delete_position` | positions DELETE. `is_manual=false` 티커이고 잔여 positions 0이면 tickers도 DELETE |
| `_delete_cash` | `confirm_delete_cash` | positions DELETE (현금 행) |

> CRUD 핸들러 공통: 처리 후 `refresh.set(refresh() + 1)` + `_notify_position_changed()` 호출

---

## 주요 주의사항

- `window._acNs`는 asset.py UI `<script>`에서 설정 — accounts JS는 이를 참조해 `Shiny.setInputValue` 네임스페이스 구성
- `ac_acc_tick` / `ac_acc_static_tick` 분리: dynamic(시세 연동) 필드와 static(구조·메타) 필드를 별도 채널로 패치 — portfolio.py의 단일 `pf_tick`과 다른 점
- `_db_accounts` / `_db_account_positions` calc는 `price_signal` 비의존 — 시세는 `_send_update()` 내 `get_all_prices()`로 직접 주입
- `_delete_position`은 `is_manual=false` 티커에 한해 잔여 positions 0이면 tickers 자동 삭제
- `_edit_position`은 tickers 메타 변경이므로 `_notify_ticker_changed()` 추가 발행
- 매수(`_buy`) / 매도(`_sell`) 처리는 `accounts_DAL.py`의 `execute_buy` / `execute_sell`에 위임 — `usd_markets` 집합을 `get_market_map()`에서 추출해 전달
- `NUM` 마켓(지수 등)은 현금 개념 없음 — 종목 추가 preview 박스 숨김 처리 및 현금 초과 경고 스킵