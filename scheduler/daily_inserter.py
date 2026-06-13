"""
daily_inserter.py
매일 config.json의 daily_insert_time(HH:MM, KST)에 전날 스냅샷을 계산하여
daily_summary 테이블에 UPSERT한다.

동작 방식:
    1. 서비스 시작 시 DB에서 마지막 스냅샷 날짜 확인
       - 빠진 날짜가 있으면 snap.py 로직으로 즉시 순서대로 채움
    2. 보정 완료 후 다음 daily_insert_time 까지 타이머 등록
    3. 타이머 도달 시 미국 시장 상태 확인
       - after, closed : 전날 스냅샷 계산 후 INSERT → 다음날 타이머 등록       
    4. while 루프 없이 threading.Timer 로만 동작
"""

import datetime
import threading
import sys
import os

from zoneinfo import ZoneInfo
KST = ZoneInfo("Asia/Seoul")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.db import get_db, get_config
from app.utils.daily_snapshot import get_daily_snapshot
from scheduler.price_updater_common import get_market_status
import app.utils.snap as snap

# ---------------------------------------------------------------------------
# 계좌별 prev_total_asset 업데이트
# ---------------------------------------------------------------------------
def _update_account_prev_totals(account_totals: dict) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            for acc_id, total in account_totals.items():
                cur.execute(
                    "UPDATE accounts SET prev_total_asset = %s WHERE id = %s",
                    (int(total), acc_id)
                )
        conn.commit()

# ---------------------------------------------------------------------------
# DB UPSERT
# ---------------------------------------------------------------------------
def _upsert(snapshot: dict) -> None:
    # Redis에서 오늘 입출금 읽기 — 실패해도 0으로 진행
    cash_flow = 0
    cash_flow_note = None
    try:
        from common.redis_store import get_redis
        r = get_redis()
        if r:
            cash_flow      = int(r.get("today_cash_flow") or 0)
            cash_flow_note = r.get("today_cash_flow_note")
    except Exception as e:
        print(f"[daily_inserter] Redis cash_flow 읽기 실패 (0으로 진행): {e}")

    sql = """
        INSERT INTO daily_summary
            (date, total_asset, usd_krw, ndx100,
             exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio,
             twr_asset, cash_flow, cash_flow_note)
        VALUES
            (%(date)s, %(total_asset)s, %(usd_krw)s, %(ndx100)s,
             %(exposure)s, %(cash_ratio)s, %(x1_ratio)s, %(x2_ratio)s, %(x3_ratio)s,
             %(twr_asset)s, %(cash_flow)s, %(cash_flow_note)s)
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
    snapshot_with_cf = {**snapshot, "cash_flow": cash_flow, "cash_flow_note": cash_flow_note}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, snapshot_with_cf)
        conn.commit()

    # INSERT 완료 후 Redis cash_flow 리셋
    try:
        from common.redis_store import get_redis
        r = get_redis()
        if r:
            r.set("today_cash_flow", 0)
            r.delete("today_cash_flow_note")
    except Exception as e:
        print(f"[daily_inserter] Redis cash_flow 리셋 실패 (무시): {e}")

# ---------------------------------------------------------------------------
# DB 헬퍼
# ---------------------------------------------------------------------------
def _fetch_last_snapshot_date() -> datetime.date | None:
    """DB에서 가장 최근 스냅샷 날짜 반환. 없으면 None."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(date) FROM daily_summary")
            row = cur.fetchone()
            return row[0] if row and row[0] else None

def _fetch_prev_summary(date: datetime.date) -> tuple | None:
    """전날 (total_asset, twr_asset) 반환."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT total_asset, twr_asset
                FROM daily_summary WHERE date = %s
            """, (date,))
            return cur.fetchone()

# ---------------------------------------------------------------------------
# 빠진 날짜 보정
# ---------------------------------------------------------------------------
def _backfill(start_date: datetime.date, end_date: datetime.date) -> None:
    """
    start_date ~ end_date 범위의 빠진 스냅샷을 snap.py 로직으로 채운다.
    snap.py의 글로벌 캐시(_GLOBAL_START_DATE_STR 등)를 활용해 API 호출을 최소화한다.
    """
    now_kst = datetime.datetime.now(KST)
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"🔄 누락 스냅샷 보정 시작: {start_date} ~ {end_date}", flush=True)

    # snap.py 글로벌 캐시 범위 세팅 (API 호출 최소화)
    snap._GLOBAL_START_DATE_STR = start_date.strftime("%Y%m%d")
    snap._GLOBAL_END_DATE_STR   = end_date.strftime("%Y%m%d")

    token        = snap.get_kis_access_token()
    position_rows = snap.fetch_positions()
    weekdays     = snap.date_range(start_date, end_date)

    # 시작일 전날 TWR 기준값 조회
    prev = _fetch_prev_summary(start_date - datetime.timedelta(days=1))
    prev_twr_asset   = float(prev[1]) if prev else None
    prev_total_asset = float(prev[0]) if prev else None

    for target_date in weekdays:
        date_str = target_date.strftime("%Y%m%d")
        print(f"  ⏳ {date_str} 보정 중...", end=" ", flush=True)

        result = snap.fetch_snapshot(target_date, position_rows, token)
        if result is None:
            print("⏭️  휴장일 스킵", flush=True)
            continue

        ratios, ndx100, usd_krw, _ = result
        total_asset = ratios["total_asset"]

        # TWR 계산
        if prev_twr_asset is None:
            twr_asset = total_asset
        else:
            denom = prev_total_asset
            twr_asset = prev_twr_asset * ((total_asset / denom) if denom else 1.0)

        prev_twr_asset   = twr_asset
        prev_total_asset = total_asset

        _upsert({
            "date":        target_date,
            "total_asset": total_asset,
            "usd_krw":     usd_krw,
            "ndx100":      ndx100,
            "exposure":    ratios["exposure"],
            "cash_ratio":  ratios["cash_ratio"],
            "x1_ratio":    ratios["x1_ratio"],
            "x2_ratio":    ratios["x2_ratio"],
            "x3_ratio":    ratios["x3_ratio"],
            "twr_asset":   twr_asset,
        })
        print(f"✅ 총자산: {total_asset:,.0f} 원", flush=True)

    now_kst = datetime.datetime.now(KST)
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"✅ 누락 스냅샷 보정 완료", flush=True)

    # 보정 마지막 날짜 기준 계좌별 prev_total_asset 업데이트
    try:
        snapshot = get_daily_snapshot(end_date, calc_account_totals=True)
        _update_account_prev_totals(snapshot["account_totals"])
        now_kst = datetime.datetime.now(KST)
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"✅ 계좌별 prev_total_asset 업데이트 완료 ({end_date})", flush=True)
    except Exception as e:
        now_kst = datetime.datetime.now(KST)
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"❌ 계좌별 prev_total_asset 업데이트 실패: {e}", flush=True)

# ---------------------------------------------------------------------------
# 타이머 스케줄링
# ---------------------------------------------------------------------------
def _next_trigger_time() -> datetime.datetime:
    """
    오늘 daily_insert_time 이 아직 안 지났으면 오늘, 지났으면 내일로 반환.
    KST 기준 datetime 반환.
    """
    config = get_config()
    raw = config.get("daily_insert_time", "07:30")
    h, m = map(int, raw.split(":"))

    now_kst = datetime.datetime.now(KST)
    trigger = now_kst.replace(hour=h, minute=m, second=0, microsecond=0)

    # 이미 지난 시각이면 내일로
    if now_kst >= trigger:
        trigger += datetime.timedelta(days=1)

    return trigger

def _schedule_next() -> None:
    """다음 daily_insert_time 까지 타이머 등록."""
    trigger = _next_trigger_time()
    now_kst = datetime.datetime.now(KST)
    delay = (trigger - now_kst).total_seconds()

    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"⏰ 다음 실행 예약: {trigger.strftime('%Y-%m-%d %H:%M:%S')} KST "
          f"({delay/3600:.1f}시간 후)", flush=True)

    threading.Timer(delay, _on_trigger).start()

def _schedule_retry() -> None:
    """애프터마켓 중일 때 1시간 후 재시도 타이머 등록."""
    now_kst = datetime.datetime.now(KST)
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"⏳ 애프터마켓 진행 중 → 1시간 후 재시도", flush=True)

    threading.Timer(3600, _on_trigger).start()

# ---------------------------------------------------------------------------
# 갱신 신호 발행 (Redis pub/sub)
# ---------------------------------------------------------------------------
def _notify_daily_inserted() -> None:
    try:
        from common.redis_store import publish_daily_inserted
        publish_daily_inserted()
    except Exception as e:
        now_kst = datetime.datetime.now(KST)
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"⚠️ daily_inserted 신호 발행 실패 (무시): {e}", flush=True)

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
    now_kst = datetime.datetime.now(KST)
    status = get_market_status("NAS")

    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"📡 시장 상태: {status}", flush=True)

    if status == "closed" or status == "after":
        yesterday = now_kst.date() - datetime.timedelta(days=1)
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"⏳ {yesterday} 스냅샷 계산 시작...", flush=True)
        try:
            snapshot = get_daily_snapshot(yesterday, calc_account_totals=True)
            _upsert(snapshot)
            _update_account_prev_totals(snapshot["account_totals"])
            now_kst = datetime.datetime.now(KST)
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"✅ {yesterday} INSERT 완료 "
                  f"| 총자산: {snapshot['total_asset']:,.0f} 원", flush=True)

            # Shiny 세션에 DB 갱신 알림
            _notify_daily_inserted()

        except Exception as e:
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"❌ 오류 발생: {e}", flush=True)

        # INSERT 완료 후 오늘 행을 즉시 히스토리에 반영
        # recalc_today_row()는 Redis prices(어제 종가)로 오늘 행을 계산해 today_row key에 저장
        # → load_history()가 이를 append해 장 시작 전에도 오늘 날짜 행이 보임
        try:
            from common.redis_store import recalc_today_row
            recalc_today_row()
            now_kst = datetime.datetime.now(KST)
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"✅ today_row 갱신 완료", flush=True)
        except Exception as e:
            now_kst = datetime.datetime.now(KST)
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"⚠️ today_row 갱신 실패 (무시): {e}", flush=True)

        _schedule_next()

    # 일단 막아두고, 야후종가 획득 가능한 시간 테스트 확정되면 그때 로직 수정하자.
    # after 마켓 중일 때는 종가 확정이 안 됐을 수 있으므로 1시간 후 재시도
    # elif status == "after":
    #     _schedule_retry()

    else:
        # open / pre / 기타 → 예상치 못한 상태, 다음날 타이머 등록
        now_kst = datetime.datetime.now(KST)
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"⚠️ 예상치 못한 시장 상태({status}) → 다음날 타이머 등록", flush=True)
        _schedule_next()

# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def main():
    now_kst = datetime.datetime.now(KST)
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"📅 daily_inserter 시작", flush=True)

    # 빠진 날짜 보정
    yesterday = now_kst.date() - datetime.timedelta(days=1)
    last_date = _fetch_last_snapshot_date()

    if last_date is None:
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"⚠️ DB에 스냅샷 없음 → 보정 스킵", flush=True)
    elif last_date < yesterday:
        # 마지막 스냅샷 다음날부터 어제까지 보정
        _backfill(last_date + datetime.timedelta(days=1), yesterday)
    else:
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"✅ 누락 없음 (최근: {last_date})", flush=True)

    # 다음 실행 타이머 등록
    _schedule_next()

    # 메인 스레드 유지
    threading.Event().wait()

if __name__ == "__main__":
    main()