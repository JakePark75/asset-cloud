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
    """
    손익 표시용 텍스트와 CSS 클래스 반환.
    Returns (text, css_class)
    예) ("+1,234원 (+1.23%)", "positive")
    """
    sign = "+" if amount >= 0 else "-"
    css = "positive" if amount >= 0 else "negative"
    amount_str = fmt_krw(abs(amount)) if currency == "KRW" else fmt_usd(abs(amount))
    text = f"{sign}{amount_str} ({fmt_pct(pct)})"
    return text, css

def fmt_change(price: float, chg_pct: float, currency: str = "KRW") -> tuple[str, str, str]:
    """
    현재가 + 등락률 표시용 텍스트와 CSS 클래스 반환.
    Returns (price_str, chg_str, css_class)
    예) ("1,234원", "+1.23%", "positive")
    """
    price_str = fmt_krw(price) if currency == "KRW" else fmt_usd(price)
    chg_str = fmt_pct(chg_pct)
    css = "positive" if chg_pct >= 0 else "negative"
    return price_str, chg_str, css


# ── 공통 헤더 컴포넌트 ────────────────────────────────────────────────────────

def render_summary_header(label: str, total_asset: float, pnl: float, pnl_pct: float,
                          usd_rate: float | None, usd_chg: float | None):
    """
    상단 요약 헤더 공통 컴포넌트.

    Parameters
    ----------
    label       : 헤더 상단 레이블 (예: "포트폴리오", "총자산", 계좌명 등)
    total_asset : 총자산 (원화)
    pnl         : 손익 금액 (원화)
    pnl_pct     : 손익률 (%)
    usd_rate    : USD/KRW 환율 (없으면 None)
    usd_chg     : USD/KRW 등락률 (없으면 None)
    """
    pnl_text, pnl_class = fmt_pnl(pnl, pnl_pct)

    # USD/KRW 환율 영역
    usd_elem = None
    if usd_rate is not None and usd_chg is not None:
        usd_css = "positive" if usd_chg >= 0 else "negative"
        usd_elem = ui.span(
            ui.span("USD/KRW ", style="color:#888888;"),
            ui.span(f"{usd_rate:,.2f} ({fmt_pct(usd_chg)})", class_=usd_css),
            style="margin-left:auto; font-size:11px;",
        )

    return ui.div(
        ui.div(label, class_="account-alias"),
        ui.div(fmt_krw(total_asset), class_="total-summary-amount"),
        ui.div(
            ui.span(pnl_text, class_=f"total-summary-pnl-text {pnl_class}"),
            usd_elem,
            class_="total-summary-pnl",
            style="justify-content:space-between;",
        ),
        class_="total-summary",
    )