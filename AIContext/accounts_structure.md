# settings.py — 구조 요약

### 역할
- 설정 화면 UI 및 서버 로직
- 시세조회 간격 설정, 수동 티커 관리, 로그아웃

### import
- `from app.db import get_db, get_config, save_config, get_market_currency, get_market_map`
- `from app.modules.components import fmt_change`
- `from app.price_signal import price_signal`
- `from scheduler.price_updater_common import get_market_status`
- `from app.utils.display_diff import diff_display`

### Reactive State / 세션 상태

| 변수 | 타입 | 설명 |
|------|------|------|
| `refresh` | `reactive.value(0)` | 티커 추가/삭제 후 증가 → `_send_update` 재실행 |
| `_last_tickers` | `list` | 이전 ticker 목록 (구성 변경 감지) |
| `_last_display` | `dict` | `diff_display` 기준 이전 표시값 |

### UI 구조 (`settings_ui()`)
- `@render.ui` 없음 — 모든 갱신은 `st_init` / `st_tick` 커스텀 메시지로 DOM 직접 패치
- 시세조회 간격 버튼: 실시간(0) / 1분(1) / 3분(3) / 5분(5) / 10분(10) / 30분(30). 클릭 시 JS로 active 클래스 전환 + `settings-btn_save_interval` input 세팅
- 티커 관리 영역: `<div id="st-ticker-list">` (내용은 `st_init`으로 주입)
- 로그아웃: JS에서 직접 `deleteCookie('auth_token')` 후 `location.reload()`
- 티커 추가 모달: 정적 HTML DOM 상주, `stShowModal()` / `stHideModal()`로 display 제어
  - input: `st-new-ticker`, `st-new-ticker-name`, `st-new-ticker-market`, `st-new-ticker-leverage`
  - 추가 버튼 클릭 → `settings-btn_confirm_add_ticker` input 세팅 (JSON payload)
  - `stHideModal()` 시 입력값 자동 초기화

### JS 커스텀 메시지 핸들러

| 핸들러 | 동작 |
|--------|------|
| `st_init` | interval 버튼 active 반영, `st-ticker-list` 골격 HTML 교체, ticker 값 즉시 반영 |
| `st_tick` | 변경된 ticker key만 DOM 패치 (status, price/chg) |

### `_send_update` (`@reactive.effect`, async)
- `price_signal`, `refresh()` 구독 + `reactive.invalidate_later(60)` (1분 자동 갱신)
- 탭 비활성 시 스킵
- Redis `get_all_prices()` + DB tickers 전체 조회 → `_sort_key` 정렬
- 구성 변경(`current_tickers != _last_tickers`) → `st_init` (골격 + interval + tick 값)
- 구성 동일 → `diff_display(ticker_values, _last_display)` → `st_tick`

### `_sort_key` 정렬 순서
`is_manual DESC → 시장 그룹 → leverage DESC → ticker ASC`

시장 그룹 순서: KR(0) → NAS/NYS/AMS/ARC(1) → CRYPTO(2) → COM(3) → FX/INDEX(4)

### 이벤트 핸들러

| 핸들러 | 트리거 | 동작 |
|--------|--------|------|
| `_` | `btn_save_interval` | `save_config()` interval 업데이트 + `systemctl restart price_updater` |
| `_` | `confirm_delete_ticker` | `DELETE FROM tickers WHERE ticker = %s AND is_manual = true` → `refresh` 증가 + `_notify_price_updated()` |
| `_` | `btn_confirm_add_ticker` | tickers INSERT (`is_manual=true`, `sort_order` 자동 채번) ON CONFLICT DO UPDATE (name/market/leverage/is_manual) → `refresh` 증가 + `_notify_price_updated()` |

### 헬퍼 함수 (모듈 레벨)

| 함수 | 설명 |
|------|------|
| `_ticker_to_id(ticker)` | 하이픈/캐럿/등호 → 언더스코어 (DOM id 안전화) |
| `_sort_key(r)` | 티커 정렬 기준 반환 |
| `_build_row_skeleton(...)` | 골격 HTML. `is_manual=true`만 삭제 버튼 포함, 아니면 빈 div |
| `_build_tick_values(ticker, market, price, change_pct)` | tick 값 dict (`id, price, chg, chg_css, status_dot, status_txt, status_cls`) |
| `_notify_price_updated()` | DB `NOTIFY price_updated` 발송 |

### 비고
- 로그아웃: JS에서 직접 `deleteCookie('auth_token')` 후 `location.reload()`
- 티커 추가 시 이미 존재하는 ticker면 name/market/leverage/is_manual 업데이트 (ON CONFLICT). `sort_order`는 `MAX(sort_order) + 1` 자동 채번
- 시세조회 간격 변경 시 `price_updater` 서비스 재시작 → `price_updater.py`(런처)가 새 interval 읽어서 REST/WS 모드 재분기
- 티커 추가 모달은 `reactive.value` 없이 정적 DOM 상주, JS `stShowModal()` / `stHideModal()` 로만 제어
- `_notify_price_updated()`: settings는 `get_connection()` 대신 `get_db()` 사용 (accounts와 구현 방식 상이)