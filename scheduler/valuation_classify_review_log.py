"""
classify_review_log.py

8차 세션 신규 작성. 문서에는 "4차 세션에서 완료"로 기록돼 있었으나 실제 VM에는
파일이 존재하지 않아(find 명령으로 부재 확인) 이번에 새로 작성함.

목적: valuation_sec_edgar_backfill.py 실행 시 생성되는 review_log.json(1% 이상 값 차이 케이스,
현재 248건)을 원인별로 분류한다. review_log 자체는 backfill 로직에 영향을 주지 않는
정보성 로그이므로, 이 스크립트는 채택 결과를 바꾸지 않고 분류만 수행한다.

분류 기준 (8차 세션 실측 분포 기반: eps_diluted 115건, ocf 37, net_income 32,
revenue 31, operating_income 24, capex 9):

1) eps_diluted (pick_mode="earliest" 전용):
   backfill.py는 eps_diluted를 "최초 filed 고정값 + 명시적 분할조정"으로 처리하는데,
   review_log 자체는 split_multiplier 적용 이전의 raw 값끼리 비교해서 기록된다.
   즉 두 filed 시점 사이에 실제 주식분할이 있었다면, 나중 filing이 이미 분할조정된
   값으로 재보고한 것뿐인데 "값이 다른 케이스"로 잡힌다 (실측 확인: NVDA 2020-07-26
   분기, 0.99 -> 0.25, 사이에 2021-07-20 4:1 분할 존재. 0.99/4=0.2475≈0.25).
   -> chosen_filed~other_filed 사이 분할 이력을 확인해, 비율이 그 분할비(또는 역수)와
      5% 이내로 맞으면 "split_explained"(정상, 조치 불필요), 아니면 "needs_review"
      (진짜 재작성/오류 가능성).

2) 나머지 5개 concept (pick_mode="latest", 절대금액):
   진짜 회계 재작성(restatement)이다. 이미 "latest" 값을 채택 중이므로 조치는
   필요 없지만, 크기별로 확인 우선순위를 나눈다.
   -> diff_ratio >= 0.5: "large_restatement" (M&A/사업부 매각/회계기준 변경 등
      구조적 사건 의심, 개별 확인 권장)
   -> 0.1 <= diff_ratio < 0.5: "moderate_restatement"
   -> diff_ratio < 0.1: "minor_restatement" (일반적 재작성 범위, 조치 불필요)

주의: SPLIT_HISTORY는 valuation_sec_edgar_backfill.py(scheduler/)의 정의와 동일해야 한다.
분할 이력이 추가/변경되면 이 파일도 같이 갱신할 것 (자동 동기화 아님, 수동 관리).
"""

import json
from datetime import date

REVIEW_LOG_PATH = "review_log.json"
OUTPUT_PATH = "review_log_classified.json"

SPLIT_TOLERANCE = 0.05  # 분할비 매칭 허용 오차 (5%)

# valuation_sec_edgar_backfill.py와 동일 (수동 동기화 필요)
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
}


def parse_date(s: str) -> date:
    return date.fromisoformat(s)


def splits_between(symbol: str, d1: date, d2: date):
    """d1과 d2(순서 무관) 사이에 발생한 분할들의 비율 리스트."""
    lo, hi = (d1, d2) if d1 <= d2 else (d2, d1)
    return [ratio for split_date, ratio in SPLIT_HISTORY.get(symbol, []) if lo < split_date <= hi]


def classify_eps(entry: dict) -> str:
    symbol = entry["symbol"]
    chosen_filed = parse_date(entry["chosen_filed"])
    other_filed = parse_date(entry["other_filed"])
    ratios = splits_between(symbol, chosen_filed, other_filed)

    if not ratios:
        return "needs_review"

    # 두 filed 시점 사이 분할 비율의 누적곱 (여러 번 분할됐을 수도 있으므로)
    cumulative = 1.0
    for r in ratios:
        cumulative *= r

    chosen_val = entry["chosen_val"]
    other_val = entry["other_val"]
    if chosen_val == 0 or other_val == 0:
        return "needs_review"

    # 어느 쪽이 분할 이전(액면 큰 값)인지 모르므로 양방향 다 확인
    ratio_a = abs(chosen_val / other_val)
    ratio_b = abs(other_val / chosen_val)

    for observed in (ratio_a, ratio_b):
        if abs(observed - cumulative) / cumulative <= SPLIT_TOLERANCE:
            return "split_explained"

    return "needs_review"


def classify_absolute(entry: dict) -> str:
    diff_ratio = entry["diff_ratio"]
    if diff_ratio >= 0.5:
        return "large_restatement"
    elif diff_ratio >= 0.1:
        return "moderate_restatement"
    else:
        return "minor_restatement"


def classify_entry(entry: dict) -> str:
    if entry["field"] == "eps_diluted":
        return classify_eps(entry)
    return classify_absolute(entry)


def main():
    with open(REVIEW_LOG_PATH) as f:
        data = json.load(f)

    for entry in data:
        entry["classification"] = classify_entry(entry)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    from collections import Counter
    by_class = Counter(e["classification"] for e in data)
    by_field_class = Counter((e["field"], e["classification"]) for e in data)

    print(f"=== 전체 {len(data)}건 분류 완료 -> {OUTPUT_PATH} ===")
    print()
    print("=== 분류별 전체 건수 ===")
    for k, v in by_class.most_common():
        print(f"{k}: {v}")
    print()
    print("=== field x 분류 교차표 ===")
    for (field, cls), v in sorted(by_field_class.items()):
        print(f"{field:20s} {cls:20s} {v}")
    print()
    needs_review = [e for e in data if e["classification"] == "needs_review"]
    large = [e for e in data if e["classification"] == "large_restatement"]
    if needs_review:
        print(f"=== needs_review {len(needs_review)}건 (분할로 설명 안 되는 eps_diluted 차이, 개별 확인 필요) ===")
        for e in needs_review[:20]:
            print(e)
    if large:
        print(f"=== large_restatement {len(large)}건 (50% 이상 재작성, 개별 확인 권장) ===")
        for e in large[:20]:
            print(e)


if __name__ == "__main__":
    main()