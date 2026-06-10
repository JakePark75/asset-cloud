from shiny import ui, render, reactive, module
import subprocess
import sys
from app.db import get_db, get_usd_krw, get_config, get_market_currency
from app.price_signal import price_signal
from app.modules.components import render_summary_header
from app.modules.accounts_components import render_ticker_row


# ── DAL ───────────────────────────────────────────────────────────────────────

def load_portfolio():
    # ---------------------------------------------------------------------------
    # Step 6-3: current_price / change_pct 조회를 DB → Redis 전환
    #   - 시세(current_price, change_pct) : Redis get_all_prices() 매핑
    #   - 메타데이터(name, market, leverage) : DB 유지
    #   - usd_rate / usd_chg              : get_usd_krw() 경유 (db.py에서 이미 Redis 읽음)
    #   - DB 연결                          : psycopg2 직접 호출 → get_db() 컨텍스트 매니저로 교체
    # ---------------------------------------------------------------------------
    from common.redis_store import get_all_prices

    # Redis 전체 시세 로드 (실패 시 빈 dict → 가격 0 처리)
    prices = get_all_prices()

    # usd_krw: db.py get_usd_krw()가 내부적으로 Redis 읽음
    usd_rate, usd_chg = get_usd_krw()
    usd_rate = usd_rate or 0

    with get_db() as conn:
        cur = conn.cursor()

        # current_price / change_pct 제거 — Redis에서 매핑
        # 메타데이터(name, market, leverage)와 수량만 조회
        # 감시계좌(is_watch=true) 제외
        cur.execute("""
            SELECT p.ticker, SUM(p.quantity) AS quantity,
                   t.name, t.market, t.leverage
            FROM positions p
            LEFT JOIN tickers t ON p.ticker = t.ticker
            LEFT JOIN accounts a ON p.account_id = a.id
            WHERE a.is_watch = false
            GROUP BY p.ticker, t.name, t.market, t.leverage
        """)
        db_rows = cur.fetchall()

        # daily_summary 마지막 행 — 어제 총자산(전일대비 손익 계산용), DB 유지
        cur.execute("SELECT total_asset FROM daily_summary ORDER BY date DESC LIMIT 1")
        row = cur.fetchone()
        yesterday_total = float(row[0]) if row else 0.0

        cur.close()

    # Redis 시세를 매핑하여 호출부가 기대하는 튜플 구조로 재구성
    # 반환 형태: (ticker, qty, name, price, change_pct, market, leverage)
    # → render_ticker_row pos 튜플: (pos_id, ticker, qty, name, price, chg_pct, market, leverage)
    rows = []
    for ticker, qty, name, market, leverage in db_rows:
        p_data     = prices.get(ticker)
        price      = float(p_data["price"])      if p_data else 0.0
        change_pct = float(p_data["change_pct"]) if p_data else 0.0
        rows.append((ticker, qty, name, price, change_pct, market, leverage))

    return rows, usd_rate, usd_chg, yesterday_total


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def portfolio_ui():
    return ui.div(
        ui.output_ui("portfolio_content"),
        class_="page-container"
    )


# ── Server ────────────────────────────────────────────────────────────────────

@module.server
def portfolio_server(input, output, session):

    # ── 강제 시세 조회 모달 ───────────────────────────────────────────────────

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
        # 강제 시세 조회: price_updater를 --force 옵션으로 별도 프로세스 실행
        subprocess.Popen(
            [sys.executable, "scheduler/price_updater.py", "--force"],
            cwd="/home/ubuntu/asset-cloud"
        )

    @reactive.effect
    @reactive.event(input.force_cancel)
    def _cancel_force_update():
        ui.modal_remove()

    # ── 포트폴리오 렌더링 ─────────────────────────────────────────────────────

    @render.ui
    def portfolio_content():
        price_signal.get()  # price_signal 구독 → 시세 갱신 시 자동 재렌더링
        rows, usd_rate, usd_chg, yesterday_total = load_portfolio()

        # 총 평가액 합산
        total_asset = 0
        for ticker, qty, name, price, chg_pct, market, leverage in rows:
            qty_f   = float(qty   or 0)
            price_f = float(price or 0)
            if ticker == "KRW":                          amount = qty_f               # 원화 현금
            elif ticker == "USD":                        amount = qty_f * usd_rate    # 달러 현금 → 원화 환산
            elif get_market_currency(market) == "USD":   amount = qty_f * price_f * usd_rate  # 해외 종목 → 원화 환산
            else:                                        amount = qty_f * price_f     # 국내 종목
            total_asset += amount

        total_pnl = total_asset - yesterday_total

        def calc_amount(ticker, qty_f, price_f, market):
            """정렬용 평가액 계산 (총자산 합산 로직과 동일)"""
            if ticker == "KRW":                        return qty_f
            elif ticker == "USD":                      return qty_f * usd_rate
            elif get_market_currency(market) == "USD": return qty_f * price_f * usd_rate
            else:                                      return qty_f * price_f

        # 현금(KRW/USD)을 후순위로, 나머지는 평가액 내림차순 정렬
        rows_sorted = sorted(
            rows,
            key=lambda r: (
                1 if r[0] in ("KRW", "USD") else 0,
                -calc_amount(r[0], float(r[1] or 0), float(r[3] or 0), r[5])
            )
        )

        pnl_pct = (total_pnl / yesterday_total * 100) if yesterday_total else 0

        summary = render_summary_header(
            label="포트폴리오",
            total_asset=total_asset,
            pnl=total_pnl,
            pnl_pct=pnl_pct,
            usd_rate=usd_rate or None,
            usd_chg=usd_chg,
        )

        # pos 튜플 구조: (pos_id, ticker, qty, name, price, chg_pct, market, leverage)
        # pos_id는 포트폴리오에서 불필요하므로 None 전달
        ticker_rows = []
        for ticker, qty, name, price, chg_pct, market, leverage in rows_sorted:
            pos = (None, ticker, qty, name, price, chg_pct, market, leverage)
            ticker_rows.append(render_ticker_row(pos, usd_rate))

        # REST 모드일 때만 강제 시세 조회 버튼 표시 (WS 모드는 실시간 push이므로 불필요)
        cfg = get_config()
        is_realtime = int(cfg.get("interval", 1)) == 0
        force_btn = [] if is_realtime else [
            ui.input_action_button("force_update", "↺", class_="force-update-btn")
        ]

        return ui.div(
            ui.div(
                summary,
                *force_btn,
                ui.div(*ticker_rows, class_="ticker-list"),
                class_="page-inner",
                style="position:relative;",
            )
        )