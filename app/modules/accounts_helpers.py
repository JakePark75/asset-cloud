from app.db import get_market_currency
from app.modules.components import (
    fmt_krw, fmt_usd, fmt_pct, fmt_pnl, fmt_change,
    build_ticker_row_skeleton, build_ticker_row_values,
)
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
    """종목 행 골격 HTML — 공통 build_ticker_row_skeleton 사용"""
    pos_id, ticker, qty, tname, price, chg_pct, t_market, leverage, avg_price = pos
    qty_f    = float(qty or 0)
    leverage = int(leverage) if leverage else 1
    is_cash  = ticker in ('KRW', 'USD')

    if ticker == 'KRW':
        display_name = "현금(KRW)"
        qty_fixed    = ""          # 수량 영역 없음
        onclick_attr = "acOpenEditCashModal(this);"
        data_attrs   = f'data-pos-id="{pos_id}" data-ticker="{ticker}" data-amount="{qty_f}"'
    elif ticker == 'USD':
        display_name = "현금(USD)"
        qty_fixed    = fmt_usd(qty_f)
        onclick_attr = "acOpenEditCashModal(this);"
        data_attrs   = f'data-pos-id="{pos_id}" data-ticker="{ticker}" data-amount="{qty_f}"'
    else:
        display_name  = tname or ticker
        qty_fixed     = f"{qty_f:g}주"
        onclick_attr  = "acOpenEditPositionModal(this);"
        avg_price_val = float(avg_price) if avg_price is not None else ""
        currency      = get_market_currency(t_market) if t_market else "KRW"
        data_attrs    = (
            f'data-pos-id="{pos_id}" data-ticker="{ticker}" '
            f'data-name="{tname or ""}" data-market="{t_market or "KR"}" '
            f'data-currency="{currency}" '
            f'data-leverage="{leverage}" data-qty="{qty_f}" '
            f'data-avg-price="{avg_price_val}"'
        )

    return build_ticker_row_skeleton(
        ticker       = ticker,
        display_name = display_name,
        market       = t_market,
        leverage     = leverage,
        id_prefix    = "ac",
        row_id       = str(pos_id),
        qty_fixed    = qty_fixed,
        onclick_attr = onclick_attr,
        data_attrs   = data_attrs,
    )


def _build_position_row_values(pos, usd_rate):
    """종목 행 가변값 dict — 공통 build_ticker_row_values 사용"""
    pos_id, ticker, qty, tname, price, chg_pct, t_market, leverage, avg_price = pos
    qty_f   = float(qty   or 0)
    price_f = float(price or 0)

    # 평가액 계산 (통화/환율 분기는 호출자 책임)
    if ticker == 'KRW':
        amount = qty_f
    elif ticker == 'USD':
        amount = qty_f * usd_rate
    else:
        currency = get_market_currency(t_market)
        rate     = usd_rate if currency == "USD" else 1
        amount   = qty_f * price_f * rate

    result = build_ticker_row_values(
        ticker                 = ticker,
        amount                 = amount,
        qty                    = qty,
        price                  = price,
        chg_pct                = chg_pct,
        market                 = t_market,
        avg_price              = avg_price,
        id_prefix              = "ac",
        row_id                 = str(pos_id),
        get_market_currency_fn = get_market_currency,
        get_market_status_fn   = get_market_status,
        qty_in_values          = False,  # 수량은 골격에 고정
    )

    # accounts 전용 추가 필드 (모달 data-* 갱신용)
    result["avg_price"]   = float(avg_price) if avg_price is not None else None
    result["cash_amount"] = qty_f if ticker in ('KRW', 'USD') else None

    return result


# ── 요약 헤더 ─────────────────────────────────────────────────────────────────

def _build_summary_html(label, total_asset, pnl, pnl_pct, usd_rate=None, usd_chg=None):
    """summary header 값 dict"""
    pnl_text, pnl_class = fmt_pnl(pnl, pnl_pct)
    usd_text = ""
    usd_css  = ""
    if usd_rate and usd_chg is not None:
        usd_text = f'{usd_rate:,.2f} ({fmt_pct(usd_chg)})'
        usd_css  = "positive" if usd_chg > 0 else "negative" if usd_chg < 0 else "neutral"
    return {
        "label":     label,
        "total":     fmt_krw(total_asset),
        "pnl_text":  pnl_text,
        "pnl_class": pnl_class,
        "usd_text":  usd_text,
        "usd_css":   usd_css,
    }