````md
# FMP 기반 미국 빅테크 가치평가 서비스 설계

작성일: 2026-06-24

---

# 1. 목표

본 서비스의 목적은 단순 종목 조회가 아니라,

> "현재 미국 빅테크 기업 중 상대적으로 저평가된 종목을 찾는 것"

이다.

대상 종목은 주로:

- MSFT
- AAPL
- GOOGL
- META
- AMZN
- NVDA
- NFLX
- AVGO
- TSLA

등 미국 대형 성장주 위주로 한정한다.

소형주, 적자기업, 테마주 분석은 우선순위에서 제외한다.

---

# 2. 데이터 소스

## 가격 데이터

Yahoo Finance

사용 목적

- 현재가
- 과거 가격
- 수익률 계산
- 벤치마크 비교
- Beta 계산
- Alpha 계산

---

## 재무 데이터

Financial Modeling Prep (FMP)

사용 목적

- 재무제표
- 현금흐름표
- 재무상태표
- 애널리스트 추정치

---

# 3. FMP 무료 플랜에서 확인된 데이터

## Quote

확인 완료

제공 데이터

- Price
- Market Cap
- 52주 고가
- 52주 저가
- 거래량

---

## Analyst Estimates

확인 완료

제공 데이터

- Revenue Forecast
- EPS Forecast
- EBITDA Forecast
- Net Income Forecast

예시

```json
{
  "date": "2026-06-30",
  "epsAvg": 16.80
}
```

---

## Income Statement

확인 완료

제공 데이터

- Revenue
- Gross Profit
- Operating Income
- EBITDA
- Net Income
- EPS

5년 이상 확보 가능

---

## Balance Sheet

확인 완료

제공 데이터

- Total Assets
- Total Liabilities
- Total Debt
- Equity
- Net Debt
- Cash

---

## Cash Flow Statement

확인 완료

제공 데이터

- Operating Cash Flow
- Capital Expenditure
- Free Cash Flow

5년 이상 확보 가능

---

# 4. 원칙

API가 계산한 지표를 사용하지 않는다.

가능한 모든 지표는 원시 데이터를 이용하여 내부 계산한다.

예)

사용 안함

```text
Forward PE (API 제공)
PEG (API 제공)
ROE (API 제공)
```

사용

```python
forward_pe = price / forecast_eps

peg = forward_pe / eps_growth

roe = net_income / equity
```

장점

- 계산 로직 통제 가능
- 공급자 변경 가능
- 검증 가능
- 일관성 유지

---

# 5. 채택 예정 지표

## 5.1 Valuation

### Trailing PE

계산

```python
price / ttm_eps
```

의미

과거 12개월 실적 기준 밸류에이션

---

### Forward PE

계산

```python
price / next_year_eps
```

의미

향후 1년 예상 실적 기준 밸류에이션

중요도

★★★★★

---

### Run Rate PE

계산

```python
price / (latest_quarter_eps * 4)
```

의미

최근 분기 실적이 향후 1년 유지된다고 가정

중요도

★★★☆☆

Forward PE 보조지표

---

### PEG

계산

```python
forward_pe / eps_growth
```

의미

성장률 대비 현재 가격 수준

중요도

★★★★★

---

### PSR

계산

```python
market_cap / revenue
```

의미

매출 기준 밸류에이션

중요도

★★★★☆

---

### EV / EBITDA

계산

```python
enterprise_value / ebitda
```

의미

기업가치 대비 영업현금창출력

중요도

★★★★☆

---

### EV / FCF

계산

```python
enterprise_value / free_cash_flow
```

의미

기업가치 대비 실제 현금창출력

중요도

★★★★★

---

# 5.2 Growth

### Revenue Growth

계산

```python
forecast_revenue_growth
```

중요도

★★★★★

---

### EPS Growth

계산

```python
forecast_eps_growth
```

중요도

★★★★★

---

### Revenue CAGR

3년

5년

계산

```python
(revenue_end / revenue_start) ** (1 / years) - 1
```

중요도

★★★★☆

---

### EPS CAGR

3년

5년

중요도

★★★★★

---

### FCF CAGR

3년

5년

중요도

★★★★★

---

# 5.3 Quality

### Gross Margin

계산

```python
gross_profit / revenue
```

중요도

★★★★☆

---

### Operating Margin

계산

```python
operating_income / revenue
```

중요도

★★★★★

---

### Net Margin

계산

```python
net_income / revenue
```

중요도

★★★★☆

---

### FCF Margin

계산

```python
free_cash_flow / revenue
```

중요도

★★★★★

---

### ROE

계산

```python
net_income / equity
```

중요도

★★★★☆

---

### Debt / Equity

계산

```python
total_debt / equity
```

중요도

★★★☆☆

---

# 6. 보조 지표

## 1년 수익률

Yahoo 사용

---

## 3년 수익률

Yahoo 사용

---

## NASDAQ100 대비 성과

계산

```python
stock_return - ndx_return
```

---

## S&P500 대비 성과

계산

```python
stock_return - sp500_return
```

---

# 7. 채택하지 않는 지표

## Beta

채택 안함

이유

- 가치평가와 관계 약함
- 투자판단 기여도 낮음
- 빅테크 비교에 유용성 제한

---

## Alpha

채택 안함

이유

- 트레이딩 성격 지표
- 장기 가치평가에 활용도 낮음

---

## API 제공 Ratio

채택 안함

예

- API PE
- API PEG
- API ROE

이유

원시 데이터 기반 직접 계산 원칙

---

# 8. 커스텀 지표

## Growth Adjusted Value

계산

```python
eps_growth / forward_pe
```

의미

성장률 대비 현재 가격 매력도

높을수록 유리

---

## Revenue Efficiency Score

계산

```python
revenue_growth * operating_margin
```

의미

성장성과 수익성을 동시에 평가

---

## FCF Efficiency Score

계산

```python
fcf_margin * fcf_growth
```

의미

현금창출 능력 평가

---

# 9. 최종 우선순위

## S급

- Forward PE
- PEG
- EV / FCF
- EPS Growth
- Revenue Growth
- FCF Margin
- FCF CAGR

---

## A급

- EV / EBITDA
- Operating Margin
- Revenue CAGR
- EPS CAGR
- ROE

---

## B급

- Trailing PE
- Run Rate PE
- PSR
- Debt / Equity

---

# 10. 구현 방향

## 데이터 수집

Yahoo

- 가격
- 벤치마크

FMP

- 재무
- 추정치

---

## 계산 엔진

모든 지표 직접 계산

---

## 비교 대상

기본

- MSFT
- AAPL
- GOOGL
- META
- AMZN
- NVDA

---

## 결과 예시

```text
MSFT

Valuation
--------------------------------
Forward PE        22.3
PEG               0.99
EV/FCF            18.7

Growth
--------------------------------
Revenue Growth    16.9%
EPS Growth        22.6%

Quality
--------------------------------
Operating Margin  45.6%
FCF Margin        25.4%

Score
--------------------------------
Growth Value Score 87
```

---

# 결론

현재 확인된 FMP 무료 플랜 데이터만으로도 미국 빅테크 중심의 가치평가 서비스 구축은 충분히 가능하다.

특히 다음 지표를 핵심으로 사용한다.

1. Forward PE
2. PEG
3. EV / FCF
4. EPS Growth
5. Revenue Growth
6. FCF Margin
7. FCF CAGR

이 조합이 현재 서비스의 핵심 가치평가 축이 된다.
````
