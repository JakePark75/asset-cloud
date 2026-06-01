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
    ),
    yaxis=dict(
        gridcolor="#222222",
        linecolor="#333333",
        tickfont=dict(size=10),
    ),
    hovermode="x unified",
    hoverlabel=dict(
        bgcolor="#1a1a1a",
        bordercolor="#333333",
        font=dict(color="#ffffff", size=12),
        namelength=-1,
    ),
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
    """
    총자산 추이 + 입출금 마커 Plotly Figure 반환.
    입금: 선 위 ▲ (초록)
    출금: 선 아래 ▼ (빨강)
    """
    if not rows:
        return _empty_fig()

    dates  = [r[0] for r in rows]
    assets = [float(r[1] or 0) for r in rows]
    cflows = [float(r[4] or 0) for r in rows]
    notes  = [r[5] or "" for r in rows]

    fig = go.Figure()

    # 총자산 선
    fig.add_trace(go.Scatter(
        x=dates,
        y=assets,
        mode="lines",
        name="총자산",
        line=dict(color="#00c073", width=2),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=[fmt_krw(a) + "원" for a in assets],
    ))

    # 입금 마커 (선 위 1.2% 오프셋)
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
                color="#00c073",
                line=dict(color="#ffffff", width=1),
            ),
            hovertemplate="%{customdata}<extra>입금</extra>",
            customdata=[
                f"+{fmt_krw(cflows[i])}원" + (f"\n{notes[i]}" if notes[i] else "")
                for i in dep_idx
            ],
        ))

    # 출금 마커 (선 아래 1.2% 오프셋)
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
                color="#ff4d4d",
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
        layout = {
            **_BASE_LAYOUT,
            "xaxis": _XAXIS_MONTH,
            "yaxis": {
                **_BASE_LAYOUT["yaxis"],
                "tickmode": "array",
                "tickvals": tick_vals,
                "ticktext": tick_text,
            },
        }
        fig.update_layout(**layout)
        return fig

# ── 그래프 2: TWR vs NDX100 ────────────────────────────────────────────────────

def make_chart_twr(rows):
    """
    twr_asset 기준 보정수익률(%) vs NDX100 정규화 수익률(%) 비교 Figure 반환.
    """
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

    # 0% 기준선
    fig.add_hline(y=0, line_color="#333333", line_width=1)

    layout = {
            **_BASE_LAYOUT,
            "xaxis": _XAXIS_MONTH,
            "yaxis": {**_BASE_LAYOUT["yaxis"], "ticksuffix": "%", "zeroline": False},
        }
    fig.update_layout(**layout)
    return fig