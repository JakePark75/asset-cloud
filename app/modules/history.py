from shiny import ui, reactive, module, render
from shinywidgets import output_widget, render_widget

from app.price_signal import price_signal
from .history_DAL import load_history
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
            output_widget("chart_asset"),
            class_="chart-section",
        ),

        # 그래프 2: TWR vs NDX100
        ui.div(
            ui.p("운용 수익률 vs NDX100", class_="chart-title"),
            output_widget("chart_twr"),
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

    @render_widget
    def chart_asset():
        return make_chart_asset(history_data())

    @render_widget
    def chart_twr():
        return make_chart_twr(history_data())

    @render.ui
    def history_table():
        return render_history_table(history_data())