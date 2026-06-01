from shiny import ui

def render_asset_card(acc, ns):
    """메인 화면의 각 계좌 카드 UI"""
    a_id, name, alias, total, cash, pnl = acc
    invest = total - cash
    pnl_pct = (pnl / invest * 100) if invest > 0 else 0
    pnl_class = "positive" if pnl >= 0 else "negative"
    triangle = "▲" if pnl >= 0 else "▼"
    pnl_text = f"{triangle}{int(pnl):,}원 ({pnl_pct:.2f}%)"
    
    return ui.div(
        ui.div(
            ui.strong(name),
            ui.span(f" ({alias})" if alias else "", class_="account-alias"),
        ),
        ui.div(
            ui.div(f"{int(total):,}원", class_="amount-large"),
            ui.div(
                ui.span("일간손익 ", class_="card-pnl-label"),
                ui.span(pnl_text, class_=pnl_class),
                ui.span(f"현금 {int(cash):,}원", class_="card-cash-label"),
                class_="card-pnl-row",
            ),
        ),
        class_="asset-card",
        onclick=f"Shiny.setInputValue('{ns('selected_id')}', {a_id}, {{priority: 'event'}});",
    )

def render_ticker_row(pos, usd_rate, ns):
    """계좌 상세 화면의 각 종목 줄 UI"""
    pos_id, ticker, qty, tname, price, chg_pct, t_market, leverage = pos
    is_cash = ticker in ('KRW', 'USD')
    leverage = int(leverage) if leverage else 1
    
    if is_cash:
        display_name = "현금(KRW)" if ticker == "KRW" else "현금(USD)"
        amount = float(qty) * (usd_rate if ticker == "USD" else 1)
        amount_str = f"{int(amount):,}원"
        qty_str, chg_str, chg_class = "", "", ""
    else:
        rate = usd_rate if t_market in ('NAS', 'AMS', 'ARC') else 1
        amount = float(qty) * float(price or 0) * rate
        chg = float(chg_pct or 0)
        chg_class = "positive" if chg >= 0 else "negative"
        chg_str = f"{'+' if chg >= 0 else ''}{chg:.2f}%"
        display_name = tname or ticker
        amount_str = f"{int(amount):,}원"
        qty_str = f"{qty:g}주"

    return ui.div(
        ui.div(
            ui.div(
                ui.span(f"x{leverage}", class_=f"lev-badge lev-x{leverage}") if leverage > 1 else None,
                ui.span(display_name, class_="ticker-name"),
                class_="lev-name-wrap",
            ),
            ui.div(qty_str, class_="ticker-qty"),
        ),
        ui.div(
            ui.div(amount_str, class_="ticker-amount"),
            ui.div(chg_str, class_=f"ticker-change {chg_class}"),
        ),
        class_="ticker-row",
        onclick=f"Shiny.setInputValue('{ns('edit_pos_id')}', {pos_id}, {{priority: 'event'}});",
    )