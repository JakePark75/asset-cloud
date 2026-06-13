# 대시보드 화면 구조

## 파일 구성

| 파일 | 역할 |
|------|------|
| `app/modules/dashboard.py` | UI + Server (단일 파일) |
| `app/static/dashboard.css` | 대시보드 전용 스타일 (Bloomberg 다크테마) |
| `app/utils/metrics.py` | 순수 계산 함수 (대시보드 외 다른 모듈에서도 사용 가능) |

> **폰트**: JetBrains Mono + Noto Sans Mono KR (Google Fonts CDN)  
> `#dashboard-root` 및 하위 전체에 적용. `dashboard.css` 상단 `@import`로 로드.

---

## 화면 구성

| 섹션 | 항목 |
|------|------|
| 총자산 히어로 | 총자산(흰색), 전일 대비 증감(금액+%), 전체기간 라인차트 SVG 오버레이 |
| 오늘 | Exposure + 레버리지 바 + 현금비중 통합 카드 |
| 수익률 | 연평균 IRR, 월평균 IRR (2열 그리드) |
| 알파/베타 | 누적 알파, 30일 알파 (2열 그리드), 베타 전체/30일 통합 카드 |
| 종목 비중 | SVG 도넛 + 범례 (상위 8 + 현금 합산 + 기타) |
| 은퇴 시뮬레이션 | 은퇴시점 예상자산, 월평균 IRR 복리 근거 표시 |

> **제거된 항목**: 기준일 표시, 월평균 알파, 금일 순수익 (UI에서 삭제됨; `daily_profit`, `monthly_alpha`는 data dict에 잔존하나 미표시)

---

## 데이터 흐름

### `_load_summary_data()` — DAL
- `daily_summary` 전체 조회 (날짜 오름차순)
- 시세: `get_all_prices()` (Redis) — tickers DB 조회 없이 Redis에서 직접 매핑
- `usd_krw`: Redis `prices["USDKRW=X"]` → fallback 1300.0
- `^NDX`: Redis `prices["^NDX"]` → fallback None (prev_ndx100 사용)
- `today_cash_flow`: Redis `today_cash_flow` 키 → fallback 0
- positions 메타데이터(ticker, quantity, leverage, market)만 DB 조회
- 계산 결과를 dict로 반환

### `_load_position_data()` — DAL
- `positions` + `tickers` **LEFT JOIN** 조회 (KRW/USD 현금 누락 방지)
- SELECT 컬럼: `ticker, name, market, leverage, quantity` (name은 도넛 범례 표시용)
- 시세: `get_all_prices()` (Redis) 매핑
- KRW: eval_krw = qty
- USD: eval_krw = qty × usd_krw
- 그 외: `get_market_currency(market) == "USD"` 이면 qty × price × usd_krw, 아니면 qty × price
- 종목별 평가액(원화 환산) dict 리스트 반환

### `@reactive.calc` — `data()` / `position_data()`
- `data()`: `price_signal` + `daily_insert_signal` 모두 구독 → 시세 갱신 또는 daily_summary 신규 행 삽입 시 재계산. `_load_summary_data()` 호출 결과를 캐싱.
- `position_data()`: `price_signal` 구독 → `_load_position_data()` 호출 결과를 캐싱.
- 같은 signal 값이면 재계산 없이 캐시 반환.

### `@reactive.effect` — `_send_update()`
- `data()`, `position_data()` 의존성을 통해 `price_signal`, `daily_insert_signal`에 연결됨
- 탭 비활성 시 스킵 (`active_tab.get() != "dashboard"`)
- `_load_summary_data()` + `_load_position_data()` 결과를 화면 표시용 포맷 문자열로 변환
- `current` dict 구성 → `diff_display(current, _last_display)` → 변경된 key만 `send_custom_message("db_update", diff)`
- `_last_display`: 세션별 클로저 (`dict = {}`)
- 빈 diff면 전송 스킵
- `async def`로 선언 (`await session.send_custom_message(...)`)

---

## 주요 계산 로직

### XIRR / IRR
- cash_flows 구성:
  - 첫 항목: `(첫날, -첫날 total_asset)` — 최초 투자금 음수
  - 중간: `(date, -cash_flow)` — DB 부호 반전 (DB: +입금/-출금 → XIRR: -입금/+출금)
  - 마지막: `(오늘, 실시간 total_asset)` — 현재 자산 양수
- 연평균 IRR: `calculate_xirr(cash_flows)`
- 월평균 IRR: `calculate_monthly_irr(cash_flows)`

### 알파
- `calculate_alpha(start_row, end_row)` — `(twr_asset, ndx100)` 튜플
- 누적 알파: 전체 기간 시작 vs 실시간 end
- 30일 알파: 30일 전 행 vs 실시간 end
- **월평균 알파는 UI에서 제거됨** (계산은 data()에 포함, 표시 안 함)

### 베타
- `calculate_beta(rows)` — `[(total_asset, ndx100), ...]` 날짜 오름차순
- 일별 수익률 시계열로 공분산 / NDX100 분산
- 전체/30일 두 값을 한 카드(`out_beta`)에 통합 표시

### Exposure / 비중
- `calculate_exposure_and_ratios(pos_rows, usd_krw)` — 실시간 positions 기반
- exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio 모두 소수(0~1) → 표시 시 × 100
- Exposure 통합 카드: Exposure 수치 + 현금/투자 비중 + 레버리지 바 한 카드에 통합

### 레버리지 바 (`out_exposure_card`)
- x1/x2/x3/cash 각 비중을 flex 비율로 시각화
- 0.5% 미만 세그먼트는 렌더링 생략
- 5% 미만은 텍스트 레이블 생략

### 히어로 라인차트 (`_hero_line_svg`)
- `daily_summary.total_asset` 이력 + 실시간 total_asset 마지막 포인트
- n > 100이면 균등 샘플링 100포인트, 마지막은 항상 실시간값
- SVG viewBox="0 0 100 100", `preserveAspectRatio="none"`, 히어로 오른쪽 오버레이
- 그린 라인(#00c073) + 하단 그라데이션 fill
- 좌표 포맷 `.0f` — 미세 변화 시 문자열 동일 → diff_display가 불필요한 재전송 방지

### 종목 비중 도넛 (`_donut_svg` + `_build_donut_payload`)
- 같은 ticker 여러 계좌 합산
- KRW + USD 현금 합산 → "현금" 단일 슬라이스
- 평가액 내림차순 정렬, 상위 8 표시 + 나머지 "기타" 합산
- 레버리지별 명도 팔레트 (같은 레버리지 내 여러 종목 시 순차 어둡게):
  - x1: #00c073 → #005c30 (5단계: #00c073, #00a862, #009050, #007840, #005c30)
  - x2: #e6a817 → #755207 (5단계: #e6a817, #c98f0f, #ad7a0c, #916509, #755207)
  - x3: #ff4d4d → #991010 (5단계: #ff4d4d, #e63c3c, #cc2c2c, #b21c1c, #991010)
  - 현금: #111111
  - 기타: #3a3a3a
- 팔레트는 `_build_donut_payload()` 내부에 dict로 정의 (`lev_palettes`). 모듈 레벨 `_LEV_PALETTES` 상수도 존재하나 도넛에는 미사용 (4단계, 별도 용도)
- SVG 130×130 도넛 (r_outer=58, r_inner=36), 슬라이스 간 1.5° 갭
- 각도 2° 단위 스냅 (`angle_snap=2.0`) — 미세 비중 변화 시 SVG 문자열 동일 → diff_display 재전송 방지
- 범례: 이름 + 비중% (CSS flex 레이아웃)

### 은퇴 시뮬레이션
- `scheduler/config.json`의 `retirement_date` (형식: "YYYYMMDD")
- `calculate_retirement_asset(total_asset, monthly_irr, retirement_date)`
- 남은 개월 수 / IRR / 복리 개월 수 함께 표시

---

## UI 갱신 구조

`@render.ui` 없음. `_send_update()`가 `send_custom_message("db_update", diff)`로 변경분만 전송, JS가 DOM 직접 패치.

### JS 커스텀 메시지 핸들러: `db_update`

| key | 설명 |
|-----|------|
| `hero_text` | `total_asset`, `delta_text`, `delta_val` — 히어로 텍스트 |
| `hero_chart_svg` | 히어로 라인차트 SVG HTML |
| `exposure` | `exposure_text`, `exp_cls`, `cash_ratio_text`, `cash_eval_text`, `lev_bar_html`, `lev_legend_html` |
| `irr` | `annual_text`, `annual_val`, `monthly_text`, `monthly_val` |
| `alpha` | `cumul_text`, `cumul_val`, `alpha30_text`, `alpha30_val` |
| `beta` | `all_text`, `beta30_text` |
| `donut_text` | `legend_html`, `subtitle` — 도넛 범례/제목 |
| `donut_svg` | 도넛 SVG HTML |
| `retirement` | `subtitle`, `amount_text`, `sub_text`, `compound_text` |

> 무거운 항목(SVG, HTML 블록)은 별도 key로 분리 — 텍스트만 바뀐 경우 SVG 재전송 방지

### DOM id 목록

| DOM id | 설명 |
|--------|------|
| `db-hero-amount` | 총자산 금액 |
| `db-hero-delta-text` | 전일 대비 텍스트 |
| `db-hero-chart-inner` | 히어로 라인차트 SVG 컨테이너 |
| `db-exposure-val` | 익스포저 수치 |
| `db-cash-ratio-val` | 현금 비중 |
| `db-cash-eval-val` | 현금 평가액 |
| `db-lev-bar-track` | 레버리지 바 세그먼트 |
| `db-lev-legend` | 레버리지 범례 |
| `db-annual-irr` | 연평균 IRR |
| `db-monthly-irr` | 월평균 IRR |
| `db-cumul-alpha` | 누적 알파 |
| `db-alpha-30` | 30일 알파 |
| `db-beta-all` | 베타 전체 |
| `db-beta-30` | 베타 30일 |
| `db-donut-svg-wrap` | 도넛 SVG 컨테이너 |
| `db-donut-legend` | 도넛 범례 |
| `db-donut-title-sub` | 도넛 부제목 |
| `db-retirement-subtitle` | 은퇴 시뮬레이션 부제목 |
| `db-retirement-amount` | 은퇴 시점 예상자산 |
| `db-retirement-sub` | IRR 복리 설명 |
| `db-retirement-compound` | 복리 개월 수 |

---

## 주요 주의사항

- **텍스트 색상**: 히어로 영역만 흰색(`#ffffff`), 나머지는 `#b0b0b0` 통일
- `daily_summary.cash_flow` 부호: +입금 / -출금 → XIRR 계산 시 반전 필요
- `exposure`, `x*_ratio` 등 비율 컬럼은 0~1 소수로 저장됨
- psycopg2 NUMERIC 컬럼은 `Decimal` 타입 반환 → `to_f()` 로 float 변환
- **plotly 미사용**: 도넛/라인 차트 모두 순수 SVG로 직접 생성
- `price_signal`, `daily_insert_signal` 모두 `app.price_signal` 모듈 레벨 전역 변수 → `from app.price_signal import price_signal as _price_signal, daily_insert_signal as _daily_insert_signal`
- `diff_display`는 `app.utils.display_diff` → `from app.utils.display_diff import diff_display`
- **`daily_profit`**: `_load_summary_data()` 에서 계산되어 return dict에 포함되나, `_send_update()` 에서 미사용 — DOM id 및 UI 표시 없음 (제거된 항목)
- **월평균 알파**: `_load_summary_data()` 내부에서 계산되나 UI에 미표시 (제거됨)