# history — 구조 요약

## 파일 구성
| 파일 | 역할 |
|------|------|
| `app/modules/history.py` | UI / Server 진입점 |
| `app/modules/history_DAL.py` | DB 조회 및 TWR 재계산 |
| `app/modules/history_charts.py` | Plotly 차트 생성 |
| `app/modules/history_table.py` | 일간 누적 테이블 렌더링 |
| `app/modules/history_utils.py` | 포맷 유틸 |

---

## history.py

### UI
- 기간 버튼 (1개월 / 3개월 / 전체) — JS `setChartPeriod()`로 Plotly `relayout` 직접 호출, 서버 호출 없음
- 총자산 추이 차트 (`chart_asset`)
- TWR vs NDX100 차트 (`chart_twr`)
- 일간 누적 테이블 (`history_table`)

### Server

#### `history_data` (reactive.calc)
- `price_signal.get()` 호출로 실시간 갱신 연동
- `load_history()` 호출 후 캐싱

#### `chart_asset` / `chart_twr` / `history_table`
- `history_data()` 결과를 각 렌더러에 전달

#### `_open_edit_modal`
- `input.selected_date` 이벤트 (테이블 행 클릭 시 세팅)
- DB에서 해당 날짜의 cash_flow, cash_flow_note 조회
- Shiny 모달로 입출금 수정 UI 표시
- 입력: `edit_cf` (numeric), `edit_note` (text)

#### `_save_cash_flow`
- `input.edit_save` 이벤트
- `save_cash_flow()` 호출 후 모달 닫기 + 알림

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
- 입금 마커: 삼각형 위 (녹색)
- 출금 마커: 삼각형 아래 (빨강)
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

## history_table.py

### `render_history_table(rows)` → Shiny UI
- daily_summary rows (ASC) → 최신순(DESC)으로 뒤집어서 렌더
- 컬럼 순서: 날짜(yymmdd) / 총자산(억) / 전일대비 / Exp / 현금 / 입출금 / x1 / x2 / x3 / TWR / 나스닥 / 환율
- 전일 대비: 금액 + 등락률(%), positive/negative 색상
- 입출금: 0이 아닌 경우만 표시, 사유 있으면 dotted underline + title tooltip
- 비율 컬럼(Exp/현금/x1/x2/x3): DB값 × 100 → % 표시
- TWR: 억 단위 (`fmt_10m`)
- 나스닥: 소수점 2자리, 없으면 "-"
- 환율: 소수점 2자리, 없으면 "-"
- 행 클릭 시 `history-selected_date` input 세팅 (네임스페이스 하드코딩)

---

## history_utils.py

| 함수 | 설명 |
|------|------|
| `fmt_krw(val)` | 억/만 단위 축약 (예: "1.2억", "3,500만") |
| `fmt_10m(val)` | 억 단위 소수점 2자리 (예: "1.23억") |

### 비고
- `history_utils.fmt_krw`는 `components.fmt_krw`와 다름 — 히스토리 테이블 전용 축약 포맷