"""
SEC EDGAR XBRL facts raw collector (1단계)

목적: SEC EDGAR companyconcept API에서 가져온 모든 entry를
      어떤 가공/판단도 거치지 않고 sec_xbrl_facts_raw 테이블에 그대로 적재한다.

이 스크립트는 "원본 그대로 저장"만 담당한다.
필터링(기간 80~100일 등), latest 채택, Q4 역산, revenue 태그 fallback 판단은
sec_edgar_quarterly_build.py (2단계)에서 raw 테이블을 읽어 수행한다.

재실행 안전성:
- PK(symbol, concept_tag, accn, period_start, period_end) 충돌 시 스킵한다.
- 단, 같은 PK인데 값(val)이 다른 경우는 원본 데이터에 모순이 있다는 신호이므로
  조용히 덮어쓰지 않고 duplicates_review.json에 기록해 별도 검토할 수 있게 한다.
  (이 경우는 발생하지 않는 게 정상이라고 가정하고 PK를 설계했음 -- 실제로 발생하면
   이 가정이 틀렸다는 뜻이므로 PK 재설계가 필요하다는 신호로 취급해야 함)
"""

import json
import time
import logging
from datetime import date

import psycopg2
import requests

DB_CONFIG = dict(
    dbname="assetdb",
    user="jake",
    password="qkrworb0!",
    host="localhost",
)

SEC_HEADERS = {"User-Agent": "PersonalAssetProject jake@example.com"}
SEC_BASE = "https://data.sec.gov/api/xbrl/companyconcept"
REQUEST_INTERVAL_SEC = 0.15  # 초당 10회 제한 -> 여유있게 0.15초 간격

SYMBOL_CIK = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "AMZN": "0001018724",
    "META": "0001326801",
    "NVDA": "0001045810",
    "TSLA": "0001318605",
    "NFLX": "0001065280",
    "AMD": "0000002488",
    "PLTR": "0001321655",
}

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

ALL_TAGS = REVENUE_TAG_CANDIDATES + [
    tag for candidates in OTHER_CONCEPT_TAG_CANDIDATES.values() for tag in candidates
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("sec_raw_collect")

duplicates_review = []  # 같은 PK인데 값이 다른 케이스 (있으면 안 되는 케이스)


def fetch_concept(cik: str, tag: str):
    """SEC EDGAR companyconcept API 호출. 실패(404 등) 시 None 반환."""
    url = f"{SEC_BASE}/CIK{cik}/us-gaap/{tag}.json"
    resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
    time.sleep(REQUEST_INTERVAL_SEC)
    if resp.status_code != 200:
        return None
    return resp.json()


def extract_raw_rows(data: dict, symbol: str, concept_tag: str):
    """data['units']의 모든 unit, 모든 entry를 가공 없이 행(dict)으로 변환.
    unit 선택, 기간 필터링 등 어떤 판단도 여기서 하지 않는다."""
    rows = []
    if not data or "units" not in data:
        return rows
    for unit, entries in data["units"].items():
        for e in entries:
            required = ("form", "start", "end", "filed", "accn", "val")
            if not all(k in e for k in required):
                continue
            try:
                date.fromisoformat(e["start"])
                date.fromisoformat(e["end"])
                date.fromisoformat(e["filed"])
            except ValueError:
                continue
            rows.append({
                "symbol": symbol,
                "concept_tag": concept_tag,
                "unit": unit,
                "form": e["form"],
                "fy": e.get("fy"),
                "fp": e.get("fp"),
                "period_start": e["start"],
                "period_end": e["end"],
                "filed_date": e["filed"],
                "accn": e["accn"],
                "val": e["val"],
            })
    return rows


def upsert_raw_rows(conn, rows):
    """행마다 기존 존재 여부를 확인해, 없으면 삽입, 있으면 값 비교 후 스킵.
    1회성 backfill 스크립트라 성능보다 정확성/검증을 우선했다."""
    if not rows:
        return 0, 0

    sql_check = """
        SELECT val FROM sec_xbrl_facts_raw
        WHERE symbol=%(symbol)s AND concept_tag=%(concept_tag)s AND accn=%(accn)s
          AND period_start=%(period_start)s AND period_end=%(period_end)s
    """
    sql_insert = """
        INSERT INTO sec_xbrl_facts_raw (
            symbol, concept_tag, unit, form, fy, fp,
            period_start, period_end, filed_date, accn, val
        ) VALUES (
            %(symbol)s, %(concept_tag)s, %(unit)s, %(form)s, %(fy)s, %(fp)s,
            %(period_start)s, %(period_end)s, %(filed_date)s, %(accn)s, %(val)s
        )
        ON CONFLICT (symbol, concept_tag, accn, period_start, period_end) DO NOTHING;
    """
    inserted, skipped = 0, 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(sql_check, row)
            existing = cur.fetchone()
            if existing is not None:
                skipped += 1
                from decimal import Decimal
                if abs(Decimal(str(existing[0])) - Decimal(str(row["val"]))) > Decimal("0.001"):
                    duplicates_review.append({
                        "symbol": row["symbol"],
                        "concept_tag": row["concept_tag"],
                        "accn": row["accn"],
                        "period_start": row["period_start"],
                        "period_end": row["period_end"],
                        "existing_val": float(existing[0]),
                        "new_val": float(row["val"]),
                    })
                continue
            cur.execute(sql_insert, row)
            inserted += 1
    conn.commit()
    return inserted, skipped


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    total_inserted, total_skipped = 0, 0

    try:
        for symbol, cik in SYMBOL_CIK.items():
            log.info(f"=== {symbol} raw 수집 시작 ===")
            for tag in ALL_TAGS:
                data = fetch_concept(cik, tag)
                rows = extract_raw_rows(data, symbol, tag)
                inserted, skipped = upsert_raw_rows(conn, rows)
                total_inserted += inserted
                total_skipped += skipped
                log.info(
                    f"{symbol}/{tag}: {len(rows)}건 조회, "
                    f"{inserted}건 신규 적재, {skipped}건 기존(skip)"
                )
    finally:
        conn.close()

    log.info(f"=== 전체 완료: 신규 {total_inserted}건, 기존skip {total_skipped}건 ===")

    if duplicates_review:
        log.warning(
            f"=== 같은 PK인데 값이 다른 케이스 {len(duplicates_review)}건 발견 "
            f"-> duplicates_review.json 저장 (PK 설계 가정 위반, 검토 필요) ==="
        )
        with open("duplicates_review.json", "w") as f:
            json.dump(duplicates_review, f, indent=2, ensure_ascii=False)
    else:
        log.info("PK 충돌(값 모순) 케이스 없음 - PK 설계 가정 유효")


if __name__ == "__main__":
    main()