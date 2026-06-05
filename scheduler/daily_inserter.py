"""
daily_inserter.py
매일 config.json의 daily_insert_time(HH:MM, KST)에 전날 스냅샷을 계산하여
daily_summary 테이블에 UPSERT한다.

config.json 필요 키:
    daily_insert_time  : "07:30"  (HH:MM, KST)
    db_password        : ...
"""

import datetime
import time
import sys
import os

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.db import get_db, get_config
from app.utils.daily_snapshot import get_daily_snapshot

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def _target_time(config: dict) -> datetime.time:
    """config의 daily_insert_time 파싱. 없으면 07:30 기본값."""
    raw = config.get("daily_insert_time", "07:30")
    h, m = map(int, raw.split(":"))
    return datetime.time(h, m)

def _upsert(snapshot: dict) -> None:
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
        -- cash_flow / cash_flow_note 는 사용자 입력값이므로 덮어쓰지 않는다
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, snapshot)
        conn.commit()

# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------
def main():
    print("📅 daily_inserter 시작", flush=True)
    last_run_date: datetime.date | None = None

    while True:
        now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
        today   = now_kst.date()

        config      = get_config()
        target_time = _target_time(config)

        # 오늘 지정 시각 이후이고, 아직 오늘 실행 안 했으면 실행
        if now_kst.time() >= target_time and last_run_date != today:
            yesterday = today - datetime.timedelta(days=1)
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"⏳ {yesterday} 스냅샷 계산 시작...", flush=True)
            try:
                snapshot = get_daily_snapshot(yesterday)
                _upsert(snapshot)
                last_run_date = today
                print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                      f"✅ {yesterday} INSERT 완료 "
                      f"| 총자산: {snapshot['total_asset']:,.0f} 원", flush=True)
            except Exception as e:
                print(f"❌ 오류 발생: {e}", flush=True)
                # 오류 시 last_run_date 갱신 안 함 → 1분 후 재시도
        
        time.sleep(60)

if __name__ == "__main__":
    main()