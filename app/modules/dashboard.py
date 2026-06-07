import datetime
import json
import math
from shiny import module, ui, render, reactive

from app.db import get_db
from app.price_signal import price_signal as _price_signal
from app.utils.metrics import (
    to_f, calculate_xirr, calculate_monthly_irr,
    calculate_alpha, calculate_beta,
    calculate_daily_profit, calculate_retirement_asset,
    calculate_exposure_and_ratios,
)
from app.modules.components import fmt_krw


# ── 포맷 헬퍼 ────────────────────────────────────────────────

def _fmt_pct(val: float, decimals: int = 2) -> str:
    return f"{val * 100:+.{decimals}f}%"

def _fmt_pct_plain(val: float, decimals: int = 2) -> str:
    return f"{val * 100:.{decimals}f}%"

def _pnl_class(val: float) -> str:
    if val > 0: return "db-pos"
    if val < 0: return "db-neg"
    return "db-neu"

def _arrow(val: float) -> str:
    if val > 0: return "▲"
    if val < 0: return "▼"
    return "–"

def _fmt_krw_short(val: float) -> str:
    abs_val = abs(val)
    if abs_val >= 1_0000_0000:
        return f"₩{abs_val / 1_0000_0000:.1f}억"
    elif abs_val >= 1_000_000:
        return f"₩{abs_val / 1_000_000:.0f}M"
    elif abs_val >= 10_000:
        return f"₩{abs_val / 10_000:.0f}만"
    return fmt_krw(abs_val)


# ── DAL ──────────────────────────────────────────────────────

def _load_summary_data() -> dict:
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT date, total_asset, cash_flow, ndx100,
                   exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio, twr_asset
            FROM daily_summary
            ORDER BY date ASC
        """)
        rows = cur.fetchall()

        cur.execute("SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X'")
        fx = cur.fetchone()
        usd_krw = to_f(fx[0]) if fx else 1300.0

        cur.execute("""
            SELECT p.ticker, p.quantity, t.current_price, t.leverage, t.market
            FROM positions p
            LEFT JOIN tickers t ON p.ticker = t.ticker
        """)
        raw_rows = cur.fetchall()

        pos_rows = []
        for ticker, qty, price, leverage, market in raw_rows:
            if ticker == "KRW":
                pos_rows.append((ticker, qty, 1.0, 1, "CASH"))
            elif ticker == "USD":
                pos_rows.append((ticker, qty, usd_krw, 1, "CASH"))
            else:
                pos_rows.append((ticker, qty, price, leverage, market))

        cur.execute("SELECT current_price FROM tickers WHERE ticker = '^NDX'")
        ndx_row = cur.fetchone()
        live_ndx100 = to_f(ndx_row[0]) if ndx_row else None

        today = datetime.date.today()
        cur.execute("SELECT cash_flow FROM daily_summary WHERE date = %s", (today,))
        today_cf_row = cur.fetchone()
        today_cf = to_f(today_cf_row[0]) if today_cf_row else 0.0

    if not rows:
        return {}

    try:
        with open("scheduler/config.json", "r") as f:
            cfg = json.load(f)
        rd_str = cfg.get("retirement_date", "20351231")
        retirement_date = datetime.date(int(rd_str[:4]), int(rd_str[4:6]), int(rd_str[6:8]))
    except Exception:
        retirement_date = datetime.date(2035, 12, 31)

    rt           = calculate_exposure_and_ratios(pos_rows, usd_krw)
    total_asset  = rt["total_asset"]
    exposure     = rt["exposure"]
    cash_ratio   = rt["cash_ratio"]
    invest_ratio = 1.0 - cash_ratio
    x1_ratio     = rt["x1_ratio"]
    x2_ratio     = rt["x2_ratio"]
    x3_ratio     = rt["x3_ratio"]

    latest      = rows[-1]
    prev_asset  = to_f(latest[1])
    prev_twr    = to_f(latest[9])
    prev_ndx100 = to_f(latest[3])

    asset_delta     = total_asset - prev_asset
    asset_delta_pct = (asset_delta / prev_asset) if prev_asset else 0.0
    daily_profit    = calculate_daily_profit(total_asset, today_cf, prev_asset)

    denom    = prev_asset
    live_twr = prev_twr * ((total_asset - today_cf) / denom) if denom != 0 else prev_twr
    live_ndx = live_ndx100 if live_ndx100 else prev_ndx100

    cash_flows  = [(rows[0][0], -to_f(rows[0][1]))]
    cash_flows += [(r[0], -to_f(r[2])) for r in rows[1:] if to_f(r[2]) != 0]
    cash_flows.append((today, total_asset))

    annual_irr   = calculate_xirr(cash_flows)
    monthly_irr  = calculate_monthly_irr(cash_flows)

    start_row    = (to_f(rows[0][9]), to_f(rows[0][3]))
    end_row      = (live_twr, live_ndx)
    cumul_alpha  = calculate_alpha(start_row, end_row)
    total_months = max(1, (today - rows[0][0]).days / 30.0)
    monthly_alpha = cumul_alpha / total_months

    cutoff   = today - datetime.timedelta(days=30)
    row_30   = next((r for r in rows if r[0] >= cutoff), rows[0])
    alpha_30 = calculate_alpha((to_f(row_30[9]), to_f(row_30[3])), (live_twr, live_ndx))

    beta_rows_all = [(to_f(r[1]), to_f(r[3])) for r in rows] + [(total_asset, live_ndx)]
    beta_all      = calculate_beta(beta_rows_all)
    rows_30       = [r for r in rows if r[0] >= cutoff]
    beta_rows_30  = [(to_f(r[1]), to_f(r[3])) for r in rows_30] + [(total_asset, live_ndx)]
    beta_30       = calculate_beta(beta_rows_30) if len(beta_rows_30) >= 3 else 0.0

    retirement_asset = calculate_retirement_asset(total_asset, monthly_irr, retirement_date)

    # 히어로 차트용: total_asset 이력 최대 100포인트 샘플링 (실시간 마지막 포인트 추가)
    all_assets = [to_f(r[1]) for r in rows] + [total_asset]
    n = len(all_assets)
    if n > 100:
        step = n / 100
        sampled = [all_assets[int(i * step)] for i in range(100)]
        sampled[-1] = total_asset
    else:
        sampled = all_assets
    chart_data = sampled

    return {
        "latest_date":      today,
        "total_asset":      total_asset,
        "asset_delta":      asset_delta,
        "asset_delta_pct":  asset_delta_pct,
        "daily_profit":     daily_profit,
        "exposure":         exposure,
        "cash_ratio":       cash_ratio,
        "invest_ratio":     invest_ratio,
        "x1_ratio":         x1_ratio,
        "x2_ratio":         x2_ratio,
        "x3_ratio":         x3_ratio,
        "annual_irr":       annual_irr,
        "monthly_irr":      monthly_irr,
        "cumul_alpha":      cumul_alpha,
        "monthly_alpha":    monthly_alpha,
        "alpha_30":         alpha_30,
        "beta_all":         beta_all,
        "beta_30":          beta_30,
        "retirement_asset": retirement_asset,
        "retirement_date":  retirement_date,
        "chart_data":       chart_data,
    }


def _load_position_data() -> list[dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.ticker, t.name, t.market, t.leverage, p.quantity, t.current_price
            FROM positions p
            LEFT JOIN tickers t ON p.ticker = t.ticker
            ORDER BY p.ticker
        """)
        rows = cur.fetchall()
        cur.execute("SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X'")
        fx = cur.fetchone()
        usd_krw = to_f(fx[0]) if fx else 1300.0

    result = []
    for ticker, name, market, leverage, qty, price in rows:
        qty    = to_f(qty)
        price  = to_f(price)
        market = (market or "").upper()
        if ticker == "KRW":
            eval_krw = qty
        elif market in ("NAS", "AMS", "ARC"):
            eval_krw = qty * price * usd_krw
        else:
            eval_krw = qty * price
        result.append({
            "ticker":   ticker,
            "name":     name or ticker,
            "market":   market,
            "leverage": int(leverage) if leverage else 1,
            "eval_krw": eval_krw,
        })
    return result


# ── SVG 헬퍼 ─────────────────────────────────────────────────

# 레버리지별 색상 팔레트 (같은 레버리지 내 명도 변화)
_LEV_PALETTES = {
    1: ["#00c073", "#00a862", "#009050", "#007840"],
    2: ["#e6a817", "#c98f0f", "#ad7a0c", "#916509"],
    3: ["#ff4d4d", "#e63c3c", "#cc2c2c", "#b21c1c"],
    0: ["#444444", "#555555"],  # 현금
}

def _donut_svg(slices: list[dict]) -> str:
    """
    slices: [{"label": str, "value": float, "color": str}, ...]
    SVG 도넛 차트 생성 (130x130)
    """
    total = sum(s["value"] for s in slices)
    if total == 0:
        return ""

    cx, cy, r_outer, r_inner = 65, 65, 58, 36
    gap_angle = 1.5  # 슬라이스 간 갭 (도)

    paths = []
    angle = -90.0  # 12시 방향 시작

    for s in slices:
        frac      = s["value"] / total
        sweep     = frac * 360 - gap_angle
        if sweep <= 0:
            angle += frac * 360
            continue

        start_rad = math.radians(angle)
        end_rad   = math.radians(angle + sweep)

        x1o = cx + r_outer * math.cos(start_rad)
        y1o = cy + r_outer * math.sin(start_rad)
        x2o = cx + r_outer * math.cos(end_rad)
        y2o = cy + r_outer * math.sin(end_rad)
        x1i = cx + r_inner * math.cos(end_rad)
        y1i = cy + r_inner * math.sin(end_rad)
        x2i = cx + r_inner * math.cos(start_rad)
        y2i = cy + r_inner * math.sin(start_rad)

        large = 1 if sweep > 180 else 0

        d = (
            f"M {x1o:.2f} {y1o:.2f} "
            f"A {r_outer} {r_outer} 0 {large} 1 {x2o:.2f} {y2o:.2f} "
            f"L {x1i:.2f} {y1i:.2f} "
            f"A {r_inner} {r_inner} 0 {large} 0 {x2i:.2f} {y2i:.2f} "
            f"Z"
        )
        paths.append(f'<path d="{d}" fill="{s["color"]}" />')
        angle += frac * 360

    paths_html = "\n".join(paths)
    return f'''<svg viewBox="0 0 130 130" xmlns="http://www.w3.org/2000/svg">
{paths_html}
</svg>'''


def _hero_line_svg(values: list[float]) -> str:
    """
    총자산 히어로 오버레이 라인차트 SVG
    축 없음, 그린 라인 + 하단 그라데이션 fill
    """
    if len(values) < 2:
        return ""

    W, H = 300, 110
    pad_b = 10

    min_v = min(values)
    max_v = max(values)
    rng   = max_v - min_v or 1

    def px(i, v):
        x = i / (len(values) - 1) * W
        y = H - pad_b - ((v - min_v) / rng) * (H - pad_b - 10)
        return x, y

    pts = [px(i, v) for i, v in enumerate(values)]
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    # fill path: 라인 → 우하단 → 좌하단
    fill_d = f"M {pts[0][0]:.1f},{pts[0][1]:.1f} "
    fill_d += " ".join(f"L {x:.1f},{y:.1f}" for x, y in pts[1:])
    fill_d += f" L {pts[-1][0]:.1f},{H} L {pts[0][0]:.1f},{H} Z"

    return f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
  <defs>
    <linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#00c073" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="#00c073" stop-opacity="0.0"/>
    </linearGradient>
  </defs>
  <path d="{fill_d}" fill="url(#hg)" />
  <polyline points="{polyline}" fill="none" stroke="#00c073" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="3" fill="#00c073"/>
</svg>'''


# ── UI ───────────────────────────────────────────────────────

@module.ui
def dashboard_ui():
    return ui.div(
        {"id": "dashboard-root"},
        ui.div(
            {"class": "page-inner"},

            # ── 총자산 히어로 ─────────────────────────────
            ui.output_ui("hero_block"),

            # ── 오늘 ──────────────────────────────────────
            ui.div(
                {"class": "db-section"},
                ui.div("오늘", class_="db-section-title"),
                ui.output_ui("out_daily_profit"),
                ui.output_ui("out_exposure_card"),
            ),

            # ── 수익률 ────────────────────────────────────
            ui.div(
                {"class": "db-section"},
                ui.div("수익률", class_="db-section-title"),
                ui.div(
                    {"class": "db-grid-2"},
                    ui.div(
                        {"class": "db-metric-card"},
                        ui.div("연평균 IRR", class_="db-metric-label"),
                        ui.output_ui("out_annual_irr"),
                    ),
                    ui.div(
                        {"class": "db-metric-card"},
                        ui.div("월평균 IRR", class_="db-metric-label"),
                        ui.output_ui("out_monthly_irr"),
                    ),
                ),
            ),

            # ── 알파 / 베타 ───────────────────────────────
            ui.div(
                {"class": "db-section"},
                ui.div("알파 / 베타  (vs NDX100)", class_="db-section-title"),
                ui.div(
                    {"class": "db-grid-2"},
                    ui.div(
                        {"class": "db-metric-card"},
                        ui.div("누적 알파", class_="db-metric-label"),
                        ui.output_ui("out_cumul_alpha"),
                    ),
                    ui.div(
                        {"class": "db-metric-card"},
                        ui.div("30일 알파", class_="db-metric-label"),
                        ui.output_ui("out_alpha_30"),
                    ),
                ),
                ui.output_ui("out_beta"),
            ),

            # ── 종목 비중 ─────────────────────────────────
            ui.div(
                {"class": "db-section"},
                ui.output_ui("out_donut"),
            ),

            # ── 은퇴 시뮬레이션 ───────────────────────────
            ui.output_ui("out_retirement"),

        ),
    )


# ── Server ───────────────────────────────────────────────────

@module.server
def dashboard_server(input, output, session):

    @reactive.calc
    def data():
        _price_signal.get()
        return _load_summary_data()

    @reactive.calc
    def position_data():
        _price_signal.get()
        return _load_position_data()

    # ── 히어로 ────────────────────────────────────────

    @output
    @render.ui
    def hero_block():
        d = data()
        if not d:
            return ui.div({"class": "db-hero"}, ui.div({"class": "db-hero-content"}, ui.span("–")))

        delta = d["asset_delta"]
        pct   = d["asset_delta_pct"]
        cls   = _pnl_class(delta)

        chart_svg = _hero_line_svg(d.get("chart_data", []))

        return ui.div(
            {"class": "db-hero"},
            # 오버레이 차트
            ui.HTML(f'<div class="db-hero-chart">{chart_svg}</div>'),
            # 콘텐츠
            ui.div(
                {"class": "db-hero-content"},
                ui.div("총 자산", class_="db-hero-label"),
                ui.div(fmt_krw(d["total_asset"]), class_="db-hero-amount"),
                ui.div(
                    {"class": "db-hero-delta-row"},
                    ui.span(
                        f"{_arrow(delta)}{fmt_krw(abs(delta))}  ({_fmt_pct(pct)})",
                        class_=f"db-hero-delta {cls}",
                    ),
                    ui.span("전일 대비", class_="db-hero-delta-tag"),
                ),
            ),
        )

    # ── 금일 순수익 ───────────────────────────────────

    @output
    @render.ui
    def out_daily_profit():
        d = data()
        if not d:
            return ui.div({"class": "db-today-card"}, ui.span("–"))
        val = d["daily_profit"]
        return ui.div(
            {"class": "db-today-card"},
            ui.div("금일 순수익", class_="db-today-label"),
            ui.span(
                f"{_arrow(val)} {fmt_krw(abs(val))}",
                class_=f"db-today-value {_pnl_class(val)}",
            ),
        )

    # ── Exposure + 레버리지 통합 카드 ─────────────────

    @output
    @render.ui
    def out_exposure_card():
        d = data()
        if not d:
            return ui.div({"class": "db-exposure-card"}, ui.span("–"))

        exposure     = d["exposure"]
        cash_ratio   = d["cash_ratio"]
        invest_ratio = d["invest_ratio"]
        x1 = d["x1_ratio"] * 100
        x2 = d["x2_ratio"] * 100
        x3 = d["x3_ratio"] * 100
        cash = cash_ratio * 100

        exp_cls = "db-neg" if exposure >= 1.5 else "db-warn" if exposure >= 1.2 else "db-pos"

        # 레버리지 바 세그먼트
        segs = []
        for cls, val, label in [
            ("x1",   x1,   f"{x1:.0f}%"),
            ("x2",   x2,   f"{x2:.0f}%"),
            ("x3",   x3,   f"{x3:.0f}%"),
            ("cash", cash, f"{cash:.0f}%"),
        ]:
            if val >= 0.5:
                segs.append(ui.div(
                    {"class": f"db-lev-bar-seg {cls}", "style": f"flex:{val:.1f}"},
                    label if val >= 5 else "",
                ))

        legend_items = []
        for cls, val, label in [
            ("x1",   x1,   f"x1  {x1:.1f}%"),
            ("x2",   x2,   f"x2  {x2:.1f}%"),
            ("x3",   x3,   f"x3  {x3:.1f}%"),
            ("cash", cash, f"현금  {cash:.1f}%"),
        ]:
            legend_items.append(ui.span(
                {"class": "db-lev-legend-item"},
                ui.span({"class": f"db-lev-legend-dot {cls}"}),
                label,
            ))

        return ui.div(
            {"class": "db-exposure-card"},
            # 상단: Exposure + 현금/투자 비중
            ui.div(
                {"class": "db-exposure-top"},
                ui.div(
                    ui.div("익스포저", class_="db-today-label"),
                    ui.span(f"{exposure:.2f}x", class_=f"db-exposure-val {exp_cls}"),
                ),
                ui.div(
                    {"class": "db-exposure-right"},
                    ui.div(
                        {"class": "db-ratio-item"},
                        ui.div(_fmt_pct_plain(cash_ratio), class_="db-ratio-val"),
                        ui.div("현금", class_="db-ratio-label"),
                    ),
                    ui.div(
                        {"class": "db-ratio-item"},
                        ui.div(_fmt_pct_plain(invest_ratio), class_="db-ratio-val"),
                        ui.div("투자", class_="db-ratio-label"),
                    ),
                ),
            ),
            # 하단: 레버리지 바
            ui.div("레버리지 비중", class_="db-today-label"),
            ui.div({"class": "db-lev-bar-track"}, *segs),
            ui.div({"class": "db-lev-legend"}, *legend_items),
        )

    # ── 수익률 ────────────────────────────────────────

    @output
    @render.ui
    def out_annual_irr():
        d = data()
        if not d: return ui.span("–", class_="db-metric-value")
        val = d["annual_irr"]
        return ui.span(_fmt_pct(val), class_=f"db-metric-value {_pnl_class(val)}")

    @output
    @render.ui
    def out_monthly_irr():
        d = data()
        if not d: return ui.span("–", class_="db-metric-value")
        val = d["monthly_irr"]
        return ui.span(_fmt_pct(val), class_=f"db-metric-value {_pnl_class(val)}")

    # ── 알파 ──────────────────────────────────────────

    @output
    @render.ui
    def out_cumul_alpha():
        d = data()
        if not d: return ui.span("–", class_="db-metric-value")
        val = d["cumul_alpha"]
        return ui.span(_fmt_pct(val), class_=f"db-metric-value {_pnl_class(val)}")

    @output
    @render.ui
    def out_alpha_30():
        d = data()
        if not d: return ui.span("–", class_="db-metric-value")
        val = d["alpha_30"]
        return ui.span(_fmt_pct(val), class_=f"db-metric-value {_pnl_class(val)}")

    # ── 베타 ──────────────────────────────────────────

    @output
    @render.ui
    def out_beta():
        d = data()
        if not d: return ui.span("")
        return ui.div(
            {"class": "db-beta-card"},
            ui.div("베타 (vs NDX100)", class_="db-beta-label"),
            ui.div(
                {"class": "db-beta-values"},
                ui.span("전체 ", class_="db-beta-tag"),
                ui.span(f"{d['beta_all']:.2f}", class_="db-beta-value"),
                ui.span("/", class_="db-beta-sep"),
                ui.span("30일 ", class_="db-beta-tag"),
                ui.span(f"{d['beta_30']:.2f}", class_="db-beta-value"),
            ),
        )

    # ── 종목 비중 도넛 ────────────────────────────────

    @output
    @render.ui
    def out_donut():
        positions = position_data()
        if not positions:
            return ui.span("")

        total = sum(p["eval_krw"] for p in positions)
        if total == 0:
            return ui.span("")

        # 같은 티커 합산
        merged: dict[str, dict] = {}
        for p in positions:
            t = p["ticker"]
            if t in merged:
                merged[t]["eval_krw"] += p["eval_krw"]
            else:
                merged[t] = dict(p)

        # 현금(KRW+USD) 하나로 합산 → 종목처럼 취급
        cash_eval = sum(
            v["eval_krw"] for k, v in merged.items() if k in ("KRW", "USD")
        )
        items = [v for k, v in merged.items() if k not in ("KRW", "USD")]
        if cash_eval > 0:
            items.append({
                "ticker":   "CASH",
                "name":     "현금",
                "leverage": 1,
                "eval_krw": cash_eval,
            })

        # 평가액 내림차순 정렬, 상위 8 + 기타
        items_sorted = sorted(items, key=lambda x: x["eval_krw"], reverse=True)
        top8       = items_sorted[:8]
        others     = items_sorted[8:]
        other_eval = sum(p["eval_krw"] for p in others)

        # 슬라이스 구성
        slices = []
        lev_palettes = {
            1: ["#00c073", "#00a862", "#009050", "#007840", "#005c30"],
            2: ["#e6a817", "#c98f0f", "#ad7a0c", "#916509", "#755207"],
            3: ["#ff4d4d", "#e63c3c", "#cc2c2c", "#b21c1c", "#991010"],
        }
        lev_count = {1: 0, 2: 0, 3: 0}

        for p in top8:
            if p["ticker"] == "CASH":
                slices.append({"label": "현금", "value": p["eval_krw"], "color": "#111111"})
                continue
            lev     = p["leverage"]
            idx     = lev_count.get(lev, 0)
            palette = lev_palettes.get(lev, ["#888888"])
            color   = palette[min(idx, len(palette) - 1)]
            lev_count[lev] = idx + 1
            slices.append({
                "label": p["name"] or p["ticker"],
                "value": p["eval_krw"],
                "color": color,
            })

        if other_eval > 0:
            slices.append({"label": "기타", "value": other_eval, "color": "#3a3a3a"})

        svg_html = _donut_svg(slices)

        # 범례 행
        legend_rows = []
        for s in slices:
            pct = s["value"] / total * 100
            dot_cls = "db-donut-legend-dot cash" if s["label"] == "현금" else "db-donut-legend-dot"
            legend_rows.append(
                ui.div(
                    {"class": "db-donut-legend-row"},
                    ui.span({"class": dot_cls, "style": f"background:{s['color']}"}),
                    ui.span(s["label"], class_="db-donut-legend-name"),
                    ui.span(f"{pct:.1f}%", class_="db-donut-legend-pct"),
                )
            )

        subtitle = f"상위 {min(8, len(items))}"

        return ui.div(
            {"class": "db-donut-card"},
            ui.div(
                ui.span("종목 비중", class_="db-donut-title", style="display:inline"),
                ui.span(f"({subtitle})", class_="db-donut-title-sub"),
            ),
            ui.div(
                {"class": "db-donut-wrap"},
                ui.div({"class": "db-donut-svg-wrap"}, ui.HTML(svg_html)),
                ui.div({"class": "db-donut-legend"}, *legend_rows),
            ),
        )

    # ── 은퇴 시뮬레이션 ──────────────────────────────

    @output
    @render.ui
    def out_retirement():
        d = data()
        if not d:
            return ui.span("")
        ret_asset   = d["retirement_asset"]
        ret_date    = d["retirement_date"]
        monthly_irr = d["monthly_irr"]
        today       = datetime.date.today()
        months      = max(0, (ret_date.year - today.year) * 12 + (ret_date.month - today.month))
        years       = months / 12

        return ui.div(
            {"class": "db-retirement"},
            ui.div("미래 예측", class_="db-retirement-eyebrow"),
            ui.div(
                f"은퇴 시뮬레이션 ({ret_date.strftime('%Y년 %m월')}, +{years:.1f}년 후)",
                class_="db-retirement-subtitle",
            ),
            ui.div(fmt_krw(ret_asset), class_="db-retirement-amount"),
            ui.div(
                f"월평균 IRR {_fmt_pct(monthly_irr)} 복리 적용",
                class_="db-retirement-sub",
            ),
            ui.div(f"{months}개월 복리", class_="db-retirement-compound"),
        )