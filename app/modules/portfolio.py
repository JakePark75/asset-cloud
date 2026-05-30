from shiny import ui, render, reactive, module
import psycopg2
import json
from db import get_usd_krw
from price_signal import price_signal


def load_portfolio():
    with open("scheduler/config.json") as f:
        cfg = json.load(f)
    conn = psycopg2.connect(
        dbname="assetdb", user="jake", password=cfg["db_password"], host="localhost"
    )
    cur = conn.cursor()

    usd_rate, usd_chg = get_usd_krw()
    usd_rate = usd_rate or 0

    cur.execute("""
        SELECT p.ticker, SUM(p.quantity) as quantity,
            t.name, t.current_price, t.change_pct, t.market, t.leverage
        FROM positions p
        LEFT JOIN tickers t ON p.ticker = t.ticker
        GROUP BY p.ticker, t.name, t.current_price, t.change_pct, t.market, t.leverage
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows, usd_rate, usd_chg


def format_qty(ticker, qty, market):
    if ticker == "KRW":
        return f"{qty:,.0f}원"
    elif ticker == "USD":
        return f"${qty:,.2f}"
    elif market in ("FX", "INDEX"):
        return f"{qty:,.2f}"
    elif market == "CRYPTO":
        normalized = qty.normalize()
        return f"{normalized:f}"
    else:
        return f"{qty:,.0f}주"


@module.ui
def portfolio_ui():
    return ui.div(
        ui.output_ui("portfolio_view"),
    )


@module.server
def portfolio_server(input, output, session):

    @render.ui
    def portfolio_view():
        price_signal.get()

        rows, usd_rate, usd_chg = load_portfolio()

        # 상단 요약 계산
        total_asset = 0
        total_pnl = 0
        total_invested = 0
        for ticker, qty, name, price, chg_pct, market, leverage in rows:
            qty_f = float(qty or 0)
            price_f = float(price or 0)
            chg_pct_f = float(chg_pct or 0)

            if ticker == "KRW":
                amount = qty_f
            elif ticker == "USD":
                amount = qty_f * usd_rate
            elif market in ("NAS", "AMS", "ARC"):
                amount = qty_f * price_f * usd_rate
            else:
                amount = qty_f * price_f

            total_asset += amount

            if ticker not in ("KRW", "USD"):
                prev = amount / (1 + chg_pct_f / 100) if chg_pct_f != -100 else 0
                total_pnl += amount - prev
                total_invested += prev

        def calc_amount(ticker, qty_f, price_f, market):
            if ticker == "KRW":
                return qty_f
            elif ticker == "USD":
                return qty_f * usd_rate
            elif market in ("NAS", "AMS", "ARC"):
                return qty_f * price_f * usd_rate
            else:
                return qty_f * price_f

        rows_sorted = sorted(
            rows,
            key=lambda r: (
                1 if r[0] in ("KRW", "USD") else 0,
                -calc_amount(r[0], float(r[1] or 0), float(r[3] or 0), r[5])
            )
        )

        pnl_class = "positive" if total_pnl >= 0 else "negative"
        pnl_sign = "▲" if total_pnl >= 0 else "▼"
        pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0
        pnl_text = f"{pnl_sign} {abs(total_pnl):,.0f}원 ({abs(pnl_pct):.2f}%)"

        usd_class = "positive" if (usd_chg or 0) >= 0 else "negative"
        usd_chg_sign = "▲" if (usd_chg or 0) >= 0 else "▼"
        usd_text_label = None
        usd_text_num = None
        if usd_rate:
            usd_text_label = f"USD/KRW {usd_rate:,.2f} "
            usd_text_num = f"{usd_chg_sign}{abs(usd_chg or 0):.2f}%"

        summary = ui.div(
            ui.div("포트폴리오", class_="account-alias"),
            ui.div(f"{int(total_asset):,}원", class_="total-summary-amount"),
            ui.div(
                ui.span(pnl_text, class_=f"total-summary-pnl-text {pnl_class}"),
                ui.span(
                    ui.span(usd_text_label, style="color:#888888;"),
                    ui.span(usd_text_num, class_=usd_class),
                    style="margin-left:auto; font-size:11px;"
                ) if usd_text_label else None,
                class_="total-summary-pnl",
                style="justify-content:space-between;"
            ),
            class_="total-summary",
        )

        # 종목 행 렌더링
        ticker_rows = []
        for ticker, qty, name, price, chg_pct, market, leverage in rows_sorted:
            qty_f = float(qty or 0)
            price_f = float(price or 0)
            chg_pct_f = float(chg_pct or 0)
            leverage = int(leverage) if leverage else 1

            display_name = name or ticker
            qty_str = format_qty(ticker, qty, market)

            if ticker == "KRW":
                amount_str = f"{qty_f:,.0f}원"
                chg_str = ""
                chg_class = ""
            elif ticker == "USD":
                amount_krw = qty_f * usd_rate
                amount_str = f"{amount_krw:,.0f}원"
                chg_class = "positive" if chg_pct_f >= 0 else "negative"
                chg_sign = "▲" if chg_pct_f >= 0 else "▼"
                chg_str = f"{chg_sign}{abs(chg_pct_f):.2f}%"
            elif market in ("NAS", "AMS", "ARC"):
                amount = qty_f * price_f * usd_rate
                amount_str = f"{amount:,.0f}원"
                chg_class = "positive" if chg_pct_f >= 0 else "negative"
                chg_sign = "▲" if chg_pct_f >= 0 else "▼"
                chg_str = f"{chg_sign}{abs(chg_pct_f):.2f}%"
            else:
                amount = qty_f * price_f
                amount_str = f"{amount:,.0f}원"
                chg_class = "positive" if chg_pct_f >= 0 else "negative"
                chg_sign = "▲" if chg_pct_f >= 0 else "▼"
                chg_str = f"{chg_sign}{abs(chg_pct_f):.2f}%"

            ticker_rows.append(
                ui.div(
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
                )
            )

        return ui.div(
            summary,
            ui.div(*ticker_rows, class_="ticker-list"),
        )