import datetime
import json
import plotly.graph_objects as go
from shiny import module, ui, render, reactive

from app.db import get_db
from app.price_signal import price_signal as _price_signal
from app.utils.metrics import (
    to_f, calculate_xirr, calculate_monthly_irr,
    calculate_alpha, calculate_beta,
    calculate_daily_profit, calculate_retirement_asset,
)
from app.modules.components import fmt_krw


# ── 포맷 헬퍼 ────────────────────────────────────────────────

def _fmt_pct(val: float, decimals: int = 2) -> str:
    return f"{val * 100:+.{decimals}f}%"

def _fmt_pct_plain(val: float, decimals: int = 2) -> str:
    return f"{val * 100:.{decimals}f}%"

def _pnl_class(val: float) -> str:
    if val > 0: return "positive"
    if val < 0: return "negative"
    return "neutral"

def _arrow(val: float) -> str:
    if val > 0: return "▲"
    if val < 0: return "▼"
    return "–"


# ── DAL ──────────────────────────────────────────────────────

def _load_summary_data() -> dict:
    """daily_summary 전체 조회 + 지표 계산"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT date, total_asset, cash_flow, ndx100,
                   exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio, twr_asset
            FROM daily_summary
            ORDER BY date ASC
        """)
        rows = cur.fetchall()

    if not rows:
        return {}

    # config.json retirement_date
    try:
        with open("scheduler/config.json", "r") as f:
            cfg = json.load(f)
        rd_str = cfg.get("retirement_date", "20351231")
        retirement_date = datetime.date(int(rd_str[:4]), int(rd_str[4:6]), int(rd_str[6:8]))
    except Exception:
        retirement_date = datetime.date(2035, 12, 31)

    latest = rows[-1]
    prev   = rows[-2] if len(rows) >= 2 else None

    total_asset  = to_f(latest[1])
    today_cf     = to_f(latest[2])
    prev_asset   = to_f(prev[1]) if prev else 0.0

    asset_delta     = total_asset - prev_asset
    asset_delta_pct = (asset_delta / prev_asset) if prev_asset else 0.0
    daily_profit    = calculate_daily_profit(total_asset, today_cf, prev_asset)

    exposure     = to_f(latest[4])
    cash_ratio   = to_f(latest[5])
    x1_ratio     = to_f(latest[6])
    x2_ratio     = to_f(latest[7])
    x3_ratio     = to_f(latest[8])
    invest_ratio = 1.0 - cash_ratio

    # XIRR cash_flows: 최초자산 음수 + 중간 입출금 반전 + 현재자산 양수
    cash_flows = [(rows[0][0], -to_f(rows[0][1]))]
    cash_flows += [(r[0], -to_f(r[2])) for r in rows[1:-1] if to_f(r[2]) != 0]
    cash_flows.append((latest[0], total_asset))

    annual_irr  = calculate_xirr(cash_flows)
    monthly_irr = calculate_monthly_irr(cash_flows)

    start_row   = (rows[0][9], rows[0][3])
    end_row     = (latest[9],  latest[3])
    cumul_alpha = calculate_alpha(start_row, end_row)
    total_months = max(1, (latest[0] - rows[0][0]).days / 30.0)
    monthly_alpha = cumul_alpha / total_months

    cutoff  = latest[0] - datetime.timedelta(days=30)
    row_30  = next((r for r in rows if r[0] >= cutoff), rows[0])
    alpha_30 = calculate_alpha((row_30[9], row_30[3]), (latest[9], latest[3]))

    beta_all = calculate_beta([(r[1], r[3]) for r in rows])
    rows_30  = [r for r in rows if r[0] >= cutoff]
    beta_30  = calculate_beta([(r[1], r[3]) for r in rows_30]) if len(rows_30) >= 3 else 0.0

    retirement_asset = calculate_retirement_asset(total_asset, monthly_irr, retirement_date)

    return {
        "latest_date":      latest[0],
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
    }


def _load_position_data() -> list[dict]:
    """positions + tickers 조인 → 종목별 평가액"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                p.ticker,
                t.name,
                t.market,
                t.leverage,
                p.quantity,
                t.current_price
            FROM positions p
            JOIN tickers t ON p.ticker = t.ticker
            ORDER BY p.ticker
        """)
        rows = cur.fetchall()

        # USD/KRW 환율
        cur.execute("SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X'")
        fx = cur.fetchone()
        usd_krw = to_f(fx[0]) if fx else 1300.0

    result = []
    for ticker, name, market, leverage, qty, price in rows:
        qty   = to_f(qty)
        price = to_f(price)
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


# ── 차트 헬퍼 ────────────────────────────────────────────────

_DONUT_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(t=8, b=8, l=8, r=8),
    showlegend=True,
    legend=dict(
        orientation="v",
        x=1.02, y=0.5,
        font=dict(color="#aaaaaa", size=11),
        bgcolor="rgba(0,0,0,0)",
    ),
    font=dict(color="#ffffff"),
)

def _donut_html(labels, values, colors, title="") -> str:
    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.62,
        marker=dict(colors=colors, line=dict(color="#0a0a0a", width=2)),
        textinfo="none",
        hovertemplate="%{label}<br>%{value:,.0f}원<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(
        **_DONUT_LAYOUT,
        height=200,
        annotations=[dict(
            text=title,
            x=0.38, y=0.5,
            font=dict(size=11, color="#888888"),
            showarrow=False,
        )],
    )
    return fig.to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": False})


# ── UI 헬퍼 ──────────────────────────────────────────────────

def _metric_card(label: str, output_id: str) -> ui.Tag:
    return ui.div(
        {"class": "dash-card"},
        ui.div(label, class_="dash-card-label"),
        ui.output_ui(output_id, class_="dash-card-value"),
    )

def _wide_card(title: str, output_id: str) -> ui.Tag:
    return ui.div(
        {"class": "dash-card dash-card-wide"},
        ui.div(title, class_="dash-card-label"),
        ui.output_ui(output_id),
    )

def _section(title: str, *children) -> ui.Tag:
    return ui.div(
        {"class": "dash-section"},
        ui.div(title, class_="dash-section-title"),
        *children,
    )


# ── UI ───────────────────────────────────────────────────────

@module.ui
def dashboard_ui():
    return ui.div(
        {"class": "page-inner", "id": "dashboard-root"},

        # 총자산 히어로
        ui.div(
            {"class": "dash-hero"},
            ui.div("총자산", class_="dash-hero-label"),
            ui.output_ui("hero_asset"),
            ui.output_ui("hero_delta"),
        ),

        # 오늘
        _section(
            "오늘",
            ui.div(
                {"class": "dash-grid-2"},
                _metric_card("금일 순수익",  "out_daily_profit"),
                _metric_card("기준일",       "out_latest_date"),
            ),
        ),

        # 비중
        _section(
            "비중",
            ui.div(
                {"class": "dash-grid-2"},
                _metric_card("Exposure",  "out_exposure"),
                _metric_card("현금비중",  "out_cash_ratio"),
            ),
            # 레버리지 비중 도넛
            ui.div(
                {"class": "dash-grid-2"},
                ui.div(
                    {"class": "dash-card"},
                    ui.div("레버리지 비중", class_="dash-card-label"),
                    ui.output_ui("out_lev_donut"),
                ),
                # 종목 비중 도넛
                ui.div(
                    {"class": "dash-card"},
                    ui.div("종목 비중", class_="dash-card-label"),
                    ui.output_ui("out_ticker_donut"),
                ),
            ),
        ),

        # 수익률
        _section(
            "수익률",
            ui.div(
                {"class": "dash-grid-2"},
                _metric_card("연평균 IRR",  "out_annual_irr"),
                _metric_card("월평균 IRR",  "out_monthly_irr"),
            ),
        ),

        # 알파 / 베타
        _section(
            "알파 / 베타  (vs NDX100)",
            ui.div(
                {"class": "dash-grid-2"},
                _metric_card("누적 알파",      "out_cumul_alpha"),
                _metric_card("월평균 알파",    "out_monthly_alpha"),
            ),
            ui.div(
                {"class": "dash-grid-2"},
                _metric_card("최근 30일 알파", "out_alpha_30"),
                _metric_card("베타 (전체)",    "out_beta_all"),
            ),
            ui.div(
                {"class": "dash-grid-2"},
                _metric_card("베타 (30일)",    "out_beta_30"),
                ui.div({"class": "dash-card dash-card-empty"}),
            ),
        ),

        # 은퇴 시뮬레이션
        _section(
            "은퇴 시뮬레이션",
            ui.div(
                {"class": "dash-card dash-card-wide"},
                ui.div("은퇴시점 예상자산", class_="dash-card-label"),
                ui.output_ui("out_retirement"),
            ),
        ),

        ui.div({"style": "height:80px"}),
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

    # 총자산
    @output
    @render.ui
    def hero_asset():
        d = data()
        if not d: return ui.span("–")
        return ui.span(fmt_krw(d["total_asset"]), class_="dash-hero-amount")

    @output
    @render.ui
    def hero_delta():
        d = data()
        if not d: return ui.span("–")
        delta = d["asset_delta"]
        pct   = d["asset_delta_pct"]
        cls   = _pnl_class(delta)
        return ui.div(
            {"class": f"dash-hero-delta {cls}"},
            ui.span(f"{_arrow(delta)} {fmt_krw(abs(delta))}"),
            ui.span(f"({_fmt_pct(pct)})", class_="dash-hero-delta-pct"),
        )

    # 기준일
    @output
    @render.ui
    def out_latest_date():
        d = data()
        if not d: return ui.span("–")
        return ui.span(str(d["latest_date"]), class_="dash-card-value-text")

    # 금일 순수익
    @output
    @render.ui
    def out_daily_profit():
        d = data()
        if not d: return ui.span("–")
        val = d["daily_profit"]
        return ui.span(
            f"{_arrow(val)} {fmt_krw(abs(val))}",
            class_=f"dash-card-value-text {_pnl_class(val)}",
        )

    # Exposure
    @output
    @render.ui
    def out_exposure():
        d = data()
        if not d: return ui.span("–")
        val = d["exposure"]
        color = "#ff4d4d" if val >= 1.5 else "#f59e0b" if val >= 1.2 else "#00c073"
        return ui.span(f"{val:.2f}x", style=f"color:{color}", class_="dash-card-value-text")

    # 현금비중
    @output
    @render.ui
    def out_cash_ratio():
        d = data()
        if not d: return ui.span("–")
        return ui.div(
            ui.span(_fmt_pct_plain(d["cash_ratio"]), class_="dash-card-value-text"),
            ui.span(f"투자 {_fmt_pct_plain(d['invest_ratio'])}", class_="dash-card-sub"),
        )

    # 레버리지 비중 도넛
    @output
    @render.ui
    def out_lev_donut():
        d = data()
        if not d: return ui.span("–")
        cash = d["cash_ratio"] * 100
        x1   = d["x1_ratio"]   * 100
        x2   = d["x2_ratio"]   * 100
        x3   = d["x3_ratio"]   * 100

        labels = []
        values = []
        colors = []
        for label, val, color in [
            ("현금", cash, "#444444"),
            ("x1",   x1,  "#00c073"),
            ("x2",   x2,  "#e6a817"),
            ("x3",   x3,  "#ff4d4d"),
        ]:
            if val >= 0.1:
                labels.append(f"{label} {val:.1f}%")
                values.append(val)
                colors.append(color)

        return ui.HTML(_donut_html(labels, values, colors, "레버리지"))

    # 종목 비중 도넛
    @output
    @render.ui
    def out_ticker_donut():
        positions = position_data()
        if not positions: return ui.span("–")

        total = sum(p["eval_krw"] for p in positions)
        if total == 0: return ui.span("–")

        # 현금(KRW/USD) 제외, 종목만
        stocks = [p for p in positions if p["ticker"] not in ("KRW", "USD")]
        cash_eval = sum(p["eval_krw"] for p in positions if p["ticker"] in ("KRW", "USD"))

        # 종목별 정렬 (평가액 내림차순)
        stocks_sorted = sorted(stocks, key=lambda x: x["eval_krw"], reverse=True)

        labels = [f"{p['name']} {p['eval_krw']/total*100:.1f}%" for p in stocks_sorted]
        values = [p["eval_krw"] for p in stocks_sorted]

        # 레버리지별 색상 팔레트
        lev_colors = {1: "#00c073", 2: "#e6a817", 3: "#ff4d4d"}
        # 같은 레버리지 내에서 명도 변화
        lev_count = {1: 0, 2: 0, 3: 0}
        colors = []
        for p in stocks_sorted:
            lev = p["leverage"]
            base = lev_colors.get(lev, "#888888")
            idx  = lev_count[lev]
            # 같은 레버리지 내 종목이 여러 개면 투명도로 구분
            opacity = max(0.4, 1.0 - idx * 0.18)
            colors.append(base)
            lev_count[lev] += 1

        if cash_eval >= total * 0.001:
            labels.append(f"현금 {cash_eval/total*100:.1f}%")
            values.append(cash_eval)
            colors.append("#444444")

        return ui.HTML(_donut_html(labels, values, colors, "종목"))

    # 연평균 IRR
    @output
    @render.ui
    def out_annual_irr():
        d = data()
        if not d: return ui.span("–")
        val = d["annual_irr"]
        return ui.span(_fmt_pct(val), class_=f"dash-card-value-text {_pnl_class(val)}")

    # 월평균 IRR
    @output
    @render.ui
    def out_monthly_irr():
        d = data()
        if not d: return ui.span("–")
        val = d["monthly_irr"]
        return ui.span(_fmt_pct(val), class_=f"dash-card-value-text {_pnl_class(val)}")

    # 누적 알파
    @output
    @render.ui
    def out_cumul_alpha():
        d = data()
        if not d: return ui.span("–")
        val = d["cumul_alpha"]
        return ui.span(_fmt_pct(val), class_=f"dash-card-value-text {_pnl_class(val)}")

    # 월평균 알파
    @output
    @render.ui
    def out_monthly_alpha():
        d = data()
        if not d: return ui.span("–")
        val = d["monthly_alpha"]
        return ui.span(_fmt_pct(val), class_=f"dash-card-value-text {_pnl_class(val)}")

    # 최근 30일 알파
    @output
    @render.ui
    def out_alpha_30():
        d = data()
        if not d: return ui.span("–")
        val = d["alpha_30"]
        return ui.span(_fmt_pct(val), class_=f"dash-card-value-text {_pnl_class(val)}")

    # 베타 전체
    @output
    @render.ui
    def out_beta_all():
        d = data()
        if not d: return ui.span("–")
        return ui.span(f"{d['beta_all']:.2f}", class_="dash-card-value-text")

    # 베타 30일
    @output
    @render.ui
    def out_beta_30():
        d = data()
        if not d: return ui.span("–")
        return ui.span(f"{d['beta_30']:.2f}", class_="dash-card-value-text")

    # 은퇴시점 예상자산
    @output
    @render.ui
    def out_retirement():
        d = data()
        if not d: return ui.span("–")
        ret_asset = d["retirement_asset"]
        ret_date  = d["retirement_date"]
        today = datetime.date.today()
        years = (ret_date.year - today.year) + (ret_date.month - today.month) / 12
        return ui.div(
            ui.span(fmt_krw(ret_asset), class_="dash-hero-amount"),
            ui.div(
                ui.span(f"{ret_date.strftime('%Y년 %m월')} 기준", class_="dash-card-sub"),
                ui.span(f"({years:.1f}년 후)", class_="dash-card-sub"),
                style="margin-top:4px",
            ),
        )