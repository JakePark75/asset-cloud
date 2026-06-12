import datetime
import json
import math
from shiny import module, ui, render, reactive

from app.db import get_db, get_market_currency
from app.price_signal import price_signal as _price_signal, daily_insert_signal as _daily_insert_signal
from app.utils.metrics import (
    to_f, calculate_xirr, calculate_monthly_irr,
    calculate_alpha, calculate_beta,
    calculate_daily_profit, calculate_retirement_asset,
    calculate_exposure_and_ratios,
)
from app.modules.components import fmt_krw
from app.utils.display_diff import diff_display


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
    if val > 0: return "+"
    if val < 0: return "-"
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
    # ---------------------------------------------------------------------------
    # Step 6-2: 시세(current_price) 조회를 DB → Redis 전환
    #   - usd_krw  : Redis get_price('USDKRW=X') → fallback 1300.0
    #   - ndx100   : Redis get_price('^NDX')      → fallback None
    #   - positions current_price : Redis get_all_prices() 매핑
    #   메타데이터(ticker, quantity, leverage, market)는 DB 유지
    # ---------------------------------------------------------------------------
    from common.redis_store import get_all_prices, get_price

    # Redis에서 시세 전체 로드 (실패 시 빈 dict → 가격 0 처리)
    prices = get_all_prices()

    # usd_krw: prices hash 우선, 없으면 fallback
    fx_data = prices.get("USDKRW=X")
    usd_krw = float(fx_data["price"]) if fx_data else 1300.0

    # ^NDX: prices hash 우선, 없으면 None (live_ndx100 = None → prev 사용)
    ndx_data    = prices.get("^NDX")
    live_ndx100 = float(ndx_data["price"]) if ndx_data else None

    with get_db() as conn:
        cur = conn.cursor()

        # daily_summary 전체 이력 — DB 유지
        cur.execute("""
            SELECT date, total_asset, cash_flow, ndx100,
                   exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio, twr_asset
            FROM daily_summary
            ORDER BY date ASC
        """)
        rows = cur.fetchall()

        # positions + 메타데이터(leverage, market) — DB 유지
        # current_price는 SELECT하지 않고 Redis prices에서 매핑
        cur.execute("""
            SELECT p.ticker, p.quantity, t.leverage, t.market
            FROM positions p
            LEFT JOIN tickers t ON p.ticker = t.ticker
            LEFT JOIN accounts a ON p.account_id = a.id
            WHERE a.is_watch = false
        """)
        raw_rows = cur.fetchall()
        cur.close()

    # positions에 Redis 시세 매핑
    pos_rows = []
    for ticker, qty, leverage, market in raw_rows:
        if ticker == "KRW":
            pos_rows.append((ticker, qty, 1.0, 1, market))
        elif ticker == "USD":
            pos_rows.append((ticker, qty, usd_krw, 1, market))
        else:
            p_data = prices.get(ticker)
            price  = float(p_data["price"]) if p_data else 0.0
            pos_rows.append((ticker, qty, price, leverage, market))

    today = datetime.date.today()

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
    cash_eval    = rt["cash_eval"]
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
    daily_profit    = calculate_daily_profit(total_asset, prev_asset)

    # 오늘 입출금 — Redis에서 읽기 (실패 시 0으로 진행)
    today_cash_flow = 0
    try:
        from common.redis_store import get_redis
        r = get_redis()
        if r:
            today_cash_flow = int(r.get("today_cash_flow") or 0)
    except Exception:
        pass

    denom    = prev_asset
    live_twr = prev_twr * ((total_asset - today_cash_flow) / denom) if denom != 0 else prev_twr
    live_ndx = live_ndx100 if live_ndx100 else prev_ndx100

    cash_flows  = [(rows[0][0], -to_f(rows[0][1]))]
    cash_flows += [(r[0], -to_f(r[2])) for r in rows[1:] if to_f(r[2]) != 0]
    if today_cash_flow != 0:
        cash_flows.append((today, -today_cash_flow))
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
        "cash_eval":        cash_eval,
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
    # ---------------------------------------------------------------------------
    # Step 6-2: current_price DB JOIN → Redis get_all_prices() 매핑
    #   메타데이터(name, market, leverage)는 DB 유지
    # ---------------------------------------------------------------------------
    from common.redis_store import get_all_prices

    prices = get_all_prices()

    # usd_krw: prices hash에서 직접 읽음
    fx_data = prices.get("USDKRW=X")
    usd_krw = float(fx_data["price"]) if fx_data else 1300.0

    with get_db() as conn:
        cur = conn.cursor()
        # current_price 제거 — Redis에서 매핑
        cur.execute("""
            SELECT p.ticker, t.name, t.market, t.leverage, p.quantity
            FROM positions p
            LEFT JOIN tickers t ON p.ticker = t.ticker
            LEFT JOIN accounts a ON p.account_id = a.id
            WHERE a.is_watch = false
            ORDER BY p.ticker
        """)
        rows = cur.fetchall()
        cur.close()

    result = []
    for ticker, name, market, leverage, qty in rows:
        qty    = to_f(qty)
        market = (market or "").upper()
        if ticker == "KRW":
            eval_krw = qty
        elif ticker == "USD":
            eval_krw = qty * usd_krw
        else:
            p_data = prices.get(ticker)
            price  = float(p_data["price"]) if p_data else 0.0
            if get_market_currency(market) == "USD":
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
    angle_snap = 2.0  # 슬라이스 경계 각도 스냅 단위 (도) — 이보다 작은 비중 변화는 도넛 모양 불변

    paths = []
    angle = -90.0  # 12시 방향 시작 (정밀 누적값, 스냅 전)

    for s in slices:
        frac      = s["value"] / total
        sweep     = frac * 360 - gap_angle
        if sweep <= 0:
            angle += frac * 360
            continue

        # 좌표 계산용 각도는 angle_snap 단위로 스냅 (미세 변화 시 동일 문자열 출력 → 재전송 안 됨)
        disp_start = round(angle / angle_snap) * angle_snap
        disp_end   = round((angle + sweep) / angle_snap) * angle_snap

        start_rad = math.radians(disp_start)
        end_rad   = math.radians(disp_end)

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
            f"M {x1o:.1f} {y1o:.1f} "
            f"A {r_outer} {r_outer} 0 {large} 1 {x2o:.1f} {y2o:.1f} "
            f"L {x1i:.1f} {y1i:.1f} "
            f"A {r_inner} {r_inner} 0 {large} 0 {x2i:.1f} {y2i:.1f} "
            f"Z"
        )
        paths.append(f'<path d="{d}" fill="{s["color"]}" />')
        angle += frac * 360

    paths_html = "\n".join(paths)
    return f'''<svg viewBox="0 0 130 130" xmlns="http://www.w3.org/2000/svg">
{paths_html}
</svg>'''

def _hero_line_svg(values: list[float]) -> str:
    if not values or len(values) < 2:
        return ""
    
    # 1. 데이터를 0~100 좌표계로 정규화
    min_v, max_v = min(values), max(values)
    v_range = (max_v - min_v) or 1
    
    pts = []
    for i, v in enumerate(values):
        x = (i / (len(values) - 1)) * 100
        y = 100 - ((v - min_v) / v_range) * 100
        pts.append((x, y))
    
    # 2. Path와 Polyline 생성
    polyline = " ".join(f"{x:.0f},{y:.0f}" for x, y in pts)
    fill_d = f"M {pts[0][0]:.0f},{pts[0][1]:.0f} " + \
             " ".join(f"L {x:.0f},{y:.0f}" for x, y in pts[1:]) + \
             f" L {pts[-1][0]:.0f},100 L {pts[0][0]:.0f},100 Z"
             
    # 3. 뷰박스 고정 + preserveAspectRatio="none" (강제 맵핑)
    # vector-effect="non-scaling-stroke" (선 굵기 유지)
    return f'''<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none" style="display:block; width:100%; height:100%;">
  <defs>
    <linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#00c073" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="#00c073" stop-opacity="0.0"/>
    </linearGradient>
  </defs>
  <path d="{fill_d}" fill="url(#hg)" />
  <polyline points="{polyline}" fill="none" stroke="#00c073" stroke-width="2" 
            vector-effect="non-scaling-stroke" 
            stroke-linejoin="round" stroke-linecap="round"/>
</svg>'''


# ── 도넛 데이터 빌더 (server에서 공유) ───────────────────────

def _build_donut_payload(positions: list[dict]) -> dict:
    """
    position_data() 결과를 받아 도넛 렌더링에 필요한 데이터를 반환.
    svg_html + legend 리스트를 dict로 반환.
    """
    if not positions:
        return {}

    total = sum(p["eval_krw"] for p in positions)
    if total == 0:
        return {}

    # 같은 티커 합산
    merged: dict[str, dict] = {}
    for p in positions:
        t = p["ticker"]
        if t in merged:
            merged[t]["eval_krw"] += p["eval_krw"]
        else:
            merged[t] = dict(p)

    # 현금(KRW+USD) 하나로 합산
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

    legend = []
    for s in slices:
        pct = s["value"] / total * 100
        legend.append({
            "label":    s["label"],
            "color":    s["color"],
            "pct":      f"{pct:.1f}%",
            "is_cash":  s["label"] == "현금",
        })

    subtitle = f"상위 {min(8, len(items))}"

    return {
        "svg_html": svg_html,
        "legend":   legend,
        "subtitle": subtitle,
    }


# ── UI ───────────────────────────────────────────────────────

def _dashboard_ui_dom_patch():
    return ui.div(
        {"id": "dashboard-root"},
        # JS 핸들러: db_update 메시지 수신 → DOM 패치
        ui.tags.script("""
(function() {
  function pnlClass(v) {
    return v > 0 ? 'db-pos' : v < 0 ? 'db-neg' : 'db-neu';
  }
  function setText(id, val) {
    var el = document.getElementById(id);
    if (el) el.textContent = val;
  }
  function setHTML(id, val) {
    var el = document.getElementById(id);
    if (el) el.innerHTML = val;
  }
  function setClass(id, base, extra) {
    var el = document.getElementById(id);
    if (el) el.className = base + (extra ? ' ' + extra : '');
  }

  Shiny.addCustomMessageHandler('db_update', function(m) {

    // ── 히어로 (텍스트) ───────────────────────────────
    if (m.hero_text) {
      setText('db-hero-amount',      m.hero_text.total_asset);
      setText('db-hero-delta-text',  m.hero_text.delta_text);
      setClass('db-hero-delta-text', 'db-hero-delta', pnlClass(m.hero_text.delta_val));
    }
    // ── 히어로 (차트 SVG) ─────────────────────────────
    if (m.hero_chart_svg !== undefined) {
      setHTML('db-hero-chart-inner', m.hero_chart_svg);
    }

    // ── Exposure ─────────────────────────────────────
    if (m.exposure) {
      var expEl = document.getElementById('db-exposure-val');
      if (expEl) {
        expEl.textContent = m.exposure.exposure_text;
        expEl.className   = 'db-exposure-val ' + m.exposure.exp_cls;
      }
      setText('db-cash-ratio-val', m.exposure.cash_ratio_text);
      setText('db-cash-eval-val',  m.exposure.cash_eval_text);

      // 레버리지 바 세그먼트 교체
      var track = document.getElementById('db-lev-bar-track');
      if (track) track.innerHTML = m.exposure.lev_bar_html;

      // 레버리지 범례 교체
      var legend = document.getElementById('db-lev-legend');
      if (legend) legend.innerHTML = m.exposure.lev_legend_html;
    }

    // ── 수익률 ───────────────────────────────────────
    if (m.irr) {
      var annEl = document.getElementById('db-annual-irr');
      if (annEl) { annEl.textContent = m.irr.annual_text; annEl.className = 'db-metric-value ' + pnlClass(m.irr.annual_val); }
      var monEl = document.getElementById('db-monthly-irr');
      if (monEl) { monEl.textContent = m.irr.monthly_text; monEl.className = 'db-metric-value ' + pnlClass(m.irr.monthly_val); }
    }

    // ── 알파 ─────────────────────────────────────────
    if (m.alpha) {
      var caEl = document.getElementById('db-cumul-alpha');
      if (caEl) { caEl.textContent = m.alpha.cumul_text; caEl.className = 'db-metric-value ' + pnlClass(m.alpha.cumul_val); }
      var a30El = document.getElementById('db-alpha-30');
      if (a30El) { a30El.textContent = m.alpha.alpha30_text; a30El.className = 'db-metric-value ' + pnlClass(m.alpha.alpha30_val); }
    }

    // ── 베타 ─────────────────────────────────────────
    if (m.beta) {
      setText('db-beta-all', m.beta.all_text);
      setText('db-beta-30',  m.beta.beta30_text);
    }

    // ── 도넛 (텍스트) ─────────────────────────────────
    if (m.donut_text) {
      setHTML('db-donut-legend',    m.donut_text.legend_html);
      setText('db-donut-title-sub', '(' + m.donut_text.subtitle + ')');
    }
    // ── 도넛 (SVG) ────────────────────────────────────
    if (m.donut_svg !== undefined) {
      setHTML('db-donut-svg-wrap', m.donut_svg);
    }

    // ── 은퇴 시뮬레이션 ──────────────────────────────
    if (m.retirement) {
      setText('db-retirement-subtitle', m.retirement.subtitle);
      setText('db-retirement-amount',   m.retirement.amount_text);
      setText('db-retirement-sub',      m.retirement.sub_text);
      setText('db-retirement-compound', m.retirement.compound_text);
    }
  });
})();
        """),

        ui.div(
            {"class": "page-inner"},

            # ── 총자산 히어로 ─────────────────────────────
            ui.div(
                {"class": "db-hero"},
                ui.div({"id": "db-hero-chart-inner", "class": "db-hero-chart"}),
                ui.div(
                    {"class": "db-hero-content"},
                    ui.div("총 자산", class_="db-hero-label"),
                    ui.div("–", id="db-hero-amount", class_="db-hero-amount"),
                    ui.div(
                        {"class": "db-hero-delta-row"},
                        ui.span("–", id="db-hero-delta-text", class_="db-hero-delta"),
                        ui.span("전일 대비", class_="db-hero-delta-tag"),
                    ),
                ),
            ),

            # ── 오늘 ──────────────────────────────────────
            ui.div(
                {"class": "db-section"},
                ui.div("오늘", class_="db-section-title"),
                ui.div(
                    {"class": "db-exposure-card"},
                    # 상단: Exposure + 현금/투자 비중
                    ui.div(
                        {"class": "db-exposure-top"},
                        ui.div(
                            ui.div("익스포저", class_="db-today-label"),
                            ui.span("–", id="db-exposure-val", class_="db-exposure-val"),
                        ),
                        ui.div(
                            {"class": "db-exposure-right"},
                            ui.div(
                                {"class": "db-ratio-item"},
                                ui.div("현금", class_="db-ratio-label"),
                                ui.div("–", id="db-cash-ratio-val", class_="db-ratio-val"),
                                ui.div("–", id="db-cash-eval-val",  class_="db-ratio-sub"),
                            ),
                        ),
                    ),
                    # 하단: 레버리지 바
                    ui.div("레버리지 비중", class_="db-today-label"),
                    ui.div({"id": "db-lev-bar-track",  "class": "db-lev-bar-track"}),
                    ui.div({"id": "db-lev-legend",     "class": "db-lev-legend"}),
                ),
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
                        ui.span("–", id="db-annual-irr", class_="db-metric-value"),
                    ),
                    ui.div(
                        {"class": "db-metric-card"},
                        ui.div("월평균 IRR", class_="db-metric-label"),
                        ui.span("–", id="db-monthly-irr", class_="db-metric-value"),
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
                        ui.span("–", id="db-cumul-alpha", class_="db-metric-value"),
                    ),
                    ui.div(
                        {"class": "db-metric-card"},
                        ui.div("30일 알파", class_="db-metric-label"),
                        ui.span("–", id="db-alpha-30", class_="db-metric-value"),
                    ),
                ),
                ui.div(
                    {"class": "db-beta-card"},
                    ui.div("베타 (vs NDX100)", class_="db-beta-label"),
                    ui.div(
                        {"class": "db-beta-values"},
                        ui.span("전체 ",  class_="db-beta-tag"),
                        ui.span("–", id="db-beta-all", class_="db-beta-value"),
                        ui.span("/",      class_="db-beta-sep"),
                        ui.span("30일 ", class_="db-beta-tag"),
                        ui.span("–", id="db-beta-30",  class_="db-beta-value"),
                    ),
                ),
            ),

            # ── 종목 비중 ─────────────────────────────────
            ui.div(
                {"class": "db-section"},
                ui.div(
                    {"class": "db-donut-card"},
                    ui.div(
                        ui.span("종목 비중", class_="db-donut-title", style="display:inline"),
                        ui.span("–", id="db-donut-title-sub", class_="db-donut-title-sub"),
                    ),
                    ui.div(
                        {"class": "db-donut-wrap"},
                        ui.div({"id": "db-donut-svg-wrap",  "class": "db-donut-svg-wrap"}),
                        ui.div({"id": "db-donut-legend",    "class": "db-donut-legend"}),
                    ),
                ),
            ),

            # ── 은퇴 시뮬레이션 ───────────────────────────
            ui.div(
                {"class": "db-retirement"},
                ui.div("미래 예측", class_="db-retirement-eyebrow"),
                ui.div("–", id="db-retirement-subtitle", class_="db-retirement-subtitle"),
                ui.div("–", id="db-retirement-amount",   class_="db-retirement-amount"),
                ui.div("–", id="db-retirement-sub",      class_="db-retirement-sub"),
                ui.div("–", id="db-retirement-compound", class_="db-retirement-compound"),
            ),
        ),
    )


@module.ui
def dashboard_ui():
    return _dashboard_ui_dom_patch()


# ── Server ───────────────────────────────────────────────────

@module.server
def dashboard_server(input, output, session, active_tab: reactive.value = None):

    _last_display: dict = {}

    # ── 대시보드 요약 데이터 계산 ────────────────────────────────────────────
    # price_signal 마다 Redis 시세를 새로 읽어 총자산·수익률 등 전체 지표를 재계산.
    # daily_insert_signal 수신 시 daily_summary 에 새 행이 추가됐으므로
    # DB를 새로 읽어야 IRR·알파·베타 등 이력 기반 지표가 정확해진다.
    # @reactive.calc 로 캐싱: 같은 signal 값이면 재계산 없이 캐시 반환.
    @reactive.calc
    def data():
        _price_signal.get()
        _daily_insert_signal.get()
        return _load_summary_data()

    # ── 포지션 데이터 계산 ────────────────────────────────────────────────
    # price_signal 마다 Redis 시세를 새로 읽어 종목별 평가액·비중을 재계산.
    # 포지션 자체(수량·종목)는 자주 안 바뀌므로 DB 조회는 내부에서 캐싱 없이 매번 하되,
    # 시세는 Redis에서 읽으므로 빠르다.
    @reactive.calc
    def position_data():
        _price_signal.get()
        return _load_position_data()

    # ── 시세/daily insert 수신 시 대시보드 전체 갱신 ─────────────────────
    # data(), position_data() 의존성을 통해 price_signal, daily_insert_signal 에 연결됨.
    # diff_display 로 이전 화면과 비교해 변경된 필드만 JS로 전송 (DOM 전체 교체 아님).
    # 탭 비활성 시 스킵: 보이지 않는 DOM을 패치하는 건 낭비이고,
    # 탭 활성화 순간 active_tab 이 "dashboard"로 바뀌면서 자동으로 재실행된다.
    @reactive.effect
    async def _send_update():
            if active_tab and active_tab.get() != "dashboard":
                return
            d = data()
            positions = position_data()
            if not d:
                return

            # ── 히어로 ──────────────────────────────────────
            delta     = d["asset_delta"]
            pct       = d["asset_delta_pct"]
            chart_svg = _hero_line_svg(d.get("chart_data", []))
            hero = {
                "total_asset": fmt_krw(d["total_asset"]),
                "delta_text":  f"{_arrow(delta)}{fmt_krw(abs(delta))}  ({_fmt_pct(pct)})",
                "delta_val":   delta,
                "chart_svg":   chart_svg,
            }

            # ── Exposure ─────────────────────────────────────
            exposure   = d["exposure"]
            cash_ratio = d["cash_ratio"]
            cash_eval  = d["cash_eval"]
            x1   = d["x1_ratio"] * 100
            x2   = d["x2_ratio"] * 100
            x3   = d["x3_ratio"] * 100
            cash = cash_ratio * 100
            exp_cls = "db-neg" if exposure >= 1.5 else "db-warn" if exposure >= 1.2 else "db-pos"

            lev_bar_parts = []
            for seg_cls, val, label in [
                ("x1",   x1,   f"{x1:.0f}%"),
                ("x2",   x2,   f"{x2:.0f}%"),
                ("x3",   x3,   f"{x3:.0f}%"),
                ("cash", cash, f"{cash:.0f}%"),
            ]:
                if val >= 0.5:
                    inner = label if val >= 5 else ""
                    lev_bar_parts.append(
                        f'<div class="db-lev-bar-seg {seg_cls}" style="flex:{val:.1f}">{inner}</div>'
                    )

            lev_legend_parts = []
            for seg_cls, val, label in [
                ("x1",   x1,   f"x1  {x1:.1f}%"),
                ("x2",   x2,   f"x2  {x2:.1f}%"),
                ("x3",   x3,   f"x3  {x3:.1f}%"),
                ("cash", cash, f"현금  {cash:.1f}%"),
            ]:
                lev_legend_parts.append(
                    f'<span class="db-lev-legend-item">'
                    f'<span class="db-lev-legend-dot {seg_cls}"></span>'
                    f'{label}</span>'
                )

            exposure_payload = {
                "exposure_text":   f"{exposure:.2f}x",
                "exp_cls":         exp_cls,
                "cash_ratio_text": _fmt_pct_plain(cash_ratio),
                "cash_eval_text":  fmt_krw(cash_eval),
                "lev_bar_html":    "".join(lev_bar_parts),
                "lev_legend_html": "".join(lev_legend_parts),
            }

            # ── 수익률 ───────────────────────────────────────
            irr = {
                "annual_text":  _fmt_pct(d["annual_irr"]),
                "annual_val":   d["annual_irr"],
                "monthly_text": _fmt_pct(d["monthly_irr"]),
                "monthly_val":  d["monthly_irr"],
            }

            # ── 알파 ─────────────────────────────────────────
            alpha = {
                "cumul_text":   _fmt_pct(d["cumul_alpha"]),
                "cumul_val":    d["cumul_alpha"],
                "alpha30_text": _fmt_pct(d["alpha_30"]),
                "alpha30_val":  d["alpha_30"],
            }

            # ── 베타 ─────────────────────────────────────────
            beta = {
                "all_text":    f"{d['beta_all']:.2f}",
                "beta30_text": f"{d['beta_30']:.2f}",
            }

            # ── 도넛 ─────────────────────────────────────────
            donut_data = _build_donut_payload(positions)
            if donut_data:
                legend_html_parts = []
                for item in donut_data["legend"]:
                    dot_cls = "db-donut-legend-dot cash" if item["is_cash"] else "db-donut-legend-dot"
                    legend_html_parts.append(
                        f'<div class="db-donut-legend-row">'
                        f'<span class="{dot_cls}" style="background:{item["color"]}"></span>'
                        f'<span class="db-donut-legend-name">{item["label"]}</span>'
                        f'<span class="db-donut-legend-pct">{item["pct"]}</span>'
                        f'</div>'
                    )
                donut = {
                    "svg_html":    donut_data["svg_html"],
                    "legend_html": "".join(legend_html_parts),
                    "subtitle":    donut_data["subtitle"],
                }
            else:
                donut = {"svg_html": "", "legend_html": "", "subtitle": "–"}

            # ── 은퇴 시뮬레이션 ──────────────────────────────
            ret_asset   = d["retirement_asset"]
            ret_date    = d["retirement_date"]
            monthly_irr = d["monthly_irr"]
            today       = datetime.date.today()
            months      = max(0, (ret_date.year - today.year) * 12 + (ret_date.month - today.month))
            years       = months / 12
            retirement = {
                "subtitle":      f"은퇴 시뮬레이션 ({ret_date.strftime('%Y년 %m월')}, +{years:.1f}년 후)",
                "amount_text":   fmt_krw(ret_asset),
                "sub_text":      f"월평균 IRR {_fmt_pct(monthly_irr)} 복리 적용",
                "compound_text": f"{months}개월 복리",
            }

            current = {
                "hero_text": {
                    "total_asset": hero["total_asset"],
                    "delta_text":  hero["delta_text"],
                    "delta_val":   hero["delta_val"],
                },
                "hero_chart_svg": hero["chart_svg"],
                "exposure": exposure_payload,
                "irr":   irr,
                "alpha": alpha,
                "beta":  beta,
                "donut_text": {
                    "legend_html": donut["legend_html"],
                    "subtitle":    donut["subtitle"],
                },
                "donut_svg": donut["svg_html"],
                "retirement": retirement,
            }

            diff = diff_display(current, _last_display)
            if diff:
                await session.send_custom_message("db_update", diff)