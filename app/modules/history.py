import json
import datetime
from zoneinfo import ZoneInfo
from shiny import ui, reactive, module, render

KST = ZoneInfo("Asia/Seoul")

def _today_kst() -> datetime.date:
    return datetime.datetime.now(KST).date()
from app.price_signal import price_signal, daily_insert_signal, position_signal
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
            var date    = r.date;
            var total   = parseFloat(r.total_asset) || 0;
            var twr     = parseFloat(r.twr_asset) || 0;
            var ndx     = parseFloat(r.ndx100) || 0;
            var cf      = parseFloat(r.cash_flow) || 0;
            var cf_note = r.cash_flow_note || '';
            var exp     = parseFloat(r.exposure) || 0;
            var cash    = parseFloat(r.cash_ratio) || 0;
            var x1      = parseFloat(r.x1_ratio) || 0;
            var x2      = parseFloat(r.x2_ratio) || 0;
            var x3      = parseFloat(r.x3_ratio) || 0;
            var usd_krw = parseFloat(r.usd_krw) || 0;
            var prev    = r.prev_total;

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
              '<td style="text-align:right">' + (function() {
                if (!ndx) return '-';
                var ndxChg = parseFloat(r.ndx_change_pct);
                if (isNaN(ndxChg) || r.ndx_change_pct === '') return ndx.toFixed(2);
                var sign = ndxChg >= 0 ? '+' : '';
                var cls  = ndxChg >= 0 ? 'positive' : 'negative';
                return ndx.toFixed(2) + '<br><span class="' + cls + '" style="font-size:11px">' + sign + ndxChg.toFixed(2) + '%</span>';
              })() + '</td>' +
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
              var today = r.date;
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
              var date      = r.date;
              var total     = parseFloat(r.total_asset) || 0;
              var cf        = parseFloat(r.cash_flow) || 0;
              var cf_note   = r.cash_flow_note || '';

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
              var date    = r.date;
              var twrRaw  = parseFloat(r.twr_asset) || 0;
              var ndxRaw  = parseFloat(r.ndx100) || 0;

              // 기준값 (첫 번째 데이터 포인트) 으로 % 계산
              var twrBase = gdTwr.data[0].y[0];  // 이미 % 값
              var ndxBase = gdTwr.data[1].y[0];

              // twr_asset / ndx100 은 절대값이므로 최초 기준값 필요
              // → 서버에서 % 계산해서 넘겨줌 (r[13], r[14])
              var twrPct = parseFloat(r.twr_pct);
              var ndxPct = parseFloat(r.ndx_pct);

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
def history_server(input, output, session, active_tab: reactive.value = None):

    initialized_today_row    = reactive.value(False)
    initialized_historytable = reactive.value(False)
    today_cf_trigger = reactive.value(0)  # 오늘 입출금 저장 시 강제 갱신용
    _reload_trigger  = reactive.value(0)  # 입출금 수정(과거) 시 DB rows 재로드용

    # ── 과거 DB rows 캐시 — 세션 시작 시 1회 로드, 입출금 수정 시 재로드 ──
    @reactive.calc
    def _db_rows():
        _reload_trigger.get()        # 입출금 수정 시 무효화
        daily_insert_signal.get()    # daily insert 완료 시 무효화
        if initialized_historytable.get() and active_tab and active_tab.get() != "history":
            return None
        return load_history()

    # ── 차트용 rows 계산 — DB rows + today_row 합산 ──────────────────────────
    # @reactive.calc 로 캐싱: _db_rows(), load_today_row() 결과가 바뀔 때만 재계산.
    # price_signal → recalc_today_row() → Redis today_row 갱신 → 이 함수 재실행 순서로
    # 시세 업데이트마다 차트 데이터가 갱신된다.
    @reactive.calc
    def _all_rows_for_chart():
        if initialized_historytable.get() and active_tab and active_tab.get() != "history":
            return None
        db_rows = _db_rows()
        rows = list(db_rows) if db_rows else []
        t = load_today_row()
        if t:
            today = _today_kst()
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

    # ── 차트 렌더링 ───────────────────────────────────────────────────────────
    # render.ui 는 반환값을 Shiny가 받아서 output_ui DOM에 통째로 교체한다.
    # _all_rows_for_chart() 의존성으로 인해 시세 업데이트마다 차트 전체가 재렌더링되므로,
    # 탭 비활성 시 스킵해 불필요한 연산과 DOM 교체를 방지한다.
    # 탭 활성화 순간 active_tab 이 "history"로 바뀌면서 자동으로 한 번 실행된다.
    @render.ui
    def chart_asset():
        if active_tab and active_tab.get() != "history":
            return ui.HTML("")
        return ui.HTML(make_chart_asset(_all_rows_for_chart()))

    @render.ui
    def chart_twr():
        if active_tab and active_tab.get() != "history":
            return ui.HTML("")
        return ui.HTML(make_chart_twr(_all_rows_for_chart()))

    # ── 초기 테이블 전송 ─────────────────────────────────────────────────────
    # daily_insert_signal 수신 시 DB에 새 행이 추가됐으므로 전체 테이블을 다시 전송한다.
    # JS는 수신한 데이터로 테이블을 전체 교체한다 (history_data 핸들러).
    # 탭 비활성 시 스킵: 어차피 보이지 않는 DOM에 데이터를 쏘는 건 낭비이고,
    # 탭 활성화 순간 active_tab 이 "history"로 바뀌면서 자동으로 재실행된다.
    @reactive.effect
    async def _send_history_table():
        print(f"[history] _send_history_table called, initialized={initialized_historytable.get()}, tab={active_tab.get() if active_tab else None}", flush=True)
        if initialized_historytable.get() and active_tab and active_tab.get() != "history":
            return
        db_rows = _db_rows()
        rows = list(db_rows) if db_rows is not None else []
        t = load_today_row()
        today = _today_kst()
        print(f"[history] rows_last={rows[-1][0] if rows else None}, today_row_date={t.get('date') if t else None}, today={today}", flush=True)

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
                prev       = float(rows[-1][1] or 0) if rows else None
                prev_ndx   = float(rows[-1][3] or 0) if rows else None
            else:
                idx        = index_map.get(r[0])
                prev       = float(rows[idx - 1][1] or 0) if idx is not None and idx > 0 else None
                prev_ndx   = float(rows[idx - 1][3] or 0) if idx is not None and idx > 0 else None

            # ndx 등락률
            cur_ndx = float(r[3] or 0)
            if prev_ndx and cur_ndx:
                ndx_change_pct = (cur_ndx - prev_ndx) / prev_ndx * 100
            else:
                ndx_change_pct = None

            data.append({
                "date":           str(r[0]),
                "total_asset":    str(r[1]),
                "twr_asset":      str(r[2]),
                "ndx100":         str(r[3]),
                "cash_flow":      str(r[4]),
                "cash_flow_note": r[5] or '',
                "exposure":       str(r[6]),
                "cash_ratio":     str(r[7]),
                "x1_ratio":       str(r[8]),
                "x2_ratio":       str(r[9]),
                "x3_ratio":       str(r[10]),
                "usd_krw":        str(r[11]),
                "prev_total":     str(prev) if prev is not None else '',
                "ndx_change_pct": str(ndx_change_pct) if ndx_change_pct is not None else '',
            })

        print(f"[history] sending history_data rows={len(data)}, first_date={data[0]['date'] if data else None}", flush=True)
        await session.send_custom_message("history_data", data)
        initialized_historytable.set(True)

    # ── 시세/daily insert/입출금 수정 시 today_row 갱신 ────────────────────
    # price_signal 마다 recalc_today_row() 가 Redis today_row 를 갱신하므로
    # 이 함수가 트리거되면 최신 today_row 를 읽어 JS 테이블 최상단 행만 교체한다.
    # 테이블 전체를 다시 보내지 않고 오늘 행만 패치하므로 효율적이다.
    # 탭 비활성 시 스킵: 보이지 않는 DOM을 패치하는 건 낭비이고,
    # 탭 활성화 순간 active_tab 이 "history"로 바뀌면서 자동으로 재실행된다.
    @reactive.effect
    async def _send_today_row_update():
        price_signal.get()           # 시세 업데이트 시 갱신
        daily_insert_signal.get()    # daily insert 완료 시 갱신
        position_signal.get()        # 포지션 CRUD 시 today_row 재계산 반영
        today_cf_trigger.get()       # 오늘 입출금 수정 시 갱신

        if initialized_today_row.get() and active_tab and active_tab.get() != "history":
            return

        t = load_today_row()
        if not t:
            return

        rows = _db_rows()
        today = _today_kst()
        prev     = float(rows[-1][1] or 0) if rows else None
        prev_ndx = float(rows[-1][3] or 0) if rows else None

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

        # ndx 전일 대비 등락률
        cur_ndx = float(t.get("ndx100") or 0)
        ndx_change_pct = (cur_ndx - prev_ndx) / prev_ndx * 100 if prev_ndx and cur_ndx else None

        row = {
            "date":           str(t.get("date", str(today))),
            "total_asset":    str(t.get("total_asset")),
            "twr_asset":      str(t.get("twr_asset")),
            "ndx100":         str(t.get("ndx100")),
            "cash_flow":      str(t.get("cash_flow", 0)),
            "cash_flow_note": t.get("cash_flow_note") or '',
            "exposure":       str(t.get("exposure")),
            "cash_ratio":     str(t.get("cash_ratio")),
            "x1_ratio":       str(t.get("x1_ratio")),
            "x2_ratio":       str(t.get("x2_ratio")),
            "x3_ratio":       str(t.get("x3_ratio")),
            "usd_krw":        str(t.get("usd_krw")),
            "prev_total":     str(prev) if prev is not None else '',
            "twr_pct":        str(twr_pct),
            "ndx_pct":        str(ndx_pct),
            "ndx_change_pct": str(ndx_change_pct) if ndx_change_pct is not None else '',
        }

        await session.send_custom_message("today_row_update", row)
        initialized_today_row.set(True)

    # ── 날짜 클릭 → 입출금 수정 모달 ────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.selected_date)
    def _open_edit_modal():
        date_str = input.selected_date()
        if not date_str:
            return

        today_str = str(_today_kst())
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

        today_str = str(_today_kst())
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