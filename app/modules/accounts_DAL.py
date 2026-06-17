from app.db import get_db, get_usd_krw, get_market_map, get_market_currency


# ── DAL ───────────────────────────────────────────────────────────────────────

def fetch_accounts_summary():
    """
    DB 구조만 반환. 시세 계산 없음.
    반환: [(acc_id, name, alias, is_watch, prev_total, [(ticker, qty, market), ...]), ...]
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                a.id, a.name, a.alias, a.is_watch,
                COALESCE(a.prev_total_asset, 0) AS prev_total,
                p.ticker, p.quantity,
                pr.market
            FROM accounts a
            LEFT JOIN positions p ON a.id = p.account_id
            LEFT JOIN tickers pr ON p.ticker = pr.ticker
            ORDER BY a.id
        """)
        db_rows = cur.fetchall()
        cur.close()

    accounts = {}
    for acc_id, name, alias, is_watch, prev_total, ticker, qty, market in db_rows:
        if acc_id not in accounts:
            accounts[acc_id] = {
                "id": acc_id, "name": name, "alias": alias,
                "is_watch": is_watch, "prev_total": float(prev_total),
                "positions": [],
            }
        if ticker is not None and qty is not None:
            accounts[acc_id]["positions"].append((ticker, float(qty), market))

    return list(accounts.values())


def calc_accounts_summary(db_accounts, prices, usd_rate):
    """
    DB 구조 + Redis 시세로 계좌 요약 계산.
    반환: [(id, name, alias, total, cash, is_watch, prev_total), ...]
    """
    usd_markets = {m for m, v in get_market_map().items() if v.get("currency") == "USD"}
    usd_rate_f  = float(usd_rate or 0)

    result = []
    for acc in db_accounts:
        total = 0.0
        cash  = 0.0
        for ticker, qty_f, market in acc["positions"]:
            p_data = prices.get(ticker)
            price  = float(p_data["price"]) if p_data else 0.0

            if ticker == "KRW":
                amount = qty_f
                cash  += amount
            elif ticker == "USD":
                amount = qty_f * usd_rate_f
                cash  += amount
            elif market in usd_markets:
                amount = qty_f * price * usd_rate_f
            else:
                amount = qty_f * price

            total += amount

        result.append((
            acc["id"], acc["name"], acc["alias"],
            total, cash, acc["is_watch"], acc["prev_total"],
        ))

    return result


def fetch_account_details(account_id):
    """
    DB 구조만 반환. 시세 계산 없음.
    반환: (acc_row, db_positions)
      acc_row: (name, alias, is_watch, prev_total)
      db_positions: [(pos_id, ticker, qty, name, market, leverage, avg_price), ...]
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT name, alias, is_watch, COALESCE(prev_total_asset, 0) FROM accounts WHERE id = %s",
            (account_id,)
        )
        acc = cur.fetchone()

        cur.execute("""
            SELECT p.id, p.ticker, p.quantity, pr.name, pr.market, pr.leverage, p.avg_price
            FROM positions p
            LEFT JOIN tickers pr ON p.ticker = pr.ticker
            WHERE p.account_id = %s
        """, (account_id,))
        db_rows = cur.fetchall()
        cur.close()

    return acc, db_rows


def calc_account_details(acc, db_rows, prices, usd_rate):
    """
    DB rows + Redis 시세로 포지션 계산.
    반환: (acc, positions, usd_rate)
      positions: [(pos_id, ticker, qty, name, price, change_pct, market, leverage, avg_price), ...]
    """
    usd_rate_f  = float(usd_rate or 1.0)
    usd_markets = {m for m, v in get_market_map().items() if v.get("currency") == "USD"}

    positions_raw = []
    for pos_id, ticker, qty, name, market, leverage, avg_price in db_rows:
        p_data     = prices.get(ticker)
        price      = float(p_data["price"])      if p_data else 0.0
        change_pct = float(p_data["change_pct"]) if p_data else 0.0
        positions_raw.append((pos_id, ticker, qty, name, price, change_pct, market, leverage, avg_price))

    _MARKET_ORDER = {"KR": 0, "CRYPTO": 2}

    def _sort_key(row):
        pos_id, ticker, qty, name, price, change_pct, market, leverage, avg_price = row
        qty_f = float(qty or 0)

        if ticker == "KRW":
            amount = qty_f
        elif ticker == "USD":
            amount = qty_f * usd_rate_f
        elif market in usd_markets:
            amount = qty_f * price * usd_rate_f
        else:
            amount = qty_f * price

        if market in usd_markets:
            market_order = 1
        else:
            market_order = _MARKET_ORDER.get(market, 3)

        return (
            1 if ticker in ("KRW", "USD") else 0,
            market_order,
            -(leverage or 1),
            -amount,
            ticker,
        )

    positions = sorted(positions_raw, key=_sort_key)
    return acc, positions, usd_rate_f


# ── 매수 ──────────────────────────────────────────────────────────────────────

def execute_buy(pos_id: int, qty_delta: float, trade_price: float, usd_markets: set):
    """
    매수 처리:
    - positions.quantity += qty_delta
    - positions.avg_price 재계산 (가중평균)
    - 해당 계좌의 현금(KRW 또는 USD) positions.quantity -= 매수금액
    trade_price: 원천 통화 단가 (KR→KRW, US→USD)
    """
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT p.quantity, p.avg_price, p.account_id, t.market
            FROM positions p
            LEFT JOIN tickers t ON p.ticker = t.ticker
            WHERE p.id = %s
        """, (pos_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"position id={pos_id} not found")

        cur_qty, cur_avg, account_id, market = row
        cur_qty = float(cur_qty or 0)
        cur_avg = float(cur_avg or 0)

        new_qty = cur_qty + qty_delta
        if new_qty > 0:
            new_avg = ((cur_qty * cur_avg) + (qty_delta * trade_price)) / new_qty
        else:
            new_avg = 0.0

        cur.execute(
            "UPDATE positions SET quantity = %s, avg_price = %s WHERE id = %s",
            (new_qty, new_avg, pos_id)
        )

        cash_ticker  = "USD" if market in usd_markets else "KRW"
        trade_amount = qty_delta * trade_price

        cur.execute("""
            UPDATE positions SET quantity = quantity - %s
            WHERE account_id = %s AND ticker = %s
        """, (trade_amount, account_id, cash_ticker))

        conn.commit()
        cur.close()


# ── 매도 ──────────────────────────────────────────────────────────────────────

def execute_sell(pos_id: int, qty_delta: float, trade_price: float, usd_markets: set):
    """
    매도 처리:
    - positions.quantity -= qty_delta
    - avg_price 변동 없음
    - 해당 계좌의 현금(KRW 또는 USD) positions.quantity += 매도금액
    trade_price: 원천 통화 단가
    """
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT p.quantity, p.account_id, t.market
            FROM positions p
            LEFT JOIN tickers t ON p.ticker = t.ticker
            WHERE p.id = %s
        """, (pos_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"position id={pos_id} not found")

        cur_qty, account_id, market = row
        cur_qty = float(cur_qty or 0)

        if qty_delta > cur_qty:
            raise ValueError(f"매도 수량({qty_delta})이 보유 수량({cur_qty})을 초과합니다")

        new_qty = cur_qty - qty_delta

        cur.execute(
            "UPDATE positions SET quantity = %s WHERE id = %s",
            (new_qty, pos_id)
        )

        cash_ticker  = "USD" if market in usd_markets else "KRW"
        trade_amount = qty_delta * trade_price

        cur.execute("""
            UPDATE positions SET quantity = quantity + %s
            WHERE account_id = %s AND ticker = %s
        """, (trade_amount, account_id, cash_ticker))

        conn.commit()
        cur.close()