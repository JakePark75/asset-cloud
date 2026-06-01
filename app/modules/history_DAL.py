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
