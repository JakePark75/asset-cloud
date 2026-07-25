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
- **문서에 "완료/수정됨"으로 적혀 있어도 반드시 실제 코드/DB를 재확인한 뒤 신뢰할 것.** 단, 사용자가 세션 중 "이미 확인했으니 다시 확인하지 말고 진행하라"고 명시적으로 지시하면 그 지시를 따르고 반복 요구하지 않는다 (이번 세션에도 사용자가 명시적으로 재확인 생략을 지시한 바 있음 — 정당한 예외로 처리, 매번 예외 없이 재확인을 고집하지 말 것).
- 검토가 필요한 프로젝트 내 파일이 있으면 바로 요청하고, 추론에 의해 분석이나 수정 코드를 작성하지 않는다. 파일명이나 문서 서술만으로 역할·의존성을 추측하지 말고 항상 실제로 열어보거나 grep으로 확인.
- 코드 작성 시 편의를 위한 임시적 우회나 암묵적 가정에 기대지 않는다.
- 사용자가 확정적으로 산출물을 요청하기 전에 성급히 긴 설명이나 코드 예시를 제공하지 않는다.
- 사용자가 코드 파일을 업로드한 경우, 수정 코드 제공은 반드시 artifact(파일)로 한다.
- 기술 선택/추천 시 반드시 근거(웹 검색 등) 명시.
- 코드 작성 전, 사용할 API/함수/동작 방식이 추론이나 기억 기반이면 반드시 사실 확인 후 사용자와 방향 동의를 받고 나서 작성한다.
- 디버깅/이상 신호 대응: 확증 없이 롤백/수정 등 되돌리기 어려운 조치를 먼저 실행하지 않는다.
- 여러 개의 결정이 동시에 필요할 때, 근거 없이 한 번에 몰아서 묻지 말 것. 각 결정마다 왜 확인이 필요한지 먼저 짚고, 필요한 확인들을 정리해서 한 번에 제시하되 사용자가 답하기 전에 코드를 작성하지 않는다.
- **선택지 간 실질적 차이가 없는 경우, 굳이 선택지로 나눠 묻지 말 것.** 차이가 없으면 그냥 더 간단한/표준적인 방식으로 바로 진행 (이번 세션 확정 — 실질적 차이 없는 선택지를 제시했다가 지적받음).
- 터미널 명령어를 요청할 때, 여러 개가 필요하면 한 번에 묶어서 제시할 것. 하나씩 순차적으로 반복해서 요청하지 않는다.
- **파일을 VM으로 옮기는 방법을 지레짐작하지 말 것.** 사용자가 이미 홈 디렉토리에 파일이 있다고 가정하고 `cp` 명령을 주면 안 됨 - 사용자가 "이미 VM에 반영했다"고 명시적으로 말하면 그 말을 믿고 전송 방법을 다시 묻지 않는다. 새 파일을 VM에 만들어줘야 할 때는 `cat > file << 'EOF' ... EOF` heredoc 형태로 한 번에 복붙 가능하게 줄 것 (scp/다운로드 링크 등 불확실한 전송 경로를 전제하지 않음).
- 의도 재확인: 맥락상 이상한 요청이면 맹목적으로 수행하지 않고 의도를 먼저 확인한다.
- 모든 대화는 팩트 위주로 제공하고, 팩트가 아닌 예상/추론은 반드시 그렇다고 명시한다.
- 대화 중 제공된 내용을 주장하기 전에 반드시 해당 부분을 다시 확인한다.
- 오라클 클라우드 무료 티어 범위 내에서만 서비스 구성, 유료 서비스 선택 유도하지 않는다.
- 여러 선택지가 있으면 성급히 1개만 제안하지 말고 사용자에게 선택지를 제공한다 (단, 위 항목대로 실질적 차이가 없는 경우는 예외).
- 조사/분석 범위를 한 번에 넓히지 말 것. 확정 가능한 것부터 즉시 종결하고 문서에 남긴 뒤 다음으로 넘어간다.
- 프로덕션 스크립트를 수정할 때, 백업 명령을 내려도 사용자가 실제로 그 시점에 실행했는지, 파일을 먼저 교체하고 백업한 건 아닌지 확인 없이 신뢰하지 말 것. **단, 사용자가 명시적으로 "백업/비교 절차 생략"을 지시하면 그 지시를 따르고 반복 요구하지 않는다** (이번 세션에도 사용자가 임시파일/백업 없이 프로덕션 파일 직접 교체를 지시함 — 그대로 따름).
- 기존 함수/모듈을 수정할 때, 그 함수가 이미 다른 여러 곳에서 쓰이고 있을 가능성과 그 수정이 일으킬 사이드이펙트를 반드시 짚고 넘어갈 것. 수정 전 `grep -rn`으로 다른 호출부 존재 여부를 반드시 실측 확인한다.
- 새 모듈/의존성을 프로덕션 스크립트에 추가할 때, "패키지가 설치돼 있는지"만 확인하는 걸로는 부족하다. 실제 배포 환경(systemd의 `WorkingDirectory`)에서 import 자체가 되는지까지 확인해야 한다.
- **COALESCE 방식 upsert가 있는 테이블을 재적재하기 전에는, 그 필드에 과거 수동 패치 이력이 있는지 먼저 확인할 것.**
- **`fmp_quarterly_financials`는 COALESCE upsert(새 값이 NULL이면 기존 값 유지)이지만, `fmp_metrics`는 COALESCE가 아니라 `EXCLUDED.col`로 무조건 덮어쓰는 방식이다 — 두 테이블의 upsert 방식이 다르다는 걸 재적재 전 반드시 구분해서 확인할 것 (이번 세션 재확인, 문서에 이미 있던 사실이지만 실전에서 헷갈리기 쉬움).**
- **systemd `Type=oneshot`인 서비스는, 연결된 python 스크립트 파일만 디스크에서 교체하면 되고 별도 restart/daemon-reload가 필요 없다.** `daemon-reload`가 필요한 경우는 `.service`/`.timer` unit 파일 자체를 변경했을 때뿐.
- 실행할 때마다 재생성되는 로그/캐시 파일은 git으로 추적하지 않는다(`.gitignore`).
- **파일을 상대경로로 `open()`하는 스크립트는 실행 cwd에 따라 결과물 위치가 달라질 수 있다.** systemd(`WorkingDirectory` 고정)와 사용자 수동 실행(cwd 임의)이 섞여 있는 프로젝트에서는 특히 주의. 로그/출력 파일 경로는 스크립트 자신의 디렉토리 기준 절대경로(`_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))`)로 고정할 것.
- **DB 조회 함수의 배치 위치는 프로젝트 기존 관례를 따른다**: 순수 계산 함수(DB 미접근)는 `app/utils/*.py`, 화면에서 필요한 DB 조회는 별도 서비스 파일로 분리하지 않고 해당 화면 모듈(`app/modules/*.py`) 안에서 `with get_db() as conn:` + `@reactive.calc`로 직접 구현. 스케줄러 실행 스크립트의 DB 조회는 해당 스크립트 안에 `_load_xxx(conn, ...)` 형태의 private 함수로 정의. "DB 조회만 하는 독립 서비스 파일"이라는 선례는 이 프로젝트에 없음.
- **Claude의 메모리 시스템(세션 간 자동 기억)에는 이 프로젝트 관련 내용을 저장하지 말 것.** 세션 간 인수인계는 반드시 이 진행 요약 문서(매 세션 사용자가 직접 첨부)로만 한다.
- **결측(missing data) 이슈 대응 시 진단 순서 (재사용 프로토콜):**
  1. 로그/알림에 찍힌 결측 파일(`missing_fields_new_log.json` 등)의 실제 내용을 먼저 확인 (심볼, 필드, 분기)
  2. `sec_xbrl_facts_raw`에 해당 심볼·concept·기간의 raw entry가 실제로 존재하는지 확인 — "원천 데이터 자체 부재"인지 "원천은 있는데 파생 로직이 못 채운 것"인지부터 구분
  3. 원천이 있는데 못 채웠다면, 해당 파생 함수(`collect_concept_with_q4` 등)의 실제 코드를 읽고 왜 그 케이스가 조건을 충족 못 하는지 구조적으로 특정 (추측 금지)
  4. 이 결측이 화면에 노출되는 실제 지표(`fmp_metrics`의 파생값)에 어떤 영향을 주는지 코드 기준으로 추적 — TTM 기반 지표(`ev_fcf`, `fcf_margin` 등, 분기 데이터 사용)와 연간 기반 지표(CAGR류, `fcf_efficiency` 등, `fmp_financials` 연간 데이터만 사용)는 영향 범위가 다르므로 반드시 구분해서 확인
  5. 수정 여부/범위를 정하기 전에, 해당 파생 함수가 다른 어떤 필드·심볼에도 공유되는지 확인하고 수정 범위(전체 concept vs 특정 concept만)를 사용자와 합의 후 진행
  6. **결측 알림 로직 자체를 손댈 필요는 보통 없다**: 원인(파생 로직 공백)을 고쳐서 실제 값이 채워지면, 결측 검사(`value IS NULL` 기준)는 자연히 통과한다. 알림 로직에 예외 처리를 추가하는 것은 잘못된 접근 (이번 세션 확정 — capex/operating_cash_flow 결측 알림이 별도 코드 수정 없이 자동 해소됨을 확인).

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
  - `fmp_quarterly_financials`: 분기 재무 (SEC EDGAR 백필 + Alpha Vantage 폴백으로 채워짐). **upsert 방식: COALESCE (새 값이 NULL이면 기존 값 유지).**
  - `sec_xbrl_facts_raw`: SEC EDGAR XBRL raw fact 원천 테이블. `fmp_quarterly_financials`를 만드는 재료. 스키마는 아래 "환경 정보" 참고.
- 파생/스냅샷 저장소
  - `fmp_metrics`: 일별 파생 통계 스냅샷 저장소. `(symbol, calculated_at)` PK. `net_debt_equity` 컬럼 포함. **upsert 방식: `EXCLUDED.col`로 무조건 덮어쓰기 (COALESCE 아님) — 같은 날짜의 같은 종목은 재계산 시 그대로 덮어써야 하는 history 스냅샷 저장소이기 때문.**
- 실제 데이터 흐름 (확인 완료)
  - `scheduler/valuation_fmp_collector.py`가 매일 08:40 KST에 FMP API를 호출해 `fmp_financials`/`fmp_estimates`를 채우고, **동시에 `fmp_metrics`의 `price`/`market_cap`/`enterprise_value`도 먼저 upsert**한다 (`upsert_metrics_quote()`).
  - `scheduler/valuation_sec_edgar_pipeline.timer`(08:30 KST)가 `valuation_sec_edgar_raw_collect.py` → `valuation_sec_edgar_backfill.py` → `valuation_classify_review_log.py` 순으로 `fmp_quarterly_financials`를 채운다.
  - `scheduler/valuation_fmp_metrics.py`는 그 뒤(08:50 KST, systemd 등록 완료)에 실행되어, 이미 price/market_cap/EV가 채워진 `fmp_metrics` 행에 나머지 파생 지표(trailing_pe 등)를 계산해서 채운다. **CLI 옵션: `--symbol`(복수 지정 가능), `--rebuild`, `--dry-run` 모두 지원 확인 완료 (이번 세션).**
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
- 삭제됨
  - `growth_adj_value`, `revenue_efficiency`: `fmp_metrics`에서 `ALTER TABLE ... DROP COLUMN` 완료. 코드베이스 어디에도 참조 없고(grep 0건), git 히스토리상 코드가 아니라 문서에만 등장했으며, 전체 테이블 기준 값도 0건(전부 NULL)임을 확인 후 제거.
- 제외
  - beta는 핵심 지표에서 제외
  - 과도하게 복잡한 파생점수는 v1에서 제외
  - 기간이 중복되는 과잉 지표는 v1에서 제외

## 🧮 세부 계산식 초안

- 기준 원칙
  - 모든 값은 `calculated_at` 시점 기준의 as-of snapshot으로 계산한다.
  - 원천 값이 부족하면 0으로 채우지 않고 NULL을 유지한다.
  - **CAGR 계산은 `as_of`의 달력 연도가 아니라, 실제로 존재하는 `latest_annual`(최신 연간 데이터)의 fiscal_year를 기준으로 n년 전 연도를 찾는다.**

- Valuation
  - `trailing_pe` = `price / TTM diluted EPS`
  - `forward_pe` = `price / next fiscal year consensus EPS`
  - `run_rate_pe` = `price / annualized run-rate EPS`
  - `psr` = `market_cap / TTM revenue`
  - `ev_ebitda` = `enterprise_value / latest annual EBITDA`
  - `ev_fcf` = `enterprise_value / TTM free_cash_flow`
  - **`ev_fcf`는 TTM(최근 4개 분기 discrete `operating_cash_flow`+`capex`) 기반이라, 그 4개 분기 중 하나라도 결측이면 전체가 NULL이 된다 (`sum_latest_n_fcf()`가 하나라도 None이면 즉시 None 반환, 부분합으로 대체하지 않음).**

- Growth
  - `revenue_growth` / `eps_growth`는 전년 동기 대비 성장률(TTM YoY).
  - `revenue_cagr_3y/5y`, `eps_cagr_3y/5y`, `fcf_cagr_3y/5y`는 `latest_annual.fiscal_year`를 기준으로 n년 전 연도의 값과의 CAGR. 해당 연도 행이 없으면 NULL.
  - **CAGR류는 분기 데이터가 아니라 연간 데이터(`fmp_financials`)만 사용한다.** 그래서 분기 단위 결측은 CAGR류에는 영향을 주지 않는다.
  - **NULL은 시작(3년/5년 전)이든 종료(최신 연도)든 둘 중 하나만 0 이하여도 발생한다.**
  - **적자로 인한 CAGR NULL은 값을 억지로 만들어내지 않고 그대로 NULL 유지하기로 확정.**

- Quality
  - `gross_margin` = `gross_profit / revenue`
  - `operating_margin` = `operating_income / revenue`
  - `net_margin` = `net_income / revenue`
  - `fcf_margin` = `free_cash_flow(TTM) / revenue(TTM)` — **`ev_fcf`와 동일하게 TTM FCF(분기 4개 합)를 분자로 쓰므로, 분기 결측 시 함께 NULL이 된다.**
  - `roe` = `net_income / total_equity`
  - `fcf_efficiency` = `free_cash_flow / net_income` — **annual_rows(연간 데이터)만 사용하므로 분기 결측과 무관.**

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

- 공통 방식: 비교 집단은 미국 빅테크 10개 종목 고정. 각 축 내부에서 10개 종목의 상대 순위를 0~100 percentile score로 변환.
- **Valuation**: `forward_pe 35 / ev_fcf 30 / trailing_pe 20 / psr 15`
- **Growth**: `eps_cagr_3y 40 / revenue_cagr_3y 35 / fcf_cagr_3y 25`
- **Quality**: `operating_margin 30 / fcf_margin 30 / gross_margin 20 / roe 20`
- **Risk**: `net_debt_equity 60 / debt_equity 40`
- 최종 해석: Valuation이 조금 비싸도 Growth·Quality가 크게 좋으면 상위 후보 가능.

## 🧱 확정된 설계 원칙

- `fmp_metrics`는 history 저장소로 그대로 사용. 같은 날짜의 같은 종목은 `ON CONFLICT (symbol, calculated_at)`로 덮어쓴다 (COALESCE 아님, `EXCLUDED.col` 그대로).
- 값이 산출되지 않으면 0이 아니라 NULL을 유지한다.
- 최신값 조회는 `ORDER BY calculated_at DESC` 또는 `DISTINCT ON (symbol)`로.
- NDX 대비 초과수익률처럼 "사용자가 기간을 자유롭게 고를 수 있어야 하는" 지표는 배치로 미리 정해서 저장하지 않고, 조회 시점에 원천 데이터(`fmp_price_history`)로부터 동적 계산한다.

## 🗂️ 코드 파일 배치 (확정, 실제 반영됨)

- 새 폴더는 만들지 않는다. 기존 구조 안에서만 배치한다.
- 순수 계산/랭킹 유틸: `app/utils/fmp_valuation.py`
- DB를 읽고 `fmp_metrics`에 upsert하는 실행 엔진: `scheduler/valuation_fmp_metrics.py`
  - `--symbol`(복수 지정), `--rebuild`, `--dry-run` CLI 옵션 보유 확인 완료 (이번 세션)
  - systemd 등록·활성화 완료 (08:50 KST)
- SEC EDGAR 분기 데이터 백필: `scheduler/valuation_sec_edgar_backfill.py`
  - v9까지: EPS 분할조정 순서 버그 수정, 로그 파일 경로 버그 수정 등 (과거 세션 완료)
  - **v10 (이번 세션 완료): `collect_concept_with_q4()`에 capex/operating_cash_flow 전용 "10-K 없는 중간분기 역산" 경로 추가.** 상세는 아래 "⏳ 진행 상태" 참고.
- 화면(화면 UI/server 모듈)은 나중에 `app/modules/` 아래에 둔다.

## ⏳ 진행 상태

- 완료
  - SEC EDGAR / FMP 자동 실행 실패 원인 정리 및 수정 완료
  - `fmp_metrics` 스키마 확인 완료
  - beta는 핵심 지표가 아니라는 결론 확정, 대체 지표(NDX 대비 초과수익률) 채택 확정
  - 투자 판단 지표 체계(축/가중치/순위규칙) v1 기준 확정
  - `app/utils/fmp_valuation.py`, `scheduler/valuation_fmp_metrics.py` 작성 완료
  - `fmp_metrics`에 `net_debt_equity` 컬럼 추가 및 upsert 반영 완료
  - CAGR 앵커 버그 발견·수정 완료 (AAPL 실측 검증)
  - AAPL 외 나머지 9개 종목 전체 `valuation_fmp_metrics.py --rebuild` 실행 완료
  - EPS 분할조정 순서 버그 발견·수정·재백필·재검증 완료
  - `valuation_fmp_metrics.service`/`.timer` systemd 등록 및 활성화 완료
  - 로그 파일 상대경로 버그 수정 완료 및 커밋 (2026-07-10 커밋, git log로 재검증 완료)
  - `excess_return_vs_benchmark()` 순수 함수 실제 구현 및 커밋 완료
  - `growth_adj_value`, `revenue_efficiency` 컬럼 재검토 및 삭제 완료
  - `missing_fields_new_log.json` 결측 건 진단 완료 — GOOGL/TSLA 2026-06-30(FY2026 Q2) 분기의 `capex`, `operating_cash_flow` 결측. 근본 원인: `collect_concept_with_q4()`가 `annual.items()` 순회 구조라 10-K 없는 회계연도는 discrete+YTD 쌍이 있어도 역산 자체가 안 되는 구현 범위 누락.
  - `eps_cagr_3y`/`fcf_cagr_3y` NULL 종목(AMZN, PLTR) 진단 완료 — 앵커 연도 적자로 인한 정상 NULL, 버그 아님.
  - **[이번 세션] `collect_concept_with_q4()`에 capex/operating_cash_flow 전용 "10-K 없는 중간분기 역산" 경로 추가 (v10).**
    - `MID_QUARTER_YTD_DERIVE_FIELDS = {"capex", "operating_cash_flow"}`로 대상 concept 명시적 한정.
    - 각 raw entry 자신의 `period_start`(`entry["start"]`)로 회계연도 그룹핑, annual(10-K) entry 없이도 discrete Q1 + 반기 YTD로 Q2를, 9개월 YTD로 Q3를 역산.
    - 기존 annual 기반 역산 루프 **다음**에 실행해, 이미 채워진 슬롯은 건드리지 않음 → 10-K가 나중에 등장하면 별도 flag 없이 annual 로직이 자동으로 우선권을 가짐.
    - 새로 채운 값은 `fp="derived_ytd_no10k"`로 표시(`tag_used`에 `|derived_ytd_no10k` 노출), 기존 `"derived_ytd"`(annual 기반)와 구분됨.
    - Q4는 이 블록에서 다루지 않음 (10-K 없이는 연간 총액 자체를 알 수 없어 원리상 역산 불가).
    - **VM에 직접 반영(백업/임시파일 없이 프로덕션 파일 직접 교체, 사용자 명시적 지시).**
  - **[이번 세션] dry-run 검증 완료**: `dry_run_v10_check.py`(신규, `scheduler/`에 위치)로 DB 쓰기 없이 `collect_symbol()` 결과만 계산해 확인.
    - GOOGL 2026-06-30: `capex=44,924,000,000`, `operating_cash_flow=39,069,000,000`, `tag_used`에 `|derived_ytd_no10k` 정상 노출.
    - TSLA 2026-06-30: `capex=5,789,000,000`, `operating_cash_flow=4,697,000,000`, 동일하게 정상 노출.
    - 나머지 8종목 회귀 확인 중 **67건의 "DB=값 → new=None" 차이 발견** (아래 "진행 중/미해결" 참고 — 이번 v10 패치와 무관, 별도 이슈로 분리).
  - **[이번 세션] 실제 DB 반영 및 검증 완료**:
    - `python3 valuation_sec_edgar_backfill.py` 정상 실행 → `fmp_quarterly_financials`의 GOOGL/TSLA 2026-06-30 행에 capex/operating_cash_flow 실제 값 upsert 확인 (`psql` SELECT로 직접 확인).
    - `verify_ev_fcf.py`(신규, `scheduler/`에 위치)로 `valuation_fmp_metrics.run(rebuild=True, dry_run=True, requested_symbols=["GOOGL","TSLA"])` dry-run 결과 확인: GOOGL `ev_fcf=71.37`/`fcf_margin=0.1195`, TSLA `ev_fcf=197.84`/`fcf_margin=0.0556` — 둘 다 더 이상 NULL 아님.
    - `python3 valuation_fmp_metrics.py --symbol GOOGL --symbol TSLA --rebuild` 실행 → `fmp_metrics`에 실제 반영 확인 (`psql` SELECT로 직접 확인).
    - 결측 알림(`missing_fields_new_log`)은 별도 코드 수정 없이 자동 해소됨을 확인 (원인이 없어지면 알림 조건 자체가 안 걸림).

- 진행 중 / 미해결
  - **[이번 세션 신규 발견, 우선순위 미정] v10 dry-run 회귀 확인에서 나온 67건의 "DB=값 → new=None" 차이.**
    - `revenue`, `eps_diluted`, `net_income` 등 `MID_QUARTER_YTD_DERIVE_FIELDS`에 없는 필드에서도 발견됨 → **이번 v10 패치가 원인이 아님이 확실함** (v10은 capex/operating_cash_flow에만 코드 변경이 있었음).
    - 추정(미확정): DB의 해당 옛 분기 값들이 v6~v9(자기모순 accn 격리, EPS 분할조정 순서 수정 등)가 전부 반영되기 전 버전으로 계산된 값일 가능성 — 즉 `valuation_sec_edgar_backfill.py`의 전체 히스토리 재적재(`--full`이 아니라 실제 전체 심볼 실행)가 v7/v8/v9 이후 한 번도 안 됐을 가능성.
    - `fmp_quarterly_financials`가 COALESCE upsert라 재적재해도 기존 값이 지워지지 않으므로 **DB 안전성 자체는 문제없음** (확인 완료). 다만 "왜 최신 로직으로 재계산하면 다른 값이 나오는가"는 별도로 조사할 가치가 있음.
    - 예시: PLTR 2019-09-30 `revenue`, GOOGL 2015 `eps_diluted` 3건, TSLA 2010~2016 `eps_diluted` 다수, AMD 2009~2010 `net_income` 다수, AAPL 2026-03-28 `free_cash_flow`(DB=None → new=26,731,000,000, 이건 오히려 개선 방향).
    - 다음 세션 시작 시 원인 조사 후보로만 남겨둠 — 오늘 세션 범위 아님.
  - `TSLA.operating_cash_flow_tag_used`가 일부 분기(2010-06-30~2010-12-31)에서 NULL이 아니라 빈 문자열로 저장된 것 발견 (원인 미확인, 우선순위 낮음).
  - NDX 초과수익률의 `fmp_price_history` 실제 DB 조회 로직 — 화면 모듈(`app/modules/`) 구현 시 `get_db()`+`@reactive.calc`로 함께 작성 예정.
  - **`dry_run_v10_check.py`, `verify_ev_fcf.py` (이번 세션 신규 생성, `scheduler/`에 위치)의 git 처리 여부 미정** — 재사용 가능한 검증 도구로 커밋할지, 실행할 때마다 재생성되는 1회성 스크립트로 `.gitignore` 처리할지 다음 세션에 결정 필요.
  - **이번 세션 변경사항(`valuation_sec_edgar_backfill.py` v10) git 커밋 여부 미확인** — VM 프로덕션 파일은 직접 교체 완료했으나, git 커밋 여부는 세션 내에서 확인 안 됨. 세션 마무리 전 확인 필요.
  - `AIContext/stock_analasys.md`(이 문서) — 세션 마무리 후 커밋 예정.

## 🗓️ 해야 할 일 순서

1. ~~`fmp_metrics`에 `net_debt_equity` 컬럼 추가~~ ✅ 완료
2. ~~수정된 CAGR 로직으로 AAPL `--rebuild` 재실행, 검증~~ ✅ 완료
3. ~~나머지 9개 종목 전체 실행 및 검증~~ ✅ 완료
4. ~~`valuation_fmp_metrics.service`/`.timer`를 systemd에 등록·활성화~~ ✅ 완료
5. ~~git 정리 (로그 gitignore 확인, 커밋, 로그 경로 버그 수정)~~ ✅ 완료
6. ~~`excess_return_vs_benchmark()` 순수 계산 함수 작성~~ ✅ 완료
7. ~~`growth_adj_value`, `revenue_efficiency`의 v2 지표 여부 재검토~~ ✅ 완료 (삭제 확정)
8. ~~화면에서 보여줄 항목 최종 목록 확정~~ ✅ 완료
9. ~~`collect_concept_with_q4()`에 capex/operating_cash_flow 전용 중간역산(YTD 기반) 경로 추가~~ ✅ 완료 (v10, 이번 세션)
10. ~~GOOGL/TSLA `--rebuild` 재실행, `ev_fcf`/`fcf_margin` 정상 계산 검증~~ ✅ 완료 (이번 세션)
11. **화면(UI) 구현 ← 다음 세션 시작점** — `fmp_price_history` 조회 로직(get_db + @reactive.calc)도 이 단계에서 함께 작성
12. (낮은 우선순위) 67건 회귀성 diff 원인 조사 (이번 세션 신규 발견)
13. (낮은 우선순위) TSLA `operating_cash_flow_tag_used` 빈 문자열 이슈 원인 확인
14. (세션 마무리 확인 필요) v10 변경사항 git 커밋 여부 확인, `dry_run_v10_check.py`/`verify_ev_fcf.py` git 처리 방침 결정

## 📌 나중에 다시 볼 가치가 있는 사실

- `fmp_symbols`와 `tickers`는 절대 같은 모집단이 아니다.
- `fmp_price_history`에는 `close_price`만 있다 (symbol별 + `NDX` 벤치마크, PK `(symbol, date)`).
- `fmp_metrics`는 `(symbol, calculated_at)` PK를 가진다.
- **CAGR 앵커 원칙**: 연도/기간 기반 파생 지표(CAGR 등) 계산 시, 조회 기준 시점(`as_of`)의 달력 연도가 아니라 실제 존재하는 최신 원천 데이터 행의 연도를 앵커로 삼을 것.
- **CAGR NULL은 버그가 아닐 수 있다**: 시작/종료 연도 값이 0 이하(적자)면 거듭제곱근 계산이 정의되지 않아 NULL이 정상.
- **EPS 분할조정은 뺄셈(Q4 역산) 이전에 이루어져야 한다.**
- `NDX`는 벤치마크로만 쓰고, `fmp_symbols` 기준 계산 대상 필터링에서는 제외된다.
- NDX 대비 초과수익률은 저장하지 않고 동적 계산하는 것으로 확정.
- `common/` 같은 로컬 패키지를 새로 import하는 프로덕션 스크립트는 systemd 실행 디렉토리 기준 `sys.path`까지 확인해야 한다.
- **프로젝트에는 원래 별도 venv가 없는 게 정상 상태다.**
- **`fmp_quarterly_financials`는 COALESCE upsert(새 값 NULL이면 기존 값 유지)이고, `fmp_metrics`는 `EXCLUDED.col`로 무조건 덮어쓰는 방식이다 — 재적재 안전성 판단 시 이 둘을 절대 혼동하지 말 것.** COALESCE 테이블은 "새로 계산한 값이 NULL이어도 기존 값을 안 지운다"는 성질 때문에, 회귀처럼 보이는 "DB=값 → new=None" 차이가 나와도 실제 upsert 시 DB가 깨지지 않는다 (이번 세션 실증).
- **systemd `Type=oneshot`은 restart/daemon-reload 없이 파일 교체만으로 다음 트리거부터 자동 반영된다.**
- **상대경로 `open()`으로 인한 파일 중복 생성 가능성을 항상 의심할 것.**
- **DB 조회 함수는 화면 모듈 또는 스케줄러 스크립트 안에 직접 둔다. 별도의 DB 조회 전용 서비스 파일은 만들지 않는다.**
- **결측 알림(로그/텔레그램)이 떴다고 바로 버그로 단정하지 말 것.** 원천 데이터 존재 여부부터 확인해 "설계된 정상 알림"과 "실제 버그"를 구분한다.
- **공유 함수(여러 concept/여러 심볼에 쓰이는 함수) 수정 시, 수정 범위를 꼭 필요한 concept으로 좁히고, 그 근거(로그 등)를 확인한 뒤 진행할 것.**
- **매 실행마다 원천부터 새로 계산하는 함수라면, "정확한 값을 만드는 로직"을 "임시로 채우는 로직"보다 먼저 실행되도록 순서만 잡으면, 별도의 우선순위 flag 없이도 정확한 값이 항상 우선하게 만들 수 있다.** (v10에서도 동일 패턴 적용: annual 기반 로직 다음에 10-K 없는 역산 로직을 배치.)
- **결측 알림 로직을 고칠 필요 없이, 결측의 근본 원인(파생 로직 공백)만 고치면 알림은 자연히 해소된다** — "결측 케이스를 예외처리"하는 방향은 잘못된 접근 (이번 세션 확정).
- **raw entry 자신의 `period_start`(`entry["start"]`)를 이용하면, annual(10-K) entry 없이도 회계연도 그룹핑이 기술적으로 가능하다** — `collect_concept_with_q4()`의 v10 확장이 이 사실에 기반함.

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
- `_tag_used`류 provenance 컬럼은 값(value)이 NULL이어도 "결측 사유"만 기록하는 용도로 쓸 수 있다.
- 여러 곳에서 공유하는 leaf 모듈을 수정할 때: (1) 다른 호출부 동작이 바뀌는 게 의도된 것인지 확인, (2) 새 의존성 추가 시 그 모듈을 쓰는 모든 실행 환경에 패키지가 있는지 확인.
- 새 import를 프로덕션 스크립트에 추가할 때는 패키지 설치 여부뿐 아니라, 그 스크립트가 실행되는 실제 작업 디렉토리에서 관련 로컬 패키지 경로가 잡히는지까지 확인할 것.
- 실행할 때마다 재생성되는 로그/캐시/백업 파일은 git으로 추적하지 않는다(`.gitignore`). 단, 디스크에는 그대로 남겨서 재조사 방지 목적은 유지.
- 프로젝트와 무관한 별도 목적의 디렉토리/설정은, 형상관리 불필요 판단이 있으면 매 세션 재질문 없이 `.gitignore`로 영구 제외한다.
- **연도/기간 기반 파생 지표(CAGR 등) 계산 시, 조회 기준 시점(`as_of`)의 달력 연도가 아니라 실제 존재하는 최신 원천 데이터 행의 연도를 앵커로 삼을 것.**
- **사용자가 기간을 자유롭게 선택해야 하는 지표(예: NDX 대비 초과수익률)는 배치로 고정 기간을 미리 계산해서 저장하지 않고, 조회 시점에 동적으로 계산한다.**
- **다른 도구/에이전트(Copilot 등)가 이미 실제 코드를 작성하고 DB에 반영했을 가능성을 항상 열어둘 것.**
- **문서의 "완료/수정됨" 서술을 곧이곧대로 믿지 말고, 실제 코드와 DB 값으로 재검증할 것.** 단, 사용자가 세션 중 명시적으로 재확인 생략을 지시하면 그 지시를 따른다.
- **COALESCE 재적재 전에는 수동 패치 이력과 이번 수정의 실제 영향 필드를 먼저 대조할 것.**
- **`fmp_quarterly_financials`(COALESCE)와 `fmp_metrics`(무조건 덮어쓰기)는 upsert 방식이 다르다 — 재적재 안전성 판단 시 반드시 구분할 것.**
- **systemd `Type=oneshot`은 파일 교체만으로 충분, unit 파일 변경 시에만 `daemon-reload` 필요.**
- **상대경로 `open()`으로 인한 파일 중복 생성 가능성을 항상 의심할 것.**
- **DB 조회 함수는 화면 모듈 또는 스케줄러 스크립트 안에 직접 둔다. 별도의 DB 조회 전용 서비스 파일은 만들지 않는다.**
- **결측 알림(로그/텔레그램)이 떴다고 바로 버그로 단정하지 말 것.**
- **공유 함수 수정 시, 수정 범위를 꼭 필요한 concept으로 좁히고, 그 근거를 확인한 뒤 진행할 것.**
- **매 실행마다 원천부터 새로 계산하는 함수라면, "정확한 값을 만드는 로직"을 "임시로 채우는 로직"보다 먼저 실행되도록 순서만 잡으면, 별도의 우선순위 flag 없이도 정확한 값이 항상 우선하게 만들 수 있다.**
- **결측 알림 로직을 고칠 필요 없이, 결측의 근본 원인만 고치면 알림은 자연히 해소된다.**
- **raw entry 자신의 period_start를 이용하면 annual(10-K) entry 없이도 회계연도 그룹핑이 기술적으로 가능하다.**
- **파일을 VM으로 옮기는 방법을 지레짐작하지 말고, 필요하면 heredoc(`cat > file << 'EOF'`)으로 바로 붙여넣기 가능한 형태로 줄 것.**
- **선택지 간 실질적 차이가 없으면 굳이 선택지로 나눠 묻지 말고 바로 진행할 것.**

---

## 🗂️ 주요 파일

- `~/asset-cloud/scheduler/valuation_sec_edgar_raw_collect.py` — SEC EDGAR raw 수집(1단계)
- `~/asset-cloud/scheduler/valuation_sec_edgar_backfill.py` — 텔레그램 알림 훅 + PROJECT_ROOT sys.path 고정. **v10 (이번 세션): capex/operating_cash_flow 전용 "10-K 없는 중간분기 역산" 경로 추가, VM 프로덕션 파일 직접 교체 완료. git 커밋 여부는 다음 세션에 확인 필요.**
- `~/asset-cloud/scheduler/valuation_classify_review_log.py` — review_log 원인별 분류
- `~/asset-cloud/scheduler/valuation_fmp_collector.py` — FMP 수집. `fmp_financials`/`fmp_estimates` upsert + `fmp_metrics`의 price/market_cap/EV upsert. 매일 08:40 KST.
- `~/asset-cloud/scheduler/valuation_fmp_price_backfill.py` — yfinance 기반 주가 히스토리 1회성 backfill (`fmp_price_history`)
- `~/asset-cloud/scheduler/valuation_alphavantage_fallback_fill.py` — v3. 항목 단위 apply 자동화는 "불필요"로 최종 폐기.
- `~/asset-cloud/app/utils/fmp_valuation.py` — 순수 계산 모듈. trailing/forward/run-rate PE, CAGR, margin류, percentile score, `excess_return_vs_benchmark`, `sum_latest_n_fcf`, `cagr`, **`build_snapshot_metrics`, `build_axis_scores`(이번 세션 실제 사용 확인, 내부 구현은 미확인 상태 — 필요 시 다음 세션에 직접 확인할 것)** 포함. DB write 없음. 커밋 완료.
- `~/asset-cloud/scheduler/valuation_fmp_metrics.py` — FMP 가치평가 스냅샷 엔진(배치 runner). 10종목 전체 처리 완료, `net_debt_equity` upsert 반영 완료. **`--symbol`(복수), `--rebuild`, `--dry-run` CLI 옵션 보유 확인 (이번 세션).** 커밋 완료.
- `~/asset-cloud/scheduler/valuation_fmp_metrics.service` / `.timer` — systemd 등록·활성화 완료 (08:50 KST). 커밋 완료.
- `~/asset-cloud/scheduler/dry_run_v10_check.py` — **(이번 세션 신규)** DB 쓰기 없이 `collect_symbol()` 결과만 계산해 기존 DB 값과 비교하는 검증 스크립트. git 처리 방침 미정.
- `~/asset-cloud/scheduler/verify_ev_fcf.py` — **(이번 세션 신규)** `valuation_fmp_metrics.run(dry_run=True, ...)` 결과에서 `ev_fcf`/`fcf_margin` 값을 직접 출력해 확인하는 검증 스크립트. git 처리 방침 미정.
- `~/asset-cloud/common/notify.py` — 실제 텔레그램 전송 구현.
- `~/asset-cloud/common/price_updater_common.py` — `common/` 패키지 접근용 `sys.path.insert` 패턴의 원본 예시.
- `~/asset-cloud/scheduler/config.json` — `telegram_token`, `telegram_chat_id`, `fmp_api_key`, `db_password` 포함. `.gitignore` 대상, 커밋 안 됨.
- `~/asset-cloud/scheduler/valuation_sec_edgar_pipeline.service` / `.timer` — 08:30 KST.
- `~/asset-cloud/scheduler/valuation_fmp_collector.service` / `.timer` — 08:40 KST.
- `fmp_metrics` 테이블 — `trailing_pe`, `run_rate_pe`, `peg`, CAGR류, margin류, `roe`, `debt_equity`, `net_debt_equity` 컬럼 존재. `growth_adj_value`, `revenue_efficiency`는 삭제 완료.
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
- **`sec_xbrl_facts_raw` 테이블 스키마:**
  ```
  symbol        varchar(10)   not null
  concept_tag   varchar(100)  not null
  unit          varchar(20)   not null
  form          varchar(10)   not null
  fy            integer
  fp            varchar(10)
  period_start  date          not null
  period_end    date          not null
  filed_date    date          not null
  accn          varchar(30)   not null
  val           numeric       not null
  collected_at  timestamp     default now()

  PK: (symbol, concept_tag, accn, period_start, period_end)
  ```
- **`fmp_quarterly_financials` 컬럼 구성**: `fiscal_quarter_end`, `revenue`(+`revenue_tag_used`), `eps_diluted`(+`_tag_used`), `net_income`(+`_tag_used`), `operating_income`(+`_tag_used`), `operating_cash_flow`(+`_tag_used`), `capex`(+`_tag_used`), `free_cash_flow`. **`_tag_used` 컬럼에 나올 수 있는 파생 표시 suffix: `|derived_q4`, `|derived_ytd`(annual 기반), `|derived_ytd_no10k`(v10 신규, 10-K 없는 중간역산), `|split_adj`(eps_diluted 분할조정 적용).**

---

# 현재 우선순위 (다음 세션 여기서 바로 시작)

## 🖥️ 화면(UI) 구현 — 최우선

### 확정된 UI 구조
- **기본 화면**: 10종목 한 줄씩 테이블, `composite_score` 기준 정렬. 행 클릭 시 종목 상세 뷰로 전환 (테이블 ↔ 상세 뷰 2단 구조).
- **테이블에 노출할 컬럼**: v1 우선 노출 지표만
  - Valuation: `trailing_pe`, `ev_fcf`, `psr`
  - Growth: `revenue_cagr_3y`, `eps_cagr_3y`, `fcf_cagr_3y`
  - Quality: `gross_margin`, `operating_margin`, `fcf_margin`, `roe`
  - Risk: `debt_equity`, `net_debt_equity`
  - 그 외(5년 CAGR, `net_margin`, `fcf_efficiency`, `ev_ebitda` 등 보조 지표)는 상세 뷰에서만.
- **raw 값 vs score 표시 방식**: raw 값을 기본으로 노출하고, 셀 배경색을 percentile score(0~100) 기준 heatmap으로 표시. `composite_score`만 별도 숫자 컬럼으로 노출.
- **NULL 표시 방식**: 결측(NULL)은 빈 칸으로 두되, 적자로 인한 CAGR NULL처럼 "구조적으로 계산 불가"인 경우를 구분 표시할지는 미확정 — 화면 작업 시 재논의.
- **NDX 초과수익률 기간 UI**: 고정 옵션(1년/3년/5년) 드롭다운 + "직접 입력" 커스텀 기간 옵션 추가.

### 작업 순서 (합의된 방향)
사용자가 화면을 보면서 계속 수정 요청할 것을 감안해, 성능 최적화를 뒤로 미루고 기능/레이아웃 확정을 먼저 하는 순서로 진행. 처음부터 Shiny로 시작 (HTML 프로토타입 후 교체 방식은 채택 안 함).

1. **화면 뼈대 작업**
   - `app/modules/valuation.py` 신규 생성 (`accounts.py`, `dashboard.py`, `portfolio.py` 네이밍 관례 따름)
   - 이 단계에서는 캐싱 없이 `get_db()`로 직접 조회 → 렌더링만 구현
   - 테이블 + 상세뷰, heatmap 색상, NDX 기간 드롭다운 구현
   - 라우팅/네비게이션 연결 방식은 시작 전 먼저 `app/app.py` 확인 후 결정 — 아직 미확인.
2. **반복 수정 단계**
   - 사용자가 보면서 레이아웃/표시 항목/색상 등 계속 수정 요청 → 그때그때 반영
   - 최적화 관련 지적 사항은 메모만 해두고 나중으로 미룸
3. **확정 후 최적화 단계**
   - DB/Redis 호출 리뷰 (반복 호출 여부 점검, `@reactive.calc` 캐싱 적용)
   - JS DOM patch 최적화 — 다른 화면 모듈(`accounts_js.py`, `news_js.py`, `settings_js.py`)의 실제 패턴을 이 단계 시작 시 반드시 먼저 grep해서 확인 후 따라갈 것.

### NDX 초과수익률 `fmp_price_history` 조회 로직
- 화면 모듈(`valuation.py`) 안에서 `with get_db() as conn:` + `@reactive.calc`로 구현.
- 계산은 시작일 이전/종료일 이전 각각 최근 거래일 종가 1건씩만 조회하면 충분.
- 순수 계산 함수 `excess_return_vs_benchmark()`는 이미 구현·커밋 완료 — 화면에서는 호출만 하면 됨.

### 다음 세션 시작 시 먼저 할 일
1. `app/app.py` 확인 — 기존 탭/라우팅 구조에 새 화면을 어떻게 끼워 넣을지 파악
2. 다른 화면 모듈(`portfolio.py` 등) 하나를 참고용으로 열어서 Shiny UI/server 함수 구조, `@reactive.calc` 패턴 실측 확인 후 동일 스타일로 시작
3. (병행 확인) v10 변경사항 git 커밋 여부, `dry_run_v10_check.py`/`verify_ev_fcf.py` git 처리 방침

## 🔽 낮은 우선순위 (여유 있을 때)

1. v10 dry-run에서 발견된 67건 회귀성 diff 원인 조사 (COALESCE라 DB 안전은 확인됨, 원인 자체는 미상)
2. TSLA `operating_cash_flow_tag_used` 빈 문자열 이슈 원인 확인