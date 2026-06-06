"""
daily_inserter.py
매일 config.json의 daily_insert_time(HH:MM, KST)에 전날 스냅샷을 계산하여
daily_summary 테이블에 UPSERT한다.

동작 방식:
    1. 서비스 시작 시 오늘(or 내일) daily_insert_time 까지 타이머 등록
    2. 타이머 도달 시 미국 시장 상태 확인
       - closed : 전날 스냅샷 계산 후 INSERT, 다음날 타이머 등록
       - after  : 아직 애프터마켓 중 → 1시간 후 재시도 타이머 등록
    3. while 루프 없이 threading.Timer 로만 동작
"""

import datetime
import threading
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.db import get_db, get_config
from app.utils.daily_snapshot import get_daily_snapshot
from scheduler.price_updater import get_market_status

# ---------------------------------------------------------------------------
# DB UPSERT
# ---------------------------------------------------------------------------
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
# 타이머 스케줄링
# ---------------------------------------------------------------------------
def _next_trigger_time() -> datetime.datetime:
    """
    오늘 daily_insert_time 이 아직 안 지났으면 오늘, 지났으면 내일로 반환.
    KST 기준 datetime 반환.
    """
    config = get_config()
    raw = config.get("daily_insert_time", "09:10")
    h, m = map(int, raw.split(":"))

    now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    trigger = now_kst.replace(hour=h, minute=m, second=0, microsecond=0)

    # 이미 지난 시각이면 내일로
    if now_kst >= trigger:
        trigger += datetime.timedelta(days=1)

    return trigger

def _schedule_next() -> None:
    """다음 daily_insert_time 까지 타이머 등록."""
    trigger = _next_trigger_time()
    now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    delay = (trigger - now_kst).total_seconds()

    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"⏰ 다음 실행 예약: {trigger.strftime('%Y-%m-%d %H:%M:%S')} KST "
          f"({delay/3600:.1f}시간 후)", flush=True)

    threading.Timer(delay, _on_trigger).start()

def _schedule_retry() -> None:
    """애프터마켓 중일 때 1시간 후 재시도 타이머 등록."""
    now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    delay = 3600  # 1시간

    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"⏳ 애프터마켓 진행 중 → 1시간 후 재시도", flush=True)

    threading.Timer(delay, _on_trigger).start()

# ---------------------------------------------------------------------------
# 트리거 핸들러
# ---------------------------------------------------------------------------
def _on_trigger() -> None:
    """
    타이머 도달 시 호출.
    - closed : 전날 스냅샷 실행 후 다음날 타이머 등록
    - after  : 1시간 후 재시도 타이머 등록
    - 그 외  : 다음날 타이머 등록 (예상치 못한 상태)
    """
    now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    status = get_market_status("NAS")

    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"📡 시장 상태: {status}", flush=True)

    if status == "closed":
        # 전날 스냅샷 계산 및 INSERT
        yesterday = now_kst.date() - datetime.timedelta(days=1)
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"⏳ {yesterday} 스냅샷 계산 시작...", flush=True)
        try:
            snapshot = get_daily_snapshot(yesterday)
            _upsert(snapshot)
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"✅ {yesterday} INSERT 완료 "
                  f"| 총자산: {snapshot['total_asset']:,.0f} 원", flush=True)
        except Exception as e:
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"❌ 오류 발생: {e}", flush=True)
        # 성공/실패 무관하게 다음날 타이머 등록
        _schedule_next()

    elif status == "after":
        # 애프터마켓 중 → 1시간 후 재시도
        _schedule_retry()

    else:
        # open / pre / 기타 → 예상치 못한 상태, 다음날 타이머 등록
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"⚠️ 예상치 못한 시장 상태({status}) → 다음날 타이머 등록", flush=True)
        _schedule_next()

# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def main():
    now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"📅 daily_inserter 시작", flush=True)
    _schedule_next()

    # 메인 스레드가 종료되지 않도록 대기
    # (threading.Timer는 데몬 스레드가 아니므로 타이머가 살아있는 한 프로세스 유지)
    threading.Event().wait()

if __name__ == "__main__":
    main()
