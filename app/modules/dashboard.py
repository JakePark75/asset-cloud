import datetime
import hashlib
import json
import math
from zoneinfo import ZoneInfo
from shiny import module, ui, render, reactive

from app.db import get_db, get_market_currency
from app.price_signal import price_signal as _price_signal, daily_insert_signal as _daily_insert_signal, position_signal as _position_signal, ticker_signal as _ticker_signal
from app.utils.metrics import (
    to_f, calculate_xirr, calculate_monthly_irr, calculate_period_irr,
    calculate_alpha, calculate_beta, calculate_drawdown_metrics,
    calculate_retirement_asset,
    calculate_exposure_and_ratios,
)
from app.modules.components import fmt_krw
from app.utils.display_diff import diff_display

# ── 포맷 헬퍼 ────────────────────────────────────────────────

def _fmt_ratio_pct(val: float, decimals: int = 2) -> str:
    return f"{val * 100:+.{decimals}f}%"

def _fmt_ratio_pct_plain(val: float, decimals: int = 2) -> str:
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

def _load_summary_data(rows, raw_rows) -> dict:
    from common.redis_store import get_all_prices, get_price

    # Redis에서 시세 전체 로드 (실패 시 빈 dict → 가격 0 처리)
    prices = get_all_prices()

    # usd_krw: prices hash 우선, 없으면 fallback
    fx_data = prices.get("USDKRW=X")
    usd_krw = float(fx_data["price"]) if fx_data else 1300.0

    # ^NDX: prices hash 우선, 없으면 None (live_ndx100 = None → prev 사용)
    ndx_data    = prices.get("^NDX")
    live_ndx100 = float(ndx_data["price"]) if ndx_data else None

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

    today = datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()

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

    # 최근 30일 IRR: 30일 이전 시점 자산을 초기 투자금(유출)으로, 이후 cash_flow + 현재 자산
    cutoff_irr = today - datetime.timedelta(days=30)
    row_30_irr = next((r for r in rows if r[0] >= cutoff_irr), rows[0])
    cf_30_xirr = [(row_30_irr[0], -to_f(row_30_irr[1]))]
    cf_30_xirr += [(r[0], -to_f(r[2])) for r in rows if r[0] > row_30_irr[0] and to_f(r[2]) != 0]
    if today_cash_flow != 0 and today >= cutoff_irr:
        cf_30_xirr.append((today, -today_cash_flow))
    cf_30_xirr.append((today, total_asset))
    irr_30 = calculate_period_irr(cf_30_xirr)

    # 총 입출금 누적 (rows의 cash_flow 합 + 오늘)
    total_cash_flow = sum(to_f(r[2]) for r in rows) + today_cash_flow

    start_row    = (to_f(rows[0][9]), to_f(rows[0][3]))
    end_row      = (live_twr, live_ndx)
    cumul_alpha  = calculate_alpha(start_row, end_row)
    total_months = max(1, (today - rows[0][0]).days / 30.0)
    monthly_alpha = cumul_alpha / total_months

    cutoff   = today - datetime.timedelta(days=30)
    row_30   = next((r for r in rows if r[0] >= cutoff), rows[0])
    alpha_30 = calculate_alpha((to_f(row_30[9]), to_f(row_30[3])), (live_twr, live_ndx))

    beta_rows_all = [(to_f(r[9]), to_f(r[3])) for r in rows] + [(live_twr, live_ndx)]
    beta_all      = calculate_beta(beta_rows_all)
    rows_30       = [r for r in rows if r[0] >= cutoff]
    beta_rows_30  = [(to_f(r[9]), to_f(r[3])) for r in rows_30] + [(live_twr, live_ndx)]
    beta_30       = calculate_beta(beta_rows_30) if len(beta_rows_30) >= 3 else 0.0

    # MDD / Current DD / Recovery — 전체 기간, TWR(내 실적) vs NDX100 비교
    my_series  = [to_f(r[9]) for r in rows] + [live_twr]
    ndx_series = [to_f(r[3]) for r in rows] + [live_ndx]
    dd_mine = calculate_drawdown_metrics(my_series)
    dd_ndx  = calculate_drawdown_metrics(ndx_series)

    retirement_asset = calculate_retirement_asset(total_asset, monthly_irr, retirement_date)

    return {
        "latest_date":      today,
        "total_asset":      total_asset,
        "exposure":         exposure,
        "cash_ratio":       cash_ratio,
        "cash_eval":        cash_eval,
        "invest_ratio":     invest_ratio,
        "x1_ratio":         x1_ratio,
        "x2_ratio":         x2_ratio,
        "x3_ratio":         x3_ratio,
        "annual_irr":       annual_irr,
        "monthly_irr":      monthly_irr,
        "irr_30":           irr_30,
        "total_cash_flow":  total_cash_flow,
        "cumul_alpha":      cumul_alpha,
        "monthly_alpha":    monthly_alpha,
        "alpha_30":         alpha_30,
        "beta_all":         beta_all,
        "beta_30":          beta_30,
        "dd_mine":          dd_mine,
        "dd_ndx":           dd_ndx,
        "retirement_asset": retirement_asset,
        "retirement_date":  retirement_date,
    }

def _load_position_data(rows) -> list[dict]:
    from common.redis_store import get_all_prices

    prices = get_all_prices()

    # usd_krw: prices hash에서 직접 읽음
    fx_data = prices.get("USDKRW=X")
    usd_krw = float(fx_data["price"]) if fx_data else 1300.0

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

# ── 도넛 데이터 빌더 (server에서 공유) ───────────────────────

def _build_donut_payload(positions: list[dict]) -> dict:
    """
    position_data() 결과를 받아 도넛 렌더링에 필요한 데이터를 반환.
    svg_html + legend dict를 반환. legend는 {label: {color, pct, is_cash}} 구조.
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

    # 다크테마(#0a0a0a) + base.css 기존 액센트 토큰(green/amber/red/blue/purple)과
    # 동일한 채도(S≈68%)·명도(L≈58%) 대역에서 색상환(H)만 균등 10분배한 카테고리 팔레트
    TICKER_PALETTE = [
        "#dd634b", "#ddbb4b", "#a7dd4b", "#50dd4b", "#4bdd9e",
        "#4bc4dd", "#4b6ddd", "#804bdd", "#d84bdd", "#dd4b8a",
    ]

    def _ticker_color(ticker: str) -> str:
        # 프로세스 재시작 시에도 항상 같은 색이 나오도록 안정적인 해시(md5) 사용.
        # 파이썬 내장 hash()는 보안을 위해 실행마다 시드가 랜덤화되어 사용 불가.
        digest = hashlib.md5(ticker.encode("utf-8")).hexdigest()
        idx = int(digest, 16) % len(TICKER_PALETTE)
        return TICKER_PALETTE[idx]

    slices = []
    for p in top8:
        if p["ticker"] == "CASH":
            slices.append({"label": "현금", "value": p["eval_krw"], "color": "#111111"})
            continue
        slices.append({
            "label": p["name"] or p["ticker"],
            "value": p["eval_krw"],
            "color": _ticker_color(p["ticker"]),
        })

    if other_eval > 0:
        slices.append({"label": "기타", "value": other_eval, "color": "#3a3a3a"})

    svg_html = _donut_svg(slices)

    legend = {}
    for s in slices:
        pct = s["value"] / total * 100
        legend[s["label"]] = {
            "color":   s["color"],
            "pct":     f"{pct:.1f}%",
            "is_cash": s["label"] == "현금",
        }

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

  function lerp(a, b, t) { return a + (b - a) * t; }
  function ddColor(val, lo, hi) {
    // lo=나쁨(빨강) 기준값, hi=좋음(초록) 기준값. val을 0~1로 정규화 후 빨강→초록 보간
    var t = (val - lo) / (hi - lo);
    if (t < 0) t = 0;
    if (t > 1) t = 1;
    var r = Math.round(lerp(255, 0,   t));
    var g = Math.round(lerp(77,  192, t));
    var b = Math.round(lerp(77,  115, t));
    return 'rgb(' + r + ',' + g + ',' + b + ')';
  }

  Shiny.addCustomMessageHandler('db_update', function(m) {

    // ── Exposure ─────────────────────────────────────
    if (m.exposure) {
      var expEl = document.getElementById('db-exposure-val');
      if (expEl) {
        if (m.exposure.exposure_text != null) expEl.textContent = m.exposure.exposure_text;
        if (m.exposure.exp_cls      != null) expEl.className   = 'db-exposure-val ' + m.exposure.exp_cls;
      }
      if (m.exposure.cash_ratio_text != null) setText('db-cash-ratio-val', m.exposure.cash_ratio_text);
      if (m.exposure.cash_eval_text  != null) setText('db-cash-eval-val',  m.exposure.cash_eval_text);

      // 레버리지 바 세그먼트 교체
      var track = document.getElementById('db-lev-bar-track');
      if (track && m.exposure.lev_segs) {
        var segs = m.exposure.lev_segs; // [x1, x2, x3, cash]
        var keys = ['x1','x2','x3','cash'];
        var html = '';
        for (var i = 0; i < 4; i++) {
          var v = segs[i];
          if (v >= 0.5) {
            var inner = v >= 5 ? Math.round(v) + '%' : '';
            html += '<div class="db-lev-bar-seg ' + keys[i] + '" style="flex:' + v + '">' + inner + '</div>';
          }
        }
        track.innerHTML = html;
      }

      // 레버리지 범례 교체
      var legend = document.getElementById('db-lev-legend');
      if (legend && m.exposure.lev_segs) {
        var segs = m.exposure.lev_segs;
        var defs = [['x1','x1'],['x2','x2'],['x3','x3'],['cash','현금']];
        var html = '';
        for (var i = 0; i < 4; i++) {
          var cls   = defs[i][0];
          var label = defs[i][1];
          html += '<span class="db-lev-legend-item">'
                + '<span class="db-lev-legend-dot ' + cls + '"></span>'
                + label + '  ' + segs[i].toFixed(1) + '%'
                + '</span>';
        }
        legend.innerHTML = html;
      }
    }

    // ── 수익률 ───────────────────────────────────────
    if (m.irr) {
      var annEl = document.getElementById('db-annual-irr');
      if (annEl) {
        if (m.irr.annual_text != null) annEl.textContent = m.irr.annual_text;
        if (m.irr.annual_sign != null) annEl.className = 'db-metric-value ' + pnlClass(m.irr.annual_sign);
      }
      var monEl = document.getElementById('db-monthly-irr');
      if (monEl) {
        if (m.irr.monthly_text != null) monEl.textContent = m.irr.monthly_text;
        if (m.irr.monthly_sign != null) monEl.className = 'db-metric-value ' + pnlClass(m.irr.monthly_sign);
      }
      var irr30El = document.getElementById('db-irr-30');
      if (irr30El) {
        if (m.irr.irr30_text != null) irr30El.textContent = m.irr.irr30_text;
        if (m.irr.irr30_sign != null) irr30El.className = 'db-metric-value ' + pnlClass(m.irr.irr30_sign);
      }
      var cfEl = document.getElementById('db-cash-flow-val');
      if (cfEl) {
        if (m.irr.cash_flow_text != null) cfEl.textContent = m.irr.cash_flow_text;
        if (m.irr.cash_flow_sign != null) cfEl.className = 'db-cashflow-val ' + pnlClass(m.irr.cash_flow_sign);
      }
    }

    // ── 알파 ─────────────────────────────────────────
    if (m.alpha) {
      var caEl = document.getElementById('db-cumul-alpha');
      if (caEl) {
        if (m.alpha.cumul_text != null) caEl.textContent = m.alpha.cumul_text;
        if (m.alpha.cumul_sign != null) caEl.className = 'db-metric-value ' + pnlClass(m.alpha.cumul_sign);
      }
      var maEl = document.getElementById('db-monthly-alpha');
      if (maEl) {
        if (m.alpha.monthly_text != null) maEl.textContent = m.alpha.monthly_text;
        if (m.alpha.monthly_sign != null) maEl.className = 'db-metric-value ' + pnlClass(m.alpha.monthly_sign);
      }
      var a30El = document.getElementById('db-alpha-30');
      if (a30El) {
        if (m.alpha.alpha30_text != null) a30El.textContent = m.alpha.alpha30_text;
        if (m.alpha.alpha30_sign != null) a30El.className = 'db-metric-value ' + pnlClass(m.alpha.alpha30_sign);
      }
    }

    // ── 베타 ─────────────────────────────────────────
    if (m.beta) {
      if (m.beta.all_text    != null) setText('db-beta-all', m.beta.all_text);
      if (m.beta.beta30_text != null) setText('db-beta-30',  m.beta.beta30_text);
    }

    // ── 낙폭 분석 (MDD / Current DD / Recovery) ───────
    // 색상은 절대값이 아니라 NDX 대비 우위(diff = 내 - NDX) 기준으로 빨강~초록 결정
    if (m.dd) {
      var mddMineEl = document.getElementById('db-mdd-mine');
      if (mddMineEl) {
        if (m.dd.mdd_mine_text != null) mddMineEl.textContent = m.dd.mdd_mine_text;
        if (m.dd.mdd_diff      != null) mddMineEl.style.color = ddColor(m.dd.mdd_diff, -0.10, 0.10);
      }
      if (m.dd.mdd_ndx_text != null) setText('db-mdd-ndx', m.dd.mdd_ndx_text);

      var cddMineEl = document.getElementById('db-cdd-mine');
      if (cddMineEl) {
        if (m.dd.cdd_mine_text != null) cddMineEl.textContent = m.dd.cdd_mine_text;
        if (m.dd.cdd_diff      != null) cddMineEl.style.color = ddColor(m.dd.cdd_diff, -0.10, 0.10);
      }
      if (m.dd.cdd_ndx_text != null) setText('db-cdd-ndx', m.dd.cdd_ndx_text);

      var recMineEl = document.getElementById('db-rec-mine');
      if (recMineEl) {
        if (m.dd.rec_mine_text != null) recMineEl.textContent = m.dd.rec_mine_text;
        if (m.dd.rec_diff      != null) recMineEl.style.color = ddColor(m.dd.rec_diff, -0.20, 0.20);
      }
      if (m.dd.rec_ndx_text != null) setText('db-rec-ndx', m.dd.rec_ndx_text);
    }

    // ── 도넛 (텍스트) ─────────────────────────────────
    if (m.donut_text) {
      var legendEl = document.getElementById('db-donut-legend');
      if (legendEl && m.donut_text.legend) {
        var entries = Object.entries(m.donut_text.legend);
        if (entries.length > 0) {
          // 기존 행이 없으면(초기 또는 종목 구성 변경) 전체 재렌더
          // 기존 행이 있으면 pct/color만 패치
          var isInit = legendEl.children.length === 0;
          if (isInit) {
            var html = '';
            for (var i = 0; i < entries.length; i++) {
              var label = entries[i][0];
              var it    = entries[i][1];
              var dotCls = 'db-donut-legend-dot' + (it.is_cash ? ' cash' : '');
              html += '<div class="db-donut-legend-row" data-label="' + label + '">'
                    + '<span class="' + dotCls + '" style="background:' + it.color + '"></span>'
                    + '<span class="db-donut-legend-name">' + label + '</span>'
                    + '<span class="db-donut-legend-pct">' + it.pct + '</span>'
                    + '</div>';
            }
            legendEl.innerHTML = html;
          } else {
            for (var i = 0; i < entries.length; i++) {
              var label = entries[i][0];
              var it    = entries[i][1];
              var row = legendEl.querySelector('[data-label="' + label + '"]');
              if (row) {
                // 기존 행 패치
                if (it.pct   !== undefined) row.querySelector('.db-donut-legend-pct').textContent = it.pct;
                if (it.color !== undefined) row.querySelector('.db-donut-legend-dot').style.background = it.color;
              } else {
                // 신규 종목 — 전체 재렌더 (순서가 바뀔 수 있으므로)
                var html = '';
                var allEntries = Object.entries(m.donut_text.legend);
                for (var j = 0; j < allEntries.length; j++) {
                  var l2  = allEntries[j][0];
                  var it2 = allEntries[j][1];
                  var dotCls2 = 'db-donut-legend-dot' + (it2.is_cash ? ' cash' : '');
                  html += '<div class="db-donut-legend-row" data-label="' + l2 + '">'
                        + '<span class="' + dotCls2 + '" style="background:' + it2.color + '"></span>'
                        + '<span class="db-donut-legend-name">' + l2 + '</span>'
                        + '<span class="db-donut-legend-pct">' + it2.pct + '</span>'
                        + '</div>';
                }
                legendEl.innerHTML = html;
                break;
              }
            }
          }
        }
      }
      if (m.donut_text.subtitle !== undefined) setText('db-donut-title-sub', '(' + m.donut_text.subtitle + ')');
    }
    // ── 도넛 (SVG) ────────────────────────────────────
    if (m.donut_svg !== undefined) {
      setHTML('db-donut-svg-wrap', m.donut_svg);
    }

    // ── 은퇴 시뮬레이션 ──────────────────────────────
    if (m.retirement) {
      if (m.retirement.subtitle      != null) setText('db-retirement-subtitle', m.retirement.subtitle);
      if (m.retirement.amount_text   != null) setText('db-retirement-amount',   m.retirement.amount_text);
      if (m.retirement.sub_text      != null) setText('db-retirement-sub',      m.retirement.sub_text);
      if (m.retirement.compound_text != null) setText('db-retirement-compound', m.retirement.compound_text);
    }
  });
})();
        """),

        ui.div(
            {"class": "page-inner"},

            # ── 오늘 ──────────────────────────────────────
            ui.div(
                {"class": "db-section"},
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

            # ── 수익률 ────────────────────────────────────
            ui.div(
                {"class": "db-section"},
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
                    ui.div(
                        {"class": "db-metric-card"},
                        ui.div("30일 IRR", class_="db-metric-label"),
                        ui.span("–", id="db-irr-30", class_="db-metric-value"),
                    ),
                ),
                ui.div(
                    {"class": "db-cashflow-row"},
                    ui.span("총 입출금", class_="db-cashflow-label"),
                    ui.span("–", id="db-cash-flow-val", class_="db-cashflow-val"),
                ),
            ),

            # ── 알파 / 베타 ───────────────────────────────
            ui.div(
                {"class": "db-section"},
                ui.div(
                    {"class": "db-grid-2"},
                    ui.div(
                        {"class": "db-metric-card"},
                        ui.div("누적 알파", class_="db-metric-label"),
                        ui.span("–", id="db-cumul-alpha", class_="db-metric-value"),
                    ),
                    ui.div(
                        {"class": "db-metric-card"},
                        ui.div("월평균 알파", class_="db-metric-label"),
                        ui.span("–", id="db-monthly-alpha", class_="db-metric-value"),
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
                ui.div(
                    {"class": "db-dd-card"},
                    ui.div("낙폭 분석 (vs NDX100)", class_="db-beta-label"),
                    ui.div(
                        {"class": "db-dd-row"},
                        ui.span("최대낙폭", class_="db-dd-row-label"),
                        ui.div(
                            {"class": "db-beta-values"},
                            ui.span("–", id="db-mdd-mine", class_="db-beta-value"),
                            ui.span("/", class_="db-beta-sep"),
                            ui.span("–", id="db-mdd-ndx", class_="db-beta-value db-dd-ndx"),
                        ),
                    ),
                    ui.div(
                        {"class": "db-dd-row"},
                        ui.span("현재낙폭", class_="db-dd-row-label"),
                        ui.div(
                            {"class": "db-beta-values"},
                            ui.span("–", id="db-cdd-mine", class_="db-beta-value"),
                            ui.span("/", class_="db-beta-sep"),
                            ui.span("–", id="db-cdd-ndx", class_="db-beta-value db-dd-ndx"),
                        ),
                    ),
                    ui.div(
                        {"class": "db-dd-row"},
                        ui.span("회복률", class_="db-dd-row-label"),
                        ui.div(
                            {"class": "db-beta-values"},
                            ui.span("–", id="db-rec-mine", class_="db-beta-value"),
                            ui.span("/", class_="db-beta-sep"),
                            ui.span("–", id="db-rec-ndx", class_="db-beta-value db-dd-ndx"),
                        ),
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
def dashboard_server(input, output, session, active_tab: reactive.value = None,
                     active_sub_tab: reactive.value = None,
                     db_summary_rows=None, db_position_rows=None):

    _initialized = False  # 일반 변수: data()/position_data()/_send_update() 자기-재트리거 방지
    _last_display: dict = {}

    # ── DB 캐시 (asset_server에서 주입) ─────────────────────────────────────
    _db_summary_rows  = db_summary_rows
    _db_position_rows = db_position_rows

    @reactive.calc
    def _db_position_detail_rows():
        """도넛 차트용 positions + tickers 메타 — position_changed / ticker_changed 시에만 재조회."""
        _position_signal.get()
        _ticker_signal.get()
        with get_db() as conn:
            cur = conn.cursor()
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
        return rows

    # ── 대시보드 요약 데이터 계산 ────────────────────────────────────────────
    # price_signal 마다 Redis 시세를 새로 읽어 총자산·수익률 등 전체 지표를 재계산.
    # DB 파트(_db_summary_rows, _db_position_rows)는 각자의 signal에 의해서만 갱신됨.
    @reactive.calc
    def data():
        nonlocal _initialized
        _price_signal.get()
        _daily_insert_signal.get()
        _position_signal.get()
        _ticker_signal.get()
        tab = active_sub_tab if active_sub_tab is not None else active_tab
        if _initialized and tab and tab.get() != "dashboard":
            return None
        return _load_summary_data(_db_summary_rows(), _db_position_rows())

    # ── 포지션 데이터 계산 ────────────────────────────────────────────────
    # price_signal 마다 Redis 시세를 새로 읽어 종목별 평가액·비중을 재계산.
    # DB 파트(_db_position_detail_rows)는 position_changed / ticker_changed 시에만 갱신됨.
    @reactive.calc
    def position_data():
        nonlocal _initialized
        _price_signal.get()
        _position_signal.get()
        _ticker_signal.get()
        tab = active_sub_tab if active_sub_tab is not None else active_tab
        if _initialized and tab and tab.get() != "dashboard":
            return None
        return _load_position_data(_db_position_detail_rows())

    # ── 시세/daily insert 수신 시 대시보드 전체 갱신 ─────────────────────
    # data(), position_data() 의존성을 통해 price_signal, daily_insert_signal 에 연결됨.
    # diff_display 로 이전 화면과 비교해 변경된 필드만 JS로 전송 (DOM 전체 교체 아님).
    # 탭 비활성 시 스킵: 보이지 않는 DOM을 패치하는 건 낭비이고,
    # 탭 활성화 순간 active_tab 이 "dashboard"로 바뀌면서 자동으로 재실행된다.
    @reactive.effect
    async def _send_update():
            nonlocal _initialized
            tab = active_sub_tab if active_sub_tab is not None else active_tab
            if _initialized and tab and tab.get() != "dashboard":
                return
            d = data()
            positions = position_data()
            if not d:
                return

            # ── Exposure ─────────────────────────────────────
            exposure   = d["exposure"]
            cash_ratio = d["cash_ratio"]
            cash_eval  = d["cash_eval"]
            x1   = d["x1_ratio"] * 100
            x2   = d["x2_ratio"] * 100
            x3   = d["x3_ratio"] * 100
            cash = cash_ratio * 100
            exp_cls = "db-neg" if exposure >= 1.5 else "db-warn" if exposure >= 1.2 else "db-pos"

            # lev 비중: JS에서 DOM 생성 — [x1, x2, x3, cash] 소수점 1자리
            lev_segs = [round(x1, 1), round(x2, 1), round(x3, 1), round(cash, 1)]

            exposure_payload = {
                "exposure_text":   f"{exposure:.2f}x",
                "exp_cls":         exp_cls,
                "cash_ratio_text": _fmt_ratio_pct_plain(cash_ratio),
                "cash_eval_text":  fmt_krw(cash_eval),
                "lev_segs":        lev_segs,
            }

            # ── 수익률 ───────────────────────────────────────
            # _sign: 1(양수) / -1(음수) / 0 — JS pnlClass() 색상 판단용
            def _sign(v): return 1 if v > 0 else (-1 if v < 0 else 0)
            total_cash_flow = d["total_cash_flow"]
            irr = {
                "annual_text":    _fmt_ratio_pct(d["annual_irr"]),
                "annual_sign":    _sign(d["annual_irr"]),
                "monthly_text":   _fmt_ratio_pct(d["monthly_irr"]),
                "monthly_sign":   _sign(d["monthly_irr"]),
                "irr30_text":     _fmt_ratio_pct(d["irr_30"]),
                "irr30_sign":     _sign(d["irr_30"]),
                "cash_flow_text": fmt_krw(total_cash_flow),
                "cash_flow_sign": _sign(total_cash_flow),
            }

            # ── 알파 ─────────────────────────────────────────
            alpha = {
                "cumul_text":    _fmt_ratio_pct(d["cumul_alpha"]),
                "cumul_sign":    _sign(d["cumul_alpha"]),
                "monthly_text":  _fmt_ratio_pct(d["monthly_alpha"]),
                "monthly_sign":  _sign(d["monthly_alpha"]),
                "alpha30_text":  _fmt_ratio_pct(d["alpha_30"]),
                "alpha30_sign":  _sign(d["alpha_30"]),
            }

            # ── 베타 ─────────────────────────────────────────
            beta = {
                "all_text":    f"{d['beta_all']:.2f}",
                "beta30_text": f"{d['beta_30']:.2f}",
            }

            # ── 낙폭 분석 (MDD / Current DD / Recovery) ───────
            # JS ddColor()는 mine - ndx 차이값만 필요 → diff 3개로 압축
            dd_mine = d["dd_mine"]
            dd_ndx  = d["dd_ndx"]
            dd = {
                "mdd_mine_text":  _fmt_ratio_pct(dd_mine["mdd"]),
                "mdd_ndx_text":   _fmt_ratio_pct(dd_ndx["mdd"]),
                "mdd_diff":       round(dd_mine["mdd"]        - dd_ndx["mdd"],        4),
                "cdd_mine_text":  _fmt_ratio_pct(dd_mine["current_dd"]),
                "cdd_ndx_text":   _fmt_ratio_pct(dd_ndx["current_dd"]),
                "cdd_diff":       round(dd_mine["current_dd"] - dd_ndx["current_dd"], 4),
                "rec_mine_text":  _fmt_ratio_pct_plain(dd_mine["recovery"]),
                "rec_ndx_text":   _fmt_ratio_pct_plain(dd_ndx["recovery"]),
                "rec_diff":       round(dd_mine["recovery"]   - dd_ndx["recovery"],   4),
            }

            # ── 도넛 ─────────────────────────────────────────
            donut_data = _build_donut_payload(positions)
            if donut_data:
                donut = {
                    "svg_html": donut_data["svg_html"],
                    "legend":   donut_data["legend"],   # [{label, color, pct, is_cash}, ...]
                    "subtitle": donut_data["subtitle"],
                }
            else:
                donut = {"svg_html": "", "legend": {}, "subtitle": "–"}

            # ── 은퇴 시뮬레이션 ──────────────────────────────
            ret_asset   = d["retirement_asset"]
            ret_date    = d["retirement_date"]
            monthly_irr = d["monthly_irr"]
            today       = datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()
            months      = max(0, (ret_date.year - today.year) * 12 + (ret_date.month - today.month))
            years       = months / 12
            retirement = {
                "subtitle":      f"은퇴 시뮬레이션 ({ret_date.strftime('%Y년 %m월')}, +{years:.1f}년 후)",
                "amount_text":   fmt_krw(ret_asset),
                "sub_text":      f"월평균 IRR {_fmt_ratio_pct(monthly_irr)} 복리 적용",
                "compound_text": f"{months}개월 복리",
            }

            current = {
                "exposure": exposure_payload,
                "irr":      irr,
                "alpha":    alpha,
                "beta":     beta,
                "dd":       dd,
                "donut_text": {
                    "legend":   donut["legend"],    # {label: {color, pct, is_cash}, ...}
                    "subtitle": donut["subtitle"],
                },
                "donut_svg": donut["svg_html"],
                "retirement": retirement,
            }

            diff = diff_display(current, _last_display, depth=3)
            if diff:
                await session.send_custom_message("db_update", diff)
            _initialized = True