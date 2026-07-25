"""
v10 변경사항(capex/operating_cash_flow 10-K 없는 중간분기 역산) dry-run 검증 스크립트.

- DB에 절대 쓰지 않음 (upsert_rows 호출 안 함, collect_symbol()만 호출해 메모리상에서 계산).
- ~/asset-cloud/scheduler/valuation_sec_edgar_backfill.py (프로덕션 파일, v10으로 이미 교체됨)를
  import해서 collect_symbol()만 재사용. upsert_rows()는 호출하지 않으므로 이 스크립트
  실행 자체는 DB에 아무 영향 없음.
- 10개 종목 전체에 대해:
  1) 새 버전으로 계산한 결과(new_rows)와 현재 DB에 있는 값을 비교
  2) GOOGL/TSLA 2026-06-30 분기는 capex/operating_cash_flow/tag_used를 강조 출력
  3) 그 외 종목/분기는 "기존 DB 값과 달라진 게 있는지"만 회귀 확인 (달라지면 안 됨)

실행: python3 dry_run_v10_check.py
"""
import sys
import os

# scheduler 폴더를 sys.path에 추가 (common/ 패키지 및 v10 모듈 import 위해)
SCHEDULER_DIR = os.path.expanduser("~/asset-cloud/scheduler")
sys.path.insert(0, SCHEDULER_DIR)
sys.path.insert(0, os.path.expanduser("~/asset-cloud"))  # common/ 패키지 접근용

import psycopg2
import valuation_sec_edgar_backfill as v10

DB_CONFIG = v10.DB_CONFIG
SYMBOLS = v10.SYMBOLS

FIELDS_TO_CHECK = [
    "revenue", "eps_diluted", "net_income", "operating_income",
    "operating_cash_flow", "capex", "free_cash_flow",
]


def fetch_existing_row(conn, symbol, qend):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT revenue, eps_diluted, net_income, operating_income,
                   operating_cash_flow, capex, free_cash_flow,
                   capex_tag_used, operating_cash_flow_tag_used
            FROM fmp_quarterly_financials
            WHERE symbol = %s AND fiscal_quarter_end = %s
            """,
            (symbol, qend),
        )
        row = cur.fetchone()
        if row is None:
            return None
        keys = FIELDS_TO_CHECK + ["capex_tag_used", "operating_cash_flow_tag_used"]
        return dict(zip(keys, row))


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        print("=" * 80)
        print("1) GOOGL/TSLA 2026-06-30 분기 - v10 신규 역산 결과 확인")
        print("=" * 80)
        for symbol in ("GOOGL", "TSLA"):
            new_rows = v10.collect_symbol(conn, symbol)
            target = next(
                (r for r in new_rows if r["fiscal_quarter_end"].isoformat() == "2026-06-30"),
                None,
            )
            if target is None:
                print(f"[{symbol}] 2026-06-30 행 자체가 계산 결과에 없음 (예상과 다름, 확인 필요)")
                continue
            print(f"\n[{symbol}] 2026-06-30 (v10 새로 계산한 값, DB에는 아직 안 씀)")
            print(f"  capex                = {target['capex']}")
            print(f"  capex_tag_used       = {target['capex_tag_used']}")
            print(f"  operating_cash_flow  = {target['operating_cash_flow']}")
            print(f"  operating_cash_flow_tag_used = {target['operating_cash_flow_tag_used']}")
            print(f"  free_cash_flow       = {target['free_cash_flow']}")

            existing = fetch_existing_row(conn, symbol, target["fiscal_quarter_end"])
            print(f"  --- 현재 DB에 있는 값(수정 전) ---")
            if existing is None:
                print("  DB에 해당 행 없음")
            else:
                print(f"  capex(DB)               = {existing['capex']}")
                print(f"  operating_cash_flow(DB) = {existing['operating_cash_flow']}")

        print("\n" + "=" * 80)
        print("2) 나머지 종목/분기 회귀 확인 (기존 DB 값과 달라지면 안 됨)")
        print("=" * 80)
        total_diff_count = 0
        for symbol in SYMBOLS:
            new_rows = v10.collect_symbol(conn, symbol)
            for row in new_rows:
                qend = row["fiscal_quarter_end"]
                if symbol in ("GOOGL", "TSLA") and qend.isoformat() == "2026-06-30":
                    continue  # 위에서 이미 확인한 신규 케이스는 회귀 비교 대상에서 제외
                existing = fetch_existing_row(conn, symbol, qend)
                if existing is None:
                    continue  # 기존 DB에 아예 없던 신규 행은 회귀 대상 아님
                diffs = []
                for f in FIELDS_TO_CHECK:
                    old_val = existing[f]
                    new_val = row[f]
                    if old_val is None and new_val is None:
                        continue
                    if old_val is None or new_val is None:
                        diffs.append((f, old_val, new_val))
                        continue
                    if abs(float(old_val) - float(new_val)) > 1e-6:
                        diffs.append((f, old_val, new_val))
                if diffs:
                    total_diff_count += 1
                    print(f"\n[{symbol} / {qend}] 기존 값과 차이 발견 (회귀 문제 가능성):")
                    for f, old_val, new_val in diffs:
                        print(f"    {f}: DB={old_val}  ->  new={new_val}")

        if total_diff_count == 0:
            print("\n회귀 확인 결과: GOOGL/TSLA 2026-06-30 외 모든 종목/분기가 기존 DB 값과 동일함 (정상)")
        else:
            print(f"\n⚠️ 회귀 확인 결과: {total_diff_count}건의 차이 발견 - DB 반영 전 반드시 원인 확인 필요")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
