import json
from shiny import ui, reactive, module, render
from app.price_signal import price_signal
from app.db import get_db
from .history_DAL import load_history, save_cash_flow
from .history_charts import make_chart_asset, make_chart_twr


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def history_ui():
    return ui.div(
        # 기간 버튼 (JS에서 직접 relayout, 서버 호출 없음)
        ui.div(
            ui.tags.button("1개월", class_="period-btn",
                           onclick="setChartPeriod('1m', this)"),
            ui.tags.button("3개월", class_="period-btn active",
                           onclick="setChartPeriod('3m', this)"),
            ui.tags.button("전체",  class_="period-btn",
                           onclick="setChartPeriod('all', this)"),
            class_="period-btn-group",
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

        # 테이블 (JS 렌더링)
        ui.div(
            ui.tags.table(
                ui.tags.thead(
                    ui.tags.tr(
                        ui.tags.th("날짜"),
                        ui.tags.th("총자산"),
                        ui.tags.th("전일대비"),
                        ui.tags.th("Exp"),
                        ui.tags.th("현금"),
                        ui.tags.th("입출금"),
                        ui.tags.th("x1"),
                        ui.tags.th("x2"),
                        ui.tags.th("x3"),
                        ui.tags.th("TWR"),
                        ui.tags.th("나스닥"),
                        ui.tags.th("환율"),
                    )
                ),
                ui.tags.tbody(id="history-tbody"),
                class_="history-table",
            ),
            ui.div("▼ 더 보기", id="history-load-more", class_="history-load-more"),
            class_="history-table-wrap",
        ),

        # ── JS ──────────────────────────────────────────────────────────────
        ui.tags.script("""
        (function() {

          // ── 기간 버튼 ────────────────────────────────────────────────────
          window.setChartPeriod = function(period, el) {
            document.querySelectorAll('.period-btn').forEach(function(b) {
              b.classList.remove('active');
            });
            el.classList.add('active');

            var charts = ['chart-asset', 'chart-twr'];
            charts.forEach(function(id) {
              var gd = document.getElementById(id);
              if (!gd || !gd.data) return;

              var xs = gd.data[0].x;
              if (!xs || xs.length === 0) return;
              var first = xs[0];
              var last  = xs[xs.length - 1];

              var endDate   = new Date(last);
              var startDate;
              if (period === '1m') {
                startDate = new Date(endDate);
                startDate.setMonth(startDate.getMonth() - 1);
                if (startDate < new Date(first)) startDate = new Date(first);
              } else if (period === '3m') {
                startDate = new Date(endDate);
                startDate.setMonth(startDate.getMonth() - 3);
                if (startDate < new Date(first)) startDate = new Date(first);
              } else {
                startDate = new Date(first);
              }

              Plotly.relayout(gd, {
                'xaxis.range': [
                  startDate.toISOString().slice(0,10),
                  endDate.toISOString().slice(0,10),
                ]
              });
            });
          };

          // ── 테이블 JS 렌더링 ─────────────────────────────────────────────
          var _allRows = [];
          var _rendered = 0;
          var PAGE = 50;

          function fmtKrw(v) {
            var n = parseFloat(v) || 0;
            var abs = Math.abs(n);
            var s;
            if (abs >= 1e8)      s = (n / 1e8).toFixed(1) + "억";
            else if (abs >= 1e4) s = Math.round(n / 1e4) + "만";
            else                 s = Math.round(n).toLocaleString();
            return s;
          }

          function fmtPct(v) {
            var n = parseFloat(v) || 0;
            return (n * 100).toFixed(1) + "%";
          }

          function renderRows(rows) {
            var tbody = document.getElementById('history-tbody');
            if (!tbody) return;
            rows.forEach(function(r, idx) {
              var date    = r[0];
              var total   = parseFloat(r[1]) || 0;
              var twr     = parseFloat(r[2]) || 0;
              var ndx     = parseFloat(r[3]) || 0;
              var cf      = parseFloat(r[4]) || 0;
              var cf_note = r[5] || '';
              var exp     = parseFloat(r[6]) || 0;
              var cash    = parseFloat(r[7]) || 0;
              var x1      = parseFloat(r[8]) || 0;
              var x2      = parseFloat(r[9]) || 0;
              var x3      = parseFloat(r[10]) || 0;
              var usd_krw = parseFloat(r[11]) || 0;
              var prev    = r[12];  // 전일 total (서버에서 미리 계산)

              // 전일대비
              var diffCell = '<span style="color:#555">-</span>';
              if (prev !== null && prev !== '' && parseFloat(prev) !== 0) {
                var diff = total - parseFloat(prev);
                var pct  = diff / parseFloat(prev) * 100;
                var sign = diff >= 0 ? '+' : '';
                var cls  = diff >= 0 ? 'positive' : 'negative';
                diffCell = '<span class="' + cls + '">' + sign + fmtKrw(diff) + '<br><span style="font-size:11px">' + sign + pct.toFixed(2) + '%</span></span>';
              }

              // 입출금
              var cfCell = '<span style="color:#555">-</span>';
              if (cf !== 0) {
                var cfSign = cf > 0 ? '+' : '';
                var cfCls  = cf > 0 ? 'positive' : 'negative';
                var cfStr  = cfSign + fmtKrw(cf);
                if (cf_note) {
                  cfCell = '<span class="' + cfCls + '" title="' + cf_note + '" style="cursor:pointer;border-bottom:1px dotted">' + cfStr + '</span>';
                } else {
                  cfCell = '<span class="' + cfCls + '">' + cfStr + '</span>';
                }
              }

              var dateShort = date.slice(2).replace(/-/g, '');
              var tr = document.createElement('tr');
              tr.style.cursor = 'pointer';
              tr.innerHTML =
                '<td>' + dateShort + '</td>' +
                '<td style="text-align:right">' + fmtKrw(total) + '</td>' +
                '<td style="text-align:right">' + diffCell + '</td>' +
                '<td style="text-align:right">' + (exp * 100).toFixed(1) + '%</td>' +
                '<td style="text-align:right">' + (cash * 100).toFixed(1) + '%</td>' +
                '<td style="text-align:right">' + cfCell + '</td>' +
                '<td style="text-align:right">' + (x1 * 100).toFixed(1) + '%</td>' +
                '<td style="text-align:right">' + (x2 * 100).toFixed(1) + '%</td>' +
                '<td style="text-align:right">' + (x3 * 100).toFixed(1) + '%</td>' +
                '<td style="text-align:right">' + fmtKrw(twr) + '</td>' +
                '<td style="text-align:right">' + (ndx ? ndx.toFixed(2) : '-') + '</td>' +
                '<td style="text-align:right">' + (usd_krw ? usd_krw.toFixed(2) : '-') + '</td>';
              tr.addEventListener('click', function() {
                Shiny.setInputValue('history-selected_date', date, {priority: 'event'});
              });
              tbody.appendChild(tr);
            });
          }

          function loadMore() {
            var next = _allRows.slice(_rendered, _rendered + PAGE);
            renderRows(next);
            _rendered += next.length;
            var btn = document.getElementById('history-load-more');
            if (btn) btn.style.display = _rendered >= _allRows.length ? 'none' : 'block';
          }

          Shiny.addCustomMessageHandler('history_data', function(data) {
            _allRows = data;
            _rendered = 0;
            var tbody = document.getElementById('history-tbody');
            if (tbody) tbody.innerHTML = '';
            loadMore();
          });

          document.addEventListener('click', function(e) {
            if (e.target && e.target.id === 'history-load-more') {
              loadMore();
            }
          });

        })();
        """),

        class_="page-inner",
    )


# ── Server ────────────────────────────────────────────────────────────────────

@module.server
def history_server(input, output, session):

    @reactive.calc
    def history_data():
        price_signal.get()
        return load_history()

    @render.ui
    def chart_asset():
        return ui.HTML(make_chart_asset(history_data()))

    @render.ui
    def chart_twr():
        return ui.HTML(make_chart_twr(history_data()))

    @reactive.effect
    def _send_history_table():
        # NOTE: 테이블 데이터가 send_custom_message(JSON)로 전송되어 JS가 즉시 그리기 때문에
        # 차트(output_ui)보다 테이블이 먼저 화면에 나타나는 증상이 있음.
        # send_custom_message가 Shiny WebSocket 렌더링 큐와 별개로 동작하기 때문.
        # 현재는 빠른 렌더링의 부작용으로 간주하고 그냥 둠.
        rows = history_data()
        if not rows:
            return
        # 내림차순, 전일 total 미리 계산해서 같이 전송
        rows_desc = list(reversed(rows))
        asset_map = {r[0]: float(r[1] or 0) for r in rows}
        dates_asc = [r[0] for r in rows]
        index_map = {r[0]: i for i, r in enumerate(rows)}

        data = []
        for r in rows_desc:
            idx  = index_map[r[0]]
            prev = float(rows[idx - 1][1] or 0) if idx > 0 else None
            data.append([
                str(r[0]),   # date
                str(r[1]),   # total_asset
                str(r[2]),   # twr_asset
                str(r[3]),   # ndx100
                str(r[4]),   # cash_flow
                r[5] or '',  # cash_flow_note
                str(r[6]),   # exposure
                str(r[7]),   # cash_ratio
                str(r[8]),   # x1_ratio
                str(r[9]),   # x2_ratio
                str(r[10]),  # x3_ratio
                str(r[11]),  # usd_krw
                str(prev) if prev is not None else '',  # 전일 total
            ])

        import asyncio
        async def _send():
            await session.send_custom_message("history_data", data)
        asyncio.ensure_future(_send())

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