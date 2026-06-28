# 필드명 축약 매핑
# dt=date, ta=total_asset, tw=twr_asset, nx=ndx100,
# cf=cash_flow, cn=cash_flow_note, ex=exposure, cr=cash_ratio,
# x1=x1_ratio, x2=x2_ratio, x3=x3_ratio, ur=usd_krw,
# np=ndx_change_pct, tp=twr_change_pct

import json
import datetime
from app.db import get_db


def load_history():
    """DB에서 과거 rows만 반환. today_row는 포함하지 않음."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT date, total_asset, twr_asset, ndx100, cash_flow, cash_flow_note,
                   exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio, usd_krw
            FROM daily_summary
            ORDER BY date ASC
        """)
        rows = cur.fetchall()
        cur.close()
    return rows


def load_today_row() -> dict | None:
    """Redis에서 today_row만 조회. 없으면 None."""
    try:
        from common.redis_store import get_redis
        r = get_redis()
        if not r:
            return None
        raw = r.get("today_row")
        if not raw:
            return None
        t = json.loads(raw)
        from zoneinfo import ZoneInfo
        KST = ZoneInfo("Asia/Seoul")
        today_kst = datetime.datetime.now(KST).date()
        t["date"] = str(today_kst)
        return t
    except Exception as e:
        print(f"[history_DAL] load_today_row 실패 (무시): {e}")
        return None


def build_today_row(t: dict, rows: list) -> dict:
    """
    today_row dict와 DB rows를 받아 JS 전송용 row dict 구성.
    값은 표시 단위로 라운딩 — 표시값 변화 없는 tick은 diff_display가 전송을 스킵.

    라운딩 기준 (JS 표시 단위):
      total_asset / twr_asset / prev_total : 만 단위 반올림 (fmtKrw2 기준)
      twr_pct / ndx_pct / twr_change_pct / ndx_change_pct : 소수점 2자리
      exposure / cash_ratio / x1~x3_ratio : 소수점 3자리 (표시 1자리 충분)
      ndx100 / usd_krw : 소수점 2자리
    """
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
    today = datetime.datetime.now(KST).date()

    prev     = round(float(rows[-1][1] or 0) / 10000) * 10000 if rows else None
    prev_ndx = float(rows[-1][3] or 0) if rows else None
    prev_twr = float(rows[-1][2] or 0) if rows else None

    twr_pct = 0.0
    ndx_pct = 0.0
    if rows:
        base_twr = float(rows[0][2] or 0)
        base_ndx = float(rows[0][3] or 0)
        if base_twr:
            twr_pct = (float(t.get("twr_asset") or 0) / base_twr - 1) * 100
        if base_ndx:
            ndx_pct = (float(t.get("ndx100") or 0) / base_ndx - 1) * 100

    cur_ndx        = float(t.get("ndx100") or 0)
    cur_twr        = float(t.get("twr_asset") or 0)
    ndx_change_pct = (cur_ndx - prev_ndx) / prev_ndx * 100 if prev_ndx and cur_ndx else None
    twr_change_pct = (cur_twr - prev_twr) / prev_twr * 100 if prev_twr and cur_twr else None

    def _r10k(v):
        """만 단위 반올림"""
        return round(float(v or 0) / 10000) * 10000

    def _r2(v):
        return round(float(v or 0), 2)

    def _r3(v):
        return round(float(v or 0), 3)

    return {
        "dt":      str(t.get("date", str(today))),
        "ta":      _r10k(t.get("total_asset")),
        "tw":      _r10k(t.get("twr_asset")),
        "nx":      _r2(t.get("ndx100")),
        "cf":      str(t.get("cash_flow", 0)),
        "cn":      t.get("cash_flow_note") or '',
        "ex":      _r2(t.get("exposure")),
        "cr":      _r2(t.get("cash_ratio")),
        "x1":      _r2(t.get("x1_ratio")),
        "x2":      _r2(t.get("x2_ratio")),
        "x3":      _r2(t.get("x3_ratio")),
        "ur":      _r2(t.get("usd_krw")),
        "tp":      round(twr_change_pct, 2) if twr_change_pct is not None else '',
        "np":      round(ndx_change_pct, 2) if ndx_change_pct is not None else '',
        "twr_pct": round(twr_pct, 2),
        "ndx_pct": round(ndx_pct, 2),
    }


def build_history_rows(rows: list, today_row_tuple=None) -> list:
    """
    DB rows (+ 선택적 today_row_tuple)를 JS history_data 전송용 dict 리스트로 변환.
    - 값은 표시 단위로 라운딩 (불필요한 정밀도 제거)
    - None 값은 '' 로 변환 (JS에서 표시 생략)

    라운딩 기준:
      total_asset / twr_asset / prev_total : 만 단위 반올림
      ndx100 / usd_krw                     : 소수점 2자리
      exposure / cash_ratio / x1~x3_ratio  : 소수점 3자리 (표시 1자리)
      change_pct 류                         : 소수점 2자리
    """
    import datetime
    from zoneinfo import ZoneInfo

    def _r10k(v):
        try:
            return round(float(v) / 10000) * 10000
        except (TypeError, ValueError):
            return ''

    def _r2(v):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return ''

    def _r3(v):
        try:
            return round(float(v), 3)
        except (TypeError, ValueError):
            return ''

    today = datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()
    index_map = {r[0]: i for i, r in enumerate(rows)}

    rows_desc = []
    if today_row_tuple is not None:
        rows_desc.append(today_row_tuple)
    rows_desc += list(reversed(rows))

    data = []
    for r in rows_desc:
        is_today = (r[0] == today)
        if is_today:
            prev     = float(rows[-1][1] or 0) if rows else None
            prev_ndx = float(rows[-1][3] or 0) if rows else None
            prev_twr = float(rows[-1][2] or 0) if rows else None
        else:
            idx      = index_map.get(r[0])
            prev     = float(rows[idx - 1][1] or 0) if idx is not None and idx > 0 else None
            prev_ndx = float(rows[idx - 1][3] or 0) if idx is not None and idx > 0 else None
            prev_twr = float(rows[idx - 1][2] or 0) if idx is not None and idx > 0 else None

        cur_ndx = float(r[3] or 0)
        cur_twr = float(r[2] or 0)
        ndx_change_pct = round((cur_ndx - prev_ndx) / prev_ndx * 100, 2) if prev_ndx and cur_ndx else ''
        twr_change_pct = round((cur_twr - prev_twr) / prev_twr * 100, 2) if prev_twr and cur_twr else ''

        data.append({
            "dt": str(r[0]),
            "ta": _r10k(r[1]),
            "tw": _r10k(r[2]),
            "nx": _r2(r[3]),
            "cf": str(int(float(r[4] or 0))),
            "cn": r[5] or '',
            "ex": _r2(r[6]),
            "cr": _r2(r[7]),
            "x1": _r2(r[8]),
            "x2": _r2(r[9]),
            "x3": _r2(r[10]),
            "ur": _r2(r[11]),
            "np": ndx_change_pct,
            "tp": twr_change_pct,
        })

    return data


def calc_twr_pct(rows):
    if not rows:
        return []
    base = float(rows[0][2] or 0)
    if base == 0:
        return [0.0] * len(rows)
    return [(float(r[2] or 0) / base - 1) * 100 for r in rows]


def calc_ndx_pct(rows):
    if not rows:
        return []
    base = float(rows[0][3] or 0)
    if base == 0:
        return [0.0] * len(rows)
    return [(float(r[3] or 0) / base - 1) * 100 for r in rows]


def save_cash_flow(date_str: str, cash_flow: int, note: str):
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("""
            UPDATE daily_summary
               SET cash_flow = %s, cash_flow_note = %s
             WHERE date = %s
        """, (cash_flow, note, date_str))

        cur.execute("""
            SELECT date, total_asset, cash_flow
            FROM daily_summary
            WHERE date >= %s
            ORDER BY date ASC
        """, (date_str,))
        rows = cur.fetchall()

        cur.execute("""
            SELECT total_asset, twr_asset FROM daily_summary
            WHERE date < %s
            ORDER BY date DESC LIMIT 1
        """, (date_str,))
        prev = cur.fetchone()
        prev_twr = float(prev[1]) if prev else float(rows[0][1] or 0)
        prev_total = float(prev[0]) if prev else float(rows[0][1] or 0)

        for i, (d, total_asset, cf) in enumerate(rows):
            total_f = float(total_asset or 0)
            cf_f = float(cf or 0)

            if i == 0:
                denom = prev_total
            else:
                denom = float(rows[i-1][1] or 0)

            twr = prev_twr * ((total_f - cf_f) / denom) if denom != 0 else prev_twr
            prev_twr = twr

            cur.execute("""
                UPDATE daily_summary SET twr_asset = %s WHERE date = %s
            """, (twr, d))

        conn.commit()
        cur.close()