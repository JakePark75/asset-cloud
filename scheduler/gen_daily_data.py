"""
gen_daily_data.py
날짜 범위를 입력받아 daily_summary를 강제로 UPSERT한다.
"""

import datetime
import sys
import os

# 프로젝트 루트 경로 설정
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.db import get_db
# 원본 스냅샷 핵심 함수들과 캐시/토큰 관련 모듈 가져오기
from app.utils.daily_snapshot import get_daily_snapshot, _get_token
import app.utils.snap as snap 

def _update_account_prev_totals(account_totals: dict) -> None:
    """계좌별 전일 자산 업데이트 (원본 로직 유지)"""
    with get_db() as conn:
        with conn.cursor() as cur:
            for acc_id, total in account_totals.items():
                cur.execute(
                    "UPDATE accounts SET prev_total_asset = %s WHERE id = %s",
                    (int(total), acc_id)
                )
        conn.commit()

def upsert(snapshot: dict) -> None:
    """DB daily_summary 테이블에 데이터 삽입/갱신 (UPSERT)"""
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

def date_range(start, end):
    """지정된 범위의 모든 날짜 생성"""
    days, cur = [], start
    while cur <= end:
        days.append(cur)
        cur += datetime.timedelta(days=1)
    return days

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 gen_daily_data.py YYYYMMDD 또는 YYYYMMDD-YYYYMMDD")
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
        print("❌ 날짜 형식 오류.")
        sys.exit(1)

    # 1. [성능] 초고속 조회를 위한 글로벌 캐시 범위 미리 설정
    snap._GLOBAL_START_DATE_STR = start.strftime("%Y%m%d")
    snap._GLOBAL_END_DATE_STR   = end.strftime("%Y%m%d")
    _get_token()

    days = date_range(start, end)
    
    # 2. [원본 의도] DB의 최신 날짜를 조회하여 계좌 업데이트 기준 마련
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(date) FROM daily_summary")
            row = cur.fetchone()
            max_date = row[0] if row and row[0] else None

    # 마지막 날짜가 DB 최신 상태와 같거나 이후면 계좌 갱신 필요 (need_account)
    need_account = max_date is None or end >= max_date

    last_snapshot = None
    for target_date in days:
        print(f"  ⏳ {target_date} 계산 중...", end=" ", flush=True)
        try:
            is_last = (target_date == end)
            # 계좌 합산은 마지막 날짜이고, 최신 작업일 때만 수행
            snapshot = get_daily_snapshot(target_date, calc_account_totals=(is_last and need_account))
            
            upsert(snapshot)
            print(f"✅ 총자산: {snapshot['total_asset']:,.0f} 원  |  환율: {snapshot['usd_krw']:,.2f}")
            
            if is_last:
                last_snapshot = snapshot
        except Exception as e:
            print(f"❌ 오류: {e}")

    # 3. [원본 의도] 마지막 날짜가 최신일 때만 계좌별 최종 갱신 수행
    if last_snapshot and need_account and snapshot.get("account_totals"):
        _update_account_prev_totals(last_snapshot["account_totals"])
        print(f"\n✅ 계좌별 prev_total_asset 업데이트 완료 ({end})")

    print(f"\n🎉 모든 작업이 완료되었습니다!")

if __name__ == "__main__":
    main()