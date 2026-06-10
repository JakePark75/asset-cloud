import plotly.graph_objects as go
from .history_DAL import calc_twr_pct, calc_ndx_pct
from .history_utils import fmt_krw, fmt_10m

# ── 공통 레이아웃 ──────────────────────────────────────────────────────────────

_BASE_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="#111111",
    font=dict(color="#aaaaaa", size=11),
    margin=dict(l=8, r=8, t=8, b=8),
    legend=dict(
        orientation="h",
        yanchor="bottom", y=1.02,
        xanchor="right", x=1,
        font=dict(size=11),
        bgcolor="rgba(0,0,0,0)",
    ),
    xaxis=dict(
        gridcolor="#222222",
        linecolor="#333333",
        tickfont=dict(size=10),
        showspikes=True,
        spikecolor="#444444",
        spikemode="across",
        spikesnap="cursor",
        fixedrange=True,   # JS로 range 직접 제어
    ),
    yaxis=dict(
        gridcolor="#222222",
        linecolor="#333333",
        tickfont=dict(size=10),
        fixedrange=True,
    ),
    hovermode="x unified",
    hoverlabel=dict(
        bgcolor="#1a1a1a",
        bordercolor="#333333",
        font=dict(color="#ffffff", size=12),
        namelength=-1,
    ),
    dragmode=False,   # Plotly 기본 drag 비활성 → JS가 터치 직접 처리
    height=220,
)

_XAXIS_MONTH = dict(
    **_BASE_LAYOUT["xaxis"],
    tickformat="%-m\n%Y",
    dtick="M1",
)


def _empty_fig():
    fig = go.Figure()
    fig.update_layout(**_BASE_LAYOUT)
    return fig

# ── 그래프 1: 총자산 추이 ──────────────────────────────────────────────────────

def make_chart_asset(rows):
    if not rows:
        return _empty_fig()

    dates  = [r[0] for r in rows]
    assets = [float(r[1] or 0) for r in rows]
    cflows = [float(r[4] or 0) for r in rows]
    notes  = [r[5] or "" for r in rows]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=dates,
        y=assets,
        mode="lines",
        name="총자산",
        line=dict(color="#00c073", width=2),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=[[fmt_krw(a) + "원", cflows[i], notes[i]] for i, a in enumerate(assets)],
    ))

    dep_idx = [i for i, cf in enumerate(cflows) if cf > 0]
    if dep_idx:
        fig.add_trace(go.Scatter(
            x=[dates[i] for i in dep_idx],
            y=[assets[i] * 1.012 for i in dep_idx],
            mode="markers",
            name="입금",
            marker=dict(
                symbol="triangle-up",
                size=10,
                color="#ff4d4d",
                line=dict(color="#ffffff", width=1),
            ),
            hovertemplate="%{customdata}<extra>입금</extra>",
            customdata=[
                f"+{fmt_krw(cflows[i])}원" + (f"\n{notes[i]}" if notes[i] else "")
                for i in dep_idx
            ],
        ))

    wd_idx = [i for i, cf in enumerate(cflows) if cf < 0]
    if wd_idx:
        fig.add_trace(go.Scatter(
            x=[dates[i] for i in wd_idx],
            y=[assets[i] * 0.988 for i in wd_idx],
            mode="markers",
            name="출금",
            marker=dict(
                symbol="triangle-down",
                size=10,
                color="#4d9fff",
                line=dict(color="#ffffff", width=1),
            ),
            hovertemplate="%{customdata}<extra>출금</extra>",
            customdata=[
                f"{fmt_krw(cflows[i])}원" + (f"\n{notes[i]}" if notes[i] else "")
                for i in wd_idx
            ],
        ))

    y_min, y_max = min(assets), max(assets)
    tick_vals = [y_min + (y_max - y_min) * i / 4 for i in range(5)]
    tick_text = [fmt_10m(v) for v in tick_vals]

    # 초기 범위: 최근 3개월
    date_strs = [str(r[0]) for r in rows]
    init_range = _init_range(date_strs, "3m")

    layout = {
        **_BASE_LAYOUT,
        "xaxis": {
            **_XAXIS_MONTH,
            "range": init_range,
        },
        "yaxis": {
            **_BASE_LAYOUT["yaxis"],
            "tickmode": "array",
            "tickvals": tick_vals,
            "ticktext": tick_text,
        },
    }
    fig.update_layout(**layout)

    html = fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        div_id="chart-asset",
        config={"displayModeBar": False},
    )
    html += _touch_script("chart-asset")
    return html

# ── 그래프 2: TWR vs NDX100 ────────────────────────────────────────────────────

def make_chart_twr(rows):
    if not rows:
        return _empty_fig()

    dates   = [r[0] for r in rows]
    twr_pct = calc_twr_pct(rows)
    ndx_pct = calc_ndx_pct(rows)

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=dates,
        y=twr_pct,
        mode="lines",
        name="내 수익률",
        line=dict(color="#00c073", width=2),
        hovertemplate="%{y:.2f}%<extra>내 수익률</extra>",
    ))

    fig.add_trace(go.Scatter(
        x=dates,
        y=ndx_pct,
        mode="lines",
        name="NDX100",
        line=dict(color="#4d9fff", width=2, dash="dot"),
        hovertemplate="%{y:.2f}%<extra>NDX100</extra>",
    ))

    fig.add_hline(y=0, line_color="#333333", line_width=1)

    date_strs = [str(r[0]) for r in rows]
    init_range = _init_range(date_strs, "3m")

    layout = {
        **_BASE_LAYOUT,
        "xaxis": {
            **_XAXIS_MONTH,
            "range": init_range,
        },
        "yaxis": {
            **_BASE_LAYOUT["yaxis"],
            "ticksuffix": "%",
            "zeroline": False,
        },
    }
    fig.update_layout(**layout)

    html = fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        div_id="chart-twr",
        config={"displayModeBar": False},
    )
    html += _touch_script("chart-twr")
    return html

# ── 유틸 ──────────────────────────────────────────────────────────────────────

def _touch_script(chart_id: str) -> str:
    """차트 HTML 뒤에 삽입할 터치 이벤트 스크립트."""
    return f"""
<script>
(function() {{
  var chartId = '{chart_id}';

  function attachTouch(gd) {{
    if (gd._touchAttached) return;
    gd._touchAttached = true;

    gd.style.touchAction = 'pan-y';
    gd.on('plotly_beforehover', function() {{ return false; }});
    // gd는 position:relative여야 팝업이 gd 기준으로 배치됨
    if (getComputedStyle(gd).position === 'static') {{
      gd.style.position = 'relative';
    }}

    var touchStartX     = null;
    var touchStartY     = null;
    var touchStartRange = null;
    var isPanning       = false;
    var isHovering      = false;
    var longTimer       = null;
    var LONG_MS         = 500;
    var PAN_THRESHOLD   = 8;
    var LONG_THRESHOLD  = 6;

    var toMs  = function(s) {{ return new Date(s).getTime(); }};
    var toStr = function(ms) {{ return new Date(ms).toISOString().slice(0,10); }};

    function getCurrentRange() {{
      return gd.layout.xaxis.range.map(toMs);
    }}

    function getDataRange() {{
      var xs = gd.data[0].x;
      return [toMs(xs[0]), toMs(xs[xs.length - 1])];
    }}

    // ── 커스텀 팝업 ──────────────────────────────────────────────────────────

    var popup = document.createElement('div');
    popup.style.cssText = [
      'position:absolute',
      'background:#1a1a1a',
      'border:1px solid #444',
      'border-radius:6px',
      'padding:7px 10px',
      'font-size:12px',
      'color:#fff',
      'pointer-events:none',
      'white-space:nowrap',
      'display:none',
      'z-index:999',
      'line-height:1.7',
    ].join(';');
    gd.appendChild(popup);

    // 수직 보조선
    var vline = document.createElement('div');
    vline.style.cssText = [
      'position:absolute',
      'top:0',
      'width:1px',
      'height:100%',
      'background:#666',
      'pointer-events:none',
      'display:none',
      'z-index:998',
    ].join(';');
    gd.appendChild(vline);

    // 수평 보조선 (트레이스별, 동적으로 생성)
    var hlines = [];

    // clientX → 가장 가까운 데이터 인덱스 반환
    function clientXToIndex(clientX) {{
      var rect  = gd.getBoundingClientRect();
      var range = getCurrentRange();
      var r0    = range[0];
      var r1    = range[1];

      // 실제 Plotly 플롯 영역
      var plot = gd.querySelector('.nsewdrag');
      if (!plot) return 0;

      var plotRect = plot.getBoundingClientRect();

      var px = clientX - plotRect.left;

      if (px < 0) px = 0;
      if (px > plotRect.width) px = plotRect.width;

      var ratio = px / plotRect.width;
      var targetMs = r0 + ratio * (r1 - r0);

      var xs  = gd.data[0].x;
      var best = 0;
      var bestDiff = Math.abs(toMs(xs[0]) - targetMs);

      for (var i = 1; i < xs.length; i++) {{
        var diff = Math.abs(toMs(xs[i]) - targetMs);
        if (diff < bestDiff) {{
          bestDiff = diff;
          best = i;
        }} else {{
          break;
        }}
      }}

      return best;
    }}

    function showPopup(clientX) {{
      var idx = clientXToIndex(clientX);
      var xs  = gd.data[0].x;
      var dateStr = String(xs[idx]);  // "YYYY-MM-DD"

      // 날짜 포맷: YYYY년 MM월 DD일
      var parts = dateStr.split('-');
      var label = parts[0] + '년 ' + parseInt(parts[1]) + '월 ' + parseInt(parts[2]) + '일';

      // 각 트레이스 값 수집 (markers 트레이스는 x 배열이 다르므로 별도 처리)
      var lines = ['<b>' + label + '</b>'];
      for (var t = 0; t < gd.data.length; t++) {{
        var trace = gd.data[t];
        if (!trace.y || trace.mode === 'markers') continue;
        // trace.x와 data[0].x가 같은 경우만 idx 그대로 사용
        var yVal = trace.y[idx];
        if (yVal === undefined || yVal === null) continue;
        var name  = trace.name || ('trace' + t);
        var color = (trace.line && trace.line.color) || '#aaa';
        // y값 포맷: 퍼센트 트레이스면 소수점 2자리, 아니면 정수 천단위
        var valStr;
        if (trace.hovertemplate && trace.hovertemplate.indexOf('%') !== -1
            && trace.hovertemplate.indexOf(':.2f') !== -1) {{
          valStr = yVal.toFixed(2) + '%';
        }} else {{
          valStr = Math.round(yVal).toLocaleString() + '원';
        }}
        lines.push(
          '<span style="color:' + color + '">■</span> ' + name + ': ' + valStr
        );
      }}
      var cd0 = gd.data[0].customdata && gd.data[0].customdata[idx];
      if (Array.isArray(cd0)) {{
        var cf = cd0[1];
        var note = cd0[2];

        if (cf !== 0) {{
          var cfColor = cf > 0 ? '#ff4d4d' : '#4d9fff';

          lines.push(
            '<span style="color:' + cfColor + '">■</span> 입출금: ' +
            (cf > 0 ? '+' : '') +
            Math.round(cf).toLocaleString() +
            '원'
          );

          if (note) {{
            lines.push(
              '<span style="color:' + cfColor + '">■</span> 내역: ' + note
            );
          }}
        }}
      }}
      popup.innerHTML = lines.join('<br>');
      popup.style.display = 'block';

      // 팝업 위치: 터치 x 기준, gd 좌측 상단 기준 좌표로 변환
      var gdRect = gd.getBoundingClientRect();
      var localX = clientX - gdRect.left;
      var popX   = localX + 12;
      var popY   = 8;
      popup.style.left = popX + 'px';
      popup.style.top  = popY + 'px';
      // 렌더 후 오른쪽 잘림 보정
      var popW = popup.offsetWidth;
      if (popX + popW > gdRect.width - 4) {{
        popup.style.left = (localX - popW - 12) + 'px';
      }}

      // 수직 보조선 위치
      var plot = gd.querySelector('.nsewdrag');

      if (plot) {{
        var plotRect = plot.getBoundingClientRect();
        vline.style.left = (plotRect.left - gdRect.left + (clientX - plotRect.left)) + 'px';
      }} else {{
        vline.style.left = localX + 'px';
      }}

      vline.style.display = 'block';

      // 수평 보조선 (트레이스별 y값 기준)
      // 기존 hline 제거
      hlines.forEach(function(hl) {{ if (hl.parentNode) hl.parentNode.removeChild(hl); }});
      hlines = [];

      if (plot) {{
        var plotRect2 = plot.getBoundingClientRect();
        var plotTop    = plotRect2.top  - gdRect.top;
        var plotHeight = plotRect2.height;

        for (var t = 0; t < gd.data.length; t++) {{
          var trace = gd.data[t];
          if (!trace.y || trace.mode === 'markers') continue;
          var yVal2 = trace.y[idx];
          if (yVal2 === undefined || yVal2 === null) continue;

          var yRange  = gd.layout.yaxis.range;
          var yMin    = yRange[0];
          var yMax    = yRange[1];
          var yRatio  = 1 - (yVal2 - yMin) / (yMax - yMin);
          var yPx     = plotTop + yRatio * plotHeight;

          var color = (trace.line && trace.line.color) || '#666';
          var hl = document.createElement('div');
          hl.style.cssText = [
            'position:absolute',
            'left:0',
            'width:100%',
            'height:1px',
            'background:' + color,
            'opacity:0.5',
            'pointer-events:none',
            'z-index:997',
          ].join(';');
          hl.style.top = yPx + 'px';
          gd.appendChild(hl);
          hlines.push(hl);
        }}
      }}
    }}

    function hidePopup() {{
      popup.style.display = 'none';
      vline.style.display = 'none';
      hlines.forEach(function(hl) {{ if (hl.parentNode) hl.parentNode.removeChild(hl); }});
      hlines = [];
    }}

    // ── 터치 이벤트 ──────────────────────────────────────────────────────────

    gd.addEventListener('touchstart', function(e) {{
      if (e.touches.length !== 1) return;
      var t = e.touches[0];
      touchStartX     = t.clientX;
      touchStartY     = t.clientY;
      touchStartRange = getCurrentRange();
      isPanning       = false;
      isHovering      = false;

      longTimer = setTimeout(function() {{
        if (!isPanning) {{
          isHovering = true;
          showPopup(touchStartX);
        }}
      }}, LONG_MS);
    }}, {{ passive: true }});

    gd.addEventListener('touchmove', function(e) {{
      if (e.touches.length !== 1 || touchStartX === null) return;
      var t  = e.touches[0];
      var dx = t.clientX - touchStartX;
      var dy = t.clientY - touchStartY;

      if (longTimer && Math.abs(dx) > LONG_THRESHOLD) {{
        clearTimeout(longTimer);
        longTimer = null;
      }}

      if (isHovering) {{
        e.preventDefault();
        showPopup(t.clientX);
        return;
      }}

      if (!isPanning && Math.abs(dx) < PAN_THRESHOLD) return;
      isPanning = true;
      e.preventDefault();

      var r0         = touchStartRange[0];
      var r1         = touchStartRange[1];
      var rangeMs    = r1 - r0;
      var chartWidth = gd.getBoundingClientRect().width;
      var msPerPx    = rangeMs / chartWidth;
      var shiftMs    = -dx * msPerPx;

      var dr    = getDataRange();
      var newR0 = r0 + shiftMs;
      var newR1 = r1 + shiftMs;
      if (newR0 < dr[0]) {{ newR0 = dr[0]; newR1 = dr[0] + rangeMs; }}
      if (newR1 > dr[1]) {{ newR1 = dr[1]; newR0 = dr[1] - rangeMs; }}

      Plotly.relayout(gd, {{
        'xaxis.range': [toStr(newR0), toStr(newR1)],
      }});
    }}, {{ passive: false }});

    gd.addEventListener('touchend', function(e) {{
      if (longTimer) {{ clearTimeout(longTimer); longTimer = null; }}
      hidePopup();
      touchStartX = null;
      isPanning   = false;
      isHovering  = false;
    }}, {{ passive: true }});
  }}

  // 차트 렌더링 완료 대기 후 부착
  function tryAttach() {{
    var gd = document.getElementById(chartId);
    if (gd && gd._fullLayout) {{
      attachTouch(gd);
      return;
    }}
    var parent = document.querySelector('.page-inner') || document.body;
    var observer = new MutationObserver(function() {{
      var gd = document.getElementById(chartId);
      if (gd && gd._fullLayout) {{
        observer.disconnect();
        attachTouch(gd);
      }}
    }});
    observer.observe(parent, {{ childList: true, subtree: true }});
  }}

  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', tryAttach);
  }} else {{
    tryAttach();
  }}
}})();
</script>
"""


def _init_range(date_strs: list[str], period: str):
    """전체 데이터 기준으로 초기 x축 범위 계산."""
    if not date_strs:
        return None
    last = date_strs[-1]
    first = date_strs[0]
    if period == "1m":
        from datetime import date, timedelta
        end = date.fromisoformat(last)
        start = max(date.fromisoformat(first), end - timedelta(days=30))
        return [str(start), str(end)]
    elif period == "3m":
        from datetime import date, timedelta
        end = date.fromisoformat(last)
        start = max(date.fromisoformat(first), end - timedelta(days=90))
        return [str(start), str(end)]
    else:
        return [first, last]