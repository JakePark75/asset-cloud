from app.db import get_db


# ── 종목 순서 ─────────────────────────────────────────────────────────────────

def update_ticker_order(ordered_tickers: list[str]):
    """
    드래그 앤 드롭으로 결정된 종목 순서를 tickers.sort_order에 반영.
    ordered_tickers: 화면에 표시된 순서 그대로의 ticker 리스트
    (현금(KRW/USD)과 감시종목은 드래그 대상이 아니므로 이 리스트에 포함되지 않음).
    """
    with get_db() as conn:
        cur = conn.cursor()
        for order, ticker in enumerate(ordered_tickers):
            cur.execute(
                "UPDATE tickers SET sort_order = %s WHERE ticker = %s",
                (order, ticker)
            )
        conn.commit()
        cur.close()