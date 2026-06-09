import psycopg2
from app.db import get_connection, get_market_map

def fetch_accounts_summary():
    conn = get_connection()
    cur = conn.cursor()

    # config의 market_map에서 USD 통화 마켓 목록을 동적으로 추출.
    # 새 마켓 추가 시 config.json만 수정하면 자동 반영됨.
    # psycopg2는 튜플을 SQL IN절 값으로 자동 변환함 (예: ('NAS','AMS','ARC',...))
    usd_markets = tuple(
        m for m, v in get_market_map().items() if v.get("currency") == "USD"
    )

    cur.execute("""
        SELECT
            a.id, a.name, a.alias,
            COALESCE(SUM(
                CASE
                    WHEN p.ticker = 'KRW' THEN p.quantity
                    WHEN p.ticker = 'USD' THEN p.quantity * (SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X')
                    -- USD 마켓 종목은 현재가에 환율 곱함 (usd_markets는 Python에서 동적으로 주입)
                    ELSE p.quantity * pr.current_price * (CASE WHEN pr.market IN %s THEN (SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X') ELSE 1 END)
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
    """, (usd_markets,))
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

    # USD 마켓 목록 동적 추출 (정렬 기준에 사용)
    usd_markets = tuple(
        m for m, v in get_market_map().items() if v.get("currency") == "USD"
    )

    cur.execute("SELECT name, alias, is_watch, COALESCE(prev_total_asset, 0) FROM accounts WHERE id = %s", (account_id,))
    acc = cur.fetchone()

    cur.execute("""
        SELECT p.id, p.ticker, p.quantity, pr.name, pr.current_price, pr.change_pct, pr.market, pr.leverage
        FROM positions p
        LEFT JOIN tickers pr ON p.ticker = pr.ticker
        WHERE p.account_id = %s
        ORDER BY
            CASE WHEN p.ticker IN ('KRW','USD') THEN 1 ELSE 0 END,
            -- KR=0, USD마켓=1, CRYPTO=2, 나머지=3 (usd_markets 동적 주입)
            CASE WHEN pr.market = 'KR' THEN 0 WHEN pr.market IN %s THEN 1 WHEN pr.market = 'CRYPTO' THEN 2 ELSE 3 END,
            pr.leverage DESC NULLS LAST,
            p.quantity * COALESCE(pr.current_price, 0) DESC,
            p.ticker
    """, (account_id, usd_markets))
    positions = cur.fetchall()

    cur.execute("SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X'")
    result = cur.fetchone()
    usd_rate = float(result[0]) if result else 1.0

    cur.close()
    conn.close()
    return acc, positions, usd_rate