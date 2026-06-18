from shiny import ui


# ── 포맷 유틸 ─────────────────────────────────────────────────────────────────

def fmt_krw(amount: float) -> str:
    """원화 금액. 예) 1,234,567원"""
    return f"{int(amount):,}원"

def fmt_usd(amount: float) -> str:
    """달러 금액. 예) $1,234.56"""
    return f"${amount:,.2f}"

def fmt_pct(pct: float) -> str:
    """등락률. 예) +1.23% / -1.23%"""
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"

def fmt_pnl(amount: float, pct: float, currency: str = "KRW") -> tuple[str, str]:
    sign = "+" if amount >= 0 else "-"
    css = "positive" if amount >= 0 else "negative"
    amount_str = fmt_krw(abs(amount)) if currency == "KRW" else fmt_usd(abs(amount))
    text = f"{sign}{amount_str} ({fmt_pct(pct)})"
    return text, css

def fmt_change(price: float, chg_pct: float, currency: str = "KRW") -> tuple[str, str, str]:
    if currency == "KRW":
        price_str = fmt_krw(price)
    elif currency == "NUM":
        price_str = f"{price:,.2f}"
    else:
        price_str = fmt_usd(price)
    chg_str = fmt_pct(chg_pct)
    css = "positive" if chg_pct >= 0 else "negative"
    return price_str, chg_str, css


def _fmt_amount_short(amount: float) -> str:
    """평가액 축약 포맷 (포트폴리오/드릴다운 공통)"""
    if amount >= 100_000_000:
        return f"{amount / 100_000_000:.1f}억원"
    elif amount >= 1_000_000:
        return f"{amount / 10_000:.0f}만원"
    else:
        return fmt_krw(amount)


# ── 공통 ticker 행 ────────────────────────────────────────────────────────────
#
# portfolio, accounts 상세, 포트폴리오 드릴다운(계좌 행) 모두 동일한 HTML 구조를 사용.
#
# 파라미터:
#   is_cash     : ticker in ('KRW','USD') 로 내부 판단
#   display_name: 행에 표시할 이름 (종목명 또는 계좌명)
#   id_prefix   : DOM id 접두사. 예) "pf", "ac", "pfd"
#   row_id      : DOM id 식별자. portfolio/드릴다운은 ticker_safe, accounts는 pos_id
#   qty_fixed   : None → qty span 비워둠(tick에서 채움), "" → 수량 없음(KRW),
#                 문자열 → 고정 표시(USD 잔고, accounts 종목 수량 등)
#   onclick_attr: 행 클릭 JS. "" 이면 클릭 없음
#   data_attrs  : 행에 붙일 data-* 속성 문자열. "" 이면 없음

def build_ticker_row_skeleton(
    ticker: str,
    display_name: str,
    market: str | None,
    leverage: int,
    id_prefix: str,
    row_id: str,
    qty_fixed: str | None = None,
    onclick_attr: str = "",
    data_attrs: str = "",
) -> str:
    is_cash  = ticker in ('KRW', 'USD')
    leverage = int(leverage) if leverage else 1

    # 레버리지는 종목 수정 후에도 바뀔 수 있으므로 항상 렌더링해두고 표시 여부만 토글.
    # (id를 부여해 tick에서 텍스트/표시여부를 갱신할 수 있게 함)
    lev_html = (
        f'<span id="{id_prefix}-lev-{row_id}" class="lev-badge lev-x{leverage}" '
        f'style="{"" if leverage > 1 else "display:none;"}">x{leverage}</span>'
    )

    SEP = '<span style="color:var(--text-dim);margin:0 4px;">·</span>'

    if is_cash and qty_fixed == "":
        # KRW: 수량 영역 없음, 시세 없음
        qty_html    = ""
        change_html = ""
    elif is_cash:
        # USD: 수량(잔액)도 바뀔 수 있으므로 span으로 감싸 tick에서 갱신 가능하게 함
        qty_html    = f'<span id="{id_prefix}-qty-{row_id}">{qty_fixed or ""}</span>'
        change_html = ""
    else:
        if qty_fixed is None:
            # portfolio: 수량도 span으로 비워둠 (tick에서 채움)
            qty_html = (
                f'<span id="{id_prefix}-qty-{row_id}"></span>'
                f'{SEP}'
                f'<span id="{id_prefix}-avgprice-{row_id}"></span>'
                f'{SEP}'
                f'<span id="{id_prefix}-pnlpct-{row_id}"></span>'
            )
        else:
            # accounts/드릴다운: 수량은 고정, 평단가/수익률은 span으로 비워둠
            qty_html = (
                f'{qty_fixed}'
                f'{SEP}'
                f'<span id="{id_prefix}-avgprice-{row_id}"></span>'
                f'{SEP}'
                f'<span id="{id_prefix}-pnlpct-{row_id}"></span>'
            )
        change_html = (
            f'<div class="ticker-change">'
            f'<span id="{id_prefix}-price-{row_id}" style="margin-right:4px;"></span>'
            f'<span id="{id_prefix}-chg-{row_id}"></span>'
            f'</div>'
        )

    status_html = (
        "" if is_cash
        else f'<span id="{id_prefix}-status-{row_id}" class="ticker-status"></span>'
    )

    onclick_str = f'onclick="{onclick_attr}" style="cursor:pointer;"' if onclick_attr else ""

    return (
        f'<div {onclick_str} {data_attrs}>'
        f'  <div class="ticker-row" id="{id_prefix}-row-{row_id}">'
        f'    <div>'
        f'      <div class="lev-name-wrap">'
        f'        {lev_html}'
        f'        <span id="{id_prefix}-name-{row_id}" class="ticker-name">{display_name}</span>'
        f'        {status_html}'
        f'      </div>'
        f'      <div class="ticker-qty">{qty_html}</div>'
        f'    </div>'
        f'    <div>'
        f'      <div class="ticker-amount" id="{id_prefix}-amount-{row_id}"></div>'
        f'      {change_html}'
        f'    </div>'
        f'  </div>'
        f'</div>'
    )


def build_ticker_row_values(
    ticker: str,
    amount: float,          # 호출자가 계산해서 전달 (통화/환율 분기는 호출자 책임)
    qty,
    price,
    chg_pct,
    market: str | None,
    avg_price,
    id_prefix: str,
    row_id: str,
    get_market_currency_fn,   # app.db.get_market_currency 주입
    get_market_status_fn,     # scheduler.price_updater_common.get_market_status 주입
    name: str | None = None,     # 종목명/표시명 — tick으로 동적 갱신
    leverage: int = 1,           # 레버리지 — tick으로 동적 갱신
    qty_in_values: bool = True,  # portfolio: True, accounts/드릴다운: False(골격에 고정)
) -> dict:
    is_cash  = ticker in ('KRW', 'USD')
    qty_f    = float(qty      or 0)
    price_f  = float(price    or 0)
    chg_f    = float(chg_pct  or 0)
    avg_f    = float(avg_price or 0)
    leverage = int(leverage) if leverage else 1

    amount_str = fmt_krw(amount)

    currency = get_market_currency_fn(market) if not is_cash else None

    # ── 현재가 / 등락률 ───────────────────────────────────────
    if is_cash:
        price_str = chg_str = chg_css = ""
    else:
        price_str, chg_str, chg_css = fmt_change(price_f, chg_f, currency=currency)

    # ── 수량 ─────────────────────────────────────────────────
    qty_str = ""
    if not is_cash and qty_f > 0 and qty_in_values:
        qty_str = f"≈{qty_f:.2f}주" if qty_f != int(qty_f) else f"{qty_f:g}주"
    elif ticker == 'USD':
        qty_str = fmt_usd(qty_f)

    # ── 평단가 / 수익률 ───────────────────────────────────────
    avgprice_str = pnlpct_str = pnlpct_css = ""
    if not is_cash and avg_f > 0 and price_f > 0:
        avgprice_str = f"${avg_f:,.2f}" if currency == "USD" else _fmt_amount_short(avg_f)
        pnl_pct      = (price_f - avg_f) / avg_f * 100
        sign         = "+" if pnl_pct >= 0 else ""
        pnlpct_str   = f"{sign}{pnl_pct:.2f}%"
        pnlpct_css   = "positive" if pnl_pct >= 0 else "negative"

    # ── 시장 상태 ─────────────────────────────────────────────
    status_dot = status_text = status_cls = ""
    if not is_cash and market:
        status  = get_market_status_fn(market)
        dot_map = {
            "open":    ("●", "Open",        "status-open"),
            "pre":     ("●", "Pre",         "status-pre"),
            "after":   ("●", "After",       "status-after"),
            "closing": ("●", "Closing...",  "status-closing"),
        }
        status_dot, status_text, status_cls = dot_map.get(status, ("○", "Closed", "status-closed"))

    return {
        "id":          row_id,
        "name":        name,
        "leverage":    leverage,
        "currency":    currency,
        "amount":      amount_str,
        "qty":         qty_str,
        "price":       price_str,
        "chg":         chg_str,
        "chg_css":     chg_css,
        "avgprice":    avgprice_str,
        "pnlpct":      pnlpct_str,
        "pnlpct_css":  pnlpct_css,
        "status_dot":  status_dot,
        "status_txt":  status_text,
        "status_cls":  status_cls,
    }


# ── 계좌 행 (드릴다운 전용) ────────────────────────────────────────────────────
#
# 포트폴리오 종목 행(build_ticker_row_*)과는 정보 구조가 다르다.
# 드릴다운은 "선택된 종목 하나"의 계좌별 보유 현황이므로:
#   - 종목 단위 정보(현재가/등락률/레버리지뱃지/시장상태)는 행마다 반복할 필요 없음
#     → 드릴다운 헤더에 이미 1회 표시됨
#   - 평단가는 계좌별로 다르므로(매수 시점이 다름) 행에 그대로 표시
#   - 계좌 단위 정보(수량/평단가/평가금액/계좌별 손익)만 행에 표시
# 드릴다운은 cash(KRW/USD) 종목에선 열리지 않으므로 is_cash 분기도 불필요.

def build_account_row_skeleton(
    display_name: str,   # 계좌명 (+ alias)
    qty_text: str,       # 보유수량 고정 텍스트. 구조 변경(계좌 추가/삭제) 시에만 재생성
    row_id: str,          # acc_id
    id_prefix: str = "pfd",
) -> str:
    SEP = '<span style="color:var(--text-dim);margin:0 4px;">·</span>'
    return (
        f'<div>'
        f'  <div class="ticker-row" id="{id_prefix}-row-{row_id}">'
        f'    <div>'
        f'      <div class="lev-name-wrap">'
        f'        <span class="ticker-name">{display_name}</span>'
        f'      </div>'
        f'      <div class="ticker-qty">'
        f'        {qty_text}'
        f'        {SEP}'
        f'        <span id="{id_prefix}-avgprice-{row_id}"></span>'
        f'      </div>'
        f'    </div>'
        f'    <div>'
        f'      <div class="ticker-amount" id="{id_prefix}-amount-{row_id}"></div>'
        f'      <div class="ticker-change">'
        f'        <span id="{id_prefix}-pnl-{row_id}"></span>'
        f'      </div>'
        f'    </div>'
        f'  </div>'
        f'</div>'
    )


def build_account_row_values(
    avg_price: float,
    amount: float,        # 평가금액 (호출자가 계산해서 전달)
    pnl_amount: float,    # 이 계좌 포지션의 손익액 (호출자가 계산해서 전달)
    pnl_pct: float,       # 이 계좌 포지션의 수익률
    currency: str | None,  # get_market_currency 결과 ("USD" / "KRW")
    row_id: str,
) -> dict:
    avg_f = float(avg_price or 0)

    avgprice_str = ""
    if avg_f > 0:
        avgprice_str = f"${avg_f:,.2f}" if currency == "USD" else _fmt_amount_short(avg_f)

    pnl_text, pnl_css = fmt_pnl(pnl_amount, pnl_pct)

    return {
        "id":       row_id,
        "amount":   fmt_krw(amount),
        "avgprice": avgprice_str,
        "pnl_text": pnl_text,
        "pnl_css":  pnl_css,
    }


# ── 공통 요약 헤더 ────────────────────────────────────────────────────────────
#
# DOM 골격과 페이로드(tick값)를 분리해 JS DOM 패치 방식에 대응.
#
# build_summary_header_dom(id_prefix, back_btn_onclick)
#   → Shiny UI 골격. 최초 1회 렌더링.
#   id_prefix      : DOM id 접두사. 예) "pf", "ac"
#   label_text     : 초기 레이블 텍스트. 예) "포트폴리오", "총자산"
#   back_btn_onclick: 뒤로가기 버튼 onclick JS. None이면 버튼 미생성.
#
# build_summary_payload(total_asset, pnl, pnl_pct, usd_rate, usd_chg)
#   → tick마다 JS로 전송할 값 dict.
#   키: total, pnl_text, pnl_class, usd_text, usd_css

def build_summary_header_dom(
    id_prefix: str,
    label_text: str,
    back_btn_onclick: str | None = None,
    delta_row_extra: ui.Tag | None = None,
) -> ui.Tag:
    back_btn = ui.tags.button(
        "‹",
        id=f"{id_prefix}-back-btn",
        class_="summary-label",
        style="display:none; background:none; border:none; padding:0; margin-right:6px; cursor:pointer; vertical-align:middle; line-height:1; font-family:inherit;",
        onclick=back_btn_onclick,
    ) if back_btn_onclick else None

    label_row_children = []
    if back_btn:
        label_row_children.append(back_btn)
    label_row_children.append(
        ui.span(label_text, id=f"{id_prefix}-summary-label", class_="summary-label",
                style="vertical-align:middle;")
    )

    return ui.div(
        {"class": "total-summary"},
        ui.div(
            *label_row_children,
            style="display:flex; align-items:center; height:20px; margin-bottom:4px;",
        ),
        ui.div("–", id=f"{id_prefix}-summary-total", class_="summary-amount"),
        ui.div(
            ui.span("–", id=f"{id_prefix}-summary-pnl", class_="summary-delta"),
            ui.span(
                {"id": f"{id_prefix}-usd-wrap",
                 "style": "display:none; margin-left:auto; align-items:baseline; gap:4px;"},
                ui.span("USD", style="font-size:11px; color:#888888;"),
                ui.span("–", id=f"{id_prefix}-usd-text", style="font-size:13px;"),
            ),
            *(([delta_row_extra]) if delta_row_extra is not None else []),
            class_="summary-delta-row",
        ),
    )


def build_summary_payload(
    total_asset: float,
    pnl: float,
    pnl_pct: float,
    usd_rate: float | None = None,
    usd_chg: float | None = None,
) -> dict:
    pnl_text, pnl_class = fmt_pnl(pnl, pnl_pct)
    usd_text = ""
    usd_css  = ""
    if usd_rate and usd_chg is not None:
        usd_text = f'{usd_rate:,.2f} ({fmt_pct(usd_chg)})'
        usd_css  = "positive" if usd_chg > 0 else "negative" if usd_chg < 0 else "neutral"
    return {
        "total":     fmt_krw(total_asset),
        "pnl_text":  pnl_text,
        "pnl_class": pnl_class,
        "usd_text":  usd_text,
        "usd_css":   usd_css,
    }