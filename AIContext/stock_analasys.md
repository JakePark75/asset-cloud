# FMP 빅테크 가치평가 서비스 — 진행 요약 (정리본, 37차 세션 기준)

> 이 문서는 매 세션 시작 시 첫 메시지에 첨부해서 사용.
> **아래 "확정 상태"와 "영구 원칙"은 재검증/재확인 불필요.** 조사 과정이나 시행착오는 결론이 난 시점에 삭제하고 결론만 남기는 방식으로 정리됨 — 과정을 다시 궁금해하지 말 것.

---

## 🏗️ 이 서비스의 위치 — 개인 자산관리 시스템(`asset-cloud`)에 통합됨 (34차 확정, 변경 없음)

가치평가 서비스는 독립 프로젝트가 아니라 기존 개인 자산관리 클라우드 서비스(`asset-cloud`)의 일부 기능. `asset-cloud/scheduler/`는 가치평가와 자산관리 시스템(`price_updater`, `daily_inserter`, `myassets`, `news_fetcher`)이 공유하는 디렉토리. 가치평가 파일은 `valuation_` 프리픽스로 flat 배치(폴더 분리는 전체 리팩토링 시점까지 보류, 34차 A안 재확정). `fmp_symbols`(가치평가 대상)과 `tickers`(자산관리 보유 종목)는 완전히 다른 모집단 — 절대 같은 것으로 취급하지 말 것.

---

## 🤖 Claude 작업 지침 (매 세션 최우선 적용)

- DB/터미널 명령어 제공 시 항상 계정 정보 포함해서 바로 실행 가능한 형태로 줄 것:
  `PGPASSWORD=qkrworb0! psql -U jake -d assetdb -P pager=off -c "..."`
- 여러 단계로 이루어진 작업(백업→실행→검증 등)을 안내할 때는, 검증(diff) 단계를 반드시 실행(쓰기) 단계보다 먼저 제시할 것.
- DB/Redis 읽기·쓰기 시 반드시 검토: 해당 호출이 꼭 필요한 시점인지, 불필요하게 반복 실행되는 건 아닌지 확인.
- 검토가 필요한 프로젝트 내 파일이 있으면 바로 요청하고, 추론에 의해 분석이나 수정 코드를 작성하지 않는다. 파일명이나 문서 서술만으로 역할·의존성을 추측하지 말고 항상 실제로 열어보거나 grep으로 확인. **37차 실제 적용 사례**: eps_diluted 3건 결측 원인을, 문서 서술이나 추측이 아니라 `sec_xbrl_facts_raw` 원본 쿼리와 `collect_concept_with_q4` 실제 코드를 직접 읽어 규명함(META/PLTR는 3-slot 조건 미충족, TSLA는 raw 자체가 없음). 텔레그램 알림 구현 시에도 `common/kis_auth.py`의 `CONFIG_PATH` 계산 방식과 `price_updater_ws.py`의 `common/` import 패턴을 직접 grep해서 확인 후 동일하게 따름.
- 코드 작성 시 편의를 위한 임시적 우회나 암묵적 가정에 기대지 않는다.
- 사용자가 확정적으로 산출물을 요청하기 전에 성급히 긴 설명이나 코드 예시를 제공하지 않는다.
- 사용자가 코드 파일을 업로드한 경우, 수정 코드 제공은 반드시 artifact(파일)로 한다.
- 기술 선택/추천 시 반드시 근거(웹 검색 등) 명시. **37차 실제 적용 사례**: FMP 무료(Basic) 플랜의 Historical Data Range가 5년(Premium 이상만 30년+)이라는 점을 공식 가격 페이지에서 확인 후, eps_diluted 3건에 FMP 다른 endpoint 활용 옵션을 "유료 필요"로 기각함. Telegram Bot API `sendMessage` 요청 형식(`https://api.telegram.org/bot<TOKEN>/sendMessage`, POST, `chat_id`/`text`)도 공식 문서로 확인 후 구현.
- 코드 작성 전, 사용할 API/함수/동작 방식이 추론이나 기억 기반이면 반드시 사실 확인 후 사용자와 방향 동의를 받고 나서 작성한다.
- 디버깅/이상 신호 대응: 확증 없이 롤백/수정 등 되돌리기 어려운 조치를 먼저 실행하지 않는다.
- "여러 필징이 동의하는 값(다수결)이 항상 옳다"는 가정은 위험함(TSLA 2024 사례로 폐기 확정). 오염 탐지는 "같은 필징 하나 안의 자기모순"으로 판단.
- 의도 재확인: 맥락상 이상한 요청이면 맹목적으로 수행하지 않고 의도를 먼저 확인한다.
- 모든 대화는 팩트 위주로 제공하고, 팩트가 아닌 예상/추론은 반드시 그렇다고 명시한다.
- 대화 중 제공된 내용을 주장하기 전에 반드시 해당 부분을 다시 확인한다.
- 오라클 클라우드 무료 티어 범위 내에서만 서비스 구성, 유료 서비스 선택 유도하지 않는다.
- 여러 선택지가 있으면 성급히 1개만 제안하지 말고 사용자에게 선택지를 제공한다.
- 조사/분석 범위를 한 번에 넓히지 말 것. 확정 가능한 것부터 즉시 종결하고 문서에 남긴 뒤 다음으로 넘어간다.
- 프로덕션 스크립트를 수정할 때, 백업 명령을 내려도 사용자가 실제로 그 시점에 실행했는지, 파일을 먼저 교체하고 백업한 건 아닌지 확인 없이 신뢰하지 말 것.
- **기존 함수/모듈을 수정할 때, 그 함수가 이미 다른 여러 곳에서 쓰이고 있을 가능성과 그 수정이 일으킬 사이드이펙트(신규 의존성 추가로 인한 import 실패 등)를 반드시 짚고 넘어갈 것.** (37차 신규 — 아래 "실제 적용 사례" 및 영구 원칙 참조)
- **Claude의 메모리 시스템(세션 간 자동 기억)에는 이 프로젝트 관련 내용을 저장하지 말 것.** 사용자가 명시적으로 강하게 요청함. 세션 간 인수인계는 반드시 이 진행 요약 문서(매 세션 사용자가 직접 첨부)로만 한다.

---

## 🔧 최종 확정 상태 (재검증 불필요)

### ⭐⭐⭐ 32~36차 세션: DB 무결성 스캔, v8/v9 반영, systemd 스케줄링, AV 폴백 캐시 버그 수정, 결측 6건 해소 — 완전 종결 (요약)

- v8 자기모순 격리, v9 결측 자동 감지(`--full` 옵션), systemd timer 2종 등록(08:30/08:40 KST).
- 36차: systemd 타이머 첫 실행 누락(등록 시각이 그날 스케줄을 이미 지난 뒤였던 것) 확인 후 수동 실행으로 검증. `--full` 재스캔으로 전체 결측 6건 확정, AMZN/NVDA capex 결측은 이미 해소된 상태 확인.
- 36차: AV 폴백 스크립트(`valuation_alphavantage_fallback_fill.py`) v3 — 캐시 키에 `value_key` 누락으로 같은 AV endpoint를 공유하는 필드(capex/ocf)가 서로 값을 덮어쓰는 버그 발견·수정(`fetch_av_raw`/`fetch_av_function` 분리), unmatched 항목도 로그 파일에 기록하도록 개선, 당일 TTL 파일 캐시 추가. 기존 DB 63건 전수 대조로 과거 오염 없음 확인.
- 36차: 6건 중 3건(TSLA capex/ocf, AAPL ocf) AV 폴백으로 수동 반영 완료. 나머지 3건(TSLA/META/PLTR eps_diluted)은 37차로 이월.
- (상세 경위는 35~36차 원본 문서 참고, 이 문서에서는 결론만 유지)

### ⭐⭐⭐ 37차 세션 A: eps_diluted 결측 3건(TSLA/META/PLTR) 원인 규명 및 해소 — 완전 종결

**원인 규명 (raw 쿼리 + 코드 직접 확인)**: 세 건 모두 공통 원인 — **해당 분기 시점에 아직 정기 공시(10-Q) 의무가 없었고**, 이후 IPO/직상장 등록 과정에서 과거 실적을 소급 공시하며 분기 해상도가 불완전하게 채워진 것.
- **TSLA 2010-03-31**: `sec_xbrl_facts_raw`에 raw 데이터 자체가 없음. IPO(2010-06-29) 이전 분기라 애초에 10-Q 제출 의무 없었음 — 원천 데이터 부재, 영구 결측.
- **META 2011-12-31**: raw에 FY값(0.46)과 반기YTD(0.22)·9개월YTD(-0.32)는 있으나 discrete Q1이 없어(2012-05 IPO 전 S-1 소급공시라 분기 해상도 불완전), `collect_concept_with_q4`의 "정확히 3개 slot 필요" 조건(694번 줄)을 충족 못 해 자동 역산 안 됨.
- **PLTR 2019-12-31**: 마찬가지로 FY값(-1.02)·9개월YTD(-0.73)만 있고 Q1/Q2 자체가 없음(2020-09 직상장 전 소급공시) — 동일 이유로 자동 역산 안 됨.

**처리**:
- TSLA: 영구 결측으로 확정. `eps_diluted`는 NULL 유지, **`eps_diluted_tag_used = 'permanently_missing_pre_ipo'`**로 사유만 기록(다음 재조사 방지 목적, 값 없이 tag만 세팅 — 컬럼에 NOT NULL 제약 없음 확인, 결측 감지 로직은 `eps_diluted IS NULL`만 보므로 `--full` 재스캔에는 계속 잡히지만 원인은 즉시 파악 가능).
- META/PLTR: `Q4 = FY − 9개월YTD` 수동 산술 역산(코드가 다른 필드에 이미 쓰는 방식과 동일한 원리, 3-slot 조건만 미충족이라 자동화 밖이었을 뿐). META=0.78, PLTR=-0.29. `eps_diluted_tag_used = 'manual_fy_minus_9moytd'`로 반영, DB 직접 UPDATE(`IS NULL` 가드).

**처리 방향: 완전 종결.**

### ⭐⭐⭐ 37차 세션 B: 미해결 항목 1번(결측 필드 선택적 apply 스크립트) — "불필요" 판단으로 종결

**배경**: 35차부터 이월된 항목. "결측 감지 → 사람이 항목 단위로 승인/보류 → DB 반영"을 자동화하는 별도 큐 시스템을 만들 계획이었음.

**판단 근거**: 37차에서 실제 처리한 결측 6건(36차 3건 + 37차 3건)의 원인이 캐시 버그, 상장 전 원천 데이터 부재, 반올림/역산 판단 등 **매번 완전히 달랐음**. 이런 상황에서 "항목 단위 승인 큐"라는 일반화된 시스템을 만드는 건 과설계로 판단 — AV 폴백처럼 "같은 방식으로 대량 처리가 실제로 성립하는 케이스"에만 자동화 가치가 있고, 나머지는 매번 원인 규명이 선행되어야 하므로 자동화로 시간이 절약되지 않음.

**최종 방향**: 결측 처리는 아래를 표준 절차로 확정.
1. (37차 C 참고) 텔레그램 알림으로 결측 발생 즉시 인지
2. `--full` 스캔 또는 알림 내용으로 원인 규명(raw 쿼리 + 코드 확인)
3. 구조적으로 고칠 수 있으면 로직 수정(예: AV 캐시 버그), 아니면 개별 수동 처리 + `_tag_used` 컬럼에 사유 기록

별도의 apply 스크립트/승인 큐 설계는 **폐기**. 미해결 항목 목록에서 제거.

**처리 방향: 완전 종결.**

### ⭐⭐⭐ 37차 세션 C: 결측 발생 시 텔레그램 알림 구현 — 완전 종결

**배경**: `common/notify.py`의 `notify_telegram_alert()`가 기존엔 콘솔 출력만 하는 더미였음(다른 프로젝트 모듈, `snap.py`/`daily_snapshot.py`/`daily_inserter.py`에서도 순환임포트 방지 목적으로 이미 이 leaf 모듈을 공유해서 사용 중이었음 — 이번 수정으로 이 세 파일의 호출부도 자동으로 실제 텔레그램 전송으로 전환됨, 사용자 의도와 일치 확인).

**구현**:
- `common/notify.py`: `scheduler/config.json`의 `telegram_token`/`telegram_chat_id`를 읽어(경로 계산은 `common/kis_auth.py`의 `CONFIG_PATH` 패턴과 동일) 실제 `POST https://api.telegram.org/bot<TOKEN>/sendMessage` 호출. 전송 실패는 예외를 삼키고 로그만 남김(알림 실패가 이미 끝난 파이프라인을 죽이면 안 되므로).
- `valuation_sec_edgar_backfill.py`: `missing_fields_new_log`(매일 신규 INSERT 시 결측 감지, `--full` 스캔은 제외 — 사용자 확정)가 비어있지 않을 때만 심플한 요약(건수 + symbol 목록)으로 알림 발송. 상세 내역은 어차피 `missing_fields_new_log.json`에 있으므로 알림 메시지 자체는 최소화.

**검증**: `requests` 신규 의존성 추가로 인한 import 실패 위험을 짚었고, 세 파일이 같은 서비스(`/usr/bin/python3`, `requests` 2.34.2 설치 확인)에서 실행됨을 확인해 리스크 해소. `python3 -c "from common.notify import notify_telegram_alert; notify_telegram_alert(...)"`로 실전 테스트, 텔레그램 수신 확인.

**처리 방향: 완전 종결.**

### ⭐⭐⭐ 37차 세션 D: 가치평가 관련 파일 최초 git 커밋 준비(하우스키핑) — 진행 중

**배경**: 가치평가 관련 파일들이 지금까지 한 번도 커밋된 적 없었음(37차에서 최초 확인).

**정리 결과**:
- 삭제: `scheduler/tests/`(테스트 파일, 불필요 확인), `scheduler/fmp_quarterly_financials_backup_v8_20260706_223359.sql`(v8→v9 전환 이후 불필요 확인), `*.timer.bak` 2개(타이머 정상 등록 검증 완료 후 불필요), 원인불명 `git` 파일(사용자 확인 후 삭제)
- `.gitignore` 추가: 실행할 때마다 재생성되는 로그/캐시류(`alphavantage_fill_log.json`, `eps_restatement_override_log.json`, `review_log.json`, `review_log_classified.json`, `self_inconsistency_log.json`, `split_adjustment_log.json`, `missing_fields_full_log.json`, `missing_fields_new_log.json`, `scheduler/*.sql`, `scheduler/*.timer.bak`), `.github/`(GitHub Copilot 지침 저장 디렉토리 — 형상관리 대상 아님으로 사용자 확정, 오늘뿐 아니라 앞으로도 계속 ignore)
- 커밋 대상 확정: 가치평가 소스 코드/서비스 정의 전체 + `AIContext/stock_analasys.md`(이 문서 자체)
- `scheduler/config.json`이 `.gitignore`에 이미 걸려있음을 재확인(텔레그램 토큰 등 시크릿 유출 방지, 커밋 전 필수 확인 사항이었음)

**처리 방향**: 이 문서(`stock_analasys.md`) 최신화 후 최종 `git add`/`git commit` 실행 예정 — **다음 세션 시작 전 사용자가 직접 커밋 완료할 것.**

---

## ⏳ 미해결 항목 (37차 세션 기준 갱신)

1. ~~결측 필드 선택적 apply 스크립트~~ — **37차: "불필요" 판단으로 폐기, 목록에서 제거** (37차 세션 B 참고)
2. ~~META/PLTR/TSLA eps_diluted 3건~~ — **37차: 완전 해소** (37차 세션 A 참고)
3. historical 통계 저장 구조 설계
4. `fmp_price_history`↔`fmp_quarterly_financials` 결합, Trailing PE 일별 시계열 계산 알고리즘 설계
5. `fmp_metrics` 최종 컬럼 ALTER TABLE 적용
6. 계산 엔진 스크립트 작성
7. 화면(Shiny) 설계 — `app/modules/` 컨벤션 따라 진행 예정, 상세 설계는 미착수

**32~36차의 종결 항목과 37차의 "eps_diluted 3건 해소", "apply 스크립트 폐기 판단", "텔레그램 알림 구현"은 전부 완전 종결. 목록에서 제거됨. 37차 D(git 커밋)는 진행 중 — 다음 세션 시작 전 커밋 완료 여부 확인 필요.**

---

## 📌 영구 원칙 (계속 유효, 매 세션 적용)

- 값 채택 로직에 "다수결"을 넣지 말 것. 오염 탐지는 "같은 필징(accn) 하나 안의 자기모순"으로 판단(`SELF_CONSISTENCY_TOLERANCE = 0.05`, eps_diluted는 구조적으로 제외).
- 백업→실행→검증 순서로 작업을 안내할 때, diff/검증 단계를 실행(쓰기) 단계보다 반드시 먼저 제시할 것.
- `fmp_symbols`(가치평가 대상)와 `tickers`(자산관리 보유 종목)는 별개 모집단. 절대 같은 것으로 취급해 로직/시세를 엮지 말 것.
- `scheduler/` 폴더는 가치평가와 개인 자산관리 시스템이 공유하는 디렉토리. 가치평가 관련 파일은 `valuation_` 프리픽스로 구분, 폴더 단위 분리는 전체 리팩토링 시점까지 보류.
- 검토 필요한 파일은 반드시 실제로 열어보거나 grep해서 확인한 뒤 판단할 것. 문서 서술이나 파일명만으로 의존관계·역할을 추론해서 결론 내리지 말 것.
- COALESCE 방식 upsert가 적용된 테이블에서 "결측/변경 여부"를 감지할 때는, 매번 전체를 재조회하지 말고 "이번에 실제로 새로 쓰여진 행"만 검사 대상으로 좁힐 것 (`RETURNING ..., (xmax = 0) AS inserted` 패턴, PG14 기준).
- 프로덕션 쓰기 스크립트를 수정할 때, 백업 명령의 실행 시점(파일 교체 전인지 후인지)을 결과로 반드시 재확인할 것.
- 사용자가 붙여넣은 터미널 출력이 방금 요청한 명령의 결과가 맞는지 항상 재확인할 것.
- 같은 필드(리소스)를 공유하는 여러 값을 캐싱할 때는, 캐시 키에 "무엇을 캐싱하는지" 전체를 포함시킬 것 — raw 응답 자체를 캐싱하고 값 추출은 호출부에서 하는 방식으로 설계(36차 TSLA capex/ocf 캐시버그 사례).
- "매칭 실패"나 "결측" 같은 부정적 결과도 로그 파일에 남겨야 함 — 콘솔 출력에만 있고 파일에 없으면 다음 세션에서 같은 조사를 반복하게 됨.
- 디버깅/재검토 목적의 반복 실행이 예상되는 스크립트는, 실행할 때마다 외부 API를 다시 부르는 구조가 있는지 점검할 것. 캐시 기간은 데이터가 나중에 바뀔 가능성을 고려해 무기한이 아니라 짧게(당일 등) 제한.
- **⭐ (37차 신규) 원천 데이터가 특정 시기(예: 상장 전 소급공시)에 구조적으로 불완전해 자동 역산 조건(예: 3-slot)을 충족 못 하는 경우, "자동화 조건 미충족"과 "데이터 자체 부재"를 구분해서 판단할 것.** 후자는 영구 결측으로 확정하고 `_tag_used` 컬럼에 사유를 남기고(값은 NULL 유지), 전자는 남은 값들만으로 수동 산술 역산이 가능한지 검토(이번 사례: `Q4 = FY − 9개월YTD`는 Q1/Q2 discrete 없이도 성립).
- **⭐ (37차 신규) `_tag_used`류 provenance 컬럼은 실제 값(value)이 NULL이어도 "결측 사유"만 기록하는 용도로 쓸 수 있다** (컬럼에 NOT NULL 제약이 없는 한). 단, 결측 감지 로직이 `tag_used`가 아니라 `value IS NULL`만 검사하는 구조라면, 사유를 기록해도 재스캔 시 다시 잡힌다는 점을 사용자에게 명확히 알릴 것 — 사유 기록이 재조사 자체를 막아주는 건 아님.
- **⭐ (37차 신규) 여러 곳에서 공유해서 쓰는 leaf 모듈(예: `common/notify.py`)을 수정할 때는 반드시 아래 두 가지를 짚을 것**: (1) 이 함수를 부르는 다른 호출부들도 동작이 바뀌는 게 의도된 것인지 사용자에게 명시적으로 확인, (2) 새 외부 패키지(예: `requests`) 의존성을 추가하면 그 leaf 모듈을 가져다 쓰는 모든 실행 환경(다른 파일이 다른 venv/인터프리터에서 돌 수도 있음)에 그 패키지가 있는지 먼저 확인 — 없으면 원래 무조건 성공하던 import가 새로운 실패 지점이 됨.
- **⭐ (37차 신규) 실행할 때마다 재생성되는 로그/캐시/백업 파일은 git으로 추적하지 않는다(`.gitignore`).** 소스가 아니라 산출물이라 커밋해봐야 매번 diff 노이즈만 생김. 단, 디스크에는 그대로 남겨서(재조사 방지 목적) git 추적과 파일 보존은 별개로 다룰 것.
- **⭐ (37차 신규) 프로젝트와 무관한 별도 목적의 디렉토리/설정(예: `.github/` Copilot 지침)은, 형상관리가 필요 없다는 사용자 판단이 있으면 매 세션 재질문 없이 `.gitignore`로 영구 제외한다** (오늘 커밋에서만 빼는 것과는 다름 — 구분해서 처리할 것).

(이하 기존 영구 원칙 전부 유지: DB 분리 안 함, Trailing PE/Run-rate PE 정의, `get_concept_for_symbol` 구조, `collect_concept_with_q4` Q4 역산 조건, `_canonicalize_end_dates` tolerance 10일, COALESCE upsert, psql 사용법 등 — 필요시 코드에서 직접 재확인)

---

## 📁 주요 파일 위치 (37차 갱신)

- `~/asset-cloud/scheduler/valuation_sec_edgar_raw_collect.py` — SEC EDGAR raw 수집(1단계)
- `~/asset-cloud/scheduler/valuation_sec_edgar_backfill.py` — v9, 프로덕션 정상 가동 중. **37차: 텔레그램 알림 훅 추가**(`missing_fields_new_log` 발생 시, `--full`은 제외)
- `~/asset-cloud/scheduler/valuation_classify_review_log.py` — review_log 원인별 분류
- `~/asset-cloud/scheduler/valuation_fmp_collector.py` — FMP 수집 (변경 없음)
- `~/asset-cloud/scheduler/valuation_fmp_price_backfill.py` — yfinance 기반 주가 히스토리 1회성 backfill
- `~/asset-cloud/scheduler/valuation_alphavantage_fallback_fill.py` — v3(36차), 프로덕션 반영·검증 완료. 여전히 수동 실행 전용(`DRY_RUN` 상수). **37차: 항목 단위 apply 자동화는 "불필요"로 최종 폐기 결정.**
- `~/asset-cloud/common/notify.py` — **37차: 더미(콘솔 출력)에서 실제 텔레그램 전송으로 전환.** `scheduler/config.json`의 `telegram_token`/`telegram_chat_id` 사용. `snap.py`/`daily_snapshot.py`/`daily_inserter.py`도 이 함수를 공유하므로 해당 호출부들도 자동으로 실전 전환됨(의도된 것, 확인 완료).
- `~/asset-cloud/scheduler/config.json` — **37차 확인**: `telegram_token`, `telegram_chat_id` 키 추가됨. `.gitignore`에 이미 포함되어 커밋 대상 아님(재확인 완료).
- `~/asset-cloud/scheduler/av_response_cache_YYYY-MM-DD.json` — AV 폴백 스크립트의 당일 응답 캐시, 매일 새 파일 생성. `.gitignore`에 이미 포함.
- `~/asset-cloud/scheduler/valuation_sec_edgar_pipeline.service` / `.timer` — 프로덕션 등록·활성화, 08:30 KST. 36차 수동 실행으로 검증 완료, **7/9 첫 자동 실행 결과는 아직 미확인(다음 세션 최우선 확인 사항 1번)**.
- `~/asset-cloud/scheduler/valuation_fmp_collector.service` / `.timer` — 프로덕션 등록·활성화, 08:40 KST. 위와 동일 상황.
- `~/asset-cloud/scheduler/missing_fields_new_log.json` / `missing_fields_full_log.json` — v9 결측 감지 로그. `.gitignore`에 포함(37차).
- `~/asset-cloud/scheduler/alphavantage_fill_log.json` — matched/unmatched 모두 기록. `.gitignore`에 포함(37차).
- **37차 삭제**: `scheduler/tests/`(불필요 테스트 파일), `scheduler/fmp_quarterly_financials_backup_v8_20260706_223359.sql`(v8 백업, v9 전환 후 불필요), `scheduler/*.timer.bak` 2개(타이머 검증 완료 후 불필요)
- **37차 확인, 형상관리 영구 제외**: `.github/`(GitHub Copilot 지침, 프로젝트와 무관, `.gitignore` 대상)

---

## 🌐 환경 정보

- DB: `assetdb` / 사용자 `jake` / 비밀번호 `qkrworb0!` (psql 접속: `PGPASSWORD=qkrworb0! psql -U jake -d assetdb -P pager=off -c "..."`)
- PostgreSQL 버전: 14.23 (Ubuntu, aarch64)
- FMP API: 무료 플랜(Basic), 하루 250회 한도. **37차 확인**: 무료 플랜 Historical Data Range는 5년(Premium $59/mo 이상만 30년+) — 과거 데이터(2010~2019년대) 필요한 케이스는 무료 플랜으로 원천적으로 불가.
- Alpha Vantage API: 무료 플랜, 5 calls/min, 25 calls/day (`AV_CALL_INTERVAL_SEC=15`). 당일 파일 캐시로 디버깅 중 반복 호출 절약.
- Telegram Bot API: 무료. `https://api.telegram.org/bot<TOKEN>/sendMessage`, POST, `chat_id`/`text` 파라미터. 토큰/chat_id는 `scheduler/config.json`에 저장(git 추적 안 됨).
- 실행 환경: `/usr/bin/python3`, `requests` 2.34.2 설치 확인(37차). `snap.py`/`daily_snapshot.py`/`daily_inserter.py`/가치평가 스크립트 모두 같은 서비스/환경에서 실행됨.
- 오라클 클라우드 무료 티어 범위 내 운영 원칙 유지
- (DB 스키마, SEC/AV API 상세 정보 등은 이전 문서와 동일, 변경 없음 — 필요시 `\d 테이블명`으로 직접 재확인)

---

# 다음 세션(38차) 시작 시 최우선 확인 사항

1. **7/9 08:30/08:40 KST 첫 자동 실행이 실전에서 정상 동작했는지 확인**: `journalctl -u valuation_sec_edgar_pipeline.service --since "2026-07-09 00:00" --until "2026-07-09 01:00" --no-pager` (UTC 기준 시간대 주의), `valuation_fmp_collector.service`도 동일하게. 텔레그램 알림(37차 신규)이 정상 동작했다면 결측 여부도 별도 조회 없이 바로 확인 가능.
2. **37차 D(git 커밋)가 실제로 완료됐는지 확인** — 37차 세션 종료 시점엔 커밋 대상 정리만 끝나고 최종 `git commit`은 사용자가 세션 종료 후 직접 실행 예정이었음. `git log --oneline -5`로 재확인.
3. 3~7번(historical 통계 구조, Trailing PE 알고리즘, `fmp_metrics` ALTER, 계산 엔진, 화면 설계) 순서로 착수 — 미해결 1, 2번은 37차에 완전 종결됐으므로 다음 세션은 이 항목부터 바로 시작 가능.