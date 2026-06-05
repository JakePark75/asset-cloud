"""
gen_daily_data.py
날짜 범위를 입력받아 daily_summary를 강제로 UPSERT한다.
Usage: python3 gen_daily_data.py YYYYMMDD-YYYYMMDD
       python3 gen_daily_data.py YYYYMMDD  (단일 날짜)
"""

import datetime
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.db import get_db
from app.utils.daily_snapshot import get_daily_snapshot, _KR_CACHE, _US_CACHE, _YAHOO_CACHE, _get_token

def upsert(snapshot: dict) -> None:
    sql = """
        INSERT INTO daily_summary
            (date, total_asset, usd_krw, ndx100,
             exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio,
             twr_asset, cash_flow, cash_flow_note)
        VALUES
            (%(date)s, %(total_asset)s, %(usd_krw)s, %(ndx100)s,
             %(exposure)s, %(cash_ratio)s, %(x1_ratio)s, %(x2_ratio)s, %(x3_ratio)s,
             %(twr_asset)s, 0, NULL)
        ON CONFLICT (date) DO UPDATE SET
            total_asset = EXCLUDED.total_asset,
            usd_krw     = EXCLUDED.usd_krw,
            ndx100      = EXCLUDED.ndx100,
            exposure    = EXCLUDED.exposure,
            cash_ratio  = EXCLUDED.cash_ratio,
            x1_ratio    = EXCLUDED.x1_ratio,
            x2_ratio    = EXCLUDED.x2_ratio,
            x3_ratio    = EXCLUDED.x3_ratio,
            twr_asset   = EXCLUDED.twr_asset
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, snapshot)
        conn.commit()

def date_range(start: datetime.date, end: datetime.date) -> list:
    days, cur = [], start
    while cur <= end:
        days.append(cur)
        cur += datetime.timedelta(days=1)
    return days

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 daily_snapshot_fill.py YYYYMMDD 또는 YYYYMMDD-YYYYMMDD")
        sys.exit(1)

    raw = sys.argv[1].strip()
    try:
        if "-" in raw:
            parts = raw.split("-")
            start = datetime.datetime.strptime(parts[0], "%Y%m%d").date()
            end   = datetime.datetime.strptime(parts[1], "%Y%m%d").date()
        else:
            start = end = datetime.datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        print("❌ 날짜 형식 오류. YYYYMMDD 또는 YYYYMMDD-YYYYMMDD")
        sys.exit(1)

    if start > end:
        print("❌ 시작일이 종료일보다 늦습니다.")
        sys.exit(1)

    days = date_range(start, end)
    print(f"📅 {start} ~ {end} | 총 {len(days)}일 채우기 시작...\n")

    # KIS 토큰 1회만 발급
    _get_token()

    for target_date in days:
        print(f"  ⏳ {target_date} 계산 중...", end=" ", flush=True)
        try:
            snapshot = get_daily_snapshot(target_date)
            upsert(snapshot)
            print(f"✅ 총자산: {snapshot['total_asset']:,.0f} 원  |  환율: {snapshot['usd_krw']:,.2f}")
        except Exception as e:
            print(f"❌ 오류: {e}")

    print(f"\n🎉 완료!")

if __name__ == "__main__":
    main()