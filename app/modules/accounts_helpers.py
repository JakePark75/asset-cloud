from app.db import get_market_currency
from app.modules.components import (
    fmt_krw, fmt_usd, fmt_pct, fmt_pnl, fmt_change,
    build_ticker_row_skeleton, build_ticker_row_values,
    build_summary_payload,
)
from scheduler.price_updater_common import get_market_status


def _ticker_to_id(ticker: str) -> str:
    return ticker.replace("-", "_").replace("^", "_").replace("=", "_")


# ── 계좌 카드 ─────────────────────────────────────────────────────────────────

def _build_account_card_skeleton(acc, ns_str):
    """계좌 카드 골격 HTML — 드릴다운 계좌 카드와 동일한 ticker-row 컨셉 사용"""
    a_id, name, alias, total, cash, is_watch, prev_total = acc
    alias_str = f" ({alias})" if alias else ""
    return (
        f'<div onclick="Shiny.setInputValue(\'{ns_str}card_clicked\', {a_id}, {{priority: \'event\'}});" '
        f'style="cursor:pointer;">'
        f'  <div class="ticker-row" id="ac-card-{a_id}">'
        f'    <div>'
        f'      <div class="lev-name-wrap">'
        f'        <span class="ticker-name">{name}{alias_str}</span>'
        f'      </div>'
        f'      <div class="ticker-qty">현금 <span id="ac-card-cash-{a_id}"></span></div>'
        f'    </div>'
        f'    <div>'
        f'      <div class="ticker-amount" id="ac-card-total-{a_id}"></div>'
        f'      <div class="ticker-change">'
        f'        <span id="ac-card-pnl-{a_id}"></span>'
        f'      </div>'
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
        qty_fixed     = None  # span으로 비워둠 — tick에서 채움(매수/매도 직후 즉시 반영)
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
    is_cash = ticker in ('KRW', 'USD')

    # 평가액 계산 + 표시명 (호출자 책임 분기 — skeleton과 동일 기준)
    if ticker == 'KRW':
        amount       = qty_f
        display_name = "현금(KRW)"
    elif ticker == 'USD':
        amount       = qty_f * usd_rate
        display_name = "현금(USD)"
    else:
        currency     = get_market_currency(t_market)
        rate         = usd_rate if currency == "USD" else 1
        amount       = qty_f * price_f * rate
        display_name = tname or ticker

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
        name                   = display_name,
        leverage               = leverage,
        usd_rate               = usd_rate,
        qty_in_values          = True,  # 수량도 tick으로 갱신 (매수/매도 직후 즉시 반영)
    )

    # accounts 전용 추가 필드 — 종목 수정 모달을 같은 세션에서 다시 열 때
    # data-* 프리필 값(이름/시장/레버리지/평단가/통화/수량)이 최신 상태를 유지하도록
    # raw 값을 같이 보냄 (JS에서 closest('[data-pos-id]')로 찾은 wrap div의
    # data-* 속성을 갱신하는 데 사용)
    result["avg_price"]   = float(avg_price) if avg_price is not None else None
    result["cash_amount"] = qty_f if is_cash else None
    result["raw_qty"]     = qty_f if not is_cash else None
    result["market"]      = t_market if not is_cash else None

    return result


# ── 요약 헤더 ─────────────────────────────────────────────────────────────────

def _build_summary_html(label, total_asset, pnl, pnl_pct, usd_rate=None, usd_chg=None):
    """summary header 값 dict — build_summary_payload 위임"""
    payload = build_summary_payload(total_asset, pnl, pnl_pct, usd_rate, usd_chg)
    payload["label"] = label
    return payload