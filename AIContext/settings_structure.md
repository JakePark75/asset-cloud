# settings.py — 구조 요약

### 역할
- 설정 화면 UI 및 서버 로직
- 시세조회 간격 설정, 수동 티커 관리, 로그아웃

### Reactive State

| 변수 | 타입 | 설명 |
|------|------|------|
| `refresh` | `reactive.value(0)` | 증가시키면 ticker_list 강제 재렌더 |
| `show_modal_ticker` | `reactive.value(False)` | 티커 추가 모달 표시 여부 |

### UI 렌더러 (output_ui)

#### `interval_buttons`
- `config.json`의 `interval` 값 읽어서 현재 선택된 버튼 active 표시
- 1 / 3 / 5 / 10 / 30 분 세그먼트 버튼
- 버튼 클릭 시 JS로 active 클래스 전환 + `settings-btn_save_interval` input 세팅

#### `ticker_list`
- `is_manual = true` 인 티커만 조회
- 종목명(티커) / 시장/레버리지 표시
- 삭제 버튼: JS confirm 후 `confirm_delete_ticker` input 세팅

#### `modal_add_ticker`
- `show_modal_ticker()` True일 때 렌더
- input: `new_ticker`, `new_ticker_name`, `new_ticker_market`, `new_ticker_leverage`
- 추가: `btn_confirm_add_ticker`
- 닫기: `modal_ticker_close`

### 이벤트 핸들러

| 핸들러 | 트리거 | 동작 |
|--------|--------|------|
| `_` | `btn_save_interval` | config.json의 interval 업데이트 |
| `_` | `confirm_delete_ticker` | tickers DELETE (is_manual=true 조건), refresh |
| `_` | `btn_add_ticker` | `show_modal_ticker = True` |
| `_` | `modal_ticker_close` | `show_modal_ticker = False` |
| `_` | `btn_confirm_add_ticker` | tickers INSERT or UPDATE (is_manual=true), refresh |

### 비고
- 로그아웃: JS에서 직접 `deleteCookie('auth_token')` 후 `location.reload()`
- 티커 추가 시 이미 존재하는 ticker면 name/market/leverage/is_manual 업데이트 (ON CONFLICT)
- 시세조회 간격 변경은 `price_updater.py`가 `config.json`을 실시간으로 읽으므로 즉시 반영