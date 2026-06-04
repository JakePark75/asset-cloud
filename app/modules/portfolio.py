from shiny import ui, render, reactive, module
import subprocess
import sys
import psycopg2
import json
from app.db import get_usd_krw
from app.price_signal import price_signal
from app.modules.components import render_summary_header
from app.modules.accounts_components import render_ticker_row

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


@module.ui
def portfolio_ui():
    return ui.div(
        ui.output_ui("portfolio_content"),
        class_="page-container"
    )

@module.server
def portfolio_server(input, output, session):

    @reactive.effect
    @reactive.event(input.force_update)
    def _show_force_modal():
        m = ui.modal(
            ui.div(
                ui.p("전체 종목 시세를 강제 조회합니다. 장외시간 종목도 포함됩니다."),
                ui.div(
                    ui.input_action_button("force_confirm", "확인", class_="btn-primary"),
                    ui.input_action_button("force_cancel", "취소", class_="btn-secondary"),
                    class_="modal-btn-row-half",
                    style="display:flex; gap:8px; margin-top:12px;",
                ),
                class_="modal-body-inner",
            ),
            title="강제 시세 조회",
            easy_close=True,
            footer=None,
        )
        ui.modal_show(m)

    @reactive.effect
    @reactive.event(input.force_confirm)
    def _do_force_update():
        ui.modal_remove()
        subprocess.Popen(
            [sys.executable, "scheduler/price_updater.py", "--force"],
            cwd="/home/ubuntu/asset-cloud"
        )

    @reactive.effect
    @reactive.event(input.force_cancel)
    def _cancel_force_update():
        ui.modal_remove()

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

        pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0

        summary = render_summary_header(
            label="포트폴리오",
            total_asset=total_asset,
            pnl=total_pnl,
            pnl_pct=pnl_pct,
            usd_rate=usd_rate or None,
            usd_chg=usd_chg,
        )

        ticker_rows = []
        for ticker, qty, name, price, chg_pct, market, leverage in rows_sorted:
            pos = (None, ticker, qty, name, price, chg_pct, market, leverage)
            ticker_rows.append(render_ticker_row(pos, usd_rate))

        return ui.div(
            ui.div(
                summary,
                ui.input_action_button("force_update", "↺", class_="force-update-btn"),
                style="position:relative;",
            ),
            ui.div(
                ui.div(*ticker_rows, class_="ticker-list"),
                class_="page-inner"
            )
        )