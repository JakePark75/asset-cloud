# history — 구조 요약

## 파일 구성
| 파일 | 역할 |
|------|------|
| `app/modules/history.py` | UI / Server 진입점, 테이블 JS 렌더링 포함 |
| `app/modules/history_DAL.py` | DB 조회 및 TWR 재계산 |
| `app/modules/history_charts.py` | Plotly 차트 생성 |
| `app/modules/history_utils.py` | 포맷 유틸 |

---

## history.py

### UI
- 기간 버튼 (1개월 / 3개월 / 전체) — JS `setChartPeriod()`로 Plotly `relayout` 직접 호출, 서버 호출 없음
- 총자산 추이 차트 (`chart_asset`)
- TWR vs NDX100 차트 (`chart_twr`)
- 일간 누적 테이블 — `<table id="history-tbody">` + JS로 직접 렌더링 (`history_table.py` 미사용)
  - 컬럼 순서: 날짜 / 총자산 / 전일대비 / Exp / 현금 / 입출금 / **x3 / x2 / x1** / TWR / 나스닥 / 환율
  - "▼ 더 보기" 버튼으로 50건씩 페이지 로드 (`PAGE = 50`)
- 행 클릭 → `Shiny.setInputValue('history-selected_date', date)` (네임스페이스 하드코딩)

### Server

#### `_db_rows` (`@reactive.calc`)
- `_reload_trigger`, `daily_insert_signal` 구독 → 과거 입출금 수정 또는 daily insert 시 무효화
- `load_history()` 호출 결과 캐싱

#### `_all_rows_for_chart` (`@reactive.calc`)
- `_db_rows()` + `load_today_row()` 합산 → 오늘 행이 DB에 없으면 today_row를 마지막에 append
- 차트 렌더링 전용

#### `chart_asset` / `chart_twr` (`@render.ui`)
- `_all_rows_for_chart()` 의존 → 시세 업데이트마다 차트 전체 재렌더링
- 탭 비활성 시 `ui.HTML("")` 반환

#### `_send_history_table` (`@reactive.effect`, async)
- `_db_rows()` 의존 + 탭 활성 확인
- `load_today_row()` 결과를 맨 앞에 붙여 내림차순 구성
- `ndx_change_pct` (전일 대비 NDX 등락률) 계산 후 포함
- `send_custom_message("history_data", data)` → JS가 테이블 전체 교체

#### `_send_today_row_update` (`@reactive.effect`, async)
- `price_signal`, `daily_insert_signal`, `today_cf_trigger` 모두 구독
- 탭 비활성 시 스킵
- `load_today_row()` + `_db_rows()` 기반으로 today 행만 구성
- `twr_pct`, `ndx_pct` (차트 끝단 % 업데이트용) 서버에서 계산해서 포함
- `send_custom_message("today_row_update", row)` → JS가 테이블 최상단 행 + 차트 끝단 패치

#### `_open_edit_modal` (`@reactive.effect`)
- `input.selected_date` 이벤트 (테이블 행 클릭 시 세팅)
- **오늘 날짜면 Redis** (`today_cash_flow`, `today_cash_flow_note`) 조회, 과거면 DB 조회
- Shiny 모달로 입출금 수정 UI 표시
- 입력: `edit_cf` (numeric), `edit_note` (text)

#### `_save_cash_flow` (`@reactive.effect`)
- `input.edit_save` 이벤트
- **오늘 날짜면 Redis** 저장 → `recalc_today_row()` → `today_cf_trigger` 증가
- **과거면** `save_cash_flow()` → `_reload_trigger` 증가 (DB rows 재로드 + 차트 갱신)
- 모달 닫기 + 알림

---

## history_DAL.py

### `load_history()`
- daily_summary 전체 조회 (ASC)
- 반환 컬럼: date, total_asset, twr_asset, ndx100, cash_flow, cash_flow_note, exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio, usd_krw

### `calc_twr_pct(rows)`
- twr_asset 첫 번째 값 기준으로 정규화 → 수익률(%) 리스트 반환

### `calc_ndx_pct(rows)`
- ndx100 첫 번째 값 기준으로 정규화 → 수익률(%) 리스트 반환

### `save_cash_flow(date_str, cash_flow, note)`
- 해당 날짜 cash_flow / cash_flow_note UPDATE
- 해당 날짜 이후 전체 twr_asset 재계산 후 UPDATE
- TWR 계산식: `twr = prev_twr × (total - cf) / prev_total`

---

## history_charts.py

### 공통 레이아웃 `_BASE_LAYOUT`
- 다크테마 (paper_bgcolor 투명, plot_bgcolor #111111)
- hovermode: x unified
- dragmode: False (JS 터치 직접 처리)
- fixedrange: True (Plotly 줌 비활성, JS로 range 제어)
- 높이: 220px

### `make_chart_asset(rows)` → HTML str
- 총자산 추이 라인 차트 (녹색 #00c073)
- 입금 마커: 삼각형 위 (빨강 #ff4d4d)
- 출금 마커: 삼각형 아래 (파랑 #4d9fff)
- y축: 억 단위 포맷 (`fmt_10m`)
- 초기 범위: 최근 3개월
- `fig.to_html(full_html=False, include_plotlyjs=False, div_id="chart-asset")`
- 뒤에 `_touch_script("chart-asset")` 삽입

### `make_chart_twr(rows)` → HTML str
- TWR(녹색 실선) vs NDX100(파랑 점선) 비교 차트
- y축: % 단위
- 초기 범위: 최근 3개월
- `div_id="chart-twr"`, `_touch_script("chart-twr")` 삽입

### `_touch_script(chart_id)` → str
- 모바일 터치 이벤트 처리 스크립트 (차트 HTML 뒤에 삽입)
- 롱프레스: 커스텀 팝업 + 수직 보조선 표시
- 스와이프: x축 범위 패닝
- Plotly 기본 hover/drag 비활성화 후 직접 구현

### `_init_range(date_strs, period)` → list
- "1m" / "3m" / "all" 기준으로 초기 x축 범위 반환

### 비고
- plotly.js는 `app.py` head에서 CDN으로 전역 로드 (`include_plotlyjs=False`)

---

## history_utils.py

| 함수 | 설명 |
|------|------|
| `fmt_krw(val)` | 억/만 단위 축약 (예: "1.2억", "3,500만") |
| `fmt_10m(val)` | 억 단위 소수점 2자리 (예: "1.23억") |

### 비고
- `history_utils.fmt_krw`는 `components.fmt_krw`와 다름 — 히스토리 테이블 전용 축약 포맷