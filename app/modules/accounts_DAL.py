import psycopg2
from app.db import get_connection

def fetch_accounts_summary():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            a.id, a.name, a.alias,
            COALESCE(SUM(
                CASE
                    WHEN p.ticker = 'KRW' THEN p.quantity
                    WHEN p.ticker = 'USD' THEN p.quantity * (SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X')
                    ELSE p.quantity * pr.current_price * (CASE WHEN pr.market IN ('NAS', 'AMS', 'ARC') THEN (SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X') ELSE 1 END)
                END
            ), 0) as total,
            COALESCE(SUM(
                CASE
                    WHEN p.ticker = 'KRW' THEN p.quantity
                    WHEN p.ticker = 'USD' THEN p.quantity * (SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X')
                    ELSE 0
                END
            ), 0) as cash,
            a.is_watch,
            COALESCE(a.prev_total_asset, 0) as prev_total
        FROM accounts a
        LEFT JOIN positions p ON a.id = p.account_id
        LEFT JOIN tickers pr ON p.ticker = pr.ticker
        GROUP BY a.id, a.name, a.alias, a.is_watch, a.prev_total_asset
        ORDER BY a.id
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # 반환 형식: (id, name, alias, total, cash, is_watch, prev_total)
    result = []
    for r in rows:
        result.append((r[0], r[1], r[2], float(r[3]), float(r[4]), r[5], float(r[6])))
    return result

def fetch_account_details(account_id):
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT name, alias, is_watch, COALESCE(prev_total_asset, 0) FROM accounts WHERE id = %s", (account_id,))
    acc = cur.fetchone()
    
    cur.execute("""
        SELECT p.id, p.ticker, p.quantity, pr.name, pr.current_price, pr.change_pct, pr.market, pr.leverage
        FROM positions p
        LEFT JOIN tickers pr ON p.ticker = pr.ticker
        WHERE p.account_id = %s
        ORDER BY
            CASE WHEN p.ticker IN ('KRW','USD') THEN 1 ELSE 0 END,
            CASE WHEN pr.market = 'KR' THEN 0 WHEN pr.market IN ('NAS', 'AMS', 'ARC') THEN 1 WHEN pr.market = 'CRYPTO' THEN 2 ELSE 3 END,
            pr.leverage DESC NULLS LAST,
            p.quantity * COALESCE(pr.current_price, 0) DESC,
            p.ticker
    """, (account_id,))
    positions = cur.fetchall()
    
    cur.execute("SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X'")
    result = cur.fetchone()
    usd_rate = float(result[0]) if result else 1.0
    
    cur.close()
    conn.close()
    return acc, positions, usd_rate