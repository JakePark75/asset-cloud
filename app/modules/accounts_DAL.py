import psycopg2
from app.db import get_connection

def fetch_accounts_summary():
    conn = get_connection()
    cur = conn.cursor()
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
            ), 0) as pnl
        FROM (
            SELECT a.id, a.name, a.alias, p.ticker, p.quantity, pr.current_price, pr.change_pct, pr.market
            FROM accounts a
            LEFT JOIN positions p ON a.id = p.account_id
            LEFT JOIN tickers pr ON p.ticker = pr.ticker
        ) as sub
        GROUP BY id, name, alias
        ORDER BY id
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def fetch_account_details(account_id):
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT name, alias FROM accounts WHERE id = %s", (account_id,))
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