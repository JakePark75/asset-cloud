from shiny import ui
from app.modules.components import fmt_krw, fmt_usd, fmt_pct, fmt_pnl, fmt_change
from scheduler.price_updater import get_market_status


def render_asset_card(acc, ns):
    """메인 화면의 각 계좌 카드 UI"""
    a_id, name, alias, total, cash, pnl = acc
    invest = total - cash
    pnl_pct = (pnl / invest * 100) if invest > 0 else 0
    pnl_text, pnl_class = fmt_pnl(pnl, pnl_pct)

    return ui.div(
        ui.div(
            ui.strong(name),
            ui.span(f" ({alias})" if alias else "", class_="account-alias"),
        ),
        ui.div(
            ui.div(fmt_krw(total), class_="amount-large"),
            ui.div(
                ui.span("일간손익 ", class_="card-pnl-label"),
                ui.span(pnl_text, class_=pnl_class),
                ui.span(f"현금 {fmt_krw(cash)}", class_="card-cash-label"),
                class_="card-pnl-row",
            ),
        ),
        class_="asset-card",
        onclick=f"Shiny.setInputValue('{ns('selected_id')}', {a_id}, {{priority: 'event'}});",
    )


def render_ticker_row(pos, usd_rate):
    """
    계좌 상세 / 포트폴리오 공통 종목 행 UI (순수 디자인).
    클릭 이벤트는 호출하는 쪽에서 감싸서 처리.

    pos 튜플: (pos_id, ticker, qty, name, price, chg_pct, market, leverage)
    """
    pos_id, ticker, qty, tname, price, chg_pct, t_market, leverage = pos
    is_cash = ticker in ('KRW', 'USD')
    leverage = int(leverage) if leverage else 1
    qty_f = float(qty or 0)
    price_f = float(price or 0)
    chg_f = float(chg_pct or 0)

    # 상태 배지 (현금 제외)
    status_dot = status_text = status_class = None
    if not is_cash and t_market:
        status = get_market_status(t_market)
        if status == "open":
            status_dot, status_text, status_class = "●", "장중", "status-open"
        elif status == "pre":
            status_dot, status_text, status_class = "●", "프리", "status-pre"
        elif status == "after":
            status_dot, status_text, status_class = "●", "애프터", "status-after"
        else:
            status_dot, status_text, status_class = "○", "휴장", "status-closed"

    if ticker == 'KRW':
        display_name = "현금(KRW)"
        amount_str = fmt_krw(qty_f)
        qty_str = ""
        price_str = chg_str = chg_css = ""

    elif ticker == 'USD':
        display_name = "현금(USD)"
        amount_str = fmt_krw(qty_f * usd_rate)
        qty_str = fmt_usd(qty_f)
        price_str = ""
        chg_str = fmt_pct(chg_f)
        chg_css = "positive" if chg_f >= 0 else "negative"

    else:
        rate = usd_rate if t_market in ('NAS', 'AMS', 'ARC') else 1
        currency = "USD" if t_market in ('NAS', 'AMS', 'ARC') else "KRW"
        amount_str = fmt_krw(qty_f * price_f * rate)
        qty_str = f"{qty_f:g}주"
        display_name = tname or ticker
        price_str, chg_str, chg_css = fmt_change(price_f, chg_f, currency=currency)

    return ui.div(
        ui.div(
            ui.div(
                ui.span(f"x{leverage}", class_=f"lev-badge lev-x{leverage}") if leverage > 1 else None,
                ui.span(display_name, class_="ticker-name"),
                ui.span(f"{status_dot} {status_text}", class_=f"ticker-status {status_class}") if status_dot else None,
                class_="lev-name-wrap",
            ),
            ui.div(qty_str, class_="ticker-qty"),
        ),
        ui.div(
            ui.div(amount_str, class_="ticker-amount"),
            ui.div(
                ui.span(price_str, class_ = chg_css, style="margin-right:4px;") if price_str else None,
                ui.span(chg_str, class_=chg_css),
                class_="ticker-change",
            ) if chg_str else ui.div(),
        ),
        class_="ticker-row",
    )