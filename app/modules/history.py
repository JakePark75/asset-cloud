import json
import datetime
from zoneinfo import ZoneInfo
from shiny import ui, reactive, module, render

KST = ZoneInfo("Asia/Seoul")

def _today_kst() -> datetime.date:
    return datetime.datetime.now(KST).date()

from app.price_signal import price_signal, daily_insert_signal, position_signal
from app.db import get_db
from .history_DAL import (
    load_history, load_today_row, save_cash_flow, build_today_row, build_history_rows,
    get_history_cache_meta, get_patch_start_date,
)
from app.utils.display_diff import diff_display


# ── History Cache 순수 함수 (2/3단계) ───────────────────────────────────────
# DB/Redis 접근 없음. 유닛 테스트 가능한 순수 함수로만 구성.
# 아직 어떤 input/effect에도 연결하지 않음 (계획서 4단계 이후에 배관 연결).

def decide_sync_mode(client_version: int, client_last_date, server_version: int, patch_start) -> dict:
    """
    브라우저가 보낸 캐시 상태(client_version, client_last_date)와
    서버 상태(server_version, patch_start)를 비교해 동기화 모드를 결정한다.

    반환: {"mode": "full"|"append"|"patch"|"none", "since": date|None}

    - client_version == 0             -> full (캐시 없음, 최초 방문)
    - client_version > server_version -> full (비정상/롤백 상태 방어)
    - patch_start is not None         -> patch, since=patch_start
    - 그 외                           -> append, since=client_last_date
      (실제로 신규 확정일이 있는지는 이 함수가 판단하지 않는다.
       호출부가 서버의 최신 확정일과 client_last_date를 비교해
       append를 그대로 쓸지 none으로 낮출지 결정한다.)
    """
    if client_version == 0:
        return {"mode": "full", "since": None}
    if client_version > server_version:
        return {"mode": "full", "since": None}
    if patch_start is not None:
        return {"mode": "patch", "since": patch_start}
    return {"mode": "append", "since": client_last_date}


def find_predecessor(all_rows: list, since_date):
    """
    all_rows(날짜 오름차순 ASC)에서 date < since_date인 마지막 row를 반환.
    없으면 None. _db_rows()가 반환하는 것과 같은 형태(튜플 리스트)를 그대로 받는다.
    DB 재조회 없이 in-memory 슬라이싱만 수행.
    """
    predecessor = None
    for row in all_rows:
        if row[0] < since_date:
            predecessor = row
        else:
            break
    return predecessor


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def history_ui():
    return ui.div(
        # 기간 슬라이더 (JS에서 직접 relayout, 서버 호출 없음)
        # 최소 1개월 ~ 데이터 전체범위 사이를 로그 스케일로 연속 조절.
        # 로그 스케일을 쓰는 이유: 데이터가 수년치로 쌓이면 선형 스케일에서는
        # "1개월~수개월" 같은 짧은 구간을 슬라이더로 정밀하게 고르기 어려워짐.
        ui.tags.style("""
        .period-slider-group   { padding: 2px 24px 12px; }
        .period-slider-label-wrap { text-align:center; margin-bottom:4px; }
        .period-slider-label   { font-size:12px; color:#aaaaaa; }

        /* 이 블록 안의 값들만 조절하면 슬라이더 폭/크기를 바꿀 수 있습니다.
           - .period-slider-group 의 padding 좌우값을 늘리면 슬라이더가
             화면 좌우 끝에서 안쪽으로 들어옵니다 (폭이 줄어듦).
           - 또는 아래 input.period-slider 의 width를 100% 대신
             예: 80% 로 주고 margin: 0 auto 로 가운데 정렬해도 됩니다. */
        input.period-slider {
          -webkit-appearance: none;
          appearance: none;
          width: 100%;
          height: 32px;           /* 터치 인식 영역 (실제 트랙보다 훨씬 크게) */
          background: transparent;
          outline: none;
          margin: 0;
        }
        /* 눈에 보이는 얇은 트랙은 pseudo-element로 별도 정의 */
        input.period-slider::-webkit-slider-runnable-track {
          height: 4px;
          border-radius: 2px;
          background: #333333;
        }
        input.period-slider::-moz-range-track {
          height: 4px;
          border-radius: 2px;
          background: #333333;
        }
        input.period-slider::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 22px; height: 22px;
          margin-top: -9px;       /* 트랙(4px) 중앙에 손잡이(22px)를 맞춤 */
          border-radius: 50%;
          background: #00c073;
          cursor: pointer;
          border: 2px solid #0a0a0a;
        }
        input.period-slider::-moz-range-thumb {
          width: 22px; height: 22px;
          border-radius: 50%;
          background: #00c073;
          cursor: pointer;
          border: 2px solid #0a0a0a;
        }
        """),
        ui.div(
            ui.div(
                ui.tags.span("3개월", id="period-slider-label", class_="period-slider-label"),
                class_="period-slider-label-wrap",
            ),
            ui.tags.input(
                type="range", id="period-slider", min="0", max="1000", value="500",
                class_="period-slider",
                oninput="onPeriodSliderInput(this.value)",
            ),
            class_="period-slider-group",
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
          var _pendingDraw = false; // 숨겨진 상태에서 데이터 수신 시 true

          // _todayRow: 확정 rows(_cachedConfirmedRows)와 분리된 "오늘 row" 상태(A1 설계).
          // 세션 연결 시 서버가 보내는 첫 today_row_update로 채워지고, 이후
          // 계속 갱신됨. IndexedDB(daily_rows)에는 넣지 않음(today는 확정 row가 아님).
          var _todayRow = null;

          // _cachedConfirmedRows: IndexedDB의 daily_rows를 메모리에 올린 배열(오름차순, dt ASC).
          // null = 아직 IndexedDB에서 초기 로드가 끝나지 않음(구분 필요, []는 "로드했는데 비어있음").
          var _cachedConfirmedRows = null;

          // history_cache_response가 초기 로드 완료 전에 먼저 도착하는 레이스 대비.
          var _pendingCacheResponse = null;

          // 서버 history_cache_meta.version과 동일한 값(디버그 노출용으로만 사용).
          var _cacheVersion = undefined;

          // ── History Cache: 디버그용 상태 노출 ───────────────────────────────
          window.__histCacheDebug = function() {
            return {
              todayRow:        _todayRow,
              confirmedLoaded: _cachedConfirmedRows !== null,
              confirmedCount:  (_cachedConfirmedRows || []).length,
              confirmedFirst:  (_cachedConfirmedRows && _cachedConfirmedRows[0]) || null,
              confirmedLast:   (_cachedConfirmedRows && _cachedConfirmedRows[_cachedConfirmedRows.length - 1]) || null,
              cacheVersion:    _cacheVersion,
            };
          };

          // ── History Cache: IndexedDB 골격 (4단계) ─────────────────────────
          // 아직 서버와 통신하지 않음. DB만 열어두고, 콘솔 디버깅용으로
          // window.__histDB에 노출한다 (최종 단계에서 제거 여부 재검토).
          // 오브젝트 스토어:
          //   daily_rows (keyPath: dt)  - 확정된 날짜별 row
          //   meta       (keyPath: k)   - 캐시 메타 정보 (예: {k:'version', v:14})
          (function initHistDB() {
            if (!window.indexedDB) {
              console.warn('[HIST-CACHE] no_indexeddb_support');
              return;
            }
            var req = window.indexedDB.open('asset_history', 1);

            req.onupgradeneeded = function(event) {
              var db = event.target.result;
              if (!db.objectStoreNames.contains('daily_rows')) {
                db.createObjectStore('daily_rows', {keyPath: 'dt'});
              }
              if (!db.objectStoreNames.contains('meta')) {
                db.createObjectStore('meta', {keyPath: 'k'});
              }
            };

            // ── History Cache: IndexedDB 상태 읽기 (5단계) ──────────────────
            // meta 스토어의 {k:'version', v:N} 레코드와 daily_rows의 마지막
            // (prev 방향 커서) 날짜를 함께 읽어 콜백으로 전달. 하나의 readonly
            // 트랜잭션 안에서 두 스토어를 같이 읽고 tx.oncomplete에서 취합한다.
            function readHistCacheState(db, callback) {
              var tx = db.transaction(['meta', 'daily_rows'], 'readonly');
              var metaStore = tx.objectStore('meta');
              var rowsStore = tx.objectStore('daily_rows');

              var version  = 0;
              var lastDate = null;

              var metaReq = metaStore.get('version');
              metaReq.onsuccess = function() {
                if (metaReq.result) version = metaReq.result.v;
              };

              var cursorReq = rowsStore.openCursor(null, 'prev');
              cursorReq.onsuccess = function(event) {
                var cursor = event.target.result;
                if (cursor) lastDate = cursor.value.dt;
              };

              tx.oncomplete = function() {
                callback({version: version, lastDate: lastDate});
              };
              tx.onerror = function(event) {
                console.error('[HIST-CACHE] read_state_error', event.target.error);
                callback({version: 0, lastDate: null});
              };
            }

            function sendCacheSyncWhenReady(state) {
              // window.__shinyConnected(app.py에서 설정)를 유일한 판단 기준으로
              // 사용한다. 2026-09-09 세션에서 py-shiny 1.6.2 소스코드로 직접
              // 확인한 사실:
              //   - shiny:connected는 WebSocket이 open되는 즉시 발생하며,
              //     이 시점의 서버는 아직 ConnectionState.Start 상태다
              //     (shiny.js 6512~6524행).
              //   - update 메시지(Shiny.setInputValue)는 서버 쪽에서
              //     verify_state(ConnectionState.Running)을 요구하고,
              //     서버가 Running으로 전환되는 건 init 처리 중 app.server(...)
              //     실행이 끝난 뒤다 (_session.py 820~874행).
              //   - shiny:sessioninitialized는 서버의 config 메시지(Running
              //     전환 이후 전송)를 클라이언트가 수신해야 발생한다
              //     (shiny.js 6948~6956행).
              // 즉 shiny:connected 기준으로는 서버가 아직 Start 상태일 때
              // update 메시지가 나갈 수 있어 ProtocolError를 유발할 수
              // 있으므로, app.py는 window.__shinyConnected를
              // shiny:sessioninitialized에서 설정하도록 되어 있다
              // (이 파일에서는 그 플래그를 읽기만 하면 됨 - 로직 변경 없음).
              if (window.__shinyConnected) {
                Shiny.setInputValue('history-client_cache_sync', state, {priority: 'event'});
              } else {
                $(document).one('shiny:sessioninitialized', function() {
                  Shiny.setInputValue('history-client_cache_sync', state, {priority: 'event'});
                });
              }
            }

            // getAll()은 keyPath(dt, ISO 날짜 문자열) 기준 오름차순으로 반환됨
            // (IndexedDB 표준 동작 - 별도 정렬 불필요).
            function loadCachedRowsIntoMemory(db, callback) {
              var tx  = db.transaction('daily_rows', 'readonly');
              var req = tx.objectStore('daily_rows').getAll();
              req.onsuccess = function() {
                callback(req.result || []);
              };
              req.onerror = function(event) {
                console.error('[HIST-CACHE] load_rows_error', event.target.error);
                callback([]);
              };
            }

              req.onsuccess = function(event) {
              window.__histDB = event.target.result;

              // 버전 읽기(readHistCacheState) 완료를 먼저 기다린 뒤 rows를 읽는다.
              readHistCacheState(window.__histDB, function(state) {
                // _cacheVersion을 IndexedDB meta.version 원본값으로 초기화.
                // 이후 history_cache_response가 오면 서버 버전으로 갱신됨.
                _cacheVersion = state.version;

                // 세션 연결 후 1회만 전송 (재전송 방지 플래그)
                if (!window.__histCacheSyncSent) {
                  window.__histCacheSyncSent = true;
                  sendCacheSyncWhenReady(state);
                }

                loadCachedRowsIntoMemory(window.__histDB, function(rows) {
                  _cachedConfirmedRows = rows;
                  if (_pendingCacheResponse) {
                    applyCacheResponseToMemory(_pendingCacheResponse);
                    _pendingCacheResponse = null;
                  }
                  renderFromCache();
                });
              });
            };

            req.onerror = function(event) {
              console.error('[HIST-CACHE] db_open_error', event.target.error);
            };
          })();

          // 기간 슬라이더 상태 (drawCharts 시 데이터 범위로 갱신)
          var SLIDER_STEPS      = 1000;  // 슬라이더 해상도 (드래그 부드러움)
          var _periodMinDays    = 30;    // 최소 구간 = 1개월
          var _periodDataMinMs  = null;  // 보유 데이터의 첫 날짜 (ms)
          var _periodDataMaxMs  = null;  // 보유 데이터의 마지막 날짜 (ms)

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

          // ── 기간 슬라이더 ────────────────────────────────────────────────
          // 슬라이더 값(0~SLIDER_STEPS)을 [최소구간(1개월) ~ 전체구간] 로그 스케일로 매핑.
          // 로그 스케일이라 슬라이더 앞쪽(왼쪽)에서 1~수개월 단위를 세밀하게,
          // 뒤쪽(오른쪽)에서 연 단위를 크게 조절하게 된다.
          function periodDaysToLabel(days, totalDays) {
            if (days >= totalDays - 0.5) return '전체';
            var months = Math.round(days / 30.44);
            if (months <= 1) return '1개월';
            if (months < 12) return months + '개월';
            var years     = Math.floor(months / 12);
            var remMonths = months % 12;
            return years + '년' + (remMonths > 0 ? ' ' + remMonths + '개월' : '');
          }

          function periodSliderValueToDays(sliderVal) {
            if (_periodDataMinMs === null || _periodDataMaxMs === null) return null;
            var totalDays = (_periodDataMaxMs - _periodDataMinMs) / 86400000;
            var minDays   = Math.min(_periodMinDays, totalDays);
            var maxDays   = totalDays;
            if (maxDays <= minDays || maxDays <= 0) return maxDays;
            var t = Math.max(0, Math.min(1, sliderVal / SLIDER_STEPS));
            return minDays * Math.pow(maxDays / minDays, t);
          }

          function periodDaysToSliderValue(days) {
            var totalDays = (_periodDataMaxMs - _periodDataMinMs) / 86400000;
            var minDays   = Math.min(_periodMinDays, totalDays);
            var maxDays   = totalDays;
            if (maxDays <= minDays || maxDays <= 0) return SLIDER_STEPS;
            var clamped = Math.max(minDays, Math.min(maxDays, days));
            var t = Math.log(clamped / minDays) / Math.log(maxDays / minDays);
            return Math.round(Math.max(0, Math.min(1, t)) * SLIDER_STEPS);
          }

          function applyPeriodDays(days) {
            var totalDays = (_periodDataMaxMs - _periodDataMinMs) / 86400000;
            var endMs     = _periodDataMaxMs;
            var startMs   = endMs - days * 86400000;
            if (startMs < _periodDataMinMs) startMs = _periodDataMinMs;

            var label = document.getElementById('period-slider-label');
            if (label) label.textContent = periodDaysToLabel(days, totalDays);

            var startStr = new Date(startMs).toISOString().slice(0,10);
            var endStr   = new Date(endMs).toISOString().slice(0,10);
            var charts = ['chart-asset', 'chart-twr'];
            charts.forEach(function(id) {
              var gd = document.getElementById(id);
              if (!gd || !gd.data) return;
              Plotly.relayout(gd, {'xaxis.range': [startStr, endStr]});
            });
          }

          // input 이벤트: 드래그하는 동안 실시간으로 계속 발생 (표준 동작).
          // 두 차트를 매 프레임 relayout하므로, 저사양 환경에서 버벅이면
          // 여기에 짧은 debounce(예: 30~50ms)를 추가로 넣을 수 있다.
          window.onPeriodSliderInput = function(sliderVal) {
            var days = periodSliderValueToDays(parseFloat(sliderVal));
            if (days === null) return;
            applyPeriodDays(days);
          };

          // drawCharts()에서 새 데이터 수신 시 호출: 슬라이더의 min/max 기준(날짜 범위)을
          // 갱신하고, 기존 기본값(3개월)에 해당하는 슬라이더 위치로 맞춘다.
          function initPeriodSlider(dates) {
            if (!dates || dates.length === 0) return;
            _periodDataMinMs = new Date(dates[0]).getTime();
            _periodDataMaxMs = new Date(dates[dates.length - 1]).getTime();

            var slider = document.getElementById('period-slider');
            if (!slider) return;

            var totalDays   = (_periodDataMaxMs - _periodDataMinMs) / 86400000;
            var defaultDays = Math.min(90, totalDays); // 기존 기본값(3개월)과 동일

            slider.value = periodDaysToSliderValue(defaultDays);

            var label = document.getElementById('period-slider-label');
            if (label) label.textContent = periodDaysToLabel(defaultDays, totalDays);
          }

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

            var dates  = asc.map(function(r) { return r.dt; });
            initPeriodSlider(dates);

            var assets = asc.map(function(r) { return parseFloat(r.ta) || 0; });
            var cflows = asc.map(function(r) { return parseFloat(r.cf) || 0; });
            var notes  = asc.map(function(r) { return r.cn || ''; });
            var twrRaw = asc.map(function(r) { return parseFloat(r.tw) || 0; });
            var ndxRaw = asc.map(function(r) { return parseFloat(r.nx) || 0; });

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
          function buildTr(r, prevRow) {
            var date    = r.dt;
            var total   = parseFloat(r.ta) || 0;
            var twr     = parseFloat(r.tw) || 0;
            var ndx     = parseFloat(r.nx) || 0;
            var cf      = parseFloat(r.cf) || 0;
            var cf_note = r.cn || '';
            var exp     = r.ex  !== '' ? parseFloat(r.ex)  : null;
            var cash    = r.cr  !== '' ? parseFloat(r.cr)  : null;
            var x1      = r.x1  !== '' ? parseFloat(r.x1)  : null;
            var x2      = r.x2  !== '' ? parseFloat(r.x2)  : null;
            var x3      = r.x3  !== '' ? parseFloat(r.x3)  : null;
            var usd_krw = parseFloat(r.ur) || 0;
            var prev    = prevRow ? parseFloat(prevRow.ta) : null;

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
              '<td style="text-align:right">' + (exp  !== null ? (exp  * 100).toFixed(1) + '%' : '-') + '</td>' +
              '<td style="text-align:right">' + (cash !== null ? (cash * 100).toFixed(1) + '%' : '-') + '</td>' +
              '<td style="text-align:right">' + (function() {
                if (!twr) return '-';
                var twrChg = parseFloat(r.tp);
                if (isNaN(twrChg) || r.tp === '') return fmtKrw(twr);
                var sign = twrChg >= 0 ? '+' : '';
                var cls  = twrChg >= 0 ? 'positive' : 'negative';
                return fmtKrw(twr) + '<br><span class="' + cls + '" style="font-size:11px">' + sign + twrChg.toFixed(2) + '%</span>';
              })() + '</td>' +
              '<td style="text-align:right">' + (function() {
                if (!ndx) return '-';
                var ndxChg = parseFloat(r.np);
                if (isNaN(ndxChg) || r.np === '') return ndx.toFixed(2);
                var sign = ndxChg >= 0 ? '+' : '';
                var cls  = ndxChg >= 0 ? 'positive' : 'negative';
                return ndx.toFixed(2) + '<br><span class="' + cls + '" style="font-size:11px">' + sign + ndxChg.toFixed(2) + '%</span>';
              })() + '</td>' +
              '<td style="text-align:right">' + (usd_krw ? usd_krw.toFixed(2) : '-') + '</td>' +
              '<td style="text-align:right">' + (x3 !== null ? (x3 * 100).toFixed(1) + '%' : '-') + '</td>' +
              '<td style="text-align:right">' + (x2 !== null ? (x2 * 100).toFixed(1) + '%' : '-') + '</td>' +
              '<td style="text-align:right">' + (x1 !== null ? (x1 * 100).toFixed(1) + '%' : '-') + '</td>' +
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
            rows.forEach(function(r, i) { tbody.appendChild(buildTr(r, rows[i + 1] || null)); });
          }

          // ── History Cache: mergedRows 계산 / 렌더 트리거 / 비교 (9단계) ────

          // 서버 _today_kst()와 동일한 기준(KST)으로 오늘 날짜 문자열 계산.
          // _todayRow 분리 판단에만 쓰이며, 실제 값 자체는 항상 서버가 보낸 그대로.
          function todayKstStr() {
            var now = new Date();
            var kstMs = now.getTime() + (9 * 60 - now.getTimezoneOffset()) * 60000;
            return new Date(kstMs).toISOString().slice(0, 10);
          }

          // history_cache_response의 payload.rows(내림차순)를 _cachedConfirmedRows(오름차순)에 반영.
          // mode=full: 치환. mode=append/patch: dt 기준 upsert 병합.
          function applyCacheResponseToMemory(payload) {
            var mode      = payload.mode;
            var rowsAsc   = (payload.rows || []).slice().reverse();

            if (mode === 'full') {
              _cachedConfirmedRows = rowsAsc;
              return;
            }
            var byDate = {};
            (_cachedConfirmedRows || []).forEach(function(r) { byDate[r.dt] = r; });
            rowsAsc.forEach(function(r) { byDate[r.dt] = r; });
            _cachedConfirmedRows = Object.keys(byDate).sort().map(function(dt) { return byDate[dt]; });
          }

          // confirmed rows(오름차순) + _todayRow를 합쳐 화면 렌더링용 포맷
          // (내림차순, 최신이 [0])으로 반환. DB 재조회 없이 메모리에서만 계산.
          function computeMergedRows() {
            var confirmed = _cachedConfirmedRows || [];
            var merged = confirmed.slice();
            if (_todayRow && _todayRow.dt) {
              var lastDt = merged.length > 0 ? merged[merged.length - 1].dt : null;
              if (lastDt === _todayRow.dt) {
                merged[merged.length - 1] = Object.assign({}, merged[merged.length - 1], _todayRow);
              } else {
                merged.push(_todayRow);
              }
            }
            merged.reverse();
            return merged;
          }

          function renderRows(rows) {
            if (!rows || rows.length === 0) return;
            if (isHistoryVisible()) {
              drawTable(rows);
              requestAnimationFrame(function() { drawCharts(rows); });
              _pendingDraw = false;
            } else {
              _pendingDraw = true;
            }
          }

          // cache(_cachedConfirmedRows + _todayRow 병합) 데이터가 준비된 경우 렌더.
          // (구 legacy 비교 로직은 12단계에서 제거됨 - 11단계까지 실사용 검증 중
          // MISMATCH 0건으로 cache 경로 정확성 확인 완료, git 이력 참조.)
          function renderFromCache() {
            if (_cachedConfirmedRows === null) return;
            renderRows(computeMergedRows());
          }

          // ── active_tab 변경 감지: history 탭 진입 시 pending draw 처리 ──
          $(document).on('shiny:inputchanged', function(e) {
            if (e.name === 'active_tab' && e.value === 'history') {
              if (_pendingDraw && _cachedConfirmedRows !== null) {
                var rows = computeMergedRows();
                if (rows && rows.length > 0) {
                  drawTable(rows);
                  requestAnimationFrame(function() { drawCharts(rows); });
                  _pendingDraw = false;
                }
              }
            }
          });

          // ── today_row 갱신 — 최상단 행 교체 + 차트 끝단 업데이트 ──────────
          // diff payload: 변경된 필드만 수신. 각 블록은 필요한 핵심 필드 존재 여부를
          // 먼저 확인하고, 없으면 해당 블록 스킵 (값 미변화 = 갱신 불필요).
          Shiny.addCustomMessageHandler('today_row_update', function(r) {

            // 1. _todayRow 갱신 — diff를 기존 오늘 row에 머지해서 항상 완전한 row 유지.
            //    이후 블록들이 참조할 today(날짜 문자열)도 여기서 함께 확정한다.
            if (!_todayRow) _todayRow = {};
            Object.assign(_todayRow, r);
            var today = r.dt || _todayRow.dt || null;
            if (!_todayRow.dt) _todayRow.dt = today;

            // 2. DOM 최상단 행 교체 — total_asset 포함된 경우에만 (행 전체 재생성 필요).
            //    _pendingDraw 상태면 스킵 (탭 진입 시 drawTable이 computeMergedRows()로 다시 그림).
            //    merged[0]=오늘(방금 갱신된 _todayRow), merged[1]=직전 확정일.
            if (!_pendingDraw && r.ta !== undefined && _cachedConfirmedRows !== null) {
              var merged  = computeMergedRows();
              var rowData = merged[0];
              var tbody   = document.getElementById('history-tbody');
              if (tbody && rowData) {
                var newTr    = buildTr(rowData, merged[1] || null);
                var existing = tbody.querySelector('tr[data-date="' + today + '"]');
                if (existing) {
                  tbody.replaceChild(newTr, existing);
                } else {
                  tbody.insertBefore(newTr, tbody.firstChild);
                }
              }
            }

            // 3. chart-asset 끝단 업데이트 — total_asset 있을 때만.
            if (r.ta !== undefined) {
              var gdAsset = document.getElementById('chart-asset');
              if (gdAsset && gdAsset.data) {
                var date    = today;
                var total   = parseFloat(r.ta) || 0;
                var cf      = parseFloat(r.cf) || 0;
                var cf_note = r.cn || '';

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
            }

            // 4. chart-twr 끝단 업데이트 — twr_pct / ndx_pct 있을 때만.
            if (r.twr_pct !== undefined && r.ndx_pct !== undefined) {
              var gdTwr = document.getElementById('chart-twr');
              if (gdTwr && gdTwr.data && gdTwr.data.length >= 2) {
                var date   = today;
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
            }

            // 화면 갱신은 위의 incremental DOM/Plotly patch(2~4)가 담당한다.
            // 이 핸들러는 시세 신호 등 고빈도 이벤트에 묶여 있으므로(단,
            // diff_display가 걸러서 실제 변경 있을 때만 도착) 풀 리드로우를
            // 걸지 않는다 - _todayRow는 이미 1번에서 갱신 완료된 상태.
          });

          // ── History Cache: 서버 payload를 IndexedDB에 저장 ──────────────────
          // - mode=full   : daily_rows를 clear() 후 전체 rows를 put() (덮어쓰기)
          // - mode=append/patch : clear 없이 rows만 put() (dt가 keyPath라 upsert)
          // - meta.version을 매번 server_version으로 갱신
          // - rows 저장과 version 갱신을 하나의 트랜잭션으로 묶어, 중간 실패 시
          //   "rows는 갱신됐는데 version은 예전 값"인 불일치가 생기지 않게 함.
          // - today_row는 이 스토어에 넣지 않음 (확정 rows만 캐시 대상, 오늘 데이터는
          //   today_row_update 메시지로 계속 별도 갱신됨).
          Shiny.addCustomMessageHandler('history_cache_response', function(payload) {
            if (!window.__histDB) {
              console.error('[HIST-CACHE] no_db');
              return;
            }

            var mode  = payload.mode;
            var rows  = payload.rows || [];
            var serverVersion = payload.server_version;

            var db = window.__histDB;
            var tx = db.transaction(['daily_rows', 'meta'], 'readwrite');
            var rowsStore = tx.objectStore('daily_rows');
            var metaStore = tx.objectStore('meta');

            if (mode === 'full') {
              rowsStore.clear();
            }
            rows.forEach(function(r) {
              rowsStore.put(r);
            });
            metaStore.put({k: 'version', v: serverVersion});

            tx.onerror = function(event) {
              console.error('[HIST-CACHE] save_error', event.target.error);
            };

            // ── History Cache: 메모리 캐시(_cachedConfirmedRows) 갱신 + 렌더 ──────
            // IndexedDB 트랜잭션 완료를 기다리지 않고 메모리 반영(같은 payload를 그대로
            // 씀 - IndexedDB 저장 실패와 무관하게 이번 세션 내 표시는 정확해야 하므로).
            // 초기 로드(loadCachedRowsIntoMemory)가 아직 안 끝났으면 큐잉만 하고,
            // 로드 완료 콜백에서 순서대로 적용한다(레이스 방지).
            _cacheVersion = serverVersion;
            if (_cachedConfirmedRows === null) {
              _pendingCacheResponse = payload;
            } else {
              applyCacheResponseToMemory(payload);
              renderFromCache();
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
    today_cf_trigger = reactive.value(0)  # 오늘 입출금 저장 시 강제 갱신용
    _reload_trigger  = reactive.value(0)  # 입출금 수정(과거) 시 DB rows 재로드용
    _last_today_row: dict = {}  # diff_display 비교 기준

    # 10단계: 5단계에서 받은 (client_version, client_last_date)를 세션에 저장.
    # daily_insert_signal이 다시 발동했을 때 "이 세션이 어디까지 알고 있었는지"의
    # 기준점으로 쓰인다. 아직 client_cache_sync가 안 온 상태(None)에서는
    # daily_insert_signal이 와도 아무것도 하지 않는다 (가드).
    _client_cache_state = reactive.value(None)

    # ── 과거 DB rows 캐시 ────────────────────────────────────────────────────
    # _reload_trigger / daily_insert_signal 시에만 DB 재조회.
    # _compute_and_send_cache_payload가 재사용(추가 DB 조회 없음).
    @reactive.calc
    def _db_rows():
        _reload_trigger.get()
        daily_insert_signal.get()
        return load_history()

    # ── History Cache: mode 판단 + payload 계산/전송 (7단계, 10단계 공용 헬퍼) ──
    # _sync_history_cache(최초 1회, client_cache_sync 트리거)와
    # _sync_history_cache_on_daily_insert(반복, daily_insert_signal 트리거)가
    # 공유하는 전송 로직. 일반 코루틴이라 여기서 읽는 reactive 값(_db_rows 등)에
    # 대한 의존성은 "호출한 effect" 기준으로 잡힌다.
    async def _compute_and_send_cache_payload(client_version, client_last_date):
        meta           = get_history_cache_meta()
        server_version = meta["version"]
        patch_start    = get_patch_start_date(client_version, server_version)

        decision = decide_sync_mode(client_version, client_last_date, server_version, patch_start)
        mode  = decision["mode"]
        since = decision["since"]

        rows = _db_rows()  # 확정 rows, ASC (기존 캐시 재사용, 추가 DB 조회 없음)

        new_rows    = []
        predecessor = None

        if mode == "patch":
            new_rows    = [r for r in rows if r[0] >= since]
            predecessor = find_predecessor(rows, since)
        elif mode == "append":
            new_rows = [r for r in rows if since is not None and r[0] > since]
            if not new_rows:
                mode = "none"
            else:
                predecessor = find_predecessor(rows, new_rows[0][0])

        print(
            f"[HIST-CACHE] step=6 event=mode_decided "
            f"client_version={client_version} server_version={server_version} "
            f"mode={mode} since={since}",
            flush=True,
        )

        if mode == "none":
            # 신규 확정일도 과거 수정도 없음. 세션 저장값은 이미 최신 상태이므로 그대로 둔다.
            return

        if mode == "full":
            data = build_history_rows(rows)
        else:
            rows_for_build = ([predecessor] if predecessor else []) + new_rows
            data = build_history_rows(rows_for_build)
            if predecessor:
                data = data[:-1]  # build_history_rows는 내림차순 반환 → predecessor는 마지막 원소

        payload = {"mode": mode, "server_version": server_version, "rows": data}
        await session.send_custom_message("history_cache_response", payload)

        print(
            f"[HIST-CACHE] step=7 event=payload_sent mode={mode} row_count={len(data)} "
            f"first_dt={data[-1]['dt'] if data else None} last_dt={data[0]['dt'] if data else None} "
            f"server_version={server_version}",
            flush=True,
        )

        # 10단계: 전송 후 세션 저장값을 이번에 보낸 데이터의 최신 확정일 기준으로 갱신.
        # data는 내림차순(build_history_rows 반환 규약)이므로 data[0]이 가장 최근 날짜.
        if data:
            new_last_date = datetime.date.fromisoformat(data[0]["dt"])
            _client_cache_state.set((server_version, new_last_date))

    # ── History Cache: mode 판단 + payload 계산/전송 (7단계, 5단계 input 트리거) ──
    @reactive.effect
    @reactive.event(input.client_cache_sync)
    async def _sync_history_cache():
        client_state          = input.client_cache_sync()
        client_version        = client_state.get("version", 0)
        client_last_date_str  = client_state.get("lastDate")
        client_last_date = (
            datetime.date.fromisoformat(client_last_date_str) if client_last_date_str else None
        )
        _client_cache_state.set((client_version, client_last_date))
        await _compute_and_send_cache_payload(client_version, client_last_date)

    # ── History Cache: daily_insert_signal 재계산 (10단계, 열린 세션 실시간 반영) ──
    # _sync_history_cache와는 별개의 effect (기존 _db_rows의 daily_insert_signal
    # 의존과도 별개). 세션이 아직 최초 client_cache_sync를 보내기 전
    # (_client_cache_state가 None)에는 아무것도 하지 않는다 (가드).
    #
    # _client_cache_state.get()을 reactive.isolate() 안에서 읽는 이유:
    # 이 effect는 _compute_and_send_cache_payload 안에서 _client_cache_state를
    # 갱신한다. isolate 없이 그냥 읽으면 그 읽기 자체가 의존성으로 잡혀서,
    # 이 effect가 만든 변경에 의해 자기 자신이 다시 트리거되는 무한루프가 생긴다.
    # daily_insert_signal만을 유일한 트리거로 남기기 위해 isolate로 끊는다.
    @reactive.effect
    async def _sync_history_cache_on_daily_insert():
        daily_insert_signal.get()
        with reactive.isolate():
            state = _client_cache_state.get()
        if state is None:
            return
        client_version, client_last_date = state
        print(
            f"[HIST-CACHE] step=10 event=daily_insert_recalc "
            f"client_version={client_version} client_last_date={client_last_date}",
            flush=True,
        )
        await _compute_and_send_cache_payload(client_version, client_last_date)

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
        row  = build_today_row(t, rows)

        diff = diff_display(row, _last_today_row)
        if not diff:
            return

        await session.send_custom_message("today_row_update", diff)
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
                print(f"[history] today_cash_flow Redis 저장 실패: {e}", flush=True)
            from common.redis_store import recalc_today_row
            recalc_today_row()
            today_cf_trigger.set(today_cf_trigger.get() + 1)
        else:
            save_cash_flow(date_str, cf, note)
            _reload_trigger.set(_reload_trigger.get() + 1)

        ui.modal_remove()
        ui.notification_show("저장됐습니다.", type="message", duration=2)