# 대시보드 화면 구조

## 파일 구성

| 파일 | 역할 |
|------|------|
| `app/modules/dashboard.py` | UI + Server (단일 파일) |
| `app/utils/metrics.py` | 순수 계산 함수 (대시보드 외 다른 모듈에서도 사용 가능) |

---

## 화면 구성

| 섹션 | 항목 |
|------|------|
| 총자산 히어로 | 총자산, 전일 대비 증감 (금액 + %) |
| 오늘 | 금일 순수익, 기준일 |
| 비중 | Exposure, 현금비중, 레버리지 비중 도넛, 종목 비중 도넛 |
| 수익률 | 연평균 IRR, 월평균 IRR |
| 알파/베타 | 누적 알파, 월평균 알파, 최근 30일 알파, 베타(전체), 베타(30일) |
| 은퇴 시뮬레이션 | 은퇴시점 예상자산 |

---

## 데이터 흐름

### reactive.calc — data()
- `daily_summary` 전체 조회 (날짜 오름차순)
- `price_signal.get()` 의존 → 시세 갱신 시 자동 재계산
- 계산 결과를 dict로 반환

### reactive.calc — position_data()
- `positions` + `tickers` JOIN 조회
- `price_signal.get()` 의존
- 종목별 평가액(원화 환산) 반환

---

## 주요 계산 로직

### XIRR / IRR
- cash_flows 구성:
  - 첫 항목: `(첫날, -첫날total_asset)` — 최초 투자금 음수
  - 중간: `(date, -cash_flow)` — DB 부호 반전 (DB: +입금/-출금 → XIRR: -입금/+출금)
  - 마지막: `(최신날, total_asset)` — 현재 자산 양수
- 연평균 IRR: `calculate_xirr(cash_flows)`
- 월평균 IRR: `(1 + annual_irr) ** (1/12) - 1`

### 알파
- `calculate_alpha(start_row, end_row)` — `(total_asset, ndx100)` 튜플
- 누적 알파: 전체 기간 시작 vs 최신
- 월평균 알파: 누적 알파 / 전체 기간(월수)
- 30일 알파: 30일 전 행 vs 최신

### 베타
- `calculate_beta(rows)` — `[(total_asset, ndx100), ...]` 날짜 오름차순
- 일별 수익률 시계열로 공분산 / NDX100 분산
- 전체 기간 / 최근 30일 두 가지

### Exposure / 비중
- `daily_summary` 최신행의 `exposure`, `cash_ratio`, `x1_ratio`, `x2_ratio`, `x3_ratio` 직접 사용
- 비율 컬럼은 소수(0~1) 저장 → 화면 표시 시 × 100

### 종목 비중 도넛
- `positions` + `tickers` JOIN
- USD 종목: `qty × price × usd_krw` (USDKRW=X 환율 적용)
- KRW 현금: qty 그대로
- 현금(KRW/USD)은 별도 집계 후 마지막 슬라이스로 추가
- 색상: x1=#00c073, x2=#e6a817, x3=#ff4d4d, 현금=#444444

### 은퇴 시뮬레이션
- `scheduler/config.json`의 `retirement_date` (형식: "YYYYMMDD")
- `calculate_retirement_asset(total_asset, monthly_irr, retirement_date)`
- 현재 총자산에 월평균 IRR 복리 적용

---

## 주요 주의사항

- `daily_summary.cash_flow` 부호: +입금 / -출금 → XIRR 계산 시 반전 필요
- `exposure`, `x*_ratio` 등 비율 컬럼은 0~1 소수로 저장됨
- psycopg2 NUMERIC 컬럼은 `Decimal` 타입 반환 → `to_f()` 로 float 변환
- plotly 차트는 `fig.to_html(full_html=False, include_plotlyjs=False)` + `@render.ui` 방식 (plotly.js는 app.py head에서 CDN 전역 로드)
- `price_signal`은 `app.price_signal` 모듈 레벨 전역 변수 → 직접 import해서 사용