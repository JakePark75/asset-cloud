from shiny import ui, reactive, module, render
from app.price_signal import price_signal
from app.db import get_db
from .history_DAL import load_history, save_cash_flow
from .history_charts import make_chart_asset, make_chart_twr
from .history_table import render_history_table


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def history_ui():
    return ui.div(
        # 기간 필터 버튼
        ui.div(
            ui.tags.button("1개월", class_="period-btn",
                           onclick="setPeriod('1m', this)"),
            ui.tags.button("3개월", class_="period-btn active",
                           onclick="setPeriod('3m', this)"),
            ui.tags.button("전체",  class_="period-btn",
                           onclick="setPeriod('all', this)"),
            class_="period-btn-group",
        ),

        # hidden input: JS → Shiny 기간 값 전달
        ui.div(
            ui.input_text("period", "", value="3m"),
            style="display:none;",
        ),

        # 그래프 1: 총자산 추이
        ui.div(
            ui.p("총자산 추이", class_="chart-title"),
            ui.output_ui("chart_asset"),
            class_="chart-section",
        ),

        # 그래프 2: TWR vs NDX100
        ui.div(
            ui.p("운용 수익률 vs NDX100", class_="chart-title"),
            ui.output_ui("chart_twr"),
            class_="chart-section",
        ),

        # 테이블
        ui.div(
            ui.output_ui("history_table"),
            class_="history-table-wrap",
        ),

        # JS: 기간 버튼 클릭 → Shiny input 전달
        ui.tags.script("""
            function setPeriod(val, el) {
                document.querySelectorAll('.period-btn').forEach(function(b) {
                    b.classList.remove('active');
                });
                el.classList.add('active');
                Shiny.setInputValue('history-period', val, {priority: 'event'});
            }
        """),

        class_="page-inner",
    )


# ── Server ────────────────────────────────────────────────────────────────────

@module.server
def history_server(input, output, session):

    @reactive.calc
    def history_data():
        price_signal.get()
        period = input.period() or "3m"
        return load_history(period)

    @render.ui
    def chart_asset():
        fig = make_chart_asset(history_data())
        return ui.HTML(fig.to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": False}))
    
    @render.ui
    def chart_twr():
        fig = make_chart_twr(history_data())
        return ui.HTML(fig.to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": False}))

    @render.ui
    def history_table():
        return render_history_table(history_data())
    
    @reactive.effect
    @reactive.event(input.selected_date)
    def _open_edit_modal():
        date_str = input.selected_date()
        if not date_str:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT cash_flow, cash_flow_note FROM daily_summary WHERE date = %s", (date_str,))
            row = cur.fetchone()
            cur.close()
        cf = int(row[0]) if row and row[0] else 0
        note = row[1] if row and row[1] else ""

        m = ui.modal(
            ui.div(
                ui.div(f"{date_str}", class_="modal-date-label"),
                ui.div(
                    ui.tags.label("입출금 (+ 입금 / - 출금)", class_="modal-label"),
                    ui.input_numeric("edit_cf", "", value=cf),
                ),
                ui.div(
                    ui.tags.label("사유", class_="modal-label"),
                    ui.input_text("edit_note", "", value=note, placeholder="(선택)"),
                ),
                ui.p("입출금 수정 시 해당 날짜 이후의 TWR이 전체 재계산됩니다. 자산총액·익스포저 등 나머지 지표는 당시 기록값이 유지됩니다.", style="font-size:11px; color:#888;"),
                ui.input_action_button("edit_save", "저장", class_="btn-primary"),
                class_="modal-body-inner",
            ),
            title="입출금 수정",
            easy_close=True,
            footer=None,
        )

        ui.modal_show(m)

    @reactive.effect
    @reactive.event(input.edit_save)
    def _save_cash_flow():
        date_str = input.selected_date()
        cf = input.edit_cf() or 0
        note = input.edit_note() or ""
        save_cash_flow(date_str, cf, note)
        ui.modal_remove()
        ui.notification_show("저장됐습니다.", type="message", duration=2)    