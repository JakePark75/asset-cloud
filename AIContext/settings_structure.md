# settings.py — 구조 요약

### 역할
- 설정 화면 UI 및 서버 로직
- 시세조회 간격 설정, 수동 티커 관리, 로그아웃

### import
- `from scheduler.price_updater_common import get_market_status`

### Reactive State

| 변수 | 타입 | 설명 |
|------|------|------|
| `refresh` | `reactive.value(0)` | 증가시키면 ticker_list 강제 재렌더 |
| `show_modal_ticker` | `reactive.value(False)` | 티커 추가 모달 표시 여부 |

### UI 렌더러 (output_ui)

#### `interval_buttons`
- `config.json`의 `interval` 값 읽어서 현재 선택된 버튼 active 표시
- 옵션: 실시간(0) / 1분(1) / 3분(3) / 5분(5) / 10분(10) / 30분(30) 세그먼트 버튼
- `interval = 0` → 웹소켓 모드 (price_updater_ws.py 실행)
- `interval > 0` → REST 폴링 모드 (price_updater_rest.py 실행)
- 버튼 클릭 시 JS로 active 클래스 전환 + `settings-btn_save_interval` input 세팅
- 저장 시 `subprocess.Popen(["sudo", "systemctl", "restart", "price_updater"])` 호출 → 런처(price_updater.py)가 새 interval로 모드 재분기

#### `ticker_list`
- `get_market_status(market)` 로 시장 상태 판단, 5종류 배지 표시
  - `"open"` → `● Open` (`status-open`, 녹색)
  - `"pre"` → `● Pre` (`status-pre`, 보라)
  - `"after"` → `● After` (`status-after`, 주황)
  - `"closing"` → `● Closing...` (`status-closing`)
  - `"closed"` → `○ Closed` (`status-closed`, 회색)
- `reactive.invalidate_later(60)` 로 1분마다 자동 갱신
- 레버리지 배지 (`lev-x2`, `lev-x3`), 수동/자동 구분 표시
- 삭제 버튼: `is_manual=true` 인 경우만 표시, JS confirm 후 `confirm_delete_ticker` input 세팅
- 정렬: is_manual DESC → FX/INDEX/CRYPTO → KR → US → CRYPTO → leverage DESC → ticker

#### `modal_add_ticker`
- `show_modal_ticker()` True일 때 렌더
- input: `new_ticker`, `new_ticker_name`, `new_ticker_market`, `new_ticker_leverage`
- 추가: `btn_confirm_add_ticker`
- 닫기: `modal_ticker_close`

### 이벤트 핸들러

| 핸들러 | 트리거 | 동작 |
|--------|--------|------|
| `_` | `btn_save_interval` | config.json의 interval 업데이트 + `systemctl restart price_updater` |
| `_` | `confirm_delete_ticker` | tickers DELETE (is_manual=true 조건), refresh |
| `_` | `btn_add_ticker` | `show_modal_ticker = True` |
| `_` | `modal_ticker_close` | `show_modal_ticker = False` |
| `_` | `btn_confirm_add_ticker` | tickers INSERT or UPDATE (is_manual=true), refresh |

### 비고
- 로그아웃: JS에서 직접 `deleteCookie('auth_token')` 후 `location.reload()`
- 티커 추가 시 이미 존재하는 ticker면 name/market/leverage/is_manual 업데이트 (ON CONFLICT)
- 시세조회 간격 변경 시 price_updater 서비스 재시작 → price_updater.py(런처)가 새 interval 읽어서 REST/WS 모드 재분기