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
            ui.div(id="chart-asset", style="height:220px; width:100%; overflow:hidden;"),
            class_="chart-section",
        ),

        # 그래프 2: TWR vs NDX100
        ui.div(
            ui.p("운용 수익률 vs NDX100", class_="chart-title"),
            ui.div(id="chart-twr", style="height:220px; width:100%; overflow:hidden;"),
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
                        ui.tags.th("TWR"),
                        ui.tags.th("나스닥"),
                        ui.tags.th("환율"),
                        ui.tags.th("x3"),
                        ui.tags.th("x2"),
                        ui.tags.th("x1"),
                        ui.tags.th("입출금"),
                    )
                ),
                ui.tags.tbody(id="history-tbody"),
                class_="history-table",
            ),
            class_="history-table-wrap",
        ),

        # ── JS ──────────────────────────────────────────────────────────────
        ui.tags.script("""
        (function() {

          // ── 상태 ─────────────────────────────────────────────────────────
          var _allRows   = [];
          var _chartData = null;   // history_data 수신 시 저장
          var _pendingDraw = false; // 숨겨진 상태에서 데이터 수신 시 true

          // ── 포맷 헬퍼 ────────────────────────────────────────────────────
          function fmtKrw(v) {
            var n = parseFloat(v) || 0;
            var abs = Math.abs(n);
            if (abs >= 1e8)      return (n / 1e8).toFixed(1) + "억";
            if (abs >= 1e4)      return Math.round(n / 1e4) + "만";
            return Math.round(n).toLocaleString();
          }

          function fmtKrw2(v) {
            var n = parseFloat(v) || 0;
            var abs = Math.abs(n);
            if (abs >= 1e8) return (n / 1e8).toFixed(2) + "억";
            if (abs >= 1e4) return Math.round(n / 1e4) + "만";
            return Math.round(n).toLocaleString();
          }

          function formatKrwFull(n) {
            return Math.round(n).toLocaleString();
          }

          // ── 가시성 체크 ──────────────────────────────────────────────────
          function isHistoryVisible() {
            var tab = document.getElementById('tab-history');
            return !!tab && getComputedStyle(tab).display !== 'none';
          }

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

          // ── 차트 공통 레이아웃 ───────────────────────────────────────────
          var BASE_LAYOUT = {
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor:  '#111111',
            font:          {color: '#aaaaaa', size: 11},
            margin:        {l: 8, r: 8, t: 8, b: 8},
            legend: {
              orientation: 'h',
              yanchor: 'bottom', y: 1.02,
              xanchor: 'right',  x: 1,
              font: {size: 11},
              bgcolor: 'rgba(0,0,0,0)',
            },
            xaxis: {
              gridcolor:   '#222222',
              linecolor:   '#333333',
              tickfont:    {size: 10},
              tickformat:  '%-m\\n%Y',
              dtick:       'M1',
              showspikes:  true,
              spikecolor:  '#444444',
              spikemode:   'across',
              spikesnap:   'cursor',
              fixedrange:  true,
              automargin:  true,
            },
            yaxis: {
              gridcolor:  '#222222',
              linecolor:  '#333333',
              tickfont:   {size: 10},
              fixedrange: true,
              automargin: true,
            },
            hovermode:  'x unified',
            hoverlabel: {
              bgcolor:     '#1a1a1a',
              bordercolor: '#333333',
              font:        {color: '#ffffff', size: 12},
              namelength:  -1,
            },
            dragmode: false,
            height:   220,
            autosize: true,
          };

          // 초기 x범위: 최근 3개월
          function initRange(dates) {
            if (!dates || dates.length === 0) return undefined;
            var last  = dates[dates.length - 1];
            var first = dates[0];
            var end   = new Date(last);
            var start = new Date(end);
            start.setMonth(start.getMonth() - 3);
            if (start < new Date(first)) start = new Date(first);
            return [start.toISOString().slice(0,10), last];
          }

          // ── 차트 그리기 ──────────────────────────────────────────────────
          function drawCharts(data) {
            if (!data || data.length === 0) return;

            // 오름차순 정렬 (data는 내림차순으로 수신됨)
            var asc = data.slice().reverse();

            var dates  = asc.map(function(r) { return r.date; });
            var assets = asc.map(function(r) { return parseFloat(r.total_asset) || 0; });
            var cflows = asc.map(function(r) { return parseFloat(r.cash_flow) || 0; });
            var notes  = asc.map(function(r) { return r.cash_flow_note || ''; });
            var twrRaw = asc.map(function(r) { return parseFloat(r.twr_asset) || 0; });
            var ndxRaw = asc.map(function(r) { return parseFloat(r.ndx100) || 0; });

            // TWR / NDX 기준점 대비 % 계산
            var baseTwr = twrRaw[0] || 1;
            var baseNdx = ndxRaw[0] || 1;
            var twrPct  = twrRaw.map(function(v) { return (v / baseTwr - 1) * 100; });
            var ndxPct  = ndxRaw.map(function(v) { return (v / baseNdx - 1) * 100; });

            // ── chart-asset ──────────────────────────────────────────────
            var gdAsset = document.getElementById('chart-asset');
            if (gdAsset) {
              var traceLine = {
                x:    dates,
                y:    assets,
                mode: 'lines',
                name: '총자산',
                line: {color: '#00c073', width: 2},
                hovertemplate: '%{customdata[0]}<extra></extra>',
                customdata: assets.map(function(a, i) {
                  return [formatKrwFull(a) + '원', cflows[i], notes[i]];
                }),
              };

              var tracesAsset = [traceLine];

              // 입금 마커
              var depIdx = cflows.map(function(c, i) { return c > 0 ? i : -1; }).filter(function(i) { return i >= 0; });
              if (depIdx.length > 0) {
                tracesAsset.push({
                  x:    depIdx.map(function(i) { return dates[i]; }),
                  y:    depIdx.map(function(i) { return assets[i] * 1.012; }),
                  mode: 'markers',
                  name: '입금',
                  marker: {symbol: 'triangle-up', size: 10, color: '#ff4d4d', line: {color: '#ffffff', width: 1}},
                  hovertemplate: '%{customdata}<extra>입금</extra>',
                  customdata: depIdx.map(function(i) {
                    return '+' + formatKrwFull(cflows[i]) + '원' + (notes[i] ? String.fromCharCode(10) + notes[i] : '');
                  }),
                });
              }

              // 출금 마커
              var wdIdx = cflows.map(function(c, i) { return c < 0 ? i : -1; }).filter(function(i) { return i >= 0; });
              if (wdIdx.length > 0) {
                tracesAsset.push({
                  x:    wdIdx.map(function(i) { return dates[i]; }),
                  y:    wdIdx.map(function(i) { return assets[i] * 0.988; }),
                  mode: 'markers',
                  name: '출금',
                  marker: {symbol: 'triangle-down', size: 10, color: '#4d9fff', line: {color: '#ffffff', width: 1}},
                  hovertemplate: '%{customdata}<extra>출금</extra>',
                  customdata: wdIdx.map(function(i) {
                    return formatKrwFull(cflows[i]) + '원' + (notes[i] ? String.fromCharCode(10) + notes[i] : '');
                  }),
                });
              }

              var yMin = Math.min.apply(null, assets);
              var yMax = Math.max.apply(null, assets);
              var tickVals = [0,1,2,3,4].map(function(i) { return yMin + (yMax - yMin) * i / 4; });
              var tickText = tickVals.map(function(v) {
                var abs = Math.abs(v);
                if (abs >= 1e8) return (v / 1e8).toFixed(1) + '억';
                if (abs >= 1e4) return Math.round(v / 1e4) + '만';
                return Math.round(v).toLocaleString();
              });

              var layoutAsset = Object.assign({}, BASE_LAYOUT, {
                xaxis: Object.assign({}, BASE_LAYOUT.xaxis, {range: initRange(dates)}),
                yaxis: Object.assign({}, BASE_LAYOUT.yaxis, {
                  tickmode: 'array',
                  tickvals: tickVals,
                  ticktext: tickText,
                }),
              });

              Plotly.react(gdAsset, tracesAsset, layoutAsset, {displayModeBar: false, responsive: true});
              attachTouch(gdAsset);
            }

            // ── chart-twr ────────────────────────────────────────────────
            var gdTwr = document.getElementById('chart-twr');
            if (gdTwr) {
              var tracesTwr = [
                {
                  x:    dates,
                  y:    twrPct,
                  mode: 'lines',
                  name: '내 수익률',
                  line: {color: '#00c073', width: 2},
                  hovertemplate: '%{y:.2f}%<extra>내 수익률</extra>',
                },
                {
                  x:    dates,
                  y:    ndxPct,
                  mode: 'lines',
                  name: 'NDX100',
                  line: {color: '#4d9fff', width: 2, dash: 'dot'},
                  hovertemplate: '%{y:.2f}%<extra>NDX100</extra>',
                },
              ];

              var layoutTwr = Object.assign({}, BASE_LAYOUT, {
                xaxis: Object.assign({}, BASE_LAYOUT.xaxis, {range: initRange(dates)}),
                yaxis: Object.assign({}, BASE_LAYOUT.yaxis, {
                  ticksuffix: '%',
                  zeroline:   false,
                }),
                shapes: [{
                  type: 'line', xref: 'paper', x0: 0, x1: 1,
                  y0: 0, y1: 0,
                  line: {color: '#333333', width: 1},
                }],
              });

              Plotly.react(gdTwr, tracesTwr, layoutTwr, {displayModeBar: false, responsive: true});
              attachTouch(gdTwr);
            }
          }

          // ── 테이블 행 생성 ───────────────────────────────────────────────
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
              '<td style="text-align:right">' + (function() {
                if (!twr) return '-';
                var twrChg = parseFloat(r.twr_change_pct);
                if (isNaN(twrChg) || r.twr_change_pct === '') return fmtKrw(twr);
                var sign = twrChg >= 0 ? '+' : '';
                var cls  = twrChg >= 0 ? 'positive' : 'negative';
                return fmtKrw(twr) + '<br><span class="' + cls + '" style="font-size:11px">' + sign + twrChg.toFixed(2) + '%</span>';
              })() + '</td>' +
              '<td style="text-align:right">' + (function() {
                if (!ndx) return '-';
                var ndxChg = parseFloat(r.ndx_change_pct);
                if (isNaN(ndxChg) || r.ndx_change_pct === '') return ndx.toFixed(2);
                var sign = ndxChg >= 0 ? '+' : '';
                var cls  = ndxChg >= 0 ? 'positive' : 'negative';
                return ndx.toFixed(2) + '<br><span class="' + cls + '" style="font-size:11px">' + sign + ndxChg.toFixed(2) + '%</span>';
              })() + '</td>' +
              '<td style="text-align:right">' + (usd_krw ? usd_krw.toFixed(2) : '-') + '</td>' +
              '<td style="text-align:right">' + (x3 * 100).toFixed(1) + '%</td>' +
              '<td style="text-align:right">' + (x2 * 100).toFixed(1) + '%</td>' +
              '<td style="text-align:right">' + (x1 * 100).toFixed(1) + '%</td>' +
              '<td style="text-align:right">' + cfCell + '</td>';
            tr.addEventListener('click', function() {
              Shiny.setInputValue('history-selected_date', date, {priority: 'event'});
            });
            return tr;
          }

          function drawTable(rows) {
            var tbody = document.getElementById('history-tbody');
            if (!tbody) return;
            tbody.innerHTML = '';
            rows.forEach(function(r) { tbody.appendChild(buildTr(r)); });
          }

          // ── history_data: 데이터 수신 ────────────────────────────────────
          // 탭이 visible이면 즉시 렌더링, hidden이면 데이터만 저장 후 _pendingDraw 표시
          // drawCharts()는 requestAnimationFrame으로 한 프레임 미룬다:
          // display:none → block 전환 직후엔 브라우저가 아직 reflow를
          // 수행하지 않아 clientWidth가 0으로 읽히고, Plotly가 기본값(700)으로
          // 그려지는 현상이 실측으로 확인됨 (react 호출 직전 clientWidth=0,
          // _fullLayout.width=700; relayout 시점엔 정상폭).
          Shiny.addCustomMessageHandler('history_data', function(data) {
            _allRows   = data;
            _chartData = data;

            if (isHistoryVisible()) {
              drawTable(_allRows);
              requestAnimationFrame(function() { drawCharts(_chartData); });
              _pendingDraw = false;
            } else {
              _pendingDraw = true;
            }
          });

          // ── active_tab 변경 감지: history 탭 진입 시 pending draw 처리 ──
          $(document).on('shiny:inputchanged', function(e) {
            if (e.name === 'active_tab' && e.value === 'history') {
              if (_pendingDraw && _chartData) {
                drawTable(_allRows);
                requestAnimationFrame(function() { drawCharts(_chartData); });
                _pendingDraw = false;
              }
            }
          });

          // ── today_row 갱신 — 최상단 행 교체 + 차트 끝단 업데이트 ──────────
          Shiny.addCustomMessageHandler('today_row_update', function(r) {

            // 1. _allRows 갱신 (DOM 상태와 무관하게 배열 자체의 today 중복을 방지)
            //    history_data가 이미 today row를 포함해서 보낸 경우와
            //    이후 today_row_update가 추가로 들어오는 경우가 겹칠 수 있으므로,
            //    배열의 첫 row가 같은 날짜면 교체, 아니면만 unshift.
            var today = r.date;
            if (_allRows.length > 0 && _allRows[0].date === today) {
              _allRows[0] = r;
            } else {
              _allRows.unshift(r);
            }

            // 2. DOM 최상단 행 교체 — 테이블이 이미 실제로 그려져 있을 때만.
            //    _pendingDraw 상태(탭 숨김 중 history_data 미반영)면 여기서 DOM을
            //    건드리지 않는다. 탭 진입 시 drawTable(_allRows)가 갱신된 배열로
            //    처음부터 다시 그리므로, 여기서 직접 insert하면 중복의 원인이 된다.
            if (!_pendingDraw) {
              var tbody = document.getElementById('history-tbody');
              if (tbody) {
                var newTr    = buildTr(r);
                var existing = tbody.querySelector('tr[data-date="' + today + '"]');
                if (existing) {
                  tbody.replaceChild(newTr, existing);
                } else {
                  tbody.insertBefore(newTr, tbody.firstChild);
                }
              }
            }

            // 2. chart-asset 끝단 업데이트
            var gdAsset = document.getElementById('chart-asset');
            if (gdAsset && gdAsset.data) {
              var date    = r.date;
              var total   = parseFloat(r.total_asset) || 0;
              var cf      = parseFloat(r.cash_flow) || 0;
              var cf_note = r.cash_flow_note || '';

              var xs0 = gdAsset.data[0].x.slice();
              var ys0 = gdAsset.data[0].y.slice();
              var cd0 = (gdAsset.data[0].customdata || []).slice();

              if (xs0[xs0.length - 1] === date) {
                ys0[ys0.length - 1] = total;
                cd0[cd0.length - 1] = [formatKrwFull(total) + '원', cf, cf_note];
              } else {
                xs0.push(date);
                ys0.push(total);
                cd0.push([formatKrwFull(total) + '원', cf, cf_note]);
              }
              Plotly.restyle(gdAsset, {x: [xs0], y: [ys0], customdata: [cd0]}, [0]);

              // 오늘 마커 트레이스 제거
              var toDelete = [];
              for (var ti = gdAsset.data.length - 1; ti >= 1; ti--) {
                var tx = gdAsset.data[ti].x;
                if (tx && tx.length === 1 && tx[0] === date) toDelete.push(ti);
              }
              if (toDelete.length > 0) Plotly.deleteTraces(gdAsset, toDelete);

              // 오늘 cf 있으면 마커 추가
              if (cf !== 0) {
                var markerColor  = cf > 0 ? '#ff4d4d' : '#4d9fff';
                var markerSymbol = cf > 0 ? 'triangle-up' : 'triangle-down';
                var markerName   = cf > 0 ? '입금' : '출금';
                var markerY      = cf > 0 ? total * 1.012 : total * 0.988;
                var cdStr        = (cf > 0 ? '+' : '') + Math.round(cf).toLocaleString() + '원' + (cf_note ? String.fromCharCode(10) + cf_note : '');
                Plotly.addTraces(gdAsset, {
                  x: [date], y: [markerY], mode: 'markers', name: markerName,
                  marker: {symbol: markerSymbol, size: 10, color: markerColor, line: {color: '#ffffff', width: 1}},
                  hovertemplate: '%{customdata}<extra>' + markerName + '</extra>',
                  customdata: [cdStr],
                });
              }
            }

            // 3. chart-twr 끝단 업데이트
            var gdTwr = document.getElementById('chart-twr');
            if (gdTwr && gdTwr.data && gdTwr.data.length >= 2) {
              var date   = r.date;
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

          // ── 터치 이벤트 (pan + long-press hover) ─────────────────────────
          function attachTouch(gd) {
            if (gd._touchAttached) return;
            gd._touchAttached = true;

            gd.style.touchAction = 'manipulation';
            gd.on('plotly_beforehover', function() { return false; });
            if (getComputedStyle(gd).position === 'static') {
              gd.style.position = 'relative';
            }

            var touchStartX     = null;
            var touchStartY     = null;
            var touchStartRange = null;
            var isPanning       = false;
            var isHovering      = false;
            var longTimer       = null;
            var LONG_MS         = 500;
            var PAN_THRESHOLD   = 8;
            var LONG_THRESHOLD  = 6;

            var toMs  = function(s) { return new Date(s).getTime(); };
            var toStr = function(ms) { return new Date(ms).toISOString().slice(0,10); };

            function getCurrentRange() {
              return gd.layout.xaxis.range.map(toMs);
            }

            function getDataRange() {
              var xs = gd.data[0].x;
              return [toMs(xs[0]), toMs(xs[xs.length - 1])];
            }

            // 커스텀 팝업
            var popup = document.createElement('div');
            popup.style.cssText = [
              'position:absolute', 'background:#1a1a1a', 'border:1px solid #444',
              'border-radius:6px', 'padding:7px 10px', 'font-size:12px',
              'color:#fff', 'pointer-events:none', 'white-space:nowrap',
              'display:none', 'z-index:999', 'line-height:1.7',
            ].join(';');
            gd.appendChild(popup);

            // 수직 보조선
            var vline = document.createElement('div');
            vline.style.cssText = [
              'position:absolute', 'top:0', 'width:1px', 'height:100%',
              'background:#666', 'pointer-events:none', 'display:none', 'z-index:998',
            ].join(';');
            gd.appendChild(vline);

            // 수평 보조선 (동적 생성)
            var hlines = [];

            function clientXToIndex(clientX) {
              var range = getCurrentRange();
              var r0    = range[0];
              var r1    = range[1];
              var plot  = gd.querySelector('.nsewdrag');
              if (!plot) return 0;
              var plotRect = plot.getBoundingClientRect();
              var px = Math.max(0, Math.min(clientX - plotRect.left, plotRect.width));
              var ratio    = px / plotRect.width;
              var targetMs = r0 + ratio * (r1 - r0);
              var xs = gd.data[0].x;
              var best = 0;
              var bestDiff = Math.abs(toMs(xs[0]) - targetMs);
              for (var i = 1; i < xs.length; i++) {
                var diff = Math.abs(toMs(xs[i]) - targetMs);
                if (diff < bestDiff) { bestDiff = diff; best = i; } else { break; }
              }
              return best;
            }

            function showPopup(clientX) {
              var idx     = clientXToIndex(clientX);
              var xs      = gd.data[0].x;
              var dateStr = String(xs[idx]);
              var parts   = dateStr.split('-');
              var label   = parts[0] + '년 ' + parseInt(parts[1]) + '월 ' + parseInt(parts[2]) + '일';

              var lines = ['<b>' + label + '</b>'];
              for (var t = 0; t < gd.data.length; t++) {
                var trace = gd.data[t];
                if (!trace.y || trace.mode === 'markers') continue;
                var yVal = trace.y[idx];
                if (yVal === undefined || yVal === null) continue;
                var name  = trace.name || ('trace' + t);
                var color = (trace.line && trace.line.color) || '#aaa';
                var valStr;
                if (trace.hovertemplate && trace.hovertemplate.indexOf(':.2f') !== -1) {
                  valStr = yVal.toFixed(2) + '%';
                } else {
                  valStr = Math.round(yVal).toLocaleString() + '원';
                }
                lines.push('<span style="color:' + color + '">■</span> ' + name + ': ' + valStr);
              }
              var cd0 = gd.data[0].customdata && gd.data[0].customdata[idx];
              if (Array.isArray(cd0)) {
                var cf   = cd0[1];
                var note = cd0[2];
                if (cf !== 0) {
                  var cfColor = cf > 0 ? '#ff4d4d' : '#4d9fff';
                  lines.push('<span style="color:' + cfColor + '">■</span> 입출금: ' +
                    (cf > 0 ? '+' : '') + Math.round(cf).toLocaleString() + '원');
                  if (note) lines.push('<span style="color:' + cfColor + '">■</span> 내역: ' + note);
                }
              }
              popup.innerHTML = lines.join('<br>');
              popup.style.display = 'block';

              var gdRect = gd.getBoundingClientRect();
              var localX = clientX - gdRect.left;
              var popX   = localX + 12;
              popup.style.left = popX + 'px';
              popup.style.top  = '8px';
              var popW = popup.offsetWidth;
              if (popX + popW > gdRect.width - 4) popup.style.left = (localX - popW - 12) + 'px';

              var plot = gd.querySelector('.nsewdrag');
              if (plot) {
                var plotRect = plot.getBoundingClientRect();
                vline.style.left = (plotRect.left - gdRect.left + (clientX - plotRect.left)) + 'px';
              } else {
                vline.style.left = localX + 'px';
              }
              vline.style.display = 'block';

              hlines.forEach(function(hl) { if (hl.parentNode) hl.parentNode.removeChild(hl); });
              hlines = [];
              if (plot) {
                var plotRect2  = plot.getBoundingClientRect();
                var plotTop    = plotRect2.top - gdRect.top;
                var plotHeight = plotRect2.height;
                for (var t = 0; t < gd.data.length; t++) {
                  var trace = gd.data[t];
                  if (!trace.y || trace.mode === 'markers') continue;
                  var yVal2 = trace.y[idx];
                  if (yVal2 === undefined || yVal2 === null) continue;
                  var yRange  = gd.layout.yaxis.range;
                  var yRatio  = 1 - (yVal2 - yRange[0]) / (yRange[1] - yRange[0]);
                  var yPx     = plotTop + yRatio * plotHeight;
                  var color   = (trace.line && trace.line.color) || '#666';
                  var hl = document.createElement('div');
                  hl.style.cssText = [
                    'position:absolute', 'left:0', 'width:100%', 'height:1px',
                    'background:' + color, 'opacity:0.5', 'pointer-events:none', 'z-index:997',
                  ].join(';');
                  hl.style.top = yPx + 'px';
                  gd.appendChild(hl);
                  hlines.push(hl);
                }
              }
            }

            function hidePopup() {
              popup.style.display = 'none';
              vline.style.display = 'none';
              hlines.forEach(function(hl) { if (hl.parentNode) hl.parentNode.removeChild(hl); });
              hlines = [];
            }

            gd.addEventListener('touchstart', function(e) {
              if (e.touches.length !== 1) return;
              var t = e.touches[0];
              touchStartX     = t.clientX;
              touchStartY     = t.clientY;
              touchStartRange = getCurrentRange();
              isPanning       = false;
              isHovering      = false;
              longTimer = setTimeout(function() {
                if (!isPanning) { isHovering = true; showPopup(touchStartX); }
              }, LONG_MS);
            }, {passive: false});

            gd.addEventListener('touchmove', function(e) {
              if (e.touches.length !== 1 || touchStartX === null) return;
              var t  = e.touches[0];
              var dx = t.clientX - touchStartX;
              var dy = t.clientY - touchStartY;
              if (longTimer && Math.abs(dx) > LONG_THRESHOLD) { clearTimeout(longTimer); longTimer = null; }
              if (isHovering) { e.preventDefault(); showPopup(t.clientX); return; }
              if (!isPanning && (Math.abs(dx) < PAN_THRESHOLD || Math.abs(dx) <= Math.abs(dy))) return;
              isPanning = true;
              e.preventDefault();
              var r0      = touchStartRange[0];
              var r1      = touchStartRange[1];
              var rangeMs = r1 - r0;
              var msPerPx = rangeMs / gd.getBoundingClientRect().width;
              var shiftMs = -dx * msPerPx;
              var dr      = getDataRange();
              var newR0   = r0 + shiftMs;
              var newR1   = r1 + shiftMs;
              if (newR0 < dr[0]) { newR0 = dr[0]; newR1 = dr[0] + rangeMs; }
              if (newR1 > dr[1]) { newR1 = dr[1]; newR0 = dr[1] - rangeMs; }
              Plotly.relayout(gd, {'xaxis.range': [toStr(newR0), toStr(newR1)]});
            }, {passive: false});

            gd.addEventListener('touchend', function(e) {
              if (longTimer) { clearTimeout(longTimer); longTimer = null; }
              hidePopup();
              touchStartX = null;
              isPanning   = false;
              isHovering  = false;
            }, {passive: true});
          }

        })();
        """),

        class_="page-inner",
    )


# ── Server ────────────────────────────────────────────────────────────────────
@module.server
def history_server(input, output, session, active_tab: reactive.value = None):

    _initialized_today_row    = False  # 일반 변수: effect 자기-재트리거 방지
    _initialized_historytable = False  # 일반 변수: effect 자기-재트리거 방지
    today_cf_trigger = reactive.value(0)  # 오늘 입출금 저장 시 강제 갱신용
    _reload_trigger  = reactive.value(0)  # 입출금 수정(과거) 시 DB rows 재로드용

    # ── 과거 DB rows 캐시 ────────────────────────────────────────────────────
    # _reload_trigger / daily_insert_signal 시에만 DB 재조회.
    # 탭 조건 없이 항상 로드 (미리 패치 의도 유지, history_data 전송 조건에서 제어).
    @reactive.calc
    def _db_rows():
        _reload_trigger.get()
        daily_insert_signal.get()
        return load_history()

    # ── 초기 테이블 + 차트 데이터 전송 ──────────────────────────────────────
    # JS가 수신 시점에 탭 가시성을 체크해 렌더링 여부를 결정함.
    # 서버는 데이터 준비만 담당.
    @reactive.effect
    async def _send_history_table():
        nonlocal _initialized_historytable
        _reload_trigger.get()
        daily_insert_signal.get()

        if _initialized_historytable and active_tab and active_tab.get() != "history":
            return

        db_rows = _db_rows()
        rows    = list(db_rows) if db_rows is not None else []
        t       = load_today_row()
        today   = _today_kst()

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
            if r[0] == today:
                prev     = float(rows[-1][1] or 0) if rows else None
                prev_ndx = float(rows[-1][3] or 0) if rows else None
                prev_twr = float(rows[-1][2] or 0) if rows else None
            else:
                idx      = index_map.get(r[0])
                prev     = float(rows[idx - 1][1] or 0) if idx is not None and idx > 0 else None
                prev_ndx = float(rows[idx - 1][3] or 0) if idx is not None and idx > 0 else None
                prev_twr = float(rows[idx - 1][2] or 0) if idx is not None and idx > 0 else None

            cur_ndx = float(r[3] or 0)
            ndx_change_pct = (cur_ndx - prev_ndx) / prev_ndx * 100 if prev_ndx and cur_ndx else None

            cur_twr = float(r[2] or 0)
            twr_change_pct = (cur_twr - prev_twr) / prev_twr * 100 if prev_twr and cur_twr else None

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
                "twr_change_pct": str(twr_change_pct) if twr_change_pct is not None else '',
            })

        await session.send_custom_message("history_data", data)
        _initialized_historytable = True

    # ── 시세/daily insert/입출금 수정 시 today_row 갱신 ─────────────────────
    @reactive.effect
    async def _send_today_row_update():
        nonlocal _initialized_today_row
        price_signal.get()
        daily_insert_signal.get()
        position_signal.get()
        today_cf_trigger.get()

        if _initialized_today_row and active_tab and active_tab.get() != "history":
            return

        t = load_today_row()
        if not t:
            return

        rows = _db_rows()
        today    = _today_kst()
        prev     = float(rows[-1][1] or 0) if rows else None
        prev_ndx = float(rows[-1][3] or 0) if rows else None
        prev_twr = float(rows[-1][2] or 0) if rows else None

        twr_pct = 0.0
        ndx_pct = 0.0
        if rows:
            base_twr = float(rows[0][2] or 0)
            base_ndx = float(rows[0][3] or 0)
            if base_twr:
                twr_pct = (float(t.get("twr_asset") or 0) / base_twr - 1) * 100
            if base_ndx:
                ndx_pct = (float(t.get("ndx100") or 0) / base_ndx - 1) * 100

        cur_ndx        = float(t.get("ndx100") or 0)
        ndx_change_pct = (cur_ndx - prev_ndx) / prev_ndx * 100 if prev_ndx and cur_ndx else None

        cur_twr        = float(t.get("twr_asset") or 0)
        twr_change_pct = (cur_twr - prev_twr) / prev_twr * 100 if prev_twr and cur_twr else None

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
            "twr_change_pct": str(twr_change_pct) if twr_change_pct is not None else '',
        }

        # [DEBUG-HISTORY] 화면 갱신 시점 total_asset 로그
        print(f"[DEBUG-HISTORY] {datetime.datetime.now(KST)} "
              f"total_asset={row['total_asset']} date={row['date']}")

        await session.send_custom_message("today_row_update", row)
        _initialized_today_row = True

    # ── 날짜 클릭 → 입출금 수정 모달 ────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.selected_date)
    def _open_edit_modal():
        date_str = input.selected_date()
        if not date_str:
            return

        today_str = str(_today_kst())
        is_today  = (date_str == today_str)

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
            _reload_trigger.set(_reload_trigger.get() + 1)

        ui.modal_remove()
        ui.notification_show("저장됐습니다.", type="message", duration=2)