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
    qty_fixed: str | None = None,  # 하위 호환성 유지 (내부적으로는 ticker로 분기)
    onclick_attr: str = "",
    data_attrs: str = "",
) -> str:
    is_cash  = ticker in ('KRW', 'USD')
    leverage = int(leverage) if leverage else 1

    # 레버리지는 종목 수정 후에도 바뀔 수 있으므로 항상 렌더링해두고 표시 여부만 토글.
    lev_html = (
        f'<span id="{id_prefix}-lev-{row_id}" class="lev-badge lev-x{leverage}" '
        f'style="{"" if leverage > 1 else "display:none;"}">x{leverage}</span>'
    )

    status_html = f'<span id="{id_prefix}-status-{row_id}" class="ticker-status" style="white-space:nowrap; flex-shrink:0;"></span>'
    onclick_str = f'onclick="{onclick_attr}" style="cursor:pointer;"' if onclick_attr else ""

    if is_cash:
        # KRW / USD 현금: 1행. 금액만 우측에 표시 (USD는 amount_str에 원화+달러 포맷 통합)
        return (
            f'<div {onclick_str} {data_attrs}>'
            f'  <div class="ticker-row" id="{id_prefix}-row-{row_id}">'
            f'    <div>'
            f'      <div class="lev-name-wrap">'
            f'        <span id="{id_prefix}-name-{row_id}" class="ticker-name">{display_name}</span>'
            f'      </div>'
            f'    </div>'
            f'    <div>'
            f'      <div class="ticker-amount" id="{id_prefix}-amount-{row_id}"></div>'
            f'    </div>'
            f'  </div>'
            f'</div>'
        )
    else:
        # 일반 종목: 3행, 행마다 좌우 폭 비율을 독립적으로 지정 (1행 7:3 / 2행 5:5 / 3행 6:4)
        # 한 행의 좌우는 서로의 길이에 영향을 주지만, 다른 행과는 폭을 공유하지 않는다.
        # 좌: [레버리지뱃지][종목명][시장상태] / 보유수량 [qty] / 평균단가 [avgprice]
        # 우: [평가금액] / [손익액(수익률%)] / [현재가 / 등락률]
        return (
            f'<div {onclick_str} {data_attrs}>'
            f'  <div class="ticker-row ticker-row-3line" id="{id_prefix}-row-{row_id}">'
            f'    <div class="t-row t-row-1">'
            f'      <div class="t-row-left lev-name-wrap">'
            f'        {lev_html}'
            f'        <span id="{id_prefix}-name-{row_id}" class="ticker-name">{display_name}</span>'
            f'        {status_html}'
            f'      </div>'
            f'      <div class="t-row-right ticker-amount" id="{id_prefix}-amount-{row_id}"></div>'
            f'    </div>'
            f'    <div class="t-row t-row-2">'
            f'      <div class="t-row-left">'
            f'        <span class="ticker-label">보유수량</span>'
            f'        <span id="{id_prefix}-qty-{row_id}"></span>'
            f'      </div>'
            f'      <div class="t-row-right"><span id="{id_prefix}-pnl-{row_id}"></span></div>'
            f'    </div>'
            f'    <div class="t-row t-row-3">'
            f'      <div class="t-row-left">'
            f'        <span class="ticker-label">평균단가</span>'
            f'        <span id="{id_prefix}-avgprice-{row_id}"></span>'
            f'      </div>'
            f'      <div class="t-row-right">'
            f'        <span id="{id_prefix}-price-{row_id}" style="margin-right:4px;"></span>'
            f'        <span id="{id_prefix}-chg-{row_id}"></span>'
            f'      </div>'
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
    usd_rate: float = 1.0,       # 손익액 KRW 환산용 (USD 종목일 때 사용)
    qty_in_values: bool = True,  # portfolio: True, accounts/드릴다운: False(골격에 고정)
) -> dict:
    is_cash  = ticker in ('KRW', 'USD')
    qty_f    = float(qty      or 0)
    price_f  = float(price    or 0)
    chg_f    = float(chg_pct  or 0)
    avg_f    = float(avg_price or 0)
    leverage = int(leverage) if leverage else 1

    currency = get_market_currency_fn(market) if not is_cash else None

    # ── amount_str: 현금은 원화환산 표기, 종목은 원화 평가금액 ──
    if ticker == 'USD':
        # "₩1,234,567 ($1,234.56)" 형태로 원화+달러 통합 표시
        amount_str = f"{fmt_krw(amount)} ({fmt_usd(qty_f)})"
    else:
        amount_str = fmt_krw(amount)

    # ── 현재가 / 등락률 ───────────────────────────────────────
    if is_cash:
        price_str = chg_str = chg_css = ""
    else:
        price_str, chg_str, chg_css = fmt_change(price_f, chg_f, currency=currency)

    # ── 수량 ─────────────────────────────────────────────────
    qty_str = ""
    if not is_cash and qty_in_values:
        qty_str = f"≈{qty_f:.2f}주" if qty_f != int(qty_f) else f"{qty_f:g}주"

    # ── 평단가 ────────────────────────────────────────────────
    avgprice_str = ""
    if not is_cash and avg_f > 0:
        avgprice_str = f"${avg_f:,.2f}" if currency == "USD" else _fmt_amount_short(avg_f)

    # ── 손익액 + 수익률 (우측 2행) ───────────────────────────
    # 평단가·수량·현재가 모두 있을 때만 계산
    pnl_str = pnl_css = ""
    if not is_cash and avg_f > 0 and price_f > 0 and qty_f > 0:
        rate       = usd_rate if currency == "USD" else 1.0
        pnl_amount = (price_f - avg_f) * qty_f * rate   # KRW 환산 손익액
        pnl_pct    = (price_f - avg_f) / avg_f * 100
        pnl_str, pnl_css = fmt_pnl(pnl_amount, pnl_pct)

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
        "pnl":         pnl_str,
        "pnl_css":     pnl_css,
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
    # 뒤로가기 화살표 — 평소 display:none, 상세/드릴다운 진입 시 JS가 'inline'으로 토글.
    # 배지 자체의 클릭 가능 여부는 이 요소의 display 상태로 판단(아래 onclick 가드).
    arrow_span = ui.span(
        "‹",
        id=f"{id_prefix}-back-btn",
        class_="summary-badge-arrow",
        style="display:none;",
    )

    badge_attrs = {
        "id": f"{id_prefix}-summary-badge",
        "class": "summary-badge",
    }
    if back_btn_onclick:
        # 화살표가 보일 때(=뒤로가기 가능 상태)만 실제로 동작하도록 가드.
        badge_attrs["onclick"] = (
            f"if(document.getElementById('{id_prefix}-back-btn').style.display!=='none'){{{back_btn_onclick}}}"
        )

    badge = ui.div(
        badge_attrs,
        arrow_span,
        ui.span(label_text, id=f"{id_prefix}-summary-label", class_="summary-badge-text"),
    )

    return ui.div(
        {"class": "total-summary"},
        ui.div(
            badge,
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