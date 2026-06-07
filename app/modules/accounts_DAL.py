import psycopg2
from app.db import get_connection

def fetch_accounts_summary():
    conn = get_connection()
    cur = conn.cursor()

    # daily_summary 마지막 행 total_asset (어제 자산)
    cur.execute("SELECT total_asset FROM daily_summary ORDER BY date DESC LIMIT 1")
    row = cur.fetchone()
    yesterday_total = float(row[0]) if row else 0.0

    cur.execute("""
        SELECT 
            id, name, alias,
            COALESCE(SUM(
                CASE 
                    WHEN ticker = 'KRW' THEN quantity
                    WHEN ticker = 'USD' THEN quantity * (SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X')
                    ELSE quantity * current_price * (CASE WHEN market IN ('NAS', 'AMS', 'ARC') THEN (SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X') ELSE 1 END)
                END
            ), 0) as total,
            COALESCE(SUM(
                CASE 
                    WHEN ticker = 'KRW' THEN quantity
                    WHEN ticker = 'USD' THEN quantity * (SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X')
                    ELSE 0 
                END
            ), 0) as cash,
            COALESCE(SUM(
                CASE 
                    WHEN ticker NOT IN ('KRW', 'USD') THEN (quantity * current_price * (CASE WHEN market IN ('NAS', 'AMS', 'ARC') THEN (SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X') ELSE 1 END)) * (change_pct / 100)
                    ELSE 0 
                END
            ), 0) as pnl,
            is_watch
        FROM (
            SELECT a.id, a.name, a.alias, a.is_watch, p.ticker, p.quantity, pr.current_price, pr.change_pct, pr.market
            FROM accounts a
            LEFT JOIN positions p ON a.id = p.account_id
            LEFT JOIN tickers pr ON p.ticker = pr.ticker
        ) as sub
        GROUP BY id, name, alias, is_watch
        ORDER BY id
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # 반환 형식: (id, name, alias, total, cash, pnl, is_watch)
    result = []
    for r in rows:
        result.append((r[0], r[1], r[2], float(r[3]), float(r[4]), float(r[5]), r[6]))
    return result, yesterday_total

def fetch_account_details(account_id):
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT name, alias, is_watch FROM accounts WHERE id = %s", (account_id,))
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
