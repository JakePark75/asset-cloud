from shiny import ui, render, reactive, module
import subprocess
import sys
from scheduler.price_updater import is_market_open
import psycopg2
import json
from app.db import get_usd_krw
from app.price_signal import price_signal

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
        ui.tags.script("""
            $(document).on('click', '.force-update-btn', function(e) {
                e.preventDefault();
                if (!confirm('전체 종목 시세를 강제 조회합니다.\\n장외시간 종목도 포함됩니다. 진행할까요?')) {
                    e.stopImmediatePropagation();
                    return false;
                }
            });
        """),
        ui.output_ui("portfolio_content"),
        class_="page-container"
    )

@module.server
def portfolio_server(input, output, session):

    @reactive.effect
    @reactive.event(input.force_update)
    def _do_force_update():
        subprocess.Popen(
            [sys.executable, "scheduler/price_updater.py", "--force"],
            cwd="/home/ubuntu/asset-cloud"
        )

    @render.ui
    def portfolio_content():
        price_signal.get()
        rows, usd_rate, usd_chg = load_portfolio()

        total_asset = 0
        total_pnl = 0
        total_invested = 0
        for ticker, qty, name, price, chg_pct, market, leverage in rows:
            qty_f = float(qty or 0)
            price_f = float(price or 0)
            chg_pct_f = float(chg_pct or 0)
            if ticker == "KRW": amount = qty_f
            elif ticker == "USD": amount = qty_f * usd_rate
            elif market in ("NAS", "AMS", "ARC"): amount = qty_f * price_f * usd_rate
            else: amount = qty_f * price_f
            total_asset += amount
            if ticker not in ("KRW", "USD"):
                prev = amount / (1 + chg_pct_f / 100) if chg_pct_f != -100 else 0
                total_pnl += amount - prev
                total_invested += prev

        def calc_amount(ticker, qty_f, price_f, market):
            if ticker == "KRW": return qty_f
            elif ticker == "USD": return qty_f * usd_rate
            elif market in ("NAS", "AMS", "ARC"): return qty_f * price_f * usd_rate
            else: return qty_f * price_f

        rows_sorted = sorted(
            rows,
            key=lambda r: (1 if r[0] in ("KRW", "USD") else 0, -calc_amount(r[0], float(r[1] or 0), float(r[3] or 0), r[5]))
        )

        pnl_class = "positive" if total_pnl >= 0 else "negative"
        pnl_sign = "▲" if total_pnl >= 0 else "▼"
        pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0
        pnl_text = f"{pnl_sign} {abs(total_pnl):,.0f}원 ({abs(pnl_pct):.2f}%)"

        usd_class = "positive" if (usd_chg or 0) >= 0 else "negative"
        usd_chg_sign = "▲" if (usd_chg or 0) >= 0 else "▼"
        usd_text_label = f"USD/KRW {usd_rate:,.2f} " if usd_rate else None
        usd_text_num = f"{usd_chg_sign}{abs(usd_chg or 0):.2f}%" if usd_rate else None

        summary = ui.div(
            ui.div(
                ui.span("포트폴리오", class_="account-alias"),
                ui.input_action_button("force_update", "↺", class_="force-update-btn"),
                style="display:flex; justify-content:space-between; align-items:center;"
            ),
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

        ticker_rows = []
        for ticker, qty, name, price, chg_pct, market, leverage in rows_sorted:
            qty_f = float(qty or 0); price_f = float(price or 0); chg_pct_f = float(chg_pct or 0)
            leverage = int(leverage) if leverage else 1
            display_name = name or ticker
            qty_str = format_qty(ticker, qty, market)

            is_cash = ticker in ("KRW", "USD")
            if not is_cash and market:
                is_active = is_market_open(market)
                status_dot = "●" if is_active else "○"
                status_class = "status-active" if is_active else "status-idle"
                status_text = "업데이트 중" if is_active else "대기(휴장)"

            if ticker == "KRW":
                amount_str = f"{qty_f:,.0f}원"; chg_str = ""; chg_class = ""
            else:
                if ticker == "USD": amount = qty_f * usd_rate
                elif market in ("NAS", "AMS", "ARC"): amount = qty_f * price_f * usd_rate
                else: amount = qty_f * price_f
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
                            ui.span(f"{status_dot} {status_text}", class_=f"ticker-status {status_class}") if not is_cash and market else None,
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
            ui.div(
                ui.div(*ticker_rows, class_="ticker-list"),
                class_="page-inner"
            )
        )