# dashboard.py — 구조 요약

## 1. 파일 정보

| 파일 | 역할 |
|------|------|
| `app/modules/dashboard.py` | UI + Server (단일 파일) |
| `app/modules/asset.py` | 히어로 헤더 + 서브탭 관리 (dashboard의 상위 모듈) |
| `app/static/dashboard.css` | 대시보드 전용 스타일 (Bloomberg 다크테마) |
| `app/utils/metrics.py` | 순수 계산 함수 |

> **폰트**: JetBrains Mono + Noto Sans Mono KR (Google Fonts CDN)  
> `#dashboard-root` 및 하위 전체에 적용. `dashboard.css` 상단 `@import`로 로드.

---

## 2. 상위 구조 (app.py → asset.py → dashboard.py)

```
app.py        — 로그인/쿠키 인증, 최상위 탭(자산/실적/관리) 전환, active_tab 관리
 └─ asset.py  — 히어로 헤더(총자산/등락/USD) + 서브탭(대시보드/포트폴리오/계좌) 관리, active_sub_tab 관리
     ├─ dashboard.py   ← 이 문서의 대상
     ├─ portfolio.py
     └─ accounts.py
```

- **히어로 영역은 `asset.py` 소관** — 총자산/미니차트 DOM id는 `asset-hero-*`, dashboard에는 없음
- `dashboard_server`는 `asset_server`가 호출하며 `active_tab`, `db_summary_rows`, `db_position_rows`를 주입받음 (DB 캐시를 자체 소유하지 않고 상위에서 공유)

---

## 3. 화면 구성

| 섹션 | 항목 |
|------|------|
| 오늘 | Exposure + 레버리지 바 + 현금비중 통합 카드 |
| 종목 비중 | SVG 도넛 + 범례 (상위 8 + 현금 합산 + 기타) |
| 수익률 | 연평균 IRR, 월평균 IRR, 30일 IRR (2열 그리드) + 총 입출금 |
| 알파/베타 | 누적 알파, 30일 알파 (2열 그리드), 베타 전체/30일 카드, 낙폭 분석(MDD/현재낙폭/회복률 vs NDX100) |
| 은퇴 시뮬레이션 | 은퇴시점 예상자산, 월평균 IRR 복리 근거 표시 |

> **제거된 항목**: 기준일 표시, 금일 순수익(`daily_profit`)

---

## 4. 데이터 흐름

### `_load_summary_data(rows, raw_rows)` — DAL
- `rows`: `_db_summary_rows()` 결과 (asset_server에서 주입)
- `raw_rows`: `_db_position_rows()` 결과 (asset_server에서 주입)
- 시세: `get_all_prices()` (Redis) — `usd_krw`, `^NDX`, 종목별 price
- `today_cash_flow`: Redis `today_cash_flow` 키 → fallback 0
- 계산 결과를 dict로 반환

### `_load_position_data(rows)` — DAL
- `rows`: `_db_position_detail_rows()` 결과 (대시보드 자체 calc)
- 시세: `get_all_prices()` (Redis) 매핑
- KRW: `eval_krw = qty`
- USD: `eval_krw = qty × usd_krw`
- 그 외: `get_market_currency(market) == "USD"` 이면 `qty × price × usd_krw`, 아니면 `qty × price`
- 종목별 평가액(원화 환산) dict 리스트 반환

### `@reactive.calc`

| calc | 구독 신호 | 설명 |
|------|-----------|------|
| `_db_summary_rows` | — | asset_server에서 주입받은 calc 그대로 사용 |
| `_db_position_rows` | — | asset_server에서 주입받은 calc 그대로 사용 |
| `_db_position_detail_rows()` | `position_signal`, `ticker_signal` | 도넛용 positions + tickers JOIN (name 포함). dashboard 자체 소유 |
| `data()` | `price_signal`, `daily_insert_signal`, `position_signal`, `ticker_signal` | `_load_summary_data()` 결과 캐싱 |
| `position_data()` | `price_signal`, `position_signal`, `ticker_signal` | `_load_position_data()` 결과 캐싱 |

### `@reactive.effect` — `_send_update()` (async)
- `data()`, `position_data()` 의존
- `active_sub_tab != "dashboard"`이면 `_initialized` 후 스킵
- 화면 표시용 포맷 문자열 구성 → `diff_display(current, _last_display)` → `send_custom_message("db_update", diff)`

---

## 5. 핵심 로직 / 헬퍼 (주요 계산 로직)

### XIRR / IRR
- cash_flows 구성:
  - 첫 항목: `(첫날, -첫날 total_asset)` — 최초 투자금 음수
  - 중간: `(date, -cash_flow)` — DB 부호 반전 (+입금/-출금 → XIRR: -입금/+출금)
  - 마지막: `(오늘, 실시간 total_asset)` — 현재 자산 양수
- 연평균 IRR: `calculate_xirr(cash_flows)`
- 월평균 IRR: `calculate_monthly_irr(cash_flows)`
- 30일 IRR: 30일 전 행을 초기 투자금으로 별도 `calculate_period_irr()`

### 알파
- `calculate_alpha(start_row, end_row)` — `(twr_asset, ndx100)` 튜플
- 누적 알파: 전체 기간 시작 vs 실시간 end
- 30일 알파: 30일 전 행 vs 실시간 end
- 월평균 알파: 누적 알파를 `total_months`(기간/30일)로 나눠 산출, UI에 표시됨

### 베타
- `calculate_beta(rows)` — `[(total_asset, ndx100), ...]` 날짜 오름차순
- 전체/30일 두 값을 한 카드에 통합 표시

### 낙폭 분석 (MDD / Current DD / Recovery)
- `calculate_drawdown_metrics(series)` — TWR 기준 내 실적, NDX100 각각 계산
- JS에서 NDX 대비 내 우위(`diff = 내 - NDX`)를 빨강~초록 보간색으로 표시 (`ddColor`)

### Exposure / 비중
- `calculate_exposure_and_ratios(pos_rows, usd_krw)` — 실시간 positions 기반
- exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio 모두 소수(0~1) → 표시 시 × 100

### 레버리지 바
- x1/x2/x3/cash 각 비중을 flex 비율로 시각화
- 0.5% 미만 세그먼트는 렌더링 생략, 5% 미만은 텍스트 레이블 생략

### 종목 비중 도넛 (`_donut_svg` + `_build_donut_payload`)
- 같은 ticker 여러 계좌 합산
- KRW + USD 현금 합산 → "현금" 단일 슬라이스
- 평가액 내림차순, 상위 8 + 나머지 "기타" 합산
- 레버리지별 명도 팔레트 (`lev_palettes` — `_build_donut_payload` 내부 dict):
  - x1: `#00c073` → `#005c30` (5단계)
  - x2: `#e6a817` → `#755207` (5단계)
  - x3: `#ff4d4d` → `#991010` (5단계)
  - 현금: `#111111`, 기타: `#3a3a3a`
- SVG 130×130 (r_outer=58, r_inner=36), 슬라이스 간 1.5° 갭
- 각도 2° 단위 스냅 (`angle_snap=2.0`) — 미세 비중 변화 시 동일 문자열 → diff_display 재전송 방지

### 은퇴 시뮬레이션
- `scheduler/config.json`의 `retirement_date` (형식: "YYYYMMDD")
- `calculate_retirement_asset(total_asset, monthly_irr, retirement_date)`

---

## 6. UI 갱신 구조

`@render.ui` 없음. `_send_update()`가 `send_custom_message("db_update", diff)`로 변경분만 전송, JS가 DOM 직접 패치.

### JS 커스텀 메시지 핸들러: `db_update`

| key | 설명 |
|-----|------|
| `exposure` | `exposure_text`, `exp_cls`, `cash_ratio_text`, `cash_eval_text`, `lev_segs`(`[x1,x2,x3,cash]` 숫자 배열 — 레버리지 바/범례 HTML은 서버가 아니라 **JS가 클라이언트에서 직접 생성**) |
| `irr` | `annual_text/sign`, `monthly_text/sign`, `irr30_text/sign`, `cash_flow_text/sign` (`sign`은 `1`/`-1`/`0`, JS `pnlClass()`에서 색상 판단) |
| `alpha` | `cumul_text/sign`, `monthly_text/sign`, `alpha30_text/sign` |
| `beta` | `all_text`, `beta30_text` |
| `dd` | `mdd_mine_text`, `mdd_ndx_text`, `mdd_diff`, `cdd_mine_text`, `cdd_ndx_text`, `cdd_diff`, `rec_mine_text`, `rec_ndx_text`, `rec_diff` — `*_diff`(mine - ndx)는 `ddColor()` 색상 보간 전용 |
| `donut_text` | `legend`(dict 배열: `label`/`color`/`pct`/`is_cash` — **HTML 문자열 아님**, JS가 렌더링), `subtitle` |
| `donut_svg` | 도넛 SVG HTML (서버에서 완성된 HTML 그대로 주입) |
| `retirement` | `subtitle`, `amount_text`, `sub_text`, `compound_text` |

### DOM id 목록

| DOM id | 설명 |
|--------|------|
| `db-exposure-val` | 익스포저 수치 |
| `db-cash-ratio-val` | 현금 비중 |
| `db-cash-eval-val` | 현금 평가액 |
| `db-lev-bar-track` | 레버리지 바 세그먼트 컨테이너 |
| `db-lev-legend` | 레버리지 범례 컨테이너 |
| `db-annual-irr` | 연평균 IRR |
| `db-monthly-irr` | 월평균 IRR |
| `db-irr-30` | 30일 IRR |
| `db-cash-flow-val` | 총 입출금 |
| `db-cumul-alpha` | 누적 알파 |
| `db-monthly-alpha` | 월평균 알파 (표시됨) |
| `db-alpha-30` | 30일 알파 |
| `db-beta-all` | 베타 전체 |
| `db-beta-30` | 베타 30일 |
| `db-mdd-mine` | MDD 내 실적 |
| `db-mdd-ndx` | MDD NDX100 |
| `db-cdd-mine` | 현재낙폭 내 실적 |
| `db-cdd-ndx` | 현재낙폭 NDX100 |
| `db-rec-mine` | 회복률 내 실적 |
| `db-rec-ndx` | 회복률 NDX100 |
| `db-donut-svg-wrap` | 도넛 SVG 컨테이너 |
| `db-donut-legend` | 도넛 범례 컨테이너 |
| `db-donut-title-sub` | 도넛 부제목 |
| `db-retirement-subtitle` | 은퇴 시뮬레이션 부제목 |
| `db-retirement-amount` | 은퇴 시점 예상자산 |
| `db-retirement-sub` | IRR 복리 설명 |
| `db-retirement-compound` | 복리 개월 수 |

---

## 7. Server (`dashboard_server()`)

### 파라미터

| 파라미터 | 설명 |
|----------|------|
| `active_tab` | 상위(app.py)에서 주입 |
| `active_sub_tab` | asset_server에서 주입. `"dashboard"` 여부로 갱신 스킵 판단 |
| `db_summary_rows` | asset_server의 `_db_summary_rows` calc 주입 |
| `db_position_rows` | asset_server의 `_db_position_rows` calc 주입 |

### Reactive State

| 변수 | 타입 | 설명 |
|------|------|------|
| `_initialized` | bool (nonlocal) | 자기-재트리거 방지 |
| `_last_display` | dict | 이전 표시값 (diff 기준) |

---

## 8. 변경 이력 / 확인된 사실

- ✅ **버그 수정**: `datetime.date.today()` 직접 사용(UTC 기준) → `datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()`로 수정. 영향 범위: `_load_summary_data()` 내 `today`(L74, IRR cash_flows/30일 cutoff 기준) + `_send_update()` 내 은퇴 시뮬레이션 잔여 개월수 계산용 `today`(L848).
- ✅ **데드코드 제거**: `fmt_pct` 미사용 import 확인 후 제거 완료.
- ✅ **정정**: `daily_profit`은 구버전 문서의 "잔존하나 미사용"과 달리 **완전히 제거됨** (return dict에 키 자체 없음).
- ✅ **정정**: `monthly_alpha`는 구버전 문서의 "미표시"와 달리 **UI에 표시됨** (`db-monthly-alpha`).
- ✅ **정정**: 레버리지 바/범례(`lev_bar_html`/`lev_legend_html`)·도넛 범례(`legend_html`)는 서버가 HTML 문자열을 만들어 보내는 방식이 아니라, 서버가 데이터(`lev_segs`, `legend` dict 배열)만 보내고 **JS가 클라이언트에서 렌더링**하는 방식으로 변경됨.
- 기타 참고사항:
  - `daily_summary.cash_flow` 부호: +입금 / -출금 → XIRR 계산 시 반전 필요
  - `exposure`, `x*_ratio` 등 비율 컬럼은 0~1 소수로 저장됨
  - psycopg2 NUMERIC 컬럼은 `Decimal` 타입 반환 → `to_f()`로 float 변환
  - **plotly 미사용**: 도넛 차트 순수 SVG로 직접 생성
  - `diff_display`는 `app.utils.display_diff` → `from app.utils.display_diff import diff_display`