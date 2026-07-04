"""
daily_inserter.py
매일 config.json의 daily_insert_time(HH:MM, KST)에 전날 스냅샷을 계산하여
daily_summary 테이블에 UPSERT한다.

동작 방식 (체크포인트 기반 재설계):
    1. 서비스 시작 시 backfill_checkpoint.last_success_date 조회
       - checkpoint+1 ~ 어제 범위에서 diff로 구멍을 찾아 즉시 보정
    2. 보정 완료 후 다음 daily_insert_time 까지 타이머 등록
    3. 타이머 도달 시 (매일 1회) 시장 상태 확인
       - closed/after: checkpoint+1 ~ 어제 범위 diff 스캔 + 필요시 보정
         (이 스캔이 서비스 재시작 없이도 과거 구멍을 계속 재확인하는 유일한 경로)
    4. while 루프 없이 threading.Timer 로만 동작

체크포인트 전진 규칙:
    - 검사 범위 내 diff로 찾은 구멍을 채우다가 실패한 날짜가 남아있으면
      → checkpoint = (그중 가장 이른 실패 날짜의 전날)
    - 전부 성공(또는 구멍 자체가 없음) → checkpoint = 검사 종료일(어제)
    이 규칙 하나로 전부성공/부분성공/전부실패 세 케이스가 모두 처리된다.

사전 준비 (필수):
    backfill_checkpoint 테이블에 최초 1회 수동 시딩 필요.
        CREATE TABLE backfill_checkpoint (
            id SERIAL PRIMARY KEY,
            last_success_date DATE NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        INSERT INTO backfill_checkpoint (id, last_success_date) VALUES (1, '<확인된 마지막 날짜>');
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
from app.utils.snap import KISTokenError, KRPriceFetchError, YahooFetchError

# 텔레그램 알림 함수는 common/notify.py 로 이동됨 (순환 임포트 제거 목적).
# 하위호환: 기존에 `from scheduler.daily_inserter import _notify_telegram_alert`
# 로 참조하던 코드가 있어도 깨지지 않도록 동일한 이름으로 재노출한다.
from common.notify import notify_telegram_alert as _notify_telegram_alert

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
def _upsert(snapshot: dict, use_redis_cash_flow: bool = False) -> None:
    """
    daily_summary에 UPSERT한다.

    use_redis_cash_flow=True 일 때만 Redis today_cash_flow/today_cash_flow_note를
    읽어 반영하고 리셋한다. 이건 "오늘(=어제) 정상 마감" 전용 플래그이며,
    _insert_daily_close()에서만 True로 호출한다.
    백필(_backfill)에서는 절대 True로 호출하지 않는다 — 과거 구멍에 그날과 무관한
    "오늘 입출금" 값이 잘못 붙거나, 리셋되면서 유실되는 것을 막기 위함.
    """
    cash_flow = 0
    cash_flow_note = None
    if use_redis_cash_flow:
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

    # INSERT 완료 후 Redis cash_flow 리셋 (정상 마감 경로에서만)
    if use_redis_cash_flow:
        try:
            from common.redis_store import get_redis
            r = get_redis()
            if r:
                r.set("today_cash_flow", 0)
                r.delete("today_cash_flow_note")
        except Exception as e:
            print(f"[daily_inserter] Redis cash_flow 리셋 실패 (무시): {e}")

# ---------------------------------------------------------------------------
# 체크포인트 헬퍼
# ---------------------------------------------------------------------------
def _get_last_success_date() -> datetime.date:
    """
    backfill_checkpoint.last_success_date 조회.
    행이 없으면 RuntimeError (최초 1회 수동 시딩 필요 — 사전 준비 참고).
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT last_success_date FROM backfill_checkpoint WHERE id = 1")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(
                    "backfill_checkpoint 시딩 안 됨 (id=1 행 없음) — 수동 INSERT 필요"
                )
            return row[0]

def _set_last_success_date(d: datetime.date) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE backfill_checkpoint SET last_success_date = %s, updated_at = now() WHERE id = 1",
                (d,)
            )
        conn.commit()

def _fetch_prev_summary(date: datetime.date) -> tuple | None:
    """전날 (total_asset, twr_asset) 반환."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT total_asset, twr_asset
                FROM daily_summary WHERE date = %s
            """, (date,))
            return cur.fetchone()

def _fetch_missing_dates(start_date: datetime.date, end_date: datetime.date) -> list[datetime.date]:
    """
    start_date ~ end_date 범위에서 daily_summary에 없는 날짜(구멍) 목록 반환.
    전체 기간을 diff하는 게 아니라 항상 이 좁은 범위 안에서만 검사한다.
    """
    if start_date > end_date:
        return []

    all_dates = snap.date_range(start_date, end_date)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT date FROM daily_summary WHERE date BETWEEN %s AND %s",
                (start_date, end_date)
            )
            existing = {row[0] for row in cur.fetchall()}
    return [d for d in all_dates if d not in existing]

# ---------------------------------------------------------------------------
# 빠진 날짜 보정 (날짜 리스트 기반 — 구멍이 비연속이어도 처리 가능)
# ---------------------------------------------------------------------------
def _backfill(dates: list[datetime.date]) -> list[datetime.date]:
    """
    주어진 날짜들을 순서대로 채운다.
    개별 날짜의 시세 조회 실패는 삼키고 다음 날짜로 계속 진행한다
    (토큰 발급 실패 등 배치 전체를 막는 예외는 그대로 위로 raise).

    반환값: 끝내 채우지 못한 날짜 리스트 (빈 리스트면 전부 성공).
    """
    if not dates:
        return []

    now_kst = datetime.datetime.now(KST)
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"🔄 누락 스냅샷 보정 시작: {dates[0]} ~ {dates[-1]} (총 {len(dates)}일, 구멍 {dates})", flush=True)

    # snap.py 글로벌 캐시 범위 세팅 (+ 캐시 리셋, 이전 배치 가격 오염 방지)
    snap.set_batch_range(dates[0].strftime("%Y%m%d"), dates[-1].strftime("%Y%m%d"))

    # 토큰/포지션 조회 실패는 배치 전체를 막는 이상상황 → 그대로 위로 raise
    token         = snap.get_kis_access_token()
    position_rows = snap.fetch_positions()

    # 시작일 전날 TWR 기준값 조회
    prev = _fetch_prev_summary(dates[0] - datetime.timedelta(days=1))
    prev_twr_asset   = float(prev[1]) if prev else None
    prev_total_asset = float(prev[0]) if prev else None

    failed_dates: list[datetime.date] = []

    for target_date in dates:
        date_str = target_date.strftime("%Y%m%d")
        print(f"  ⏳ {date_str} 보정 중...", end=" ", flush=True)

        try:
            result = snap.fetch_snapshot(target_date, position_rows, token)
        except Exception as e:
            # 이 날짜만 실패 — 다음 날짜는 계속 시도한다.
            # prev_twr_asset/prev_total_asset은 갱신하지 않음 (실패 지점을 건너뛰고
            # 다음 성공 날짜는 그 이전 마지막 성공 날짜 기준으로 TWR 계산됨)
            print(f"❌ 실패: {e}", flush=True)
            failed_dates.append(target_date)
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
        }, use_redis_cash_flow=False)  # 백필은 오늘 입출금을 절대 참조하지 않는다
        print(f"✅ 총자산: {total_asset:,.0f} 원", flush=True)

    now_kst = datetime.datetime.now(KST)
    if failed_dates:
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"⚠️ 누락 스냅샷 보정 부분 완료 — 미해결: {failed_dates}", flush=True)
    else:
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"✅ 누락 스냅샷 보정 전체 완료", flush=True)

    # 성공한 마지막 날짜 기준 계좌별 prev_total_asset 업데이트
    last_success = next((d for d in reversed(dates) if d not in failed_dates), None)
    if last_success is not None:
        try:
            snapshot = get_daily_snapshot(last_success, calc_account_totals=True)
            _update_account_prev_totals(snapshot["account_totals"])
            now_kst = datetime.datetime.now(KST)
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"✅ 계좌별 prev_total_asset 업데이트 완료 ({last_success})", flush=True)
        except Exception as e:
            now_kst = datetime.datetime.now(KST)
            msg = f"🚨 계좌별 prev_total_asset 업데이트 실패 ({last_success}): {e}"
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] ❌ {msg}", flush=True)
            _notify_telegram_alert(msg)

    return failed_dates

# ---------------------------------------------------------------------------
# 정상 마감 (오늘=어제 하루 전용 — 백필과 목적이 다름)
# ---------------------------------------------------------------------------
def _insert_daily_close(target_date: datetime.date) -> None:
    """
    target_date(=어제) 하루의 "정상 마감" 처리 전용.
    _backfill()과 달리 daily_snapshot.get_daily_snapshot()을 사용하고,
    Redis today_cash_flow를 daily_summary에 반영하는 유일한 경로다
    (_upsert(use_redis_cash_flow=True)).

    실패하면 그대로 예외를 던진다 — 호출측(_run_daily_cycle)이 실패 날짜로 기록한다.
    """
    snapshot = get_daily_snapshot(target_date, calc_account_totals=True)
    _upsert(snapshot, use_redis_cash_flow=True)
    _update_account_prev_totals(snapshot["account_totals"])

# ---------------------------------------------------------------------------
# 일일 사이클 실행 래퍼 (체크포인트 전진까지 책임)
#   - 과거 구멍(정상 마감일 이전)  → _backfill()
#   - 정상 마감일(=어제) 당일     → _insert_daily_close()
#   두 경로는 목적이 다르므로(백필 vs 오늘 입출금 반영) 이 함수 안에서 명확히 나눈다.
# ---------------------------------------------------------------------------
def _run_daily_cycle(start_date: datetime.date, end_date: datetime.date) -> None:
    """
    start_date ~ end_date 범위를 diff 검사한다.
    end_date(=어제)를 제외한 나머지 구멍은 _backfill()로 채우고,
    end_date 자체는 _insert_daily_close()로 별도 처리한다.
    확정된 규칙대로 체크포인트를 전진시킨다. 절대 main()/_on_trigger()로
    예외를 전파하지 않는다 (실패해도 항상 리턴 → 상위 스케줄은 막히지 않음).

    - 실패 시 자체 타이머로 재시도하지 않고 로그만 남기며, 다음 날 정규 스케줄에서 재처리된다.
    """
    now_kst = datetime.datetime.now(KST)

    if start_date > end_date:
        return

    try:
        missing = _fetch_missing_dates(start_date, end_date)

        if not missing:
            _set_last_success_date(end_date)
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"✅ 구멍 없음 → 체크포인트 {end_date}로 전진", flush=True)
            return

        # end_date(=어제)는 "정상 마감" 전용 경로이므로 백필 대상에서 분리한다.
        # today_cash_flow 반영은 오직 _insert_daily_close() 안에서만 일어난다.
        past_gaps = [d for d in missing if d != end_date]
        failed_dates: list[datetime.date] = []

        if past_gaps:
            failed_dates.extend(_backfill(past_gaps))

        if end_date in missing:
            print(f"  ⏳ {end_date.strftime('%Y%m%d')} 정상 마감 처리 중...", end=" ", flush=True)
            try:
                _insert_daily_close(end_date)
                print("✅", flush=True)
            except Exception as e:
                print(f"❌ 실패: {e}", flush=True)
                failed_dates.append(end_date)

        new_checkpoint = (min(failed_dates) - datetime.timedelta(days=1)) if failed_dates else end_date
        current = _get_last_success_date()
        if new_checkpoint > current:
            _set_last_success_date(new_checkpoint)

        if failed_dates:
            msg = (f"🚨 일일 사이클 일부 미해결 "
                   f"- 구멍: {missing} / 미해결: {failed_dates} (내일 정규 시간에 재시도합니다)")
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] {msg}", flush=True)
            _notify_telegram_alert(msg)
        else:
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"✅ 전부 성공 → 체크포인트 {new_checkpoint}로 전진", flush=True)

    except KISTokenError as e:
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"⚠️ 토큰 발급 실패 → 내일 정규 시간에 재시도합니다: {e}", flush=True)

    except Exception as e:
        msg = (f"🚨 일일 사이클 중단 - 디버깅 필요 "
               f"[{start_date} ~ {end_date}]: {e} (내일 정규 시간에 재시도합니다)")
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] {msg}", flush=True)
        _notify_telegram_alert(msg)

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
# 트리거 핸들러 (매일 1회 — 정기 insert + 구멍 diff 스캔을 함께 수행)
# ---------------------------------------------------------------------------
def _on_trigger() -> None:
    """
    타이머 도달 시 호출.
    - closed/after : 체크포인트+1 ~ 어제 범위를 diff 스캔하고 구멍이 있으면 채움
                      (서비스 재시작 여부와 무관하게 매일 이 경로로 과거 구멍까지 재확인함)
    - 그 외        : 예상치 못한 상태 → 다음날 타이머만 등록
    - 백필 성공/실패와 무관하게 항상 _schedule_next()에 도달 (다음날 정기 트리거는 별도로 보장)
    """
    now_kst = datetime.datetime.now(KST)

    try:
        status = get_market_status("NAS")
    except Exception as e:
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"❌ 시장 상태 조회 실패 → 다음날 정규 시간에 재시도합니다: {e}", flush=True)
        _notify_telegram_alert(f"🚨 get_market_status 실패 (스케줄러는 계속 동작): {e}")
        _schedule_next()
        return

    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"📡 시장 상태: {status}", flush=True)

    if status == "closed" or status == "after":
        yesterday = now_kst.date() - datetime.timedelta(days=1)

        try:
            checkpoint = _get_last_success_date()
        except Exception as e:
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"❌ 체크포인트 조회 실패 (시딩 필요): {e}", flush=True)
            _schedule_next()
            return

        if checkpoint < yesterday:
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"⏳ {checkpoint + datetime.timedelta(days=1)} ~ {yesterday} 구멍 스캔 시작...", flush=True)
            _run_daily_cycle(checkpoint + datetime.timedelta(days=1), yesterday)
        else:
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"✅ 구멍 없음 (체크포인트: {checkpoint})", flush=True)

        # _run_daily_cycle()은 동기적으로 처리하므로, 여기서 최신 체크포인트를
        # 다시 읽어 어제까지 실제로 채워졌는지 확인한다.
        # (실패 시엔 다음 날 정규 스케줄에서 다시 시도하므로 추가 조치는 하지 않음).
        try:
            latest_checkpoint = _get_last_success_date()
        except Exception:
            latest_checkpoint = checkpoint

        if latest_checkpoint >= yesterday:
            _notify_daily_inserted()
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
        else:
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"⏳ {yesterday}까지 아직 미해결 — 백필 자체 재시도가 별도로 진행 중", flush=True)

        _schedule_next()

    else:
        # open / pre / 기타 → 예상치 못한 상태, 다음날 타이머 등록
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

    yesterday = now_kst.date() - datetime.timedelta(days=1)

    try:
        last_date = _get_last_success_date()
    except Exception as e:
        print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
              f"❌ 체크포인트 조회 실패 → 보정 스킵 (backfill_checkpoint 시딩 필요): {e}", flush=True)
        last_date = None

    if last_date is not None:
        if last_date < yesterday:
            # _run_daily_cycle()은 실패해도 예외를 삼키고 자체적으로 재시도를 걸기 때문에
            # 아래 _schedule_next()는 성패와 무관하게 항상 도달한다.
            _run_daily_cycle(last_date + datetime.timedelta(days=1), yesterday)
        else:
            print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
                  f"✅ 누락 없음 (체크포인트: {last_date})", flush=True)

    # 다음 실행 타이머 등록
    _schedule_next()

    # 메인 스레드 유지
    threading.Event().wait()

if __name__ == "__main__":
    main()