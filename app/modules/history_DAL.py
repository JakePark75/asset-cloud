from app.db import get_db


def load_history(period: str):
    if period == "1m":
        where = "WHERE date >= CURRENT_DATE - INTERVAL '1 month'"
    elif period == "3m":
        where = "WHERE date >= CURRENT_DATE - INTERVAL '3 months'"
    else:
        where = ""

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT date, total_asset, twr_asset, ndx100, cash_flow, cash_flow_note,
                   exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio
            FROM daily_summary
            {where}
            ORDER BY date ASC
        """)
        rows = cur.fetchall()
        cur.close()
    return rows

def calc_twr_pct(rows):
    """
    twr_asset 기준 정규화 수익률(%) 리스트 반환.
    첫 행의 twr_asset을 기준(0%)으로 삼음.
    """
    if not rows:
        return []
    base = float(rows[0][2] or 0)
    if base == 0:
        return [0.0] * len(rows)
    return [(float(r[2] or 0) / base - 1) * 100 for r in rows]


def calc_ndx_pct(rows):
    """
    ndx100 기준 정규화 수익률(%) 리스트 반환.
    첫 행의 ndx100을 기준(0%)으로 삼음.
    """
    if not rows:
        return []
    base = float(rows[0][3] or 0)
    if base == 0:
        return [0.0] * len(rows)
    return [(float(r[3] or 0) / base - 1) * 100 for r in rows]

def save_cash_flow(date_str: str, cash_flow: int, note: str):
    with get_db() as conn:
        cur = conn.cursor()

        # cash_flow, cash_flow_note 저장
        cur.execute("""
            UPDATE daily_summary
               SET cash_flow = %s, cash_flow_note = %s
             WHERE date = %s
        """, (cash_flow, note, date_str))

        # 해당 날짜 포함 이후 모든 행 twr_asset 재계산
        cur.execute("""
            SELECT date, total_asset, cash_flow
            FROM daily_summary
            WHERE date >= %s
            ORDER BY date ASC
        """, (date_str,))
        rows = cur.fetchall()

        # 기준 날짜 이전 마지막 total_asset, twr_asset 조회
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
                denom = prev_total + cf_f
            else:
                denom = float(rows[i-1][1] or 0) + cf_f

            twr = prev_twr * (total_f / denom) if denom != 0 else prev_twr
            prev_twr = twr

            cur.execute("""
                UPDATE daily_summary SET twr_asset = %s WHERE date = %s
            """, (twr, d))

        conn.commit()
        cur.close()