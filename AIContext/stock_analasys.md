# FMP 빅테크 가치평가 서비스 — 진행 요약

> 이 문서는 매 세션 시작 시 첫 메시지에 첨부해서 사용.
> **확정된 사실과 원칙만 남기고, 세션별 시행착오와 일기식 기록은 남기지 않는다.**

---

## 🏗️ 이 서비스의 위치 — 개인 자산관리 시스템(`asset-cloud`)에 통합됨

가치평가 서비스는 독립 프로젝트가 아니라 기존 개인 자산관리 클라우드 서비스(`asset-cloud`)의 일부 기능이다. `asset-cloud/scheduler/`는 가치평가와 자산관리 시스템(`price_updater`, `daily_inserter`, `myassets`, `news_fetcher`)이 공유하는 디렉토리다. 가치평가 파일은 `valuation_` 프리픽스로 flat 배치하고, 폴더 분리는 전체 리팩토링 시점까지 보류한다. `fmp_symbols`(가치평가 대상)과 `tickers`(자산관리 보유 종목)는 완전히 다른 모집단이다.

**이 가치평가 모듈은 대시보드(`app/modules/dashboard.py`)나 `daily_summary`와 완전히 무관한 독립 모듈이다. 설계/계산 논의 시 그쪽 구조와 절대 엮지 말 것.**

---

## 🤖 Claude 작업 지침 (매 세션 최우선 적용)

- DB/터미널 명령어 제공 시 항상 계정 정보 포함해서 바로 실행 가능한 형태로 줄 것:
  `PGPASSWORD=qkrworb0! psql -U jake -d assetdb -P pager=off -c "..."`
- 여러 단계로 이루어진 작업(백업→실행→검증 등)을 안내할 때는, 검증(diff) 단계를 실행(쓰기) 단계보다 먼저 제시할 것.
- DB/Redis 읽기·쓰기 시 반드시 검토: 해당 호출이 꼭 필요한 시점인지, 불필요하게 반복 실행되는 건 아닌지 확인.
- **다른 도구(예: GitHub Copilot)가 이미 이 프로젝트에 작업을 했을 수 있다는 걸 항상 전제할 것.** 파일을 리뷰하기 전에 "이 코드가 설계 단계 초안인지, 이미 실행되어 실제 DB에 반영된 상태인지"부터 확인. 사용자가 과거 대화 로그를 붙여주면 그 안의 실제 실행 기록(dry-run 여부, 실제 upsert 여부, DB 조회 결과)을 근거로 상태를 재구성할 것 — 추측하지 않는다.
- **문서에 "완료/수정됨"으로 적혀 있어도 반드시 실제 코드/DB를 재확인한 뒤 신뢰할 것.** CAGR 버그, `net_debt_equity` upsert 누락 사례에 이어, 이번 세션에서도 **`excess_return_vs_benchmark()`가 "이미 구현됨"으로 문서에 적혀 있었지만 실제 코드에는 없었던 것**이 확인됨 — 문서 서술과 실제 배포 코드가 어긋날 수 있음을 항상 전제.
- 검토가 필요한 프로젝트 내 파일이 있으면 바로 요청하고, 추론에 의해 분석이나 수정 코드를 작성하지 않는다. 파일명이나 문서 서술만으로 역할·의존성을 추측하지 말고 항상 실제로 열어보거나 grep으로 확인.
- 코드 작성 시 편의를 위한 임시적 우회나 암묵적 가정에 기대지 않는다.
- 사용자가 확정적으로 산출물을 요청하기 전에 성급히 긴 설명이나 코드 예시를 제공하지 않는다.
- 사용자가 코드 파일을 업로드한 경우, 수정 코드 제공은 반드시 artifact(파일)로 한다.
- 기술 선택/추천 시 반드시 근거(웹 검색 등) 명시.
- 코드 작성 전, 사용할 API/함수/동작 방식이 추론이나 기억 기반이면 반드시 사실 확인 후 사용자와 방향 동의를 받고 나서 작성한다.
- 디버깅/이상 신호 대응: 확증 없이 롤백/수정 등 되돌리기 어려운 조치를 먼저 실행하지 않는다.
- 여러 개의 결정이 동시에 필요할 때, 근거 없이 한 번에 몰아서 묻지 말 것. 각 결정마다 왜 확인이 필요한지 먼저 짚고, 필요한 확인들을 정리해서 한 번에 제시하되 사용자가 답하기 전에 코드를 작성하지 않는다.
- 터미널 명령어를 요청할 때, 여러 개가 필요하면 한 번에 묶어서 제시할 것. 하나씩 순차적으로 반복해서 요청하지 않는다.
- 의도 재확인: 맥락상 이상한 요청이면 맹목적으로 수행하지 않고 의도를 먼저 확인한다.
- 모든 대화는 팩트 위주로 제공하고, 팩트가 아닌 예상/추론은 반드시 그렇다고 명시한다.
- 대화 중 제공된 내용을 주장하기 전에 반드시 해당 부분을 다시 확인한다.
- 오라클 클라우드 무료 티어 범위 내에서만 서비스 구성, 유료 서비스 선택 유도하지 않는다.
- 여러 선택지가 있으면 성급히 1개만 제안하지 말고 사용자에게 선택지를 제공한다.
- 조사/분석 범위를 한 번에 넓히지 말 것. 확정 가능한 것부터 즉시 종결하고 문서에 남긴 뒤 다음으로 넘어간다.
- 프로덕션 스크립트를 수정할 때, 백업 명령을 내려도 사용자가 실제로 그 시점에 실행했는지, 파일을 먼저 교체하고 백업한 건 아닌지 확인 없이 신뢰하지 말 것. **단, 사용자가 명시적으로 "백업/비교 절차 생략"을 지시하면 그 지시를 따르고 반복 요구하지 않는다.**
- 기존 함수/모듈을 수정할 때, 그 함수가 이미 다른 여러 곳에서 쓰이고 있을 가능성과 그 수정이 일으킬 사이드이펙트를 반드시 짚고 넘어갈 것. 수정 전 `grep -rn`으로 다른 호출부 존재 여부를 반드시 실측 확인한다.
- 새 모듈/의존성을 프로덕션 스크립트에 추가할 때, "패키지가 설치돼 있는지"만 확인하는 걸로는 부족하다. 실제 배포 환경(systemd의 `WorkingDirectory`)에서 import 자체가 되는지까지 확인해야 한다.
- **COALESCE 방식 upsert가 있는 테이블을 재적재하기 전에는, 그 필드에 과거 수동 패치 이력이 있는지 먼저 확인할 것.**
- **systemd `Type=oneshot`인 서비스는, 연결된 python 스크립트 파일만 디스크에서 교체하면 되고 별도 restart/daemon-reload가 필요 없다.** `daemon-reload`가 필요한 경우는 `.service`/`.timer` unit 파일 자체를 변경했을 때뿐.
- 실행할 때마다 재생성되는 로그/캐시 파일은 git으로 추적하지 않는다(`.gitignore`).
- **파일을 상대경로로 `open()`하는 스크립트는 실행 cwd에 따라 결과물 위치가 달라질 수 있다.** systemd(`WorkingDirectory` 고정)와 사용자 수동 실행(cwd 임의)이 섞여 있는 프로젝트에서는 특히 주의. 로그/출력 파일 경로는 스크립트 자신의 디렉토리 기준 절대경로(`_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))`)로 고정할 것.
- **DB 조회 함수의 배치 위치는 프로젝트 기존 관례를 따른다**: 순수 계산 함수(DB 미접근)는 `app/utils/*.py`, 화면에서 필요한 DB 조회는 별도 서비스 파일로 분리하지 않고 해당 화면 모듈(`app/modules/*.py`) 안에서 `with get_db() as conn:` + `@reactive.calc`로 직접 구현. 스케줄러 실행 스크립트의 DB 조회는 해당 스크립트 안에 `_load_xxx(conn, ...)` 형태의 private 함수로 정의. "DB 조회만 하는 독립 서비스 파일"이라는 선례는 이 프로젝트에 없음.
- **Claude의 메모리 시스템(세션 간 자동 기억)에는 이 프로젝트 관련 내용을 저장하지 말 것.** 세션 간 인수인계는 반드시 이 진행 요약 문서(매 세션 사용자가 직접 첨부)로만 한다.

---

## 📌 이 문서의 역할

- 이 문서는 **FMP 빅테크 가치평가 모듈의 단일 living document**다.
- 이 문서에는 다음만 남긴다: 모듈의 목적 / 현재 확정된 구조 / 최종 목표 구조 / 현재 진행 상태 / 해야 할 일의 순서 / 나중에 다시 확인할 가치가 있는 사실·제약.
- 단순 기록성 메모, 세션별 경위, 이미 해결된 과거 시행착오는 남기지 않는다.

## 🎯 목적

이 모듈은 FMP 원천 데이터를 바탕으로 **미국 빅테크 10개 종목**(AAPL, AMD, AMZN, GOOGL, META, MSFT, NFLX, NVDA, PLTR, TSLA)의 투자 후보를 고르는 판단 엔진이다. 핵심은 단순 지표 나열이 아니라, 가치/성장/퀄리티/재무안정성 축으로 종목을 분류하고 비교하는 것이다. 대시보드/포트폴리오 모듈과는 완전히 독립적이다.

## 🏗️ 현재 구조

- 원천 데이터
  - `fmp_financials`: 연간 재무
  - `fmp_estimates`: 애널리스트 추정치
  - `fmp_price_history`: 종가 원천 이력 (symbol별 + `NDX` 벤치마크, `close_price`만 보유, PK `(symbol, date)`)
  - `fmp_quarterly_financials`: 분기 재무 (SEC EDGAR 백필 + Alpha Vantage 폴백으로 채워짐)
- 파생/스냅샷 저장소
  - `fmp_metrics`: 일별 파생 통계 스냅샷 저장소. `(symbol, calculated_at)` PK. `net_debt_equity` 컬럼 포함.
- 실제 데이터 흐름 (확인 완료)
  - `scheduler/valuation_fmp_collector.py`가 매일 08:40 KST에 FMP API를 호출해 `fmp_financials`/`fmp_estimates`를 채우고, **동시에 `fmp_metrics`의 `price`/`market_cap`/`enterprise_value`도 먼저 upsert**한다 (`upsert_metrics_quote()`).
  - `scheduler/valuation_sec_edgar_pipeline.timer`(08:30 KST)가 `valuation_sec_edgar_raw_collect.py` → `valuation_sec_edgar_backfill.py` → `valuation_classify_review_log.py` 순으로 `fmp_quarterly_financials`를 채운다.
  - `scheduler/valuation_fmp_metrics.py`는 그 뒤(08:50 KST, systemd 등록 완료)에 실행되어, 이미 price/market_cap/EV가 채워진 `fmp_metrics` 행에 나머지 파생 지표(trailing_pe 등)를 계산해서 채운다.
  - `fmp_price_history`는 이 일별 파이프라인에서 직접 쓰이지 않는다. 현재는 `valuation_fmp_price_backfill.py`(1회성 backfill)에서만 쓰인다. NDX 초과수익률 기능을 만들 때 새로 읽는 조회 로직이 필요 (화면 모듈 구현 시 함께 작성 예정, 아래 참고).
- `fmp_symbols`의 `is_active=false` 종목은 계산 대상에서 제외한다.

## 🧭 최종 목표 구조

- 계산 엔진이 `fmp_financials` + `fmp_quarterly_financials` + `fmp_estimates`를 읽고, 별도로 `fmp_collector`가 채워놓은 price/market_cap/EV 스냅샷 위에 파생 지표를 upsert한다.
- `fmp_metrics`에는 가격/시총/EV 스냅샷과 함께 trailing PE, run-rate PE, PEG, 성장률, 마진, ROE, FCF 효율, `net_debt_equity` 같은 해석 가능한 지표가 쌓인다.
- 화면은 이 원천과 파생 지표를 읽어 최신값과 추이를 보여주되, 목적은 "투자 후보 판단"이다.
- **NDX 대비 초과수익률은 `fmp_metrics`에 고정 기간으로 저장하지 않고, 화면/조회 시점에 사용자가 지정한 기간으로 동적 계산한다** (아래 "지표 체계" 참고).

## 📊 투자 판단 지표 체계

이 모듈은 단순 지표 나열이 아니라 **투자 후보를 고르는 판단 엔진**이다. 대상 유니버스가 미국 빅테크 10개 종목이므로, 범용 가치주 관점이 아니라 **성장/퀄리티 비중이 더 높은 상대평가 체계**로 설계한다.

- v1 핵심 축
  - **Valuation** ("지금 싼가?"): `trailing_pe`, `forward_pe`, `run_rate_pe`, `psr`, `ev_ebitda`, `ev_fcf`
  - **Growth** ("비싼 이유가 성장인가?"): `revenue_cagr_3y`, `revenue_cagr_5y`, `eps_cagr_3y`, `eps_cagr_5y`, `fcf_cagr_3y`, `fcf_cagr_5y`
  - **Quality** ("성장이 실제 이익/현금흐름으로 바뀌는가?"): `gross_margin`, `operating_margin`, `net_margin`, `fcf_margin`, `roe`, `fcf_efficiency`
  - **Risk** ("좋아 보여도 재무적으로 안 무너지는가?"): `debt_equity`, `net_debt_equity`
- v1 우선 노출 지표
  - Valuation: `trailing_pe`, `ev_fcf`, `psr`
  - Growth: `revenue_cagr_3y`, `eps_cagr_3y`, `fcf_cagr_3y`
  - Quality: `gross_margin`, `operating_margin`, `fcf_margin`, `roe`
  - Risk: `debt_equity`, `net_debt_equity`
- **NDX 대비 초과수익률** (beta 대체 보조지표, 확정)
  - beta는 v1 핵심 지표에서 제외 확정.
  - 대신 "지수 대비 아웃퍼폼/언더퍼폼"을 보조지표로 채택. `종목 수익률 − NDX 수익률`로 계산.
  - **기간은 고정하지 않고 동적으로 계산**한다. 사용자가 화면에서 원하는 기간(1년/3년/5년/임의 기간)을 선택하면 그때 `fmp_price_history`를 읽어 계산한다. `fmp_metrics`에 기간별 컬럼으로 저장하지 않는다.
  - 기간 시작/종료 가격은 각각 해당 시점 "이전 가장 가까운 거래일" 종가 1건씩만 조회하면 충분 (구간 전체 날짜를 맞출 필요 없음 — 종목/NDX 모두 동일한 미국 거래소 캘린더를 따르므로).
  - **계산 순수 함수 `excess_return_vs_benchmark()`는 `app/utils/fmp_valuation.py`에 실제 구현·커밋 완료.** `safe_div`로 두 비율을 각각 구한 뒤 차감, 가격 None/0이면 None 반환.
  - `fmp_price_history` 실제 DB 조회(종목 + NDX 각각의 `close_price` 시계열 가져오는 부분)는 **아직 미구현**. 화면 모듈(`app/modules/`) 구현 시 `get_db()` + `@reactive.calc`로 함께 작성하는 것으로 확정 — "해야 할 일 순서"의 조회 로직 작업과 화면 UI 작업이 하나로 통합됨 (이 프로젝트엔 DB 조회 전용 독립 서비스 파일 선례가 없기 때문).
- 검토 중 (보류, v2 후보)
  - `growth_adj_value`, `revenue_efficiency`: origin 미확인 상태. v1에서는 계산·사용하지 않고, DROP도 하지 않는다.
- 제외
  - beta는 핵심 지표에서 제외
  - 과도하게 복잡한 파생점수는 v1에서 제외
  - 기간이 중복되는 과잉 지표는 v1에서 제외

## 🧮 세부 계산식 초안

- 기준 원칙
  - 모든 값은 `calculated_at` 시점 기준의 as-of snapshot으로 계산한다.
  - 원천 값이 부족하면 0으로 채우지 않고 NULL을 유지한다.
  - **CAGR 계산은 `as_of`의 달력 연도가 아니라, 실제로 존재하는 `latest_annual`(최신 연간 데이터)의 fiscal_year를 기준으로 n년 전 연도를 찾는다.** (`app/utils/fmp_valuation.py`의 `build_snapshot_metrics()`에 실제 반영 및 AAPL 실측 검증 완료)

- Valuation
  - `trailing_pe` = `price / TTM diluted EPS` (TTM = 최신 4개 분기 `eps_diluted` 합계)
  - `forward_pe` = `price / next fiscal year consensus EPS` (`fmp_estimates.eps_avg`의 다음 회계연도 값)
  - `run_rate_pe` = `price / annualized run-rate EPS` (최신 분기 `eps_diluted × 4`)
  - `psr` = `market_cap / TTM revenue`
  - `ev_ebitda` = `enterprise_value / latest annual EBITDA` (quarterly EBITDA 없어 연간 프록시)
  - `ev_fcf` = `enterprise_value / TTM free_cash_flow`

- Growth
  - `revenue_growth` / `eps_growth`는 전년 동기 대비 성장률(TTM YoY).
  - `revenue_cagr_3y/5y`, `eps_cagr_3y/5y`, `fcf_cagr_3y/5y`는 **`latest_annual.fiscal_year`를 기준으로 n년 전 연도**의 값과의 CAGR. 해당 연도 행이 없으면 NULL. 시작/종료 값이 0 이하(적자)면 CAGR 정의상 NULL (예: AMZN/PLTR `eps_cagr_3y` — 3년 전 앵커 연도 EPS가 적자라 구조적으로 NULL, 버그 아님).

- Quality
  - `gross_margin` = `gross_profit / revenue`
  - `operating_margin` = `operating_income / revenue`
  - `net_margin` = `net_income / revenue`
  - `fcf_margin` = `free_cash_flow(TTM) / revenue(TTM)`
  - `roe` = `net_income / total_equity`
  - `fcf_efficiency` = `free_cash_flow / net_income`

- Risk
  - `debt_equity` = `total_debt / total_equity`
  - `net_debt_equity` = `(total_debt - cash) / total_equity`

- PEG
  - `peg` = `trailing_pe / (eps_cagr_3y × 100)` (eps_cagr_3y가 양수일 때만 계산, 그 외 NULL)

- 점수화
  - 원시값은 그대로 보여주고, 별도의 점수는 축별 순위화(percentile) 후 가중합.
  - Valuation / Risk는 낮을수록 좋고, Growth / Quality는 높을수록 좋다.
  - Composite Rank는 최종 투자 후보 정렬에만 사용.
  - **v1 확정 가중치: `Valuation 25 / Growth 35 / Quality 30 / Risk 10`**

## 📐 세부 순위 규칙 (확정)

- 공통 방식: 비교 집단은 미국 빅테크 10개 종목 고정. 각 축 내부에서 10개 종목의 상대 순위를 0~100 percentile score로 변환. 낮을수록 좋은 지표는 역순, 높을수록 좋은 지표는 정순.
- **Valuation**: `forward_pe 35 / ev_fcf 30 / trailing_pe 20 / psr 15` (`ev_ebitda`는 보조 확인용, 핵심 가중치에서 제외)
- **Growth**: `eps_cagr_3y 40 / revenue_cagr_3y 35 / fcf_cagr_3y 25` (5년 값은 보조 지표)
- **Quality**: `operating_margin 30 / fcf_margin 30 / gross_margin 20 / roe 20` (`net_margin`은 설명용으로만 노출)
- **Risk**: `net_debt_equity 60 / debt_equity 40`
- 최종 해석: Valuation이 조금 비싸도 Growth·Quality가 크게 좋으면 상위 후보 가능. Composite Rank는 참고용 최종 정렬값이며 개별 축 점수도 같이 본다.

## 🧱 확정된 설계 원칙

- `fmp_metrics`는 history 저장소로 그대로 사용. 같은 날짜의 같은 종목은 `ON CONFLICT (symbol, calculated_at)`로 덮어쓴다.
- 값이 산출되지 않으면 0이 아니라 NULL을 유지한다.
- 최신값 조회는 `ORDER BY calculated_at DESC` 또는 `DISTINCT ON (symbol)`로.
- NDX 대비 초과수익률처럼 "사용자가 기간을 자유롭게 고를 수 있어야 하는" 지표는 배치로 미리 정해서 저장하지 않고, 조회 시점에 원천 데이터(`fmp_price_history`)로부터 동적 계산한다.

## 🗂️ 코드 파일 배치 (확정, 실제 반영됨)

- 새 폴더는 만들지 않는다. 기존 구조 안에서만 배치한다.
- 순수 계산/순위화 유틸: `app/utils/fmp_valuation.py`
  - trailing PE, forward PE, run-rate PE, 성장률, 마진, 위험지표(`net_debt_equity` 포함), percentile score, **NDX 초과수익률 계산 함수(`excess_return_vs_benchmark`, 실제 구현·커밋 완료)** 포함
  - DB write 없이 순수 함수만
- DB를 읽고 `fmp_metrics`에 upsert하는 실행 엔진: `scheduler/valuation_fmp_metrics.py`
  - 원천 테이블 조회, as-of 계산, 일별 스냅샷 생성, upsert(`net_debt_equity` 포함), 로그
  - systemd 등록·활성화 완료 (08:50 KST)
- SEC EDGAR 분기 데이터 백필: `scheduler/valuation_sec_edgar_backfill.py`
  - v9: EPS 분할조정 순서 버그 수정. `collect_concept_with_q4()`에서 기존에는 Q1~Q3(raw)와 annual(raw) 값을 raw 상태로 뺄셈해 Q4를 역산한 뒤 사후적으로 분할조정을 적용했는데, 회계연도 중간에 분할이 낀 경우 뺄셈 자체가 왜곡되는 구조적 버그였음. `_apply_split_adjustment_pre_derivation()` 신규 함수로 수정. 실측 확인: GOOGL 2022-12-31(-22.33→1.06), NVDA 2025-01-26(-4.49→0.89), NFLX 2025-12-31(-17.14→0.56) 모두 정상화 검증 완료.
  - **로그 파일 경로 버그 수정 완료**: `review_log.json`/`split_adjustment_log.json`/`eps_restatement_override_log.json`/`self_inconsistency_log.json`/`missing_fields_full_log.json`/`missing_fields_new_log.json` 총 6곳이 상대경로 `open()`이라 실행 cwd에 따라 `scheduler/`가 아닌 프로젝트 루트에도 중복 생성되는 문제 발견. `_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))` 기준 절대경로로 전환, 별도 커밋 완료.
- 화면(화면 UI/server 모듈)은 나중에 `app/modules/` 아래에 둔다. NDX 초과수익률의 `fmp_price_history` 조회는 이 화면 모듈 안에서 `get_db()` + `@reactive.calc`로 구현 (확정 — DB 조회 전용 독립 파일은 만들지 않음, 프로젝트 관례상 선례 없음).

## ⏳ 진행 상태

- 완료
  - SEC EDGAR / FMP 자동 실행 실패 원인 정리 및 수정 완료
  - `fmp_metrics` 스키마 확인 완료
  - `fmp_price_history` / `fmp_symbols` 현황 확인 완료
  - beta는 핵심 지표가 아니라는 결론 확정, 대체 지표(NDX 대비 초과수익률) 채택 확정
  - 투자 판단 지표 체계(축/가중치/순위규칙) v1 기준 확정
  - `app/utils/fmp_valuation.py`, `scheduler/valuation_fmp_metrics.py` 작성 완료
  - `.venv` 관련 혼란 해소 — 원인 불명의 빈 venv 삭제. 프로젝트는 시스템 전역 `/usr/bin/python3` 그대로 사용
  - `fmp_metrics`에 `net_debt_equity` 컬럼 추가 완료 (ALTER TABLE)
  - `net_debt_equity` upsert 로직 누락분 발견·수정 완료 (METRIC_COLUMNS, INSERT/ON CONFLICT SQL 반영)
  - CAGR 앵커 버그: 문서상 "수정 완료"였으나 실제 코드 미반영 상태였던 것 발견·수정 완료 (AAPL `revenue_cagr_3y` 1.81% 실측 검증)
  - AAPL 외 나머지 9개 종목 전체 `valuation_fmp_metrics.py --rebuild` 실행 완료
  - EPS 분할조정 순서 버그 발견·수정·재백필·재검증 완료
  - `valuation_fmp_metrics.service`/`.timer` systemd 등록 및 활성화 완료
  - **git 정리 완료**: `.gitignore`에 로그 4종(`review_log`/`split_adjustment_log`/`eps_restatement_override_log`/`self_inconsistency_log`)이 이미 등록되어 있음을 재확인. `valuation_sec_edgar_backfill.py`(v9) 커밋 확인. `app/utils/fmp_valuation.py` + `scheduler/valuation_fmp_metrics.py` + `.service`/`.timer` 커밋 완료.
  - **루트 디렉토리에 중복 생성돼 있던 로그 4종 파일 정리 완료**: `review_log`/`eps_restatement_override_log`/`self_inconsistency_log`는 scheduler 쪽과 내용 동일 확인 후 루트본 삭제. `split_adjustment_log.json`은 루트본(494건, `stage: pre_derivation` 필드 있음, v9 수정 반영된 최신본)과 scheduler본(290건, 구버전)이 실제로 다른 것을 발견 — 루트본 내용을 scheduler로 이관해 최신본 유지.
  - **로그 파일 상대경로 버그 수정 완료 및 커밋**: 6개 `open()` 호출을 `_SCRIPT_DIR` 기준 절대경로로 전환.
  - **`excess_return_vs_benchmark()` 순수 함수 실제 구현 및 커밋 완료**: 문서에 "이미 구현됨"으로 잘못 기재되어 있었으나 실제로는 없었음을 확인 후 신규 작성. `safe_div` 재사용, 가격 None/0 시 None 반환. 동작 테스트 완료 (+20%/+5% 케이스 → 0.15).

- 진행 중 / 미해결
  - `growth_adj_value`, `revenue_efficiency` 컬럼 — origin 미확인, v1 미사용 확정, v2에서 의미 재검토 예정.
  - NDX 초과수익률의 `fmp_price_history` 실제 DB 조회 로직 — 화면 모듈(`app/modules/`) 구현 시 `get_db()`+`@reactive.calc`로 함께 작성 예정 (독립 작업으로 분리하지 않기로 확정).
  - `TSLA.operating_cash_flow_tag_used`가 일부 분기(2010-06-30~2010-12-31)에서 NULL이 아니라 빈 문자열로 저장된 것 발견 (원인 미확인, 후속 확인 필요 — 우선순위 낮음).
  - `AIContext/stock_analasys.md`(이 문서) — 세션 마무리 후 커밋 예정.

## 🗓️ 해야 할 일 순서

1. ~~`fmp_metrics`에 `net_debt_equity` 컬럼 추가~~ ✅ 완료
2. ~~수정된 CAGR 로직으로 AAPL `--rebuild` 재실행, 검증~~ ✅ 완료
3. ~~나머지 9개 종목 전체 실행 및 검증~~ ✅ 완료
4. ~~`valuation_fmp_metrics.service`/`.timer`를 systemd에 등록·활성화~~ ✅ 완료
5. ~~git 정리 (로그 gitignore 확인, 커밋 3건, 로그 경로 버그 수정)~~ ✅ 완료
6. ~~`excess_return_vs_benchmark()` 순수 계산 함수 작성~~ ✅ 완료
7. `growth_adj_value`, `revenue_efficiency`의 v2 지표 여부 재검토 ← 다음 작업
8. 화면에서 보여줄 항목 최종 목록 확정
9. 화면(UI) 구현 — **`fmp_price_history` 조회 로직(get_db + @reactive.calc)도 이 단계에서 함께 작성**
10. (낮은 우선순위) TSLA `operating_cash_flow_tag_used` 빈 문자열 이슈 원인 확인

## 📌 나중에 다시 볼 가치가 있는 사실

- `fmp_symbols`와 `tickers`는 절대 같은 모집단이 아니다.
- `fmp_price_history`에는 `close_price`만 있다 (symbol별 + `NDX` 벤치마크, PK `(symbol, date)`).
- `fmp_metrics`는 `(symbol, calculated_at)` PK를 가진다.
- **CAGR 앵커 원칙**: 연도/기간 기반 파생 지표(CAGR 등) 계산 시, 조회 기준 시점(`as_of`)의 달력 연도가 아니라 실제 존재하는 최신 원천 데이터 행의 연도를 앵커로 삼을 것.
- **CAGR NULL은 버그가 아닐 수 있다**: 시작/종료 연도 값이 0 이하(적자)면 거듭제곱근 계산이 정의되지 않아 NULL이 정상. AMZN/PLTR `eps_cagr_3y` NULL은 이 케이스.
- **EPS 분할조정은 뺄셈(Q4 역산) 이전에 이루어져야 한다**: 각 구성요소가 서로 다른 시점(분할 전/후)에 filed됐다면 raw 값끼리 그대로 빼면 안 되고, 뺄셈 이전에 각자의 filed일자 기준으로 먼저 분할조정해 같은 기준으로 정규화해야 한다.
- `NDX`는 벤치마크로만 쓰고, `fmp_symbols` 기준 계산 대상 필터링에서는 제외된다.
- NDX 대비 초과수익률은 저장하지 않고 동적 계산하는 것으로 확정. 시작/종료 각 1건씩(해당일 이전 최근 거래일 종가)만 있으면 계산 가능 — 구간 전체 정렬 불필요.
- `common/` 같은 로컬 패키지를 새로 import하는 프로덕션 스크립트는 systemd 실행 디렉토리 기준 `sys.path`까지 확인해야 한다.
- `VALUE IS NULL` 기준으로 결측을 찾는 로직은 provenance 태그만으로는 재조사를 막지 못한다.
- **프로젝트에는 원래 별도 venv가 없는 게 정상 상태다.**
- **COALESCE 방식 upsert는 "값이 있으면 무조건 덮어쓰기"이지 "출처를 가려서 보존"이 아니다.**
- **systemd `Type=oneshot`은 restart/daemon-reload 없이 파일 교체만으로 다음 트리거부터 자동 반영된다.**
- **상대경로로 `open()`하는 스크립트는 systemd(고정 cwd)와 수동 실행(임의 cwd)이 섞이면 같은 이름의 파일이 여러 위치에 중복 생성될 수 있다.** 발견 시 어느 쪽이 최신/유효한 내용인지(필드 존재 여부, 레코드 수, 타임스탬프) 확인 후 병합·정리할 것 — 무조건 최근 파일이 맞다고 가정하지 말 것.
- **DB 조회 함수는 "화면 모듈 안에 직접" 또는 "스케줄러 스크립트 안에 private 함수로" 두는 것이 이 프로젝트의 유일한 관례다.** 별도의 DB 조회 전용 서비스 파일 패턴은 없음.

---

## 📌 영구 원칙 (계속 유효, 매 세션 적용)

- 값 채택 로직에 "다수결"을 넣지 말 것. 오염 탐지는 "같은 필징(accn) 하나 안의 자기모순"으로 판단(`SELF_CONSISTENCY_TOLERANCE = 0.05`, eps_diluted는 구조적으로 제외).
- 백업→실행→검증 순서로 작업을 안내할 때, diff/검증 단계를 실행(쓰기) 단계보다 반드시 먼저 제시할 것. 단, 사용자가 명시적으로 생략을 지시하면 따른다.
- `fmp_symbols`(가치평가 대상)와 `tickers`(자산관리 보유 종목)는 별개 모집단.
- `scheduler/` 폴더는 가치평가와 개인 자산관리 시스템이 공유하는 디렉토리. 가치평가 관련 파일은 `valuation_` 프리픽스로 구분.
- 검토 필요한 파일은 반드시 실제로 열어보거나 grep해서 확인한 뒤 판단할 것.
- COALESCE 방식 upsert가 적용된 테이블에서 "결측/변경 여부"를 감지할 때는, "이번에 실제로 새로 쓰여진 행"만 검사 대상으로 좁힐 것 (`RETURNING ..., (xmax = 0) AS inserted` 패턴, PG14 기준).
- 프로덕션 쓰기 스크립트를 수정할 때, 백업 명령의 실행 시점을 결과로 반드시 재확인할 것.
- 사용자가 붙여넣은 터미널 출력이 방금 요청한 명령의 결과가 맞는지 항상 재확인할 것.
- 같은 필드(리소스)를 공유하는 여러 값을 캐싱할 때는, 캐시 키에 "무엇을 캐싱하는지" 전체를 포함시킬 것.
- "매칭 실패"나 "결측" 같은 부정적 결과도 로그 파일에 남겨야 함.
- 디버깅/재검토 목적의 반복 실행이 예상되는 스크립트는, 실행할 때마다 외부 API를 다시 부르는 구조가 있는지 점검할 것.
- 원천 데이터가 특정 시기에 구조적으로 불완전해 자동 역산 조건을 충족 못 하는 경우, "자동화 조건 미충족"과 "데이터 자체 부재"를 구분해서 판단할 것.
- `_tag_used`류 provenance 컬럼은 값(value)이 NULL이어도 "결측 사유"만 기록하는 용도로 쓸 수 있다. 단, 결측 감지 로직이 `tag_used`가 아니라 `value IS NULL`만 검사한다면 사유 기록이 재조사 자체를 막아주진 않는다.
- 여러 곳에서 공유하는 leaf 모듈을 수정할 때: (1) 다른 호출부 동작이 바뀌는 게 의도된 것인지 확인, (2) 새 의존성 추가 시 그 모듈을 쓰는 모든 실행 환경에 패키지가 있는지 확인.
- 새 import를 프로덕션 스크립트에 추가할 때는 패키지 설치 여부뿐 아니라, 그 스크립트가 실행되는 실제 작업 디렉토리에서 관련 로컬 패키지 경로가 잡히는지까지 확인할 것.
- 실행할 때마다 재생성되는 로그/캐시/백업 파일은 git으로 추적하지 않는다(`.gitignore`). 단, 디스크에는 그대로 남겨서 재조사 방지 목적은 유지.
- 프로젝트와 무관한 별도 목적의 디렉토리/설정은, 형상관리 불필요 판단이 있으면 매 세션 재질문 없이 `.gitignore`로 영구 제외한다.
- **연도/기간 기반 파생 지표(CAGR 등) 계산 시, 조회 기준 시점(`as_of`)의 달력 연도가 아니라 실제 존재하는 최신 원천 데이터 행의 연도를 앵커로 삼을 것.**
- **사용자가 기간을 자유롭게 선택해야 하는 지표(예: NDX 대비 초과수익률)는 배치로 고정 기간을 미리 계산해서 저장하지 않고, 조회 시점에 동적으로 계산한다.** 고빈도 이벤트에 묶인 반복 호출이면 캐시(`@reactive.calc` 등) 적용 여부를 별도 검토.
- **다른 도구/에이전트(Copilot 등)가 이미 실제 코드를 작성하고 DB에 반영했을 가능성을 항상 열어둘 것.**
- **문서의 "완료/수정됨" 서술을 곧이곧대로 믿지 말고, 실제 코드와 DB 값으로 재검증할 것.**
- **COALESCE 재적재 전에는 수동 패치 이력과 이번 수정의 실제 영향 필드를 먼저 대조할 것.**
- **systemd `Type=oneshot`은 파일 교체만으로 충분, unit 파일 변경 시에만 `daemon-reload` 필요.**
- **상대경로 `open()`으로 인한 파일 중복 생성 가능성을 항상 의심할 것.** 같은 이름의 파일이 여러 위치에 있으면 diff/필드/건수로 어느 쪽이 최신·정확한지 먼저 확인 후 병합.
- **DB 조회 함수는 화면 모듈 또는 스케줄러 스크립트 안에 직접 둔다. 별도의 DB 조회 전용 서비스 파일은 만들지 않는다.**

---

## 🗂️ 주요 파일

- `~/asset-cloud/scheduler/valuation_sec_edgar_raw_collect.py` — SEC EDGAR raw 수집(1단계)
- `~/asset-cloud/scheduler/valuation_sec_edgar_backfill.py` — 텔레그램 알림 훅 + PROJECT_ROOT sys.path 고정. v9: EPS 분할조정 순서 버그 수정 + 로그 파일 상대경로 버그 수정(`_SCRIPT_DIR` 도입) 모두 반영, 커밋 완료.
- `~/asset-cloud/scheduler/valuation_classify_review_log.py` — review_log 원인별 분류
- `~/asset-cloud/scheduler/valuation_fmp_collector.py` — FMP 수집. `fmp_financials`/`fmp_estimates` upsert + `fmp_metrics`의 price/market_cap/EV upsert. 매일 08:40 KST.
- `~/asset-cloud/scheduler/valuation_fmp_price_backfill.py` — yfinance 기반 주가 히스토리 1회성 backfill (`fmp_price_history`)
- `~/asset-cloud/scheduler/valuation_alphavantage_fallback_fill.py` — v3. 항목 단위 apply 자동화는 "불필요"로 최종 폐기.
- `~/asset-cloud/app/utils/fmp_valuation.py` — 순수 계산 모듈. trailing/forward/run-rate PE, CAGR, margin류, percentile score, `excess_return_vs_benchmark`(신규 구현) 포함. DB write 없음. **커밋 완료.**
- `~/asset-cloud/scheduler/valuation_fmp_metrics.py` — FMP 가치평가 스냅샷 엔진(배치 runner). 10종목 전체 처리 완료, `net_debt_equity` upsert 반영 완료. **커밋 완료.**
- `~/asset-cloud/scheduler/valuation_fmp_metrics.service` / `.timer` — systemd 등록·활성화 완료 (08:50 KST). **커밋 완료.**
- `~/asset-cloud/common/notify.py` — 실제 텔레그램 전송 구현. `snap.py`/`daily_snapshot.py`/`daily_inserter.py`도 공유.
- `~/asset-cloud/common/price_updater_common.py` — `common/` 패키지 접근용 `sys.path.insert` 패턴의 원본 예시.
- `~/asset-cloud/scheduler/config.json` — `telegram_token`, `telegram_chat_id`, `fmp_api_key`, `db_password` 포함. `.gitignore` 대상, 커밋 안 됨.
- `~/asset-cloud/scheduler/valuation_sec_edgar_pipeline.service` / `.timer` — 08:30 KST.
- `~/asset-cloud/scheduler/valuation_fmp_collector.service` / `.timer` — 08:40 KST.
- `fmp_metrics` 테이블 — `trailing_pe`, `run_rate_pe`, `peg`, CAGR류, margin류, `roe`, `debt_equity`, `net_debt_equity`, (출처 미상)`growth_adj_value`, `revenue_efficiency` 컬럼 존재.
- `~/asset-cloud/app/db.py` — `get_connection()`(단발성), `get_db()`(풀 기반 contextmanager, `ThreadedConnectionPool`) 제공. 화면 모듈은 `with get_db() as conn:` 사용.

---

## 🌐 환경 정보

- DB: `assetdb` / 사용자 `jake` / 비밀번호 `qkrworb0!` (psql 접속: `PGPASSWORD=qkrworb0! psql -U jake -d assetdb -P pager=off -c "..."`)
- PostgreSQL 버전: 14.23 (Ubuntu, aarch64)
- FMP API: 무료 플랜(Basic), 하루 250회 한도. Historical Data Range 5년(Premium 이상만 30년+).
- Alpha Vantage API: 무료 플랜, 5 calls/min, 25 calls/day. 당일 파일 캐시 적용.
- Telegram Bot API: 무료. `scheduler/config.json`에 토큰/chat_id 저장(git 추적 안 됨).
- 실행 환경: 프로젝트 전용 venv는 없음(정상 상태). `/usr/bin/python3`(시스템 전역), `psycopg2`, `requests` 설치 확인됨.
- `common/` 같은 프로젝트 내부 패키지는 스크립트별로 `sys.path` 처리가 있어야 systemd 환경에서 import됨.
- 오라클 클라우드 무료 티어 범위 내 운영 원칙 유지

---

# 현재 우선순위

1. `growth_adj_value`, `revenue_efficiency` v2 지표 여부 재검토
2. 화면에서 보여줄 항목 최종 목록 확정
3. 화면(UI) 구현 — `fmp_price_history` 조회 로직(`get_db`+`@reactive.calc`) 포함
4. (세션 마무리 시) `AIContext/stock_analasys.md` 커밋