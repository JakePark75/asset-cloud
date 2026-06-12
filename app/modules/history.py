import json
import datetime
from shiny import ui, reactive, module, render
from app.price_signal import price_signal
from app.db import get_db
from .history_DAL import load_history, load_today_row, save_cash_flow
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
                        ui.tags.th("x3"),
                        ui.tags.th("x2"),
                        ui.tags.th("x1"),
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

          function fmtKrw2(v) {
            var n = parseFloat(v) || 0;
            var abs = Math.abs(n);
            if (abs >= 1e8) return (n / 1e8).toFixed(2) + "억";
            if (abs >= 1e4) return Math.round(n / 1e4) + "만";
            return Math.round(n).toLocaleString();
          }

          function buildTr(r) {
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
            var prev    = r[12];

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
            tr.dataset.date = date;
            tr.innerHTML =
              '<td>' + dateShort + '</td>' +
              '<td style="text-align:right">' + fmtKrw2(total) + '</td>' +
              '<td style="text-align:right">' + diffCell + '</td>' +
              '<td style="text-align:right">' + (exp * 100).toFixed(1) + '%</td>' +
              '<td style="text-align:right">' + (cash * 100).toFixed(1) + '%</td>' +
              '<td style="text-align:right">' + cfCell + '</td>' +
              '<td style="text-align:right">' + (x3 * 100).toFixed(1) + '%</td>' +
              '<td style="text-align:right">' + (x2 * 100).toFixed(1) + '%</td>' +
              '<td style="text-align:right">' + (x1 * 100).toFixed(1) + '%</td>' +
              '<td style="text-align:right">' + fmtKrw(twr) + '</td>' +
              '<td style="text-align:right">' + (ndx ? ndx.toFixed(2) : '-') + '</td>' +
              '<td style="text-align:right">' + (usd_krw ? usd_krw.toFixed(2) : '-') + '</td>';
            tr.addEventListener('click', function() {
              Shiny.setInputValue('history-selected_date', date, {priority: 'event'});
            });
            return tr;
          }

          function renderRows(rows) {
            var tbody = document.getElementById('history-tbody');
            if (!tbody) return;
            rows.forEach(function(r) {
              tbody.appendChild(buildTr(r));
            });
          }

          function loadMore() {
            var next = _allRows.slice(_rendered, _rendered + PAGE);
            renderRows(next);
            _rendered += next.length;
            var btn = document.getElementById('history-load-more');
            if (btn) btn.style.display = _rendered >= _allRows.length ? 'none' : 'block';
          }

          // ── 초기 전체 로드 ────────────────────────────────────────────────
          Shiny.addCustomMessageHandler('history_data', function(data) {
            _allRows = data;
            _rendered = 0;
            var tbody = document.getElementById('history-tbody');
            if (tbody) tbody.innerHTML = '';
            loadMore();
          });

          // ── today_row 갱신 — 최상단 행 교체 + 차트 끝단 업데이트 ──────────
          Shiny.addCustomMessageHandler('today_row_update', function(r) {

            // ── 1. 테이블 최상단 행 교체 ──────────────────────────────────
            var tbody = document.getElementById('history-tbody');
            if (tbody) {
              var newTr = buildTr(r);
              var today = r[0];
              var existing = tbody.querySelector('tr[data-date="' + today + '"]');
              if (existing) {
                tbody.replaceChild(newTr, existing);
              } else {
                _allRows.unshift(r);
                _rendered += 1;
                tbody.insertBefore(newTr, tbody.firstChild);
              }
            }

            // ── 2. chart-asset 끝단 업데이트 ─────────────────────────────
            var gdAsset = document.getElementById('chart-asset');
            if (gdAsset && gdAsset.data) {
              var date      = r[0];
              var total     = parseFloat(r[1]) || 0;
              var cf        = parseFloat(r[4]) || 0;
              var cf_note   = r[5] || '';

              // trace 0 (총자산 라인) — x/y/customdata 끝단 교체
              var xs0  = gdAsset.data[0].x.slice();
              var ys0  = gdAsset.data[0].y.slice();
              var cd0  = (gdAsset.data[0].customdata || []).slice();

              if (xs0[xs0.length - 1] === date) {
                xs0[xs0.length - 1] = date;
                ys0[ys0.length - 1] = total;
                cd0[cd0.length - 1] = [formatKrwFull(total) + '원', cf, cf_note];
              } else {
                xs0.push(date);
                ys0.push(total);
                cd0.push([formatKrwFull(total) + '원', cf, cf_note]);
              }
              Plotly.restyle(gdAsset, {x: [xs0], y: [ys0], customdata: [cd0]}, [0]);

              // 입금/출금 마커 트레이스 처리
              // 기존 오늘 마커 트레이스 제거 (traceIdx >= 1 이고 x가 [date]인 것)
              var toDelete = [];
              for (var ti = gdAsset.data.length - 1; ti >= 1; ti--) {
                var tx = gdAsset.data[ti].x;
                if (tx && tx.length === 1 && tx[0] === date) {
                  toDelete.push(ti);
                }
              }
              if (toDelete.length > 0) {
                Plotly.deleteTraces(gdAsset, toDelete);
              }

              // 오늘 cf 있으면 마커 트레이스 추가
              if (cf !== 0) {
                var markerColor  = cf > 0 ? '#ff4d4d' : '#4d9fff';
                var markerSymbol = cf > 0 ? 'triangle-up' : 'triangle-down';
                var markerName   = cf > 0 ? '입금' : '출금';
                var markerY      = cf > 0 ? total * 1.012 : total * 0.988;
                var cdStr        = (cf > 0 ? '+' : '') + Math.round(cf).toLocaleString() + '원' + (cf_note ? '\\n' + cf_note : '');
                Plotly.addTraces(gdAsset, {
                  x: [date],
                  y: [markerY],
                  mode: 'markers',
                  name: markerName,
                  marker: {symbol: markerSymbol, size: 10, color: markerColor, line: {color: '#ffffff', width: 1}},
                  hovertemplate: '%{customdata}<extra>' + markerName + '</extra>',
                  customdata: [cdStr],
                });
              }
            }

            // ── 3. chart-twr 끝단 업데이트 ───────────────────────────────
            var gdTwr = document.getElementById('chart-twr');
            if (gdTwr && gdTwr.data && gdTwr.data.length >= 2) {
              var date    = r[0];
              var twrRaw  = parseFloat(r[2]) || 0;
              var ndxRaw  = parseFloat(r[3]) || 0;

              // 기준값 (첫 번째 데이터 포인트) 으로 % 계산
              var twrBase = gdTwr.data[0].y[0];  // 이미 % 값
              var ndxBase = gdTwr.data[1].y[0];

              // twr_asset / ndx100 은 절대값이므로 최초 기준값 필요
              // → 서버에서 % 계산해서 넘겨줌 (r[13], r[14])
              var twrPct = parseFloat(r[13]);
              var ndxPct = parseFloat(r[14]);

              var xs1 = gdTwr.data[0].x.slice();
              var ys1 = gdTwr.data[0].y.slice();
              var xs2 = gdTwr.data[1].x.slice();
              var ys2 = gdTwr.data[1].y.slice();

              if (xs1[xs1.length - 1] === date) {
                ys1[ys1.length - 1] = twrPct;
                ys2[ys2.length - 1] = ndxPct;
              } else {
                xs1.push(date); ys1.push(twrPct);
                xs2.push(date); ys2.push(ndxPct);
              }
              Plotly.restyle(gdTwr, {x: [xs1], y: [ys1]}, [0]);
              Plotly.restyle(gdTwr, {x: [xs2], y: [ys2]}, [1]);
            }
          });

          // 숫자 → 원화 문자열 (차트 customdata용)
          function formatKrwFull(n) {
            return Math.round(n).toLocaleString();
          }

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

    today_cf_trigger = reactive.value(0)  # 오늘 입출금 저장 시 강제 갱신용
    _reload_trigger  = reactive.value(0)  # 입출금 수정(과거) 시 DB rows 재로드용

    # ── 과거 DB rows 캐시 — 세션 시작 시 1회 로드, 입출금 수정 시 재로드 ──
    @reactive.calc
    def _db_rows():
        _reload_trigger.get()  # 입출금 수정 시 무효화
        return load_history()

    # ── 차트: DB rows + today_row 합산 (페이지 로드 시 1회) ──────────────────
    @reactive.calc
    def _all_rows_for_chart():
        rows = list(_db_rows())
        t = load_today_row()
        if t:
            today = datetime.date.today()
            if not rows or rows[-1][0] < today:
                rows.append((
                    today,
                    t.get("total_asset"),
                    t.get("twr_asset"),
                    t.get("ndx100"),
                    t.get("cash_flow", 0),
                    t.get("cash_flow_note"),
                    t.get("exposure"),
                    t.get("cash_ratio"),
                    t.get("x1_ratio"),
                    t.get("x2_ratio"),
                    t.get("x3_ratio"),
                    t.get("usd_krw"),
                ))
        return rows

    @render.ui
    def chart_asset():
        return ui.HTML(make_chart_asset(_all_rows_for_chart()))

    @render.ui
    def chart_twr():
        return ui.HTML(make_chart_twr(_all_rows_for_chart()))

    # ── 초기 테이블 전송 — 페이지 로드 시 1회 ───────────────────────────────
    @reactive.effect
    async def _send_history_table():
        rows = list(_db_rows())
        t = load_today_row()
        today = datetime.date.today()

        # today_row를 맨 앞에 붙여서 내림차순으로 전송
        if t and (not rows or rows[-1][0] < today):
            rows_desc = [
                (today,
                 t.get("total_asset"), t.get("twr_asset"), t.get("ndx100"),
                 t.get("cash_flow", 0), t.get("cash_flow_note"),
                 t.get("exposure"), t.get("cash_ratio"),
                 t.get("x1_ratio"), t.get("x2_ratio"), t.get("x3_ratio"),
                 t.get("usd_krw"))
            ] + list(reversed(rows))
        else:
            rows_desc = list(reversed(rows))

        index_map = {r[0]: i for i, r in enumerate(rows)}

        data = []
        for r in rows_desc:
            # 전일 total: DB rows 기준으로 계산 (today_row의 전일은 DB 마지막 행)
            if r[0] == today:
                prev = float(rows[-1][1] or 0) if rows else None
            else:
                idx = index_map.get(r[0])
                prev = float(rows[idx - 1][1] or 0) if idx is not None and idx > 0 else None

            data.append([
                str(r[0]),
                str(r[1]),
                str(r[2]),
                str(r[3]),
                str(r[4]),
                r[5] or '',
                str(r[6]),
                str(r[7]),
                str(r[8]),
                str(r[9]),
                str(r[10]),
                str(r[11]),
                str(prev) if prev is not None else '',
            ])

        await session.send_custom_message("history_data", data)

    # ── NOTIFY 수신 시 today_row만 갱신 ─────────────────────────────────────
    @reactive.effect
    async def _send_today_row_update():
        price_signal.get()       # NOTIFY 의존성
        today_cf_trigger.get()   # 오늘 입출금 수정 시 갱신

        t = load_today_row()
        if not t:
            return

        rows = _db_rows()
        today = datetime.date.today()
        prev = float(rows[-1][1] or 0) if rows else None

        # twr_pct, ndx_pct 계산 (차트 끝단 업데이트용)
        # 기준: DB 첫 번째 행의 twr_asset, ndx100
        twr_pct = 0.0
        ndx_pct = 0.0
        if rows:
            base_twr = float(rows[0][2] or 0)
            base_ndx = float(rows[0][3] or 0)
            if base_twr:
                twr_pct = (float(t.get("twr_asset") or 0) / base_twr - 1) * 100
            if base_ndx:
                ndx_pct = (float(t.get("ndx100") or 0) / base_ndx - 1) * 100

        row = [
            str(t.get("date", str(today))),  # [0]
            str(t.get("total_asset")),        # [1]
            str(t.get("twr_asset")),          # [2]
            str(t.get("ndx100")),             # [3]
            str(t.get("cash_flow", 0)),       # [4]
            t.get("cash_flow_note") or '',    # [5]
            str(t.get("exposure")),           # [6]
            str(t.get("cash_ratio")),         # [7]
            str(t.get("x1_ratio")),           # [8]
            str(t.get("x2_ratio")),           # [9]
            str(t.get("x3_ratio")),           # [10]
            str(t.get("usd_krw")),            # [11]
            str(prev) if prev is not None else '',  # [12] 전일 total
            str(twr_pct),                     # [13] twr %
            str(ndx_pct),                     # [14] ndx %
        ]

        await session.send_custom_message("today_row_update", row)

    # ── 날짜 클릭 → 입출금 수정 모달 ────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.selected_date)
    def _open_edit_modal():
        date_str = input.selected_date()
        if not date_str:
            return

        today_str = str(datetime.date.today())
        is_today = (date_str == today_str)

        if is_today:
            cf, note = 0, ""
            try:
                from common.redis_store import get_redis
                r = get_redis()
                if r:
                    cf   = int(r.get("today_cash_flow") or 0)
                    note = r.get("today_cash_flow_note") or ""
            except Exception:
                pass
        else:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("SELECT cash_flow, cash_flow_note FROM daily_summary WHERE date = %s", (date_str,))
                row = cur.fetchone()
                cur.close()
            cf   = int(row[0]) if row and row[0] else 0
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

    # ── 입출금 저장 ──────────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.edit_save)
    def _save_cash_flow():
        date_str = input.selected_date()
        cf   = input.edit_cf() or 0
        note = input.edit_note() or ""

        today_str = str(datetime.date.today())
        if date_str == today_str:
            try:
                from common.redis_store import get_redis
                r = get_redis()
                if r:
                    r.set("today_cash_flow", int(cf))
                    r.set("today_cash_flow_note", note)
            except Exception as e:
                print(f"[history] today_cash_flow Redis 저장 실패: {e}")
            from common.redis_store import recalc_today_row
            recalc_today_row()
            today_cf_trigger.set(today_cf_trigger.get() + 1)
        else:
            save_cash_flow(date_str, cf, note)
            # 과거 입출금 수정 → DB rows 재로드 + 차트 갱신
            _reload_trigger.set(_reload_trigger.get() + 1)

        ui.modal_remove()
        ui.notification_show("저장됐습니다.", type="message", duration=2)