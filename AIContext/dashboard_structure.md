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
| 오늘 | 금일 순수익, Exposure + 레버리지 바 + 현금비중 통합 카드 |
| 수익률 | 연평균 IRR, 월평균 IRR (2열 그리드) |
| 알파/베타 | 누적 알파, 30일 알파 (2열 그리드), 베타 전체/30일 통합 카드 |
| 종목 비중 | SVG 도넛 + 범례 (상위 8 + 현금 합산 + 기타) |
| 은퇴 시뮬레이션 | 은퇴시점 예상자산, 월평균 IRR 복리 근거 표시 |

> **제거된 항목**: 기준일 표시, 월평균 알파 (UI에서 삭제됨)

---

## 데이터 흐름

### reactive.calc — data()
- `daily_summary` 전체 조회 (날짜 오름차순)
- `price_signal.get()` 의존 → 시세 갱신 시 자동 재계산
- 총자산/Exposure/비중: positions 실시간 계산
- IRR/알파/베타의 마지막 데이터포인트: 실시간 total_asset 사용
- 히어로 라인차트용 `chart_data` 포함 (최대 100포인트 샘플링)
- 계산 결과를 dict로 반환

### reactive.calc — position_data()
- `positions` + `tickers` **LEFT JOIN** 조회 (KRW/USD 현금 누락 방지)
- `price_signal.get()` 의존
- KRW: eval_krw = qty
- USD: eval_krw = qty (LEFT JOIN이라 price=None → 별도 usd_krw 주입 필요, 현재 `market in (NAS/AMS/ARC)`로만 환율 적용)
- 종목별 평가액(원화 환산) 반환

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
- SVG 300×110, `preserveAspectRatio="none"`, 히어로 오른쪽 55% 오버레이
- 그린 라인(#00c073) + 하단 그라데이션 fill + 끝점 원형 dot

### 종목 비중 도넛 (`_donut_svg` + `out_donut`)
- 같은 ticker 여러 계좌 합산
- KRW + USD 현금 합산 → "현금" 단일 슬라이스
- 평가액 내림차순 정렬, 상위 8 표시 + 나머지 "기타" 합산
- 레버리지별 명도 팔레트 (같은 레버리지 내 여러 종목 시 순차 어둡게):
  - x1: #00c073 → #007840 (4단계)
  - x2: #e6a817 → #755207 (4단계)
  - x3: #ff4d4d → #991010 (4단계)
  - 현금: #111111
  - 기타: #3a3a3a
- SVG 130×130 도넛 (r_outer=58, r_inner=36), 슬라이스 간 1.5° 갭
- 범례: 이름 + 비중% (CSS flex 레이아웃)

### 은퇴 시뮬레이션
- `scheduler/config.json`의 `retirement_date` (형식: "YYYYMMDD")
- `calculate_retirement_asset(total_asset, monthly_irr, retirement_date)`
- 남은 개월 수 / IRR / 복리 개월 수 함께 표시

---

## UI 출력 목록

| output_id | 설명 |
|-----------|------|
| `hero_block` | 총자산 히어로 전체 블록 (라인차트 SVG 포함) |
| `out_daily_profit` | 금일 순수익 카드 |
| `out_exposure_card` | Exposure + 레버리지 바 + 현금/투자 비중 통합 카드 |
| `out_annual_irr` | 연평균 IRR |
| `out_monthly_irr` | 월평균 IRR |
| `out_cumul_alpha` | 누적 알파 |
| `out_alpha_30` | 30일 알파 |
| `out_beta` | 베타 전체/30일 통합 카드 |
| `out_donut` | 종목 비중 도넛 (SVG + 범례) |
| `out_retirement` | 은퇴 시뮬레이션 카드 |

---

## 주요 주의사항

- **텍스트 색상**: 히어로 영역만 흰색(`#ffffff`), 나머지는 `#b0b0b0` 통일
- `daily_summary.cash_flow` 부호: +입금 / -출금 → XIRR 계산 시 반전 필요
- `exposure`, `x*_ratio` 등 비율 컬럼은 0~1 소수로 저장됨
- psycopg2 NUMERIC 컬럼은 `Decimal` 타입 반환 → `to_f()` 로 float 변환
- **plotly 미사용**: 도넛/라인 차트 모두 순수 SVG로 직접 생성 (plotly.js CDN 불필요)
- `price_signal`은 `app.price_signal` 모듈 레벨 전역 변수 → 직접 import해서 사용
- `_load_position_data()`의 LEFT JOIN에서 USD 현금은 price=None으로 반환됨 → market 조건 분기로 처리