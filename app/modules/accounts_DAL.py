from app.db import get_db, get_usd_krw, get_market_map, get_market_currency


# ── DAL ───────────────────────────────────────────────────────────────────────

def fetch_accounts_summary():
    # ---------------------------------------------------------------------------
    # Step 6-4: current_price 조회를 DB → Redis 전환
    #   - 시세(current_price)  : Redis get_usd_krw() / get_all_prices() 경유
    #   - 메타데이터(market 등) : DB 유지
    #   - usd_rate             : get_usd_krw() 경유 (db.py에서 이미 Redis 읽음)
    #   - DB 연결              : get_connection() 직접 호출 → get_db() 컨텍스트 매니저로 교체
    #   - SQL 내 서브쿼리      : Python에서 Redis 읽어 계산으로 재작성
    # ---------------------------------------------------------------------------
    from common.redis_store import get_all_prices

    # Redis 전체 시세 로드 (실패 시 빈 dict → 가격 0 처리)
    prices = get_all_prices()

    # usd_rate: db.py get_usd_krw()가 내부적으로 Redis 읽음 (change_pct 불필요하므로 무시)
    usd_rate, _ = get_usd_krw()
    usd_rate = float(usd_rate or 0)

    # USD 통화 마켓 목록 동적 추출 (config.json 기반 — 새 마켓 추가 시 자동 반영)
    usd_markets = {m for m, v in get_market_map().items() if v.get("currency") == "USD"}

    with get_db() as conn:
        cur = conn.cursor()

        # current_price 제거 — Redis에서 계산
        # 메타데이터(market)와 수량만 조회
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

    # 계좌별로 집계 — SQL CASE 계산을 Python으로 재현
    # 집계 구조: {account_id: {id, name, alias, is_watch, prev_total, total, cash}}
    accounts = {}
    for acc_id, name, alias, is_watch, prev_total, ticker, qty, market in db_rows:
        if acc_id not in accounts:
            accounts[acc_id] = {
                "id": acc_id, "name": name, "alias": alias,
                "is_watch": is_watch, "prev_total": float(prev_total),
                "total": 0.0, "cash": 0.0,
            }
        if ticker is None or qty is None:
            continue

        qty_f = float(qty)
        p_data = prices.get(ticker)
        price = float(p_data["price"]) if p_data else 0.0

        # 평가액 계산 (fetch_account_details, portfolio.py와 동일 로직)
        if ticker == "KRW":
            amount = qty_f                          # 원화 현금
            accounts[acc_id]["cash"] += amount
        elif ticker == "USD":
            amount = qty_f * usd_rate               # 달러 현금 → 원화 환산
            accounts[acc_id]["cash"] += amount
        elif market in usd_markets:
            amount = qty_f * price * usd_rate       # 해외 종목 → 원화 환산
        else:
            amount = qty_f * price                  # 국내 종목

        accounts[acc_id]["total"] += amount

    # 반환 형식: (id, name, alias, total, cash, is_watch, prev_total)
    return [
        (v["id"], v["name"], v["alias"], v["total"], v["cash"], v["is_watch"], v["prev_total"])
        for v in accounts.values()
    ]


def fetch_account_details(account_id):
    # ---------------------------------------------------------------------------
    # Step 6-4: current_price / change_pct 조회를 DB → Redis 전환
    #   - 시세(current_price, change_pct) : Redis get_all_prices() 매핑
    #   - 메타데이터(name, market, leverage) : DB 유지
    #   - usd_rate                         : get_usd_krw() 경유 (db.py에서 이미 Redis 읽음)
    #   - ORDER BY 평가액 기준             : SQL → Python 정렬로 이전 (Redis 가격 사용)
    # ---------------------------------------------------------------------------
    from common.redis_store import get_all_prices

    # Redis 전체 시세 로드 (실패 시 빈 dict → 가격 0 처리)
    prices = get_all_prices()

    # usd_rate: db.py get_usd_krw()가 내부적으로 Redis 읽음
    usd_rate, _ = get_usd_krw()
    usd_rate = float(usd_rate or 1.0)

    # USD 통화 마켓 목록 동적 추출
    usd_markets = {m for m, v in get_market_map().items() if v.get("currency") == "USD"}

    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            "SELECT name, alias, is_watch, COALESCE(prev_total_asset, 0) FROM accounts WHERE id = %s",
            (account_id,)
        )
        acc = cur.fetchone()

        # current_price / change_pct 제거 — Redis에서 매핑
        # 메타데이터(name, market, leverage)와 수량만 조회
        # 정렬은 Python에서 처리하므로 ORDER BY 평가액 기준 제거
        cur.execute("""
            SELECT p.id, p.ticker, p.quantity, pr.name, pr.market, pr.leverage
            FROM positions p
            LEFT JOIN tickers pr ON p.ticker = pr.ticker
            WHERE p.account_id = %s
        """, (account_id,))
        db_rows = cur.fetchall()
        cur.close()

    # Redis 시세를 매핑하여 호출부가 기대하는 튜플 구조로 재구성
    # 반환 형태: (pos_id, ticker, qty, name, price, change_pct, market, leverage)
    positions_raw = []
    for pos_id, ticker, qty, name, market, leverage in db_rows:
        p_data     = prices.get(ticker)
        price      = float(p_data["price"])      if p_data else 0.0
        change_pct = float(p_data["change_pct"]) if p_data else 0.0
        positions_raw.append((pos_id, ticker, qty, name, price, change_pct, market, leverage))

    # Python 정렬 — 기존 SQL ORDER BY 로직과 동일하게 재현
    #   1순위: 현금(KRW/USD) 후순위
    #   2순위: 마켓 순서 (KR=0, USD마켓=1, CRYPTO=2, 나머지=3)
    #   3순위: leverage DESC
    #   4순위: 평가액 DESC (Redis 가격 기반)
    #   5순위: ticker ASC
    _MARKET_ORDER = {"KR": 0, "CRYPTO": 2}

    def _sort_key(row):
        pos_id, ticker, qty, name, price, change_pct, market, leverage = row
        qty_f = float(qty or 0)

        # 평가액 계산 (fetch_accounts_summary와 동일 로직)
        if ticker == "KRW":
            amount = qty_f
        elif ticker == "USD":
            amount = qty_f * usd_rate
        elif market in usd_markets:
            amount = qty_f * price * usd_rate
        else:
            amount = qty_f * price

        if market in usd_markets:
            market_order = 1
        else:
            market_order = _MARKET_ORDER.get(market, 3)

        return (
            1 if ticker in ("KRW", "USD") else 0,   # 현금 후순위
            market_order,                             # 마켓 순서
            -(leverage or 1),                         # leverage DESC
            -amount,                                  # 평가액 DESC
            ticker,                                   # ticker ASC
        )

    positions = sorted(positions_raw, key=_sort_key)

    return acc, positions, usd_rate