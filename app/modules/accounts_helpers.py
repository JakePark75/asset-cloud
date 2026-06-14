from app.db import get_market_currency
from app.modules.components import fmt_krw, fmt_usd, fmt_pct, fmt_pnl, fmt_change
from scheduler.price_updater_common import get_market_status


def _ticker_to_id(ticker: str) -> str:
    return ticker.replace("-", "_").replace("^", "_").replace("=", "_")


# ── 계좌 카드 ─────────────────────────────────────────────────────────────────

def _build_account_card_skeleton(acc, ns_str):
    """계좌 카드 골격 HTML — 구성 변경 시 1회 전송"""
    a_id, name, alias, total, cash, is_watch, prev_total = acc
    alias_str = f" ({alias})" if alias else ""
    return (
        f'<div class="asset-card" id="ac-card-{a_id}" '
        f'onclick="Shiny.setInputValue(\'{ns_str}card_clicked\', {a_id}, {{priority: \'event\'}});">'
        f'  <div>'
        f'    <span class="ticker-name">{name}</span>'
        f'    <span class="account-alias">{alias_str}</span>'
        f'  </div>'
        f'  <div>'
        f'    <div class="amount-large" id="ac-card-total-{a_id}"></div>'
        f'    <div class="card-pnl-row">'
        f'      <span id="ac-card-pnl-{a_id}" class="summary-delta"></span>'
        f'      <span class="card-cash-label">현금 <span id="ac-card-cash-{a_id}"></span></span>'
        f'    </div>'
        f'  </div>'
        f'</div>'
    )


def _build_account_card_values(acc):
    """계좌 카드 가변값 dict — 매 tick diff 비교용"""
    a_id, name, alias, total, cash, is_watch, prev_total = acc
    pnl = total - prev_total
    pnl_pct = (pnl / prev_total * 100) if prev_total > 0 else 0
    pnl_text, pnl_class = fmt_pnl(pnl, pnl_pct)
    return {
        "id":        a_id,
        "total":     fmt_krw(total),
        "pnl_text":  pnl_text,
        "pnl_class": pnl_class,
        "cash":      fmt_krw(cash),
    }


# ── 종목 행 ───────────────────────────────────────────────────────────────────

def _build_position_row_skeleton(pos, ns_str):
    """종목 행 골격 HTML — 구성 변경 시 1회 전송"""
    pos_id, ticker, qty, tname, price, chg_pct, t_market, leverage, avg_price = pos
    is_cash  = ticker in ('KRW', 'USD')
    leverage = int(leverage) if leverage else 1
    qty_f    = float(qty or 0)

    lev_html = f'<span class="lev-badge lev-x{leverage}">x{leverage}</span>' if leverage > 1 else ""

    if ticker == 'KRW':
        display_name = "현금(KRW)"
        qty_str      = ""
        change_html  = ""
    elif ticker == 'USD':
        display_name = "현금(USD)"
        qty_str      = fmt_usd(qty_f)
        change_html  = ""
    else:
        display_name = tname or ticker
        qty_str      = f"{qty_f:g}주"
        change_html  = (
            f'<div class="ticker-change">'
            f'<span id="ac-price-{pos_id}" style="margin-right:4px;"></span>'
            f'<span id="ac-chg-{pos_id}"></span>'
            f'</div>'
        )

    status_html = "" if is_cash else f'<span id="ac-status-{pos_id}" class="ticker-status"></span>'

    if is_cash:
        onclick_js = "acOpenEditCashModal(this);"
        data_attrs = f'data-pos-id="{pos_id}" data-ticker="{ticker}" data-amount="{qty_f}"'
    else:
        avg_price_val = float(avg_price) if avg_price is not None else ""
        currency      = get_market_currency(t_market) if t_market else "KRW"
        data_attrs = (
            f'data-pos-id="{pos_id}" data-ticker="{ticker}" '
            f'data-name="{tname or ""}" data-market="{t_market or "KR"}" '
            f'data-currency="{currency}" '
            f'data-leverage="{leverage}" data-qty="{qty_f}" '
            f'data-avg-price="{avg_price_val}"'
        )
        onclick_js = "acOpenEditPositionModal(this);"

    return (
        f'<div style="cursor:pointer;" onclick="{onclick_js}" {data_attrs}>'
        f'  <div class="ticker-row" id="ac-row-{pos_id}">'
        f'    <div>'
        f'      <div class="lev-name-wrap">'
        f'        {lev_html}'
        f'        <span class="ticker-name">{display_name}</span>'
        f'        {status_html}'
        f'      </div>'
        f'      <div class="ticker-qty">{qty_str}</div>'
        f'    </div>'
        f'    <div>'
        f'      <div class="ticker-amount" id="ac-amount-{pos_id}"></div>'
        f'      {change_html}'
        f'    </div>'
        f'  </div>'
        f'</div>'
    )


def _build_position_row_values(pos, usd_rate):
    """종목 행 가변값 dict — 매 tick diff 비교용"""
    pos_id, ticker, qty, tname, price, chg_pct, t_market, leverage, avg_price = pos
    is_cash  = ticker in ('KRW', 'USD')
    leverage = int(leverage) if leverage else 1
    qty_f    = float(qty   or 0)
    price_f  = float(price or 0)
    chg_f    = float(chg_pct or 0)

    if ticker == 'KRW':
        amount_str = fmt_krw(qty_f)
    elif ticker == 'USD':
        amount_str = fmt_krw(qty_f * usd_rate)
    else:
        currency   = get_market_currency(t_market)
        rate       = usd_rate if currency == "USD" else 1
        amount_str = fmt_krw(qty_f * price_f * rate)

    if is_cash:
        price_str = chg_str = chg_css = ""
    else:
        currency = get_market_currency(t_market)
        price_str, chg_str, chg_css = fmt_change(price_f, chg_f, currency=currency)

    status_dot = status_text = status_cls = ""
    if not is_cash and t_market:
        status = get_market_status(t_market)
        dot_map = {
            "open":    ("●", "Open",       "status-open"),
            "pre":     ("●", "Pre",        "status-pre"),
            "after":   ("●", "After",      "status-after"),
            "closing": ("●", "Closing...", "status-closing"),
        }
        status_dot, status_text, status_cls = dot_map.get(status, ("○", "Closed", "status-closed"))

    result = {
        "id":         pos_id,
        "amount":     amount_str,
        "price":      price_str,
        "chg":        chg_str,
        "chg_css":    chg_css,
        "status_dot": status_dot,
        "status_txt": status_text,
        "status_cls": status_cls,
        # 모달 data-* 속성 갱신용
        "avg_price":  float(avg_price) if avg_price is not None else None,
        "cash_amount": qty_f if is_cash else None,  # 현금 row만 유효
    }
    return result


# ── 요약 헤더 ─────────────────────────────────────────────────────────────────

def _build_summary_html(label, total_asset, pnl, pnl_pct, usd_rate=None, usd_chg=None):
    """summary header HTML"""
    pnl_text, pnl_class = fmt_pnl(pnl, pnl_pct)
    usd_html = ""
    if usd_rate and usd_chg is not None:
        usd_css  = "positive" if usd_chg >= 0 else "negative"
        usd_html = (
            f'<span style="color:#888888;">USD </span>'
            f'<span class="{usd_css}">{usd_rate:,.2f} ({fmt_pct(usd_chg)})</span>'
        )
    return {
        "label":      label,
        "total":      fmt_krw(total_asset),
        "pnl_text":   pnl_text,
        "pnl_class":  pnl_class,
        "usd_html":   usd_html,
    }