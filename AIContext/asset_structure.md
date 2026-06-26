# asset.py — 구조 요약

## 파일 위치
`app/modules/asset.py`

## 관련 파일
- `app/modules/dashboard.py` — 서브탭: 대시보드
- `app/modules/portfolio.py` — 서브탭: 포트폴리오
- `app/modules/accounts.py` — 서브탭: 계좌
- `app/modules/components.py` — `fmt_krw`, `fmt_pct`
- `app/db.py` — `get_db`
- `app/price_signal.py` — `price_signal`, `daily_insert_signal`, `position_signal`, `ticker_signal`
- `app/utils/metrics.py` — `to_f`, `calculate_exposure_and_ratios`
- `app/utils/display_diff.py` — `diff_display`
- `common/redis_store.py` — `get_all_prices`

---

## 역할
- `asset` 탭의 최상위 모듈. 히어로 헤더 + 서브탭(대시보드/포트폴리오/계좌) 를 통합 관리
- 히어로: 총자산, 전일 대비 증감, 환율, 미니 라인차트 SVG
- `_db_summary_rows`, `_db_position_rows` DB 캐시를 소유하고 하위 모듈(dashboard)에 주입
- `price_signal` 등 신호 import는 `_price_signal` 등으로 alias하여 사용

---

## UI (`asset_ui()`)

### 구조
```
#asset-root
├── <script>  (JS 핸들러 + 서브탭 전환 로직)
├── .db-hero  (히어로 헤더)
│   ├── #asset-hero-chart-inner  (미니 라인차트 SVG)
│   └── .db-hero-content
│       ├── #asset-hero-amount       (총자산)
│       ├── #asset-hero-delta-text   (전일 대비 증감, pnl 클래스)
│       └── #asset-hero-usd-text     (환율 + 등락률)
├── .asset-sub-tabbar  (서브탭 버튼 3개)
└── 서브탭 콘텐츠
    ├── #asset-sub-dashboard   (.asset-sub-content)
    ├── #asset-sub-portfolio   (.asset-sub-content, 기본 display:none)
    └── #asset-sub-accounts    (.asset-sub-content, 기본 display:none)
```

### JS 커스텀 메시지 핸들러

| 핸들러 | 동작 |
|--------|------|
| `asset_hero_update` | 히어로 텍스트(`hero_text`) 및 차트 SVG(`hero_chart_svg`) DOM 패치 |
| `restore_sub_tab` | localStorage `activeSubTab` 복원 → `switchSubTab()` 호출 |

### JS 전역 함수

| 함수 | 설명 |
|------|------|
| `switchSubTab(name, el)` | 서브탭 전환. 콘텐츠 display 제어, 버튼 active 클래스, localStorage 저장, `Shiny.setInputValue('asset-active_sub_tab', ...)` |
| `window._pfNs` | `'asset-portfolio'` — portfolio JS에서 Shiny 네임스페이스 참조용 |
| `window._acNs` | `'asset-accounts'` — accounts JS에서 Shiny 네임스페이스 참조용 |

### 히어로 DOM id

| DOM id | 설명 |
|--------|------|
| `asset-hero-chart-inner` | 미니 라인차트 SVG 컨테이너 |
| `asset-hero-amount` | 총자산 금액 |
| `asset-hero-delta-text` | 전일 대비 증감 텍스트 + pnl 클래스 |
| `asset-hero-usd-text` | 환율 + 등락률 텍스트 + pnl 클래스 |

---

## Server (`asset_server()`)

### 파라미터
- `active_tab: reactive.value` — 상위(app.py)에서 주입. `"asset"` 여부로 히어로 갱신 스킵 판단

### Reactive State

| 변수 | 타입 | 설명 |
|------|------|------|
| `_initialized` | bool (nonlocal) | 첫 렌더 완료 여부 (자기-재트리거 방지) |
| `_last_display` | dict | 히어로 이전 표시값 (diff 기준) |
| `active_sub_tab` | `reactive.value("dashboard")` | 현재 활성 서브탭 |

### DB 캐시 (`@reactive.calc`)

| calc | 구독 신호 | 설명 |
|------|-----------|------|
| `_db_summary_rows()` | `daily_insert_signal` | `daily_summary` 전체 (날짜 오름차순). dashboard에 주입 |
| `_db_position_rows()` | `position_signal`, `ticker_signal` | `positions + tickers + accounts` JOIN (`is_watch=false`). dashboard에 주입 |

- 두 calc 모두 asset_server가 소유하고 `dashboard_server`에 인자로 전달
- portfolio/accounts는 별도로 자체 DB 캐시 보유

### `_hero_data()` (`@reactive.calc`)
- 구독: `price_signal`, `daily_insert_signal`, `position_signal`, `ticker_signal`
- `active_tab != "asset"`이면 `_initialized` 후 `None` 반환 (스킵)
- Redis `get_all_prices()` → KRW/USD/종목별 평가액 계산
- `calculate_exposure_and_ratios()` → `total_asset`
- `_db_summary_rows()[-1]` → `prev_asset` → `asset_delta`, `asset_delta_pct`
- `daily_summary` + 실시간 total_asset → 샘플링 100포인트 미니차트 데이터
- 반환 dict: `total_asset`, `asset_delta`, `asset_delta_pct`, `usd_krw`, `usd_chg`, `chart_data`

### `_send_hero_update()` (`@reactive.effect`, async)
- `_hero_data()` 의존
- `active_tab != "asset"`이면 스킵
- `_hero_line_svg(chart_data)` → SVG 문자열
- `diff_display(current, _last_display)` → 변경분만 `send_custom_message("asset_hero_update", diff)`

### `_on_tab_active()` (`@reactive.effect`)
- `active_tab == "asset"` 진입 시 `send_custom_message("restore_sub_tab", {})` 발송
- JS가 localStorage에서 이전 서브탭 복원

### 서브 모듈 마운트
```python
dashboard_server("dashboard",
    active_tab=active_tab, active_sub_tab=active_sub_tab,
    db_summary_rows=_db_summary_rows, db_position_rows=_db_position_rows)
portfolio_server("portfolio", active_tab=active_tab, active_sub_tab=active_sub_tab)
accounts_server("accounts",  active_tab=active_tab, active_sub_tab=active_sub_tab)
```

---

## 히어로 라인차트 (`_hero_line_svg`)

- 모듈 레벨 순수 함수 (server 외부)
- `daily_summary.total_asset` 이력 + 실시간 total_asset 마지막 포인트
- n > 100이면 균등 샘플링 100포인트, 마지막은 항상 실시간값
- SVG `viewBox="0 0 100 100"`, `preserveAspectRatio="none"`
- 그린 라인(`#00c073`) + 하단 그라데이션 fill
- 좌표 포맷 `.0f` — 미세 변화 시 동일 문자열 → diff_display 불필요 재전송 방지

---

## `asset_hero_update` 메시지 구조

| key | 타입 | 설명 |
|-----|------|------|
| `hero_text` | dict | `total_asset`, `delta_text`, `delta_val`, `usd_text`, `usd_chg_val` |
| `hero_chart_svg` | str | 미니 라인차트 SVG HTML |

- `hero_text`와 `hero_chart_svg`는 diff_display로 분리 전송 가능 (SVG 변경 없을 때 재전송 방지)

---

## 서브탭 전환 흐름

1. 사용자가 탭 버튼 클릭 → `switchSubTab(name, el)` 호출
2. JS: 콘텐츠 display 토글, localStorage 저장, `Shiny.setInputValue('asset-active_sub_tab', name)`
3. Python: `_sync_sub_tab()` effect → `active_sub_tab.set(name)`
4. 각 서브 모듈 server의 `_send_update()`가 `active_sub_tab` 비교 → 활성 탭만 갱신

### 탭 복원 흐름 (`asset` 탭 재진입 시)
1. `_on_tab_active()` → `restore_sub_tab` 메시지 발송
2. JS: localStorage `activeSubTab` 읽어 → `switchSubTab()` 재호출