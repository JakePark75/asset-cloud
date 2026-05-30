from shiny import ui, render, module, reactive
from db import get_connection, get_config, save_config

@module.ui
def settings_ui():
    return ui.div(
        ui.div(
            # 시세조회 간격
            ui.div(
                ui.p("시세조회 간격", style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.08em; margin-bottom:12px;"),
                ui.output_ui("interval_buttons"),
                style="padding: 20px 0; border-bottom: 1px solid #1e1e1e;"
            ),
            # 티커 관리
            ui.div(
                ui.div(
                    ui.p("티커 관리", style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.08em; margin:0;"),
                    ui.input_action_button("btn_add_ticker", "+ 추가", class_="btn-danger-sm", style="color:#00c073;"),
                    style="display:flex; justify-content:space-between; align-items:center; padding: 20px 0 12px 0;"
                ),
                ui.output_ui("ticker_list"),
                style="border-bottom: 1px solid #1e1e1e; padding-bottom: 8px;"
            ),
            # 로그아웃
            ui.div(
                ui.tags.button(
                    "로그아웃",
                    style="background:none; border:none; color:#888; font-size:14px; padding: 20px 0; cursor:pointer; width:100%; text-align:center;",
                    onclick="deleteCookie('auth_token'); location.reload();"
                ),
            ),
            class_="page-inner",
        ),
        ui.output_ui("modal_add_ticker"),
    )

@module.server
def settings_server(input, output, session):
    refresh = reactive.value(0)
    show_modal_ticker = reactive.value(False)

    # 시세조회 간격 버튼 렌더링
    @render.ui
    def interval_buttons():
        current = get_config().get("interval", 1)
        buttons = []
        for v in [1, 3, 5, 10, 30]:
            active_class = "interval-btn active" if v == current else "interval-btn"
            buttons.append(
                ui.tags.button(
                    f"{v}분",
                    class_=active_class,
                    onclick=f"Shiny.setInputValue('settings-btn_save_interval', {v}, {{priority: 'event'}}); document.querySelectorAll('.interval-btn').forEach(b => b.classList.remove('active')); this.classList.add('active');"
                )
            )
        return ui.div(*buttons, style="display:flex; gap:8px;")

    # 시세조회 간격 저장
    @reactive.effect
    @reactive.event(input.btn_save_interval)
    def _():
        val = input.btn_save_interval()
        if not val or val < 1:
            return
        config = get_config()
        config["interval"] = val
        save_config(config)

    # 수동 티커 목록
    @render.ui
    def ticker_list():
        refresh()
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT ticker, name, market, leverage FROM tickers WHERE is_manual = true ORDER BY ticker")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return ui.p("등록된 수동 티커가 없습니다.", style="color:#888; padding: 8px 0;")

        items = []
        for ticker, name, market, leverage in rows:
            items.append(
                ui.div(
                    ui.div(
                        ui.div(f"{name} ({ticker})", class_="ticker-name"),
                        ui.div(f"{market} / x{leverage}", class_="ticker-qty"),
                    ),
                    ui.tags.button(
                        "삭제",
                        class_="btn-danger-sm",
                        onclick=f"if(confirm('{ticker} 티커를 삭제할까요?')) Shiny.setInputValue('{session.ns('confirm_delete_ticker')}', '{ticker}', {{priority: 'event'}});"
                    ),
                    class_="ticker-row",
                )
            )
        return ui.div(*items)

    # 티커 삭제
    @reactive.effect
    @reactive.event(input.confirm_delete_ticker)
    def _():
        ticker = input.confirm_delete_ticker()
        if not ticker:
            return
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM tickers WHERE ticker = %s AND is_manual = true", (ticker,))
        conn.commit()
        cur.close()
        conn.close()
        refresh.set(refresh() + 1)

    # 티커 추가 모달
    @reactive.effect
    @reactive.event(input.btn_add_ticker)
    def _():
        show_modal_ticker.set(True)

    @reactive.effect
    @reactive.event(input.modal_ticker_close)
    def _():
        show_modal_ticker.set(False)

    @render.ui
    def modal_add_ticker():
        if not show_modal_ticker():
            return ui.div()
        return ui.div(
            ui.div(
                ui.div(
                    ui.h4("티커 추가", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon",
                            onclick=f"Shiny.setInputValue('{session.ns('modal_ticker_close')}', Math.random(), {{priority: 'event'}});"),
                    class_="modal-header-row",
                ),
                ui.input_text("new_ticker", "티커", placeholder="예) USDKRW=X"),
                ui.input_text("new_ticker_name", "종목명", placeholder="예) 달러/원 환율"),
                ui.input_select("new_ticker_market", "시장", choices=["KR", "NAS", "AMS", "ARC", "IDX"]),
                ui.input_numeric("new_ticker_leverage", "레버리지", value=1, min=1, max=3),
                ui.input_action_button("btn_confirm_add_ticker", "추가", class_="btn-add"),
                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            class_="modal-overlay",
            onclick=f"Shiny.setInputValue('{session.ns('modal_ticker_close')}', Math.random(), {{priority: 'event'}});",
        )

    # 티커 추가 확인
    @reactive.effect
    @reactive.event(input.btn_confirm_add_ticker)
    def _():
        ticker = input.new_ticker().strip().upper()
        name = input.new_ticker_name().strip()
        market = input.new_ticker_market()
        leverage = input.new_ticker_leverage()

        if not ticker or not name:
            return

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tickers (ticker, name, market, leverage, is_manual)
            VALUES (%s, %s, %s, %s, true)
            ON CONFLICT (ticker) DO UPDATE SET
                name = EXCLUDED.name,
                market = EXCLUDED.market,
                leverage = EXCLUDED.leverage,
                is_manual = true
        """, (ticker, name, market, leverage))
        conn.commit()
        cur.close()
        conn.close()
        show_modal_ticker.set(False)
        refresh.set(refresh() + 1)