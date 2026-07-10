"""
SEC EDGAR 분기 재무데이터 가공 스크립트 (v4)

역할 변경 (v3 -> v4):
- v3는 SEC EDGAR companyconcept API를 매번 직접 호출했음.
- v4는 이미 수집된 sec_xbrl_facts_raw 테이블을 원본으로 읽어서 가공만 수행함
  (raw/가공 2단계 분리 확정, 2026-07-01 세션). API 재호출 없음.
- fmp_quarterly_financials.filed_date 컬럼은 삭제가 확정되어 더 이상 채우지 않음
  (raw 테이블이 이미 원본 filed_date를 accn 기준으로 보유하고 있어 가공 테이블에 중복 불필요).

핵심 변경: eps_diluted 분할조정 결함 수정 (0번 이슈, 2026-07-01 3차 세션에서 실측 확인)
- 문제: "filed 최신값 채택" 규칙은 그 분기가 이후 다른 filing에 재인용될 때만 소급조정을 반영함.
  분할 시점 이후 1년 넘게 재인용되지 않은 과거 분기는 원본 미조정값이 영구 고정됨.
  실측 확인: NVDA 2017-04-30 분기 EPS $0.79가 4번의 refiling(2017~2019)에서 전부 동일값으로
  남아있음 -> 2021년 4:1, 2024년 10:1 분할이 전혀 반영 안 된 원본값임이 확인됨.
- 해결: eps_diluted 개념만 별도 처리.
  1) 같은 (symbol, period_start, period_end) 그룹에서 "최초 filed" entry의 val을 원본으로 고정 채택
     (필터/재인용 여부에 의존하지 않는 결정론적 기준)
  2) 그 위에 아래 SPLIT_HISTORY(분할 날짜+비율, 웹검색으로 사실 확인 완료)의 누적 비율로
     나눠서 현재 주식수 기준으로 소급 환산
- 나머지 6개 concept(revenue x2 fallback, net_income, operating_income,
  operating_cash_flow, capex)은 절대금액이라 분할과 무관 -> v3의 "filed 최신값 채택" 규칙
  그대로 유지 (회계 재작성(restatement) 반영 목적, MSFT Nokia/AMD GlobalFoundries 케이스 등).

Q4 역산 관련 (검토 완료, 이슈 아님으로 결론):
- Q4 = 연간 - (Q1+Q2+Q3) 역산은 revenue/income류 절대금액엔 정확하나, eps_diluted는
  분기별 가중평균주식수 차이로 인해 근사값일 수밖에 없음(회계 원리상 그렇다).
  정확한 값을 얻으려면 분기별 희석주식수 원본이 필요한데 raw 테이블에 애초에 수집되지 않은
  concept이라 구조적으로 정확한 값 자체를 만들 수 없음. tag_used에 '|derived_q4' 표시로
  한계를 이미 투명하게 노출하고 있어 추가 조치 없이 그대로 유지하기로 확정.

분할 이력 출처: 웹검색 확인 완료 (2026-07-01 세션). 데이터 보유기간(각 종목 최초 분기) 이후
발생한 분할만 포함. 데이터 시작일 이전 분할(예: AAPL 1987/2000/2005, GOOGL 2014-04)은
애초에 영향 범위 밖이라 제외.

---
v5 변경 사항 (6차 세션, 5차 세션에서 발견된 버그 수정):

1) revenue 태그 폴백 방식 변경 (get_revenue_for_symbol)
   - 기존: 우선순위 태그 순회 중 값이 하나라도 있는 첫 태그를 "통째로" 채택하고 종료.
     -> AAPL처럼 RevenueFromContractWithCustomerExcludingAssessedTax가 2017-09-30부터만
        존재하면, 그 이전 기간을 커버하는 SalesRevenueNet은 아예 시도되지 않아 과거 분기가
        통째로 유실됨 (코드 확증, raw 테이블 대조로 재확인).
   - 변경: 태그를 우선순위 순서대로 순회하되 "분기(end_date) 단위"로 병합. 우선순위 태그가
     커버하지 않는 분기만 다음 순위 태그로 채움. 어느 태그를 썼는지는 분기별로
     revenue_tag_used에 개별 기록.

2) Q4 역산 매칭 방식 변경 (collect_concept_with_q4)
   - 기존: SEC가 매긴 fy(정수) 태그로 Q1/Q2/Q3를 그룹핑. fy 태그는 "최초 filed 시점"에
     종속되어 같은 회계연도 분기라도 filed 시점에 따라 다른 fy로 찍히는 경우가 있어
     매칭 실패 발생 (NVDA 2021-01-31, PLTR 2020-12-31, TSLA 2017-12-31 등 확정 발견).
   - 변경: 연간(10-K) entry의 period_start~period_end 구간에 속하는 entry를 end_date로
     직접 추출 (fy/fp 태그 미사용). 정확히 3개 슬롯 + 각 구간 길이(75~105일) 검증 후에만
     역산. 조건 미충족 시 기존과 동일하게 역산하지 않음(원본 결측 취급).

3) YTD 누계 전용 concept 역산 추가 (collect_concept_with_q4)
   - 신규 발견 사실 (SEC 규정 확인 완료, 6차 세션): 현금흐름표 항목(operating_cash_flow,
     capex 등)은 SEC 중간보고서 규정(Reg S-X)상 "연초 누계" 기준으로만 공시되는 경우가
     있어, 개별 분기(discrete) 값이 raw에 아예 존재하지 않을 수 있음. 실측(AAPL): Q1만
     단일분기(약 90일) entry가 있고, Q2/Q3는 반기(약181일)/9개월(약272일) 누계만 존재.
   - 처리: quarterly(80~100일) 버킷 외 semiannual(170~190일), ninemonth(260~290일)
     버킷을 추가로 조회. 회계연도 내 3개 슬롯을 채울 때, discrete 값이 있으면 그대로 쓰고
     없으면 누계값에서 직전 누계(running_cum)를 차감해 개별 분기값을 역산 (fp="derived_ytd"
     로 표시, tag_used에 |derived_ytd 접미사). 이 로직은 특정 concept에 하드코딩하지 않고
     실제 raw 버킷 존재 여부로 자동 결정됨 (discrete로 공시되는 concept은 기존과 동일하게
     동작, 영향 없음).

---
v6 변경 사항 (8차 세션, review_log.json 248건 분류 결과 발견된 eps_diluted 부작용 수정):

1) eps_diluted "earliest" 채택 로직에 분할/재작성 판별 추가 (_pick_eps_entry 신규)
   - 문제: 기존 "최초 filed 고정" 규칙(0번 이슈 수정, v4)은 분할 소급조정 누락은
     막았지만, 분할이 아닌 진짜 재작성(회계기준 변경, 사업부 재분류 등)까지 전부
     최초값에 영구 고정시키는 부작용이 있었음. 실측 확인: review_log.json 재분류 결과
     eps_diluted 115건 중 42건이 분할로 설명되지 않는 재작성(예: MSFT 2016-09-30분기
     0.60->0.72, MSFT는 데이터 기간 내 분할 이력 없음).
   - 해결: 같은 분기를 재인용한 filing들의 값을, 그 사이 실제 발생한 분할비로 설명되는지
     _split_ratio_between()으로 검증. 설명되면 기존과 동일하게 최초값 유지, 설명 안 되면
     진짜 재작성으로 판단해 나머지 5개 concept과 동일한 최신값(latest) 채택으로 전환.
   - 전환된 케이스는 eps_restatement_override_log.json에 기록 (검증용, 신규 로그 파일).

2) 분할 배수 적용 기준을 period_end -> 채택된 filing의 filed일자로 변경
   - 이유: latest로 전환된 케이스는 해당 filing이 그 시점까지의 분할을 이미 자체
     반영해서 재인용했을 수 있음(실측: NVDA 2020-07-26분기, 2021-08-20 filed 값이
     2021-07-20 4:1 분할을 이미 반영). period_end 기준으로 남은 분할 배수를 계산하면
     이미 반영된 분할을 또 나누는 이중조정 오류가 생기므로, filed일자 기준(그 filing
     시점 이후 아직 반영 안 된 분할만)으로 바꿔야 정확함. earliest로 유지되는 케이스는
     filed일자가 period_end 직후라 기존 결과와 사실상 동일(영향 없음).

---
v7 변경 사항 (23차 세션, NVDA operating_income 2010-07-31 결측 재조사로 발견된 버그 수정):

정정: 19차 세션 진단("분기값/YTD값 선택 로직 버그")은 오진이었음. 23차 세션에서
raw를 직접 대조한 결과, 실제 원인은 값 선택 로직이 아니라 다음과 같음:

- SEC raw 자체에서 같은 실제 회계분기라도 concept(태그)별/filing별로 period_end가
  며칠 어긋나는 경우가 있음 (실측: NVDA 2010-07-31 사례, Revenues 태그의 2012-03-13
  filed 건 하나만 period_end가 2010-07-31로 찍혀 있고, 같은 분기의 다른 모든
  concept/filing은 전부 2010-08-01. 원인은 SEC 원본 filing 자체의 오기로 추정,
  raw 수집/가공 로직 문제 아님).
- 기존 collect_symbol()은 revenue_values와 other_results를 병합할 때 end_date가
  정확히 일치하는 것만 같은 분기로 취급했음 -> Revenues는 2010-07-31 키로,
  OperatingIncomeLoss/operating_cash_flow는 2010-08-01 키로 각각 들어가면서
  하나의 실제 분기가 두 개의 행으로 쪼개짐. 2010-07-31 행은 operating_income이
  처음부터 채워질 기회 자체가 없어 NULL로 upsert됨.

해결 (_canonicalize_end_dates, _remap_end_dates 신규):
- revenue_values + other_results 전체에 흩어진 end_date를, tolerance_days=10
  (AV fiscalDateEnding 매칭에 이미 쓰는 허용오차와 통일, 12차/18차 세션 확정 기준)
  이내로 가까우면 같은 분기로 간주해 대표 날짜 하나로 통일.
- 분기 간 최소 간격이 75일(QUARTER_GAP_MIN_DAYS)이라 10일 tolerance로도 서로 다른
  분기가 잘못 합쳐질 위험은 없음(65일 여유). 실측 스캔(23차 세션): 전 종목 15일
  이내 근접 쌍 전수 검색 결과 이 NVDA 건 하나만 발견됨 - 광범위한 구조적 문제가
  아니라 드문 SEC 원본 오기에 대한 방어 로직.
- 대표 날짜 선정 (24차 세션 개정 - 최초의 "다수결" 방식은 폐기됨):
  다수결 방식은 NVDA 케이스에서 DB에 이미 정착된 값과 충돌하는 결과를 낼 수
  있음이 DRY RUN으로 확인됨. 이후 NVDA/AAPL 251개 분기 전수 검증으로, 이 두
  종목처럼 52/53주(요일 기준) 회계연도를 쓰는 symbol은 "그 symbol이 확립한
  요일 패턴과 일치하는 날짜"가 진짜 정답임이 실증됨. 현재 규칙: 클러스터 내에서
  해당 symbol 전체(클러스터 제외) 기준 확립된 요일과 유일하게 일치하는 후보를
  채택, 없거나 여러 개면 클러스터 내 최소 날짜로 폴백. 충돌(같은 canonical에
  값 2개 이상) 시 filed 최신값 우선(기존 pick_mode="latest" 관례와 일관).
- collect_symbol()에서 revenue_values/other_results를 만든 직후, all_ends 계산
  이전에 적용.

검증: NVDA 건은 요일 패턴 실증 결과에 따라 정답이 2010-08-01로 확정되어 DB도
2010-08-01로 재병합 완료됨 (operating_income=-175207000,
operating_cash_flow=34344000, revenue=811208000 - 값 자체는 기존과 동일,
날짜만 08-01로 확정). 이 코드를 VM에 반영하기 전, NVDA 단일 종목 DRY RUN으로
2010-08-01 단 하나만 출력되는지 재검증 필요 (다음 세션 최우선 - 07-31이 아니라
08-01이 기대값임에 주의).

---
v8 변경 사항 (33차 세션, 전수 데이터 무결성 스캔에서 발견된 AMZN 연도 스왑 버그 방어):

문제 (실측 확인, 33차 세션): AMZN FY2012 10-K(accn 0001193125-13-028520)의
"분기별 순이익/매출 요약(Selected Quarterly Financial Data)" 각주 XBRL에서
2011년/2012년 context가 통째로 뒤바뀌어 태깅되어 있었음 (SEC 원본 filing 자체의
filer 측 오류로 확인 - SEC companyconcept API 직접 조회 및 AMZN 자체 8-K
보도자료/10-Q 비교치와 교차검증 완료). 이 필징의 quarterly 항목이 "filed 최신"
정책에 의해 채택되면서 net_income 4개 분기, revenue 4개 분기(2011 Q1~Q4)가
실제로는 2012년 값으로 오염됨.

검토했으나 폐기된 대안: "여러 필징이 동의하는 값(다수결) 우선" 방식. TSLA 2024
Q1/Q2 net_income 사례(크립토자산 공정가치 회계기준 신규 채택, Tesla 자체 공시로
확인된 정당한 전면 재작성)에서 정당한 최신 재작성값이 오히려 소수(1개 accn)이고
구버전 값이 다수(2개 accn)라, 다수결로는 이 정당한 재작성과 AMZN의 진짜 오염을
구분할 수 없음이 확인됨. 즉 "다수 vs 단일 최신"이라는 구조 자체는 오염/정당한
재작성 양쪽에서 동일하게 나타나 신뢰할 수 있는 신호가 아님.

해결 (_quarantine_self_inconsistent_accns 신규): 다른 필징과 비교하지 않고,
"같은 필징(accn) 하나 안에서" 자기모순이 있는지만 본다 - quarterly 각주 4개의
합이 그 필징 자신의 annual 태그값과 SELF_CONSISTENCY_TOLERANCE(5%) 이상
어긋나면, 그 accn이 제공하는 quarterly 항목 전체를 후보에서 제외한다. AMZN
사례는 이 필징 자신의 annual 값(2011=631M, 2012=-39M)은 정상이었는데 같은
필징의 quarterly 4개 합("2011" 라벨 기준 -40M)이 자기 자신의 annual과도
맞지 않아 이 방식으로 잡힘. TSLA 정당한 재작성 필징은 이런 자기모순이 없어
영향 없음.

적용 위치: collect_concept_with_q4()에서 fetch_concept_from_raw() 직후,
pick_mode 분기(latest/earliest) 이전 단계. 따라서 eps_diluted를 포함한 모든
concept에 동일하게 적용되며, 기존 "filed 최신값 채택" 정책 자체는 변경하지
않음(정당한 전면 재작성은 계속 정상 동작).

검증 필요 (다음 세션 최우선): DB 쓰기 없이 AMZN만 대상으로 collect_symbol()
재실행 -> net_income 2011 Q1~Q4가 201M/191M/63M/177M, revenue가
9857M/9913M/10876M/17431M으로 나오는지 확인. self_inconsistency_log.json에
AMZN 관련 accn 0001193125-13-028520이 net_income/revenue 두 태그에 대해
격리 기록되는지도 함께 확인. 통과 전까지 DB 재실행 금지.

---
v9 변경 사항 (35차 세션, AV 3순위 폴백 대상 필드 결측 자동 감지 추가):

배경: valuation_alphavantage_fallback_fill.py(AV 3순위 폴백)이 채우는 4개 필드
(capex, operating_cash_flow, revenue, eps_diluted)의 결측을 사람이 별도로 확인해야
했음. 별도 스케줄 없이, 이 스크립트가 실제로 데이터를 새로 적재하는 시점(upsert_rows)에
결측 감지를 얹는 방식으로 결정 (35차 세션 논의 확정).

- upsert_rows()의 INSERT ... ON CONFLICT 쿼리에 `RETURNING ..., (xmax = 0) AS inserted`
  추가 (PostgreSQL 관용 패턴, 이번 실행에서 실제 새로 INSERT된 행만 xmax=0으로 식별,
  기존 행 UPDATE는 xmax>0). COALESCE upsert 특성상 한 번 채워진 값은 NULL로 되돌아가지
  않으므로, 신규 INSERT가 아닌 행은 결측 상태가 바뀔 수 없어 재검사 대상에서 제외 -
  매일 전체 테이블을 다시 SELECT ... IS NULL로 훑는 불필요한 반복 조회를 피함.
- 신규 INSERT 행 중 MISSING_CHECK_FIELDS 4개 중 NULL이 있으면 missing_fields_new_log에
  수집, main() 종료 시 missing_fields_new_log.json에 append(누적, 덮어쓰지 않음).
- `--full` 인자 실행 시: 기존 파이프라인(raw 재처리+upsert)을 건너뛰고, 현재
  fmp_quarterly_financials 전체를 대상으로 4개 필드 IS NULL 전수 조회 후
  missing_fields_full_log.json에 스냅샷으로 저장(매번 덮어쓰기, 수동 전체 재검토용).
- 이 로그들은 감지 전용이며 DB에 UPDATE를 하지 않음. 실제 값 채우기(AV 폴백 실행)는
  기존과 동일하게 별도 수동 절차 유지.
"""

import json
import logging
import os
import sys
from datetime import date
from collections import defaultdict, Counter

import psycopg2

# common/ 패키지 접근을 위해 PROJECT_ROOT를 sys.path에 추가
# (price_updater_common.py와 동일한 패턴 - WorkingDirectory=scheduler로 systemd가
#  실행하므로 common/이 형제 디렉토리라 명시적으로 추가 안 하면 ModuleNotFoundError 발생)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from common.notify import notify_telegram_alert

# ---- 설정 ----
DB_CONFIG = dict(
    dbname="assetdb",
    user="jake",
    password="qkrworb0!",
    host="localhost",
)

REVENUE_TAG_CANDIDATES = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
]

OTHER_CONCEPT_TAG_CANDIDATES = {
    "eps_diluted": ["EarningsPerShareDiluted"],
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
}

# 분할과 무관한 절대금액 concept -> "filed 최신" 유지
# eps_diluted만 아래에서 별도 로직("filed 최초" + 명시적 분할배수)으로 분기 처리됨
SPLIT_ADJUSTED_FIELD = "eps_diluted"

DIFF_REVIEW_THRESHOLD = 0.01  # 1% 이상 차이나면 review 로그에 기록 (정보용)

# eps_diluted 전용 (v6, 8차 세션): "earliest" 채택 시, 후속 filing 값이 분할비로
# 설명되는지 판정하는 허용 오차. 이 안이면 "분할일 뿐"으로 보고 무시하고,
# 벗어나면 "진짜 재작성"으로 보고 latest 채택으로 전환한다.
SPLIT_MATCH_TOLERANCE = 0.05  # 5%

# eps_diluted 전용 (v6.1, 실측 버그 수정): 분할이 실제로 있었던 구간에서만 적용되는
# 절대오차 완화. EPS는 센트(0.01) 단위로 반올림 보고되므로, 분할로 나눈 기대값이
# 0에 가까울수록 반올림 오차가 상대오차(%)로는 크게 부풀려짐. 실측 확인: TSLA
# 2020-03-31분기(0.08 -> 5:1분할 -> 기대 0.016, 반올림 0.02, 실제 재인용도 0.02인데
# 상대오차로는 25%로 과대평가되어 오분류), NFLX 2015-03-31분기(0.38 -> 7:1분할 ->
# 기대 0.0543 -> 7.9% 오분류) 둘 다 완전히 분할로 설명되는데 잘못 재작성 판정됨.
# 분할이 없는 구간(ratio==1.0)에는 이 완화를 적용하지 않음 - 적용하면 AMD 같은
# 소액 EPS 종목의 "진짜" 재작성까지 놓치게 됨(절대오차 자체는 분할로 나누는 과정의
# 반올림에서만 생기는 오차이므로, 나눗셈이 아예 없었던 경우엔 완화 근거가 없음).
EPS_SPLIT_ABS_TOLERANCE = 0.015  # 1.5센트

QUARTER_MIN_DAYS, QUARTER_MAX_DAYS = 80, 100
SEMIANNUAL_MIN_DAYS, SEMIANNUAL_MAX_DAYS = 170, 190   # 6개월 YTD (v5 신규)
NINEMONTH_MIN_DAYS, NINEMONTH_MAX_DAYS = 260, 290     # 9개월 YTD (v5 신규)
ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS = 350, 380

# 날짜 기반 Q4/YTD 역산 시, 연속된 슬롯 사이 간격이 "약 한 분기(~90일)"인지 검증하는 허용 범위
QUARTER_GAP_MIN_DAYS, QUARTER_GAP_MAX_DAYS = 75, 105

# 분할 이력: (분할 실행일, 비율[분할 후 1주가 분할 전 몇 주인지])
# 웹검색으로 사실 확인 완료 (2026-07-01 세션). 날짜는 조정거래 시작일(split-adjusted trading date) 기준.
SPLIT_HISTORY = {
    "AAPL": [
        (date(2014, 6, 9), 7.0),
        (date(2020, 8, 31), 4.0),
    ],
    "GOOGL": [
        (date(2022, 7, 15), 20.0),
    ],
    "AMZN": [
        (date(2022, 6, 6), 20.0),
    ],
    "NVDA": [
        (date(2021, 7, 20), 4.0),
        (date(2024, 6, 10), 10.0),
    ],
    "TSLA": [
        (date(2020, 8, 31), 5.0),
        (date(2022, 8, 25), 3.0),
    ],
    "NFLX": [
        (date(2015, 7, 15), 7.0),
        (date(2025, 11, 17), 10.0),
    ],
    # MSFT, META, AMD, PLTR: 데이터 보유기간 내 분할 없음 (확인 완료)
}

SYMBOLS = ["NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "NFLX", "AMD", "PLTR"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("sec_backfill_v4")

review_log = []       # 1% 이상 값 차이 케이스 (정보용)
split_adjustment_log = []  # eps_diluted 분할조정 적용 내역 (검증용)
eps_restatement_override_log = []  # eps_diluted: earliest->latest로 전환된 케이스 (v6, 8차 세션 신규, 검증용)
self_inconsistency_log = []  # accn 자기모순으로 quarterly 항목이 격리된 케이스 (v8, 33차 세션 신규, 검증용)

# v9 (35차 세션 신규): AV 3순위 폴백(valuation_alphavantage_fallback_fill.py)이 채우는
# 4개 필드(capex, operating_cash_flow, revenue, eps_diluted)를 대상으로 결측을 감지.
# upsert_rows()가 이번 실행에서 새로 INSERT한 행(기존 행의 UPDATE는 대상 아님)만
# 검사하므로, "이 필드가 신규로 씌여졌을 때"만 결측 여부를 판단하게 됨 - COALESCE
# upsert 특성상 한 번 채워진 값은 절대 NULL로 되돌아가지 않으므로, 신규 INSERT가
# 아닌 행은 결측 상태에 변화가 있을 수 없어 재검사가 불필요함 (매일 전체 재조회 회피).
MISSING_CHECK_FIELDS = ["capex", "operating_cash_flow", "revenue", "eps_diluted"]
missing_fields_new_log = []  # 이번 실행에서 신규 발견된 결측만 누적 (기본모드)

# accn 자기모순 판정 허용오차 (v8, 33차 세션 신규): 같은 필징 안에서 quarterly 4개 합이
# 그 필징 자신의 annual 태그값과 이 비율 이상 어긋나면 그 accn의 quarterly 항목을 격리한다.
# AMZN 2011 net_income/revenue 연도 스왑 사례(FY2012 10-K, accn 0001193125-13-028520)로
# 실측 확인된 오차는 수백~수천%였음. 5%는 정상적인 반올림/소폭 재분류가 걸리지 않도록
# 충분히 보수적으로 잡은 값 (33차 세션에서 확정, 실행 후 self_inconsistency_log로 재검증 예정).
SELF_CONSISTENCY_TOLERANCE = 0.05


def split_multiplier(symbol: str, as_of: date) -> float:
    """
    as_of 이후 발생한 모든 분할 비율의 누적곱을 반환한다.
    이 값으로 원본 EPS를 나누면(divide) 현재 주식수 기준 EPS가 된다.
    예: as_of가 모든 분할보다 이전이면 전체 분할 비율이 다 곱해진 값(예: 40)이 반환되고,
        호출부에서 raw_val / 40 을 수행해 소급조정한다.
        as_of가 마지막 분할 이후면 1.0 (조정 불필요, 이미 현재 주식수 기준).

    v6(8차 세션): 매개변수명을 period_end -> as_of로 변경. 호출부가 실제로는
    period_end(구 버전)와 채택된 filing의 filed일자(신 버전, eps_diluted에서
    latest로 전환된 케이스) 둘 다 넘길 수 있어 이름을 일반화함. 함수 로직 자체는
    변경 없음(단순 날짜 비교이므로 어떤 날짜를 넘기든 동일하게 동작).
    """
    multiplier = 1.0
    for split_date, ratio in SPLIT_HISTORY.get(symbol, []):
        if as_of < split_date:
            multiplier *= ratio
    return multiplier


def _split_ratio_between(symbol: str, filed_from: date, filed_to: date) -> float:
    """
    eps_diluted 전용 (v6, 8차 세션 신규).
    두 filing의 filed일자 사이(filed_from 초과 ~ filed_to 이하)에 실제로 발생한
    분할 비율의 누적곱을 반환한다.

    용도: 같은 분기를 재인용한 두 filing의 raw 값 차이가 "그 사이에 있었던 분할"만으로
    설명되는지 검증하기 위함. filed_from 시점 원본값을 이 비율로 나누면 filed_to 시점
    filing이 보고했을 것으로 기대되는 분할조정값이 된다 (실측 확인: NVDA 2020-07-26분기,
    2020-08-19 filed 0.99 / 4.0(2021-07-20 4:1 분할) = 0.2475 ≈ 2021-08-20 filed 0.25).
    """
    ratio = 1.0
    for split_date, r in SPLIT_HISTORY.get(symbol, []):
        if filed_from < split_date <= filed_to:
            ratio *= r
    return ratio


def _apply_split_adjustment_pre_derivation(period_dict, symbol, field):
    """
    eps_diluted 전용 (v9, 이 세션 신규).

    배경: 기존에는 running_cum/Q4 역산을 raw(분할조정 전) 값끼리 수행한 뒤,
    Q1~Q4 각각에 대해 사후적으로 자기 filed일자 기준 분할조정을 적용했다.
    회계연도 중간에 분할이 낀 경우(Q1~Q3는 분할 이전 filed, annual(10-K)은
    분할 이후 filed), annual의 raw 값은 이미 분할 후(작은 수)인데 Q1~Q3
    raw 합은 분할 전(큰 수) 그대로라 Q4 = annual_raw - running_cum_raw
    뺄셈 자체가 서로 다른 기준값끼리 이루어져 결과가 크게 왜곡됨.
    실측 확인: GOOGL 2022-12-31, NVDA 2025-01-26, NFLX 2025-12-31
    (모두 해당 회계연도 중 분할이 있었던 종목의 Q4).

    수정: quarterly/semiannual/ninemonth/annual 추출 직후, running_cum
    계산 이전에 각 entry를 자기 filed일자 기준으로 먼저 분할조정해
    "현재 주식수 기준"으로 정규화한다. 이후 뺄셈은 일관된 기준으로 이루어진다.
    """
    for end_date, info in period_dict.items():
        mult = split_multiplier(symbol, info["filed"])
        if mult != 1.0:
            raw_val = info["val"]
            info["val"] = raw_val / mult
            split_adjustment_log.append({
                "symbol": symbol,
                "field": field,
                "end": end_date.isoformat(),
                "raw_val": raw_val,
                "multiplier": mult,
                "adjusted_val": info["val"],
                "stage": "pre_derivation",
            })


def _pick_eps_entry(group_sorted, symbol: str):
    """
    eps_diluted 전용 (v6, 8차 세션 신규).
    같은 (symbol, end_date)를 여러 filing이 재인용한 그룹에서, 기존처럼 무조건 최초
    filed를 채택하지 않고 다음 기준으로 판정한다:

    - 최초 filed 값을 기준으로, 각 후속 filing 값이 그 사이 실제 분할비로 설명되면
      ("분할일 뿐") -> 기존과 동일하게 최초 filed 채택 (split_multiplier가 이후
      collect_concept_with_q4에서 남은 분할을 마저 적용).
    - 후속 filing 중 하나라도 분할비로 설명 안 되는 차이(SPLIT_MATCH_TOLERANCE 초과)를
      보이면 -> 분할이 아닌 진짜 재작성(회계기준 변경 등)으로 판단, 나머지 5개 concept과
      동일하게 최신 filed 채택으로 전환한다.
      (배경: review_log.json 248건 분류 결과 eps_diluted 115건 중 42건이 분할로 설명
      안 되는 재작성이었음이 8차 세션에서 확인됨. 예: MSFT 2016-09-30분기 0.60->0.72,
      MSFT는 데이터 기간 내 분할 없음.)

    반환: (chosen_entry, reason) — reason은 "earliest_unchanged" 또는 "restated_to_latest"
    """
    base = group_sorted[0]
    if len(group_sorted) == 1:
        return base, "earliest_unchanged"

    for later in group_sorted[1:]:
        ratio = _split_ratio_between(symbol, base["filed"], later["filed"])
        expected = base["val"] / ratio if ratio != 0 else base["val"]
        diff_abs = abs(later["val"] - expected)

        if ratio != 1.0:
            # 실제 분할이 있었던 구간: 상대오차 또는 절대오차(반올림 완화) 중 하나만
            # 만족해도 "분할로 설명됨"으로 인정 (센트 단위 반올림 오차 대응, v6.1)
            rel_tolerance = SPLIT_MATCH_TOLERANCE * abs(expected)
            tolerance = max(rel_tolerance, EPS_SPLIT_ABS_TOLERANCE)
        else:
            # 분할이 없었던 구간: 나눗셈 자체가 없어 반올림 완화 근거가 없음.
            # 기존과 동일하게 순수 상대오차만으로 판정.
            tolerance = SPLIT_MATCH_TOLERANCE * abs(expected)

        if diff_abs > tolerance:
            return group_sorted[-1], "restated_to_latest"

    return base, "earliest_unchanged"


def fetch_concept_from_raw(conn, symbol: str, tag: str, field: str):
    """
    sec_xbrl_facts_raw에서 (symbol, concept_tag) 전체 entry를 읽어온다.
    unit이 여러 개면 entry 수가 많은 쪽을 채택 (v3와 동일한 방어 로직).
    반환: [{"form","start","end","filed","accn","val","fy","fp"}, ...]
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT unit, COUNT(*) FROM sec_xbrl_facts_raw "
            "WHERE symbol = %s AND concept_tag = %s GROUP BY unit",
            (symbol, tag),
        )
        unit_counts = cur.fetchall()

    if not unit_counts:
        return []

    if len(unit_counts) > 1:
        unit = max(unit_counts, key=lambda x: x[1])[0]
        log.warning(f"[{symbol}/{field}] unit이 2개 이상 존재 ({[u for u, _ in unit_counts]}), '{unit}' 채택")
    else:
        unit = unit_counts[0][0]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT form, period_start, period_end, filed_date, accn, val, fy, fp "
            "FROM sec_xbrl_facts_raw WHERE symbol = %s AND concept_tag = %s AND unit = %s",
            (symbol, tag, unit),
        )
        rows = cur.fetchall()

    entries = []
    for form, start, end, filed, accn, val, fy, fp in rows:
        entries.append({
            "form": form,
            "start": start,
            "end": end,
            "filed": filed,
            "accn": accn,
            "val": float(val),
            "fy": fy,
            "fp": fp,
        })
    return entries


def _quarantine_self_inconsistent_accns(entries, symbol, field, tag):
    """
    (v8, 33차 세션 신규) 같은 accn(하나의 필징) 안에서 quarterly 각주 4개의 합이
    그 필징 자신의 annual 태그값과 SELF_CONSISTENCY_TOLERANCE 이상 어긋나면,
    그 accn이 제공하는 quarterly 항목 전체를 후보에서 제외한다.

    배경 (33차 세션, 전수 무결성 스캔에서 확정): AMZN FY2012 10-K(accn
    0001193125-13-028520)의 "분기별 순이익/매출 요약" 각주 XBRL에서 2011년/2012년
    context가 통째로 뒤바뀌어 태깅되어 있었음. 이 필징 자신의 annual 태그값(2011=631M,
    2012=-39M)은 정상이었는데, 같은 필징의 quarterly 4개 합은 "2011" 라벨 기준으로
    -40M(실제로는 2012년 값들의 합)이라 자기 자신의 annual과도 맞지 않았음 - 즉 다른
    필징과 비교할 필요 없이 그 필징 하나만 봐도 잡히는 자기모순.

    "filed 최신값 우선" 정책은 그대로 유지하되(TSLA 2024 크립토자산 회계기준 채택처럼
    정당한 전면 재작성은 계속 latest가 이김), 이 필터는 latest 채택 이전에 raw 후보
    자체에서 자기모순 필징만 제거하는 방어 단계. 다수결 방식(더 많은 필징이 동의하는
    값 우선)은 검토했으나 폐기됨 - TSLA 사례에서 정당한 최신 재작성값이 오히려
    소수(1개 accn)라 다수결로는 정당한 재작성과 진짜 오염을 구분할 수 없음이
    확인됨(33차 세션 결론).

    반환: entries에서 자기모순 accn의 quarterly(80~100일) 항목만 제거한 리스트
    (annual/semiannual/ninemonth 항목 및 정상 accn의 항목은 그대로 유지)
    """
    by_accn = defaultdict(list)
    for e in entries:
        by_accn[e["accn"]].append(e)

    bad_accns = set()
    for accn, rows in by_accn.items():
        annual_rows = [
            r for r in rows
            if ANNUAL_MIN_DAYS <= (r["end"] - r["start"]).days <= ANNUAL_MAX_DAYS
        ]
        for annual_row in annual_rows:
            q_rows = [
                r for r in rows
                if QUARTER_MIN_DAYS <= (r["end"] - r["start"]).days <= QUARTER_MAX_DAYS
                and annual_row["start"] < r["end"] <= annual_row["end"]
            ]
            if len(q_rows) != 4:
                # 4개 전부 모여야 엄격 비교 가능 (3개 이하는 기존 Q4 역산 로직 영역이지
                # 자기모순 판정 대상이 아님 - 판단 근거 부족으로 보류)
                continue
            q_sum = sum(r["val"] for r in q_rows)
            annual_val = annual_row["val"]
            if annual_val == 0:
                continue
            diff_ratio = abs(q_sum - annual_val) / abs(annual_val)
            if diff_ratio > SELF_CONSISTENCY_TOLERANCE:
                bad_accns.add(accn)
                log.warning(
                    f"[{symbol}/{field}/{tag}] accn={accn} 자기모순 감지: "
                    f"quarterly 4개 합={q_sum} vs 자신의 annual={annual_val} "
                    f"(차이 {diff_ratio:.1%}, 임계값 {SELF_CONSISTENCY_TOLERANCE:.0%}) "
                    f"-> 이 accn의 quarterly 항목을 후보에서 제외"
                )
                self_inconsistency_log.append({
                    "symbol": symbol,
                    "field": field,
                    "tag": tag,
                    "accn": accn,
                    "annual_period_start": annual_row["start"].isoformat(),
                    "annual_period_end": annual_row["end"].isoformat(),
                    "annual_val": annual_val,
                    "quarterly_sum": q_sum,
                    "diff_ratio": round(diff_ratio, 4),
                    "quarantined_quarter_ends": [r["end"].isoformat() for r in q_rows],
                })

    if not bad_accns:
        return entries

    return [
        e for e in entries
        if not (
            e["accn"] in bad_accns
            and QUARTER_MIN_DAYS <= (e["end"] - e["start"]).days <= QUARTER_MAX_DAYS
        )
    ]


def _extract_period_values(entries, forms, min_days, max_days, symbol, field, period_label, pick_mode):
    """
    주어진 form 집합 + 기간(일수) 범위에 맞는 entry만 추려서,
    같은 end날짜끼리 그룹핑 후 pick_mode에 따라 값을 채택한다.

    pick_mode:
    - "latest": filed 최신값 채택 (회계 재작성 반영 목적, 절대금액 concept용)
    - "earliest": filed 최초값 채택 (분할조정을 우리가 직접 명시적으로 처리하기 위한 결정론적 기준,
                  eps_diluted 전용). 단, field가 eps_diluted인 경우 v6(8차 세션)부터
                  단순 최초값이 아니라 _pick_eps_entry()로 "분할일 뿐인지 진짜 재작성인지"를
                  판정한 뒤 채택한다 (아래 그룹 처리 참조).

    1% 이상 차이나는 케이스는 review_log에 기록 (정보용, 채택 결과에 영향 없음).

    반환: { end_date: {"val", "filed", "accn", "fy", "fp"} }
    """
    period_entries = [e for e in entries if e["form"] in forms]
    period_entries = [
        e for e in period_entries
        if min_days <= (e["end"] - e["start"]).days <= max_days
    ]

    by_end = defaultdict(list)
    for e in period_entries:
        by_end[e["end"]].append(e)

    result = {}
    for end_date, group in by_end.items():
        group_sorted = sorted(group, key=lambda x: x["filed"])

        if field == SPLIT_ADJUSTED_FIELD and pick_mode == "earliest":
            chosen, reason = _pick_eps_entry(group_sorted, symbol)
            if reason == "restated_to_latest":
                eps_restatement_override_log.append({
                    "symbol": symbol,
                    "field": field,
                    "period": period_label,
                    "end": end_date.isoformat(),
                    "earliest_val": group_sorted[0]["val"],
                    "earliest_filed": group_sorted[0]["filed"].isoformat(),
                    "chosen_val": chosen["val"],
                    "chosen_filed": chosen["filed"].isoformat(),
                    "reason": "분할비로 설명 안 되는 재작성 -> latest 채택으로 전환",
                })
        else:
            chosen = group_sorted[-1] if pick_mode == "latest" else group_sorted[0]

        result[end_date] = {
            "val": chosen["val"],
            "filed": chosen["filed"],
            "accn": chosen["accn"],
            "fy": chosen.get("fy"),
            "fp": chosen.get("fp"),
            "start": chosen["start"],
        }

        vals = set(g["val"] for g in group)
        if len(vals) > 1:
            chosen_val = chosen["val"]
            for g in group:
                if g is chosen or chosen_val == 0:
                    continue
                diff_ratio = abs(g["val"] - chosen_val) / abs(chosen_val)
                if diff_ratio >= DIFF_REVIEW_THRESHOLD:
                    review_log.append({
                        "symbol": symbol,
                        "field": field,
                        "period": period_label,
                        "end": end_date.isoformat(),
                        "pick_mode": pick_mode,
                        "chosen_val": chosen_val,
                        "chosen_filed": chosen["filed"].isoformat(),
                        "other_val": g["val"],
                        "other_filed": g["filed"].isoformat(),
                        "diff_ratio": round(diff_ratio, 4),
                    })

    return result


def collect_concept_with_q4(conn, tag, symbol, field, pick_mode):
    """
    한 concept(태그)에 대해 raw 테이블에서 discrete 분기(10-Q, ~90일),
    반기 YTD(~181일), 9개월 YTD(~272일), 연간(10-K)을 모두 읽어서
    Q1~Q4를 날짜 기반으로 채운다 (v5, fy/fp 태그 매칭 폐기).

    회계연도 판별: 10-K entry의 (period_start, period_end) 구간을 그대로 사용.
    이 구간 안에 속하는 discrete/semiannual/ninemonth entry를 end_date로 직접 추출한다.

    분기 슬롯 채우기 규칙 (한 슬롯 = 회계연도 시작부터 순서대로 하나씩):
    - 그 위치에 discrete(~90일) 값이 있으면 그대로 채택.
    - discrete 값이 없고 semiannual/ninemonth 누계값만 있으면,
      누계값 - 직전까지의 누계(running_cum) 로 개별 분기값을 역산한다.
      (SEC 규정상 현금흐름표류 concept은 중간보고서에서 discrete가 아니라
      연초 누계로만 공시되는 경우가 있음 - 6차 세션 웹검색+raw데이터로 확정.)
    - 정확히 3개 슬롯이 나오고, 슬롯 간 간격이 각각 75~105일(약 1분기)일 때만 채택.
      조건 미충족 시 해당 회계연도는 역산하지 않고 원본 결측으로 남긴다 (기존 방침 유지).

    Q4 = 연간값 - (슬롯 3개의 누계).

    field == SPLIT_ADJUSTED_FIELD("eps_diluted")인 경우 (v9로 갱신):
    - pick_mode는 "earliest"로 호출된 상태여야 함 (단, 실제 채택은 _pick_eps_entry로
      "분할일 뿐"인지 "진짜 재작성"인지 판정 후 결정됨, v6/8차 세션)
    - quarterly/semiannual/ninemonth/annual 추출 직후, running_cum 계산 및 Q4 역산
      이전에 각 raw entry를 자기 filed일자 기준으로 먼저 분할조정한다
      (_apply_split_adjustment_pre_derivation). 회계연도 중간에 분할이 낀 경우
      annual과 quarterly가 서로 다른 분할 기준(전/후)의 raw 값으로 남아있으면
      Q4 역산 뺄셈 자체가 왜곡되기 때문에, 뺄셈 이전에 정규화를 마쳐야 한다.
    - split_adjustment_log에 적용 내역, eps_restatement_override_log에 전환 내역 기록 (검증용)

    반환: { end_date: {"val", "filed", "accn", "fy", "fp"} }
    """
    entries = fetch_concept_from_raw(conn, symbol, tag, field)
    if not entries:
        return {}

    # v8 (33차 세션): latest 채택 이전에, 같은 accn 안에서 quarterly 합과 자신의
    # annual 값이 자기모순인 필징의 quarterly 항목을 먼저 격리한다 (AMZN 2011
    # 연도 스왑 사례 방어). TSLA류 정당한 전면 재작성은 자기모순이 없어 영향 없음.
    #
    # eps_diluted는 제외 (33차 세션 실측 확인): 분기별 가중평균주식수 차이로 인해
    # quarterly 4개 합이 annual과 원래도 근사값일 수밖에 없는 구조라서(파일 상단
    # docstring "Q4 역산 관련" 항목 참조), 5% 상대오차 체크가 실제 오염이 아닌
    # 정상 데이터에서도 계속 오탐을 낸다. 실측: AMZN accn 0001018724-14-000006의
    # 2012년 quarterly 합(-0.10)과 annual(-0.09) 차이는 단 1센트인데, 분모가
    # 작아 상대오차로는 11.1%로 부풀려져 오탐 발생. eps_diluted는 이미
    # _pick_eps_entry()가 별도로 재작성/분할 여부를 판별하므로 이 체크 불필요.
    if field != SPLIT_ADJUSTED_FIELD:
        entries = _quarantine_self_inconsistent_accns(entries, symbol, field, tag)

    quarterly = _extract_period_values(
        entries, forms={"10-Q", "10-K", "10-K/A"}, min_days=QUARTER_MIN_DAYS, max_days=QUARTER_MAX_DAYS,
        symbol=symbol, field=field, period_label="quarterly", pick_mode=pick_mode,
    )
    semiannual = _extract_period_values(
        entries, forms={"10-Q"}, min_days=SEMIANNUAL_MIN_DAYS, max_days=SEMIANNUAL_MAX_DAYS,
        symbol=symbol, field=field, period_label="semiannual", pick_mode=pick_mode,
    )
    ninemonth = _extract_period_values(
        entries, forms={"10-Q"}, min_days=NINEMONTH_MIN_DAYS, max_days=NINEMONTH_MAX_DAYS,
        symbol=symbol, field=field, period_label="ninemonth", pick_mode=pick_mode,
    )
    annual = _extract_period_values(
        entries, forms={"10-K", "10-K/A"}, min_days=ANNUAL_MIN_DAYS, max_days=ANNUAL_MAX_DAYS,
        symbol=symbol, field=field, period_label="annual", pick_mode=pick_mode,
    )

    # v9: running_cum/Q4 역산 이전에 분할조정을 선반영 (자세한 배경은
    # _apply_split_adjustment_pre_derivation() docstring 참조)
    if field == SPLIT_ADJUSTED_FIELD:
        for period_dict in (quarterly, semiannual, ninemonth, annual):
            _apply_split_adjustment_pre_derivation(period_dict, symbol, field)

    # discrete 분기값은 기존과 동일하게 그대로 베이스로 사용 (영향 없는 concept은 동작 불변)
    result = dict(quarterly)

    derived_ytd_count = 0
    derived_q4_count = 0

    for period_end, annual_info in annual.items():
        period_start = annual_info["start"]

        slot_candidates = []
        for d, info in quarterly.items():
            if period_start < d <= period_end:
                slot_candidates.append((d, "q", info))
        for d, info in semiannual.items():
            if period_start < d <= period_end:
                slot_candidates.append((d, "s", info))
        for d, info in ninemonth.items():
            if period_start < d <= period_end:
                slot_candidates.append((d, "n", info))

        distinct_ends = sorted(set(d for d, _, _ in slot_candidates))
        if len(distinct_ends) != 3:
            continue  # 3개 슬롯이 안 모이면 원본 결측으로 보고 역산하지 않음 (기존 방침 유지)

        running_cum = 0.0
        quarter_results = []
        valid = True
        prev_boundary = period_start
        for d in distinct_ends:
            gap = (d - prev_boundary).days
            if not (QUARTER_GAP_MIN_DAYS <= gap <= QUARTER_GAP_MAX_DAYS):
                valid = False
                break
            prev_boundary = d

            entries_here = [c for c in slot_candidates if c[0] == d]
            q_entry = next((c for c in entries_here if c[1] == "q"), None)
            if q_entry:
                val = q_entry[2]["val"]
                running_cum += val
                source_info = q_entry[2]
                is_derived = False
            else:
                cum_entry = entries_here[0]
                cum_val = cum_entry[2]["val"]
                val = cum_val - running_cum
                running_cum = cum_val
                source_info = cum_entry[2]
                is_derived = True
            quarter_results.append((d, val, source_info, is_derived))

        if not valid:
            continue
        gap = (period_end - prev_boundary).days
        if not (QUARTER_GAP_MIN_DAYS <= gap <= QUARTER_GAP_MAX_DAYS):
            continue

        for d, val, source_info, is_derived in quarter_results:
            if is_derived and d not in result:
                result[d] = {
                    "val": val,
                    "filed": source_info["filed"],
                    "accn": source_info["accn"],
                    "fy": None,
                    "fp": "derived_ytd",
                }
                derived_ytd_count += 1

        if period_end not in result:
            q4_val = annual_info["val"] - running_cum
            result[period_end] = {
                "val": q4_val,
                "filed": annual_info["filed"],
                "accn": annual_info["accn"],
                "fy": None,
                "fp": "Q4(derived)",
            }
            derived_q4_count += 1

    if derived_ytd_count:
        log.info(f"[{symbol}/{field}] YTD 누계에서 역산한 분기 {derived_ytd_count}건 추가")
    if derived_q4_count:
        log.info(f"[{symbol}/{field}] Q4 역산 {derived_q4_count}건 추가")

    if field == SPLIT_ADJUSTED_FIELD:
        # v9: 분할조정은 이제 running_cum/Q4 역산 이전(_apply_split_adjustment_pre_derivation)
        # 에서 이미 완료됨. 여기서 다시 적용하면 이중조정이 되므로 제거함.
        # (quarterly/semiannual/ninemonth/annual 각 raw entry가 자기 filed일자 기준으로
        # 이미 조정된 상태로 result에 들어와 있고, Q4(derived)도 그 조정된 값들로
        # 역산됐으므로 result 안의 모든 entry는 이미 최종값이다.)
        pass

    return result


def _canonicalize_end_dates(concept_dicts, tolerance_days=10):
    """
    여러 concept 딕셔너리에 흩어진 end_date를, 허용오차 내로 가까우면 같은 실제
    회계분기로 간주해 하나의 대표 날짜로 통일한다 (v7, 23차 세션 신규).

    배경: SEC raw 자체에서 같은 회계분기라도 concept(태그)/filing에 따라 period_end가
    며칠 어긋나는 경우가 있음 (실측 확인: NVDA 2010-07-31 사례, Revenues 태그의
    2012-03-13 filed 건만 period_end가 다른 concept/filing 전부(2010-08-01)와
    달리 2010-07-31로 1일 어긋나 있었음. 원인은 SEC 원본 filing 자체의 오기로 추정,
    raw 수집/가공 로직 문제 아님).

    기존 코드는 end_date를 정확히 일치하는 값으로만 병합해, 이런 경우 하나의 실제
    분기가 concept별로 서로 다른 행으로 쪼개지는 버그가 있었음 (19차 세션 진단
    "분기값/YTD값 선택 로직 버그"는 오진이었음이 23차 세션에서 확인됨 - 원인 정정).

    tolerance_days=10: AV fiscalDateEnding 매칭에 이미 쓰는 허용오차와 통일
    (12차/18차 세션 확정 기준). 분기 간 최소 간격이 75일(QUARTER_GAP_MIN_DAYS)이라
    10일 tolerance로도 서로 다른 분기가 잘못 병합될 위험은 없음 (65일 여유).
    실측 스캔(23차 세션): 전 종목 15일 이내 근접 쌍 검색 결과 이 NVDA 건 하나만
    발견됨 - 광범위한 구조적 문제가 아니라 드문 SEC 원본 오기에 대한 방어 로직.

    대표 날짜 선정 (v7 개정, 24차 세션 확정 - 다수결 방식 폐기):
    처음엔 "클러스터 내 최다 concept 등장 날짜(다수결)"를 채택했으나, 이 방식은
    NVDA 2010-07-31/08-01 케이스에서 DB에 이미 정착된 값과 충돌하는 결과를 낼 수
    있음이 DRY RUN으로 확인됨. 이후 NVDA/AAPL 251개 분기 전수 검증 결과, 이 두
    종목은 52/53주(요일 기준) 회계연도를 써서 매 분기말이 거의 항상 같은 요일에
    오고, 이 요일 패턴과 다른 날짜가 바로 SEC 원본 filing 오기였음이 실증됨
    (NVDA는 2010-07-31 (토요일·89일) 단 하나만 이상값, 나머지 전부 일요일·90일).

    따라서 대표 날짜는: 해당 symbol 전체 날짜(이 클러스터 제외) 기준으로 확립된
    요일과 클러스터 내에서 유일하게 일치하는 후보가 있으면 그 날짜를 채택. 확립된
    요일이 없거나(동률), 일치하는 후보가 없거나 여러 개면 클러스터 내 최소 날짜로
    폴백 (1일 내외 차이는 TTM/as-of join 등 실사용 계산 정밀도에 영향 없는 스케일이라,
    "철학적으로 옳은 날짜"를 못 가릴 땐 결정론적 규칙이면 충분함).

    반환: {old_date: canonical_date}
    """
    all_dates = sorted(set(d for cd in concept_dicts for d in cd.keys()))

    clusters, current = [], []
    for d in all_dates:
        if not current or (d - current[-1]).days <= tolerance_days:
            current.append(d)
        else:
            clusters.append(current)
            current = [d]
    if current:
        clusters.append(current)

    # symbol 전체 날짜 기준 요일 분포 (아래에서 클러스터별로 자기 자신은 제외하고 사용)
    weekday_counts_all = Counter(d.weekday() for d in all_dates)

    mapping = {}
    for cluster in clusters:
        if len(cluster) == 1:
            mapping[cluster[0]] = cluster[0]
            continue

        # 이 클러스터의 날짜들을 뺀 "나머지"로 확립된 요일 판단 (클러스터 자체의
        # 이상값이 자기 요일 카운트를 스스로 오염시키지 않도록)
        other_counts = Counter(weekday_counts_all)
        for d in cluster:
            other_counts[d.weekday()] -= 1

        established_weekday = None
        max_count = max(other_counts.values(), default=0)
        if max_count > 0:
            top_weekdays = [wd for wd, c in other_counts.items() if c == max_count]
            if len(top_weekdays) == 1:
                established_weekday = top_weekdays[0]

        matches = []
        if established_weekday is not None:
            matches = [d for d in cluster if d.weekday() == established_weekday]

        if len(matches) == 1:
            canonical = matches[0]
            reason = "symbol 확립 요일 패턴과 유일 일치"
        else:
            canonical = min(cluster)
            reason = "요일 패턴 미일치/동률 - 최소 날짜 폴백"

        for d in cluster:
            mapping[d] = canonical
        log.info(
            f"[end_date 정규화] {cluster} -> {canonical} 로 통일 "
            f"(concept 간 period_end 불일치, {reason}, 24차 세션 확정 규칙)"
        )
    return mapping


def _remap_end_dates(d, mapping):
    """
    _canonicalize_end_dates()의 매핑을 실제 concept 딕셔너리에 적용 (v7, 23차 세션 신규).
    클러스터 병합으로 같은 canonical 날짜에 2개 이상 몰리는 충돌 시 filed 최신값 우선
    (기존 pick_mode="latest" 관례와 일관).
    """
    out = {}
    for end_date, info in d.items():
        canonical = mapping.get(end_date, end_date)
        if canonical in out and out[canonical]["filed"] >= info["filed"]:
            continue
        out[canonical] = info
    return out


def get_concept_for_symbol(conn, symbol: str, field: str, tag_candidates, pick_mode: str):
    """
    get_revenue_for_symbol()의 병합 로직을 모든 concept에 쓸 수 있게 일반화한 버전
    (v6.2, 이번 세션: capex 태그드리프트 수정이 valuation_sec_edgar_raw_collect.py에는 반영됐으나
    backfill.py 쪽엔 반영이 안 돼 있던 것을 발견해 수정. raw_collect.py의
    OTHER_CONCEPT_TAG_CANDIDATES와 반드시 같은 구조/우선순위를 유지해야 함).

    tag_candidates를 우선순위 순서대로 순회하되, 태그를 통째로 채택하지 않고
    분기(end_date) 단위로 병합한다. 아직 채워지지 않은 end_date만 다음 순위
    태그값으로 채운다. 어느 태그를 썼는지는 분기별로 info["source_tag"]에 개별 기록.

    tag_candidates가 원소 1개짜리 리스트여도(대부분의 concept이 아직 그러함) 동일하게
    동작 - 태그 드리프트가 나중에 추가로 발견되면 후보만 추가하면 되고 이 함수는
    변경할 필요 없음 (raw_collect.py 쪽에서 이미 검증된 설계 원칙과 동일).

    반환: { end_date: {"val", "filed", "accn", "fy", "fp", "source_tag"} }
    """
    merged = {}
    for tag in tag_candidates:
        values = collect_concept_with_q4(conn, tag, symbol, field, pick_mode=pick_mode)
        for end_date, info in values.items():
            if end_date not in merged:
                info = dict(info)
                info["source_tag"] = tag
                merged[end_date] = info
    return merged


def get_revenue_for_symbol(conn, symbol: str):
    """
    REVENUE_TAG_CANDIDATES 우선순위 순서대로 순회하되, 태그를 통째로 채택하지 않고
    분기(end_date) 단위로 병합한다 (v5, 5차 세션에서 발견된 버그 수정).

    기존 방식("값이 하나라도 있는 첫 태그를 통째로 채택 후 종료")은 태그가 회계기준
    전환 등으로 중간에 바뀌는 경우, 상위 우선순위 태그가 커버하지 않는 과거 분기를
    아예 시도조차 하지 않아 유실시켰다 (AAPL 확인: RevenueFromContractWith... 태그가
    2017-09-30부터만 있어 그 이전 SalesRevenueNet 구간이 전부 유실).

    v6.2: get_concept_for_symbol()의 얇은 래퍼로 변경 (로직 동일, 중복 제거).

    반환: { end_date: {"val", "filed", "accn", "fy", "fp", "source_tag"} }
    """
    return get_concept_for_symbol(conn, symbol, "revenue", REVENUE_TAG_CANDIDATES, pick_mode="latest")


def collect_symbol(conn, symbol: str):
    """한 종목에 대해 7개 concept 전부 가공, fiscal_quarter_end 기준으로 병합."""
    log.info(f"=== {symbol} 가공 시작 ===")

    revenue_values = get_revenue_for_symbol(conn, symbol)
    if revenue_values:
        tags_used = sorted(set(v["source_tag"] for v in revenue_values.values()))
        log.info(f"{symbol} revenue: {len(revenue_values)}개 분기 (사용된 태그: {tags_used})")
    else:
        log.warning(f"{symbol} revenue: raw 테이블에 사용 가능한 태그 없음")

    other_results = {}
    for field, tag_candidates in OTHER_CONCEPT_TAG_CANDIDATES.items():
        pick_mode = "earliest" if field == SPLIT_ADJUSTED_FIELD else "latest"
        values = get_concept_for_symbol(conn, symbol, field, tag_candidates, pick_mode=pick_mode)
        other_results[field] = values
        if values:
            tags_used = sorted(set(v["source_tag"] for v in values.values()))
        else:
            tags_used = []
        log.info(
            f"{symbol} {field}: {len(values)}개 분기 "
            f"(사용된 태그: {tags_used}, pick_mode={pick_mode})"
        )

    # v7 (23차 세션): concept 간 period_end 불일치로 인한 행 분열 방지
    # (NVDA 2010-07-31 사례로 발견 - 19차 세션의 "값 선택 로직 버그" 진단은 오진이었음,
    # 실제 원인은 Revenues 태그의 특정 filing만 다른 concept과 period_end가
    # 1일 어긋나 있었던 것. 상세 배경은 파일 상단 v7 docstring 참조.)
    mapping = _canonicalize_end_dates([revenue_values] + list(other_results.values()))
    revenue_values = _remap_end_dates(revenue_values, mapping)
    other_results = {field: _remap_end_dates(v, mapping) for field, v in other_results.items()}

    all_ends = set(revenue_values.keys())
    for values in other_results.values():
        all_ends |= set(values.keys())

    rows = []
    for end_date in sorted(all_ends):
        row = {
            "symbol": symbol,
            "fiscal_quarter_end": end_date,
            "revenue": None,
            "revenue_tag_used": None,
            "eps_diluted": None,
            "eps_diluted_tag_used": None,
            "net_income": None,
            "net_income_tag_used": None,
            "operating_income": None,
            "operating_income_tag_used": None,
            "operating_cash_flow": None,
            "operating_cash_flow_tag_used": None,
            "capex": None,
            "capex_tag_used": None,
            "free_cash_flow": None,
        }

        if end_date in revenue_values:
            info = revenue_values[end_date]
            row["revenue"] = info["val"]
            suffix = ""
            if info.get("fp") == "Q4(derived)":
                suffix = "|derived_q4"
            elif info.get("fp") == "derived_ytd":
                suffix = "|derived_ytd"
            row["revenue_tag_used"] = info["source_tag"] + suffix

        for field, values in other_results.items():
            if end_date in values:
                info = values[end_date]
                row[field] = info["val"]
                suffix = ""
                if info.get("fp") == "Q4(derived)":
                    suffix = "|derived_q4"
                elif info.get("fp") == "derived_ytd":
                    suffix = "|derived_ytd"
                # v6: 실제 나눗셈에 쓰인 기준(info["filed"])과 동일한 기준으로 판정해야
                # "split_adj" 표시 여부가 실제 적용 여부와 항상 일치함 (collect_concept_with_q4
                # 참조, period_end 기준 쓰면 표시만 어긋날 수 있음)
                if field == SPLIT_ADJUSTED_FIELD and split_multiplier(symbol, info["filed"]) != 1.0:
                    suffix += "|split_adj"
                # v6.2: 태그 하나 고정이 아니라 실제 채택된 후보 태그(info["source_tag"])를
                # 기록 - capex처럼 후보가 여러 개인 concept에서 분기별로 다른 태그가
                # 쓰였을 수 있음 (revenue_tag_used와 동일한 방식)
                row[f"{field}_tag_used"] = info["source_tag"] + suffix

        if row["operating_cash_flow"] is not None and row["capex"] is not None:
            row["free_cash_flow"] = row["operating_cash_flow"] - row["capex"]

        rows.append(row)

    return rows


def upsert_rows(conn, rows):
    if not rows:
        return 0
    sql = """
        INSERT INTO fmp_quarterly_financials (
            symbol, fiscal_quarter_end,
            revenue, revenue_tag_used,
            eps_diluted, eps_diluted_tag_used,
            net_income, net_income_tag_used,
            operating_income, operating_income_tag_used,
            operating_cash_flow, operating_cash_flow_tag_used,
            capex, capex_tag_used,
            free_cash_flow
        ) VALUES (
            %(symbol)s, %(fiscal_quarter_end)s,
            %(revenue)s, %(revenue_tag_used)s,
            %(eps_diluted)s, %(eps_diluted_tag_used)s,
            %(net_income)s, %(net_income_tag_used)s,
            %(operating_income)s, %(operating_income_tag_used)s,
            %(operating_cash_flow)s, %(operating_cash_flow_tag_used)s,
            %(capex)s, %(capex_tag_used)s,
            %(free_cash_flow)s
        )
        ON CONFLICT (symbol, fiscal_quarter_end) DO UPDATE SET
            revenue = COALESCE(EXCLUDED.revenue, fmp_quarterly_financials.revenue),
            revenue_tag_used = COALESCE(EXCLUDED.revenue_tag_used, fmp_quarterly_financials.revenue_tag_used),
            eps_diluted = COALESCE(EXCLUDED.eps_diluted, fmp_quarterly_financials.eps_diluted),
            eps_diluted_tag_used = COALESCE(EXCLUDED.eps_diluted_tag_used, fmp_quarterly_financials.eps_diluted_tag_used),
            net_income = COALESCE(EXCLUDED.net_income, fmp_quarterly_financials.net_income),
            net_income_tag_used = COALESCE(EXCLUDED.net_income_tag_used, fmp_quarterly_financials.net_income_tag_used),
            operating_income = COALESCE(EXCLUDED.operating_income, fmp_quarterly_financials.operating_income),
            operating_income_tag_used = COALESCE(EXCLUDED.operating_income_tag_used, fmp_quarterly_financials.operating_income_tag_used),
            operating_cash_flow = COALESCE(EXCLUDED.operating_cash_flow, fmp_quarterly_financials.operating_cash_flow),
            operating_cash_flow_tag_used = COALESCE(EXCLUDED.operating_cash_flow_tag_used, fmp_quarterly_financials.operating_cash_flow_tag_used),
            capex = COALESCE(EXCLUDED.capex, fmp_quarterly_financials.capex),
            capex_tag_used = COALESCE(EXCLUDED.capex_tag_used, fmp_quarterly_financials.capex_tag_used),
            free_cash_flow = COALESCE(EXCLUDED.free_cash_flow, fmp_quarterly_financials.free_cash_flow),
            collected_at = now()
        RETURNING symbol, fiscal_quarter_end, capex, operating_cash_flow, revenue, eps_diluted,
                  (xmax = 0) AS inserted;
    """
    # v9 (35차 세션 신규): xmax = 0 이면 이번 실행에서 "새로 INSERT"된 행, xmax > 0이면
    # 기존 행이 UPDATE된 것 (PostgreSQL 관용 패턴, PG14에서 정상 동작 확인 - 35차 세션 웹검색).
    # 신규 INSERT 행에 한해서만 MISSING_CHECK_FIELDS 중 NULL이 있는지 검사한다.
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(sql, row)
            ret = cur.fetchone()
            if ret is None:
                continue
            ret_symbol, ret_qend, ret_capex, ret_ocf, ret_revenue, ret_eps, is_inserted = ret
            if not is_inserted:
                continue
            field_values = {
                "capex": ret_capex,
                "operating_cash_flow": ret_ocf,
                "revenue": ret_revenue,
                "eps_diluted": ret_eps,
            }
            missing = [f for f in MISSING_CHECK_FIELDS if field_values[f] is None]
            if missing:
                missing_fields_new_log.append({
                    "symbol": ret_symbol,
                    "fiscal_quarter_end": ret_qend.isoformat(),
                    "missing_fields": missing,
                    "detected_at": date.today().isoformat(),
                })
    conn.commit()
    return len(rows)


def _append_missing_log(new_entries, path="missing_fields_new_log.json"):
    """
    v9 (35차 세션 신규): 신규 결측 항목을 기존 로그에 append(누적)한다.
    기존 review_log.json 등과 달리 매 실행마다 덮어쓰지 않음 - 과거 실행에서 발견된
    신규 결측 이력이 계속 쌓여야 나중에 패턴 분석(자동화 전환 판단 근거)이 가능하기 때문.
    """
    if not new_entries:
        return
    existing = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"{path} 기존 로그 파싱 실패({e}) - 새 리스트로 대체하지 않고 원인 확인 필요")
            raise
    existing.extend(new_entries)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


def scan_all_missing_full(conn):
    """
    v9 (35차 세션 신규): --full 옵션 전용. 신규/기존 구분 없이 현재 DB 상태 기준
    MISSING_CHECK_FIELDS 4개 필드의 결측 전체를 매번 새로 스캔해 스냅샷으로 남긴다
    (missing_fields_new_log.json과 달리 매번 덮어쓰기, 누적 아님).
    일반 파이프라인(raw 재처리+upsert)은 실행하지 않고 조회만 수행.
    """
    entries = []
    with conn.cursor() as cur:
        for field in MISSING_CHECK_FIELDS:
            cur.execute(
                f"SELECT symbol, fiscal_quarter_end FROM fmp_quarterly_financials "
                f"WHERE {field} IS NULL ORDER BY symbol, fiscal_quarter_end"
            )
            for symbol, qend in cur.fetchall():
                entries.append({
                    "symbol": symbol,
                    "fiscal_quarter_end": qend.isoformat(),
                    "missing_field": field,
                })

    with open("missing_fields_full_log.json", "w") as f:
        json.dump(
            {"scanned_at": date.today().isoformat(), "entries": entries},
            f, indent=2, ensure_ascii=False,
        )
    log.warning(f"=== --full 결측 전체 스캔 완료: {len(entries)}건 (missing_fields_full_log.json 저장) ===")


def main():
    if "--full" in sys.argv:
        log.warning("=== --full 모드: 파이프라인 실행 없이 결측 전체 재검토(스냅샷)만 수행 ===")
        conn = psycopg2.connect(**DB_CONFIG)
        try:
            scan_all_missing_full(conn)
        finally:
            conn.close()
        return

    conn = psycopg2.connect(**DB_CONFIG)
    total_rows = 0

    try:
        for symbol in SYMBOLS:
            rows = collect_symbol(conn, symbol)
            n = upsert_rows(conn, rows)
            total_rows += n
            log.info(f"{symbol}: {n}개 분기 upsert 완료")
    finally:
        conn.close()

    log.info(f"=== 전체 완료: 총 {total_rows}개 행 upsert ===")

    if review_log:
        log.warning(f"=== 값 차이 1% 이상 케이스 {len(review_log)}건 (review_log.json 저장) ===")
        with open("review_log.json", "w") as f:
            json.dump(review_log, f, indent=2, ensure_ascii=False)
    else:
        log.info("값 차이 1% 이상 케이스 없음")

    if split_adjustment_log:
        log.info(f"=== eps_diluted 분할조정 적용 {len(split_adjustment_log)}건 (split_adjustment_log.json 저장) ===")
        with open("split_adjustment_log.json", "w") as f:
            json.dump(split_adjustment_log, f, indent=2, ensure_ascii=False)

    if eps_restatement_override_log:
        log.info(
            f"=== eps_diluted earliest->latest 전환 {len(eps_restatement_override_log)}건 "
            f"(eps_restatement_override_log.json 저장, v6/8차 세션 신규) ==="
        )
        with open("eps_restatement_override_log.json", "w") as f:
            json.dump(eps_restatement_override_log, f, indent=2, ensure_ascii=False)

    if self_inconsistency_log:
        log.warning(
            f"=== accn 자기모순으로 quarterly 격리 {len(self_inconsistency_log)}건 "
            f"(self_inconsistency_log.json 저장, v8/33차 세션 신규) ==="
        )
        with open("self_inconsistency_log.json", "w") as f:
            json.dump(self_inconsistency_log, f, indent=2, ensure_ascii=False)
    else:
        log.info("accn 자기모순 케이스 없음")

    if missing_fields_new_log:
        log.warning(
            f"=== 신규 결측 {len(missing_fields_new_log)}건 발견 "
            f"(missing_fields_new_log.json 누적 저장, v9/35차 세션 신규) ==="
        )
        _append_missing_log(missing_fields_new_log)
        symbols = sorted(set(e["symbol"] for e in missing_fields_new_log))
        notify_telegram_alert(
            f"⚠️ SEC EDGAR 백필: 신규 결측 {len(missing_fields_new_log)}건 발견 "
            f"({', '.join(symbols)}) - missing_fields_new_log.json 확인 필요"
        )
    else:
        log.info("신규 결측 없음 (이번 실행에서 새로 INSERT된 행 중 대상 필드 NULL 없음)")


if __name__ == "__main__":
    main()