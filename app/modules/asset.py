from shiny import module, ui, reactive

from app.db import get_db
from app.price_signal import (
    price_signal as _price_signal,
    daily_insert_signal as _daily_insert_signal,
    position_signal as _position_signal,
    ticker_signal as _ticker_signal,
)
from app.utils.metrics import to_f, calculate_exposure_and_ratios
from app.modules.components import fmt_krw, fmt_pct
from app.utils.display_diff import diff_display

from app.modules.dashboard import dashboard_ui, dashboard_server
from app.modules.portfolio import portfolio_ui, portfolio_server
from app.modules.accounts import accounts_ui, accounts_server


# ── 히어로 SVG ───────────────────────────────────────────────

def _hero_line_svg(values: list[float]) -> str:
    if not values or len(values) < 2:
        return ""
    min_v, max_v = min(values), max(values)
    v_range = (max_v - min_v) or 1
    pts = []
    for i, v in enumerate(values):
        x = (i / (len(values) - 1)) * 100
        y = 100 - ((v - min_v) / v_range) * 100
        pts.append((x, y))
    polyline = " ".join(f"{x:.0f},{y:.0f}" for x, y in pts)
    fill_d = (
        f"M {pts[0][0]:.0f},{pts[0][1]:.0f} "
        + " ".join(f"L {x:.0f},{y:.0f}" for x, y in pts[1:])
        + f" L {pts[-1][0]:.0f},100 L {pts[0][0]:.0f},100 Z"
    )
    return (
        f'<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" '
        f'preserveAspectRatio="none" style="display:block; width:100%; height:100%;">'
        f'<defs><linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="#00c073" stop-opacity="0.25"/>'
        f'<stop offset="100%" stop-color="#00c073" stop-opacity="0.0"/>'
        f'</linearGradient></defs>'
        f'<path d="{fill_d}" fill="url(#hg)" />'
        f'<polyline points="{polyline}" fill="none" stroke="#00c073" stroke-width="2" '
        f'vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>'
        f'</svg>'
    )


# ── UI ───────────────────────────────────────────────────────

@module.ui
def asset_ui():
    return ui.div(
        {"id": "asset-root"},

        # JS 핸들러
        ui.tags.script("""
(function() {
  function pnlClass(v) {
    return v > 0 ? 'db-pos' : v < 0 ? 'db-neg' : 'db-neu';
  }
  function setText(id, val) {
    var el = document.getElementById(id); if (el) el.textContent = val;
  }
  function setHTML(id, val) {
    var el = document.getElementById(id); if (el) el.innerHTML = val;
  }

  Shiny.addCustomMessageHandler('asset_hero_update', function(m) {
    if (m.hero_text) {
      setText('asset-hero-amount',     m.hero_text.total_asset);
      setText('asset-hero-delta-text', m.hero_text.delta_text);
      var dEl = document.getElementById('asset-hero-delta-text');
      if (dEl) dEl.className = 'db-hero-delta ' + pnlClass(m.hero_text.delta_val);
      setText('asset-hero-usd-text', m.hero_text.usd_text);
      var uEl = document.getElementById('asset-hero-usd-text');
      if (uEl) uEl.className = pnlClass(m.hero_text.usd_chg_val);
    }
    if (m.hero_chart_svg !== undefined) {
      setHTML('asset-hero-chart-inner', m.hero_chart_svg);
    }
  });

  // 하위 모듈 네임스페이스 (portfolio, accounts JS에서 참조)
  window._pfNs = 'asset-portfolio';
  window._acNs = 'asset-accounts';

  // 서브탭 전환
  window.switchSubTab = function(name, el) {
    document.querySelectorAll('.asset-sub-content').forEach(function(t) {
      t.style.display = 'none';
    });
    document.querySelectorAll('.asset-sub-btn').forEach(function(b) {
      b.classList.remove('active');
    });
    var target = document.getElementById('asset-sub-' + name);
    if (target) {
      target.style.display = 'block';
      target.querySelectorAll('.shiny-bound-output').forEach(function(output) {
        var cb = $(output).data('shiny-intersection-observer-callback');
        if (cb) cb();
      });
    }
    el.classList.add('active');
    localStorage.setItem('activeSubTab', name);
    Shiny.setInputValue('asset-active_sub_tab', name, {priority: 'event'});
  };

  // 서브탭 복원
  Shiny.addCustomMessageHandler('restore_sub_tab', function(msg) {
    var saved = localStorage.getItem('activeSubTab') || 'dashboard';
    var subNames = ['dashboard', 'portfolio', 'accounts'];
    if (subNames.indexOf(saved) === -1) saved = 'dashboard';
    var btns = document.querySelectorAll('.asset-sub-btn');
    var idx = subNames.indexOf(saved);
    if (btns[idx]) switchSubTab(saved, btns[idx]);
  });

  // ── 서브탭 스와이프 ────────────────────────────────────────
  // asset-root가 DOM에 생긴 뒤 한 번만 등록
  var _subSwipeInit = false;
  function initSubSwipe() {
    if (_subSwipeInit) return;
    var root = document.getElementById('asset-root');
    if (!root) return;
    _subSwipeInit = true;

    var subNames = ['dashboard', 'portfolio', 'accounts'];
    var touchStartX = 0;
    var touchStartY = 0;

    root.addEventListener('touchstart', function(e) {
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
    }, { passive: true });

    root.addEventListener('touchend', function(e) {
      var dx = e.changedTouches[0].clientX - touchStartX;
      var dy = e.changedTouches[0].clientY - touchStartY;

      // 수평 스와이프만 처리 (수직 스크롤과 구분)
      if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy) * 1.5) return;

      // 현재 서브탭 인덱스 파악
      var currentIdx = -1;
      subNames.forEach(function(name, i) {
        var el = document.getElementById('asset-sub-' + name);
        if (el && el.style.display !== 'none') currentIdx = i;
      });
      if (currentIdx === -1) return;

      var nextIdx;
      if (dx < 0) {
        // 왼쪽 스와이프 → 다음 서브탭
        nextIdx = Math.min(currentIdx + 1, subNames.length - 1);
      } else {
        // 오른쪽 스와이프 → 이전 서브탭
        nextIdx = Math.max(currentIdx - 1, 0);
      }
      if (nextIdx === currentIdx) return;

      var btns = document.querySelectorAll('.asset-sub-btn');
      switchSubTab(subNames[nextIdx], btns[nextIdx]);
    }, { passive: true });
  }

  // asset 탭이 활성화될 때 스와이프 초기화 (DOM 준비 보장)
  Shiny.addCustomMessageHandler('restore_sub_tab', function(msg) {
    var saved = localStorage.getItem('activeSubTab') || 'dashboard';
    var subNames = ['dashboard', 'portfolio', 'accounts'];
    if (subNames.indexOf(saved) === -1) saved = 'dashboard';
    var btns = document.querySelectorAll('.asset-sub-btn');
    var idx = subNames.indexOf(saved);
    if (btns[idx]) switchSubTab(saved, btns[idx]);
    initSubSwipe();
  });

})();
        """),

        # ── 히어로 헤더 ──────────────────────────────────────
        ui.div(
            {"class": "db-hero"},
            ui.div({"id": "asset-hero-chart-inner", "class": "db-hero-chart"}),
            ui.div(
                {"class": "db-hero-content"},
                ui.div(
                    ui.div(
                        {"class": "summary-badge"},
                        ui.span("총자산", class_="summary-badge-text"),
                    ),
                    style="display:flex; align-items:center; height:20px; margin-bottom:4px;",
                ),
                ui.div("–", id="asset-hero-amount", class_="db-hero-amount"),
                ui.div(
                    {"class": "db-hero-delta-row"},
                    ui.span("–", id="asset-hero-delta-text", class_="db-hero-delta"),
                    ui.span(
                        {"id": "asset-hero-usd-wrap",
                         "style": "margin-left:auto; display:flex; align-items:baseline; gap:4px;"},
                        ui.span("USD", style="font-size:11px; color:#888888;"),
                        ui.span("–", id="asset-hero-usd-text", style="font-size:13px;"),
                    ),
                ),
            ),
        ),

        # ── 서브탭 버튼 ──────────────────────────────────────
        ui.div(
            {"class": "asset-sub-tabbar"},
            ui.div("대시보드", class_="asset-sub-btn active",
                   onclick="switchSubTab('dashboard', this)"),
            ui.div("포트폴리오", class_="asset-sub-btn",
                   onclick="switchSubTab('portfolio', this)"),
            ui.div("계좌", class_="asset-sub-btn",
                   onclick="switchSubTab('accounts', this)"),
        ),

        # ── 서브탭 콘텐츠 ─────────────────────────────────────
        ui.div(
            ui.div(dashboard_ui("dashboard"), id="asset-sub-dashboard", class_="asset-sub-content"),
            ui.div(portfolio_ui("portfolio"), id="asset-sub-portfolio",
                   class_="asset-sub-content", style="display:none;"),
            ui.div(accounts_ui("accounts"),  id="asset-sub-accounts",
                   class_="asset-sub-content", style="display:none;"),
        ),
    )


# ── Server ───────────────────────────────────────────────────

@module.server
def asset_server(input, output, session, active_tab: reactive.value = None):

    _initialized = False
    _last_display: dict = {}

    active_sub_tab = reactive.value("dashboard")

    @reactive.effect
    @reactive.event(input.active_sub_tab)
    def _sync_sub_tab():
        active_sub_tab.set(input.active_sub_tab())

    # ── DB 캐시 ──────────────────────────────────────────────
    @reactive.calc
    def _db_summary_rows():
        _daily_insert_signal.get()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT date, total_asset, cash_flow, ndx100,
                       exposure, cash_ratio, x1_ratio, x2_ratio, x3_ratio, twr_asset
                FROM daily_summary
                ORDER BY date ASC
            """)
            rows = cur.fetchall()
            cur.close()
        return rows

    @reactive.calc
    def _db_position_rows():
        _position_signal.get()
        _ticker_signal.get()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.ticker, p.quantity, t.leverage, t.market
                FROM positions p
                LEFT JOIN tickers t ON p.ticker = t.ticker
                LEFT JOIN accounts a ON p.account_id = a.id
                WHERE a.is_watch = false
            """)
            rows = cur.fetchall()
            cur.close()
        return rows

    # ── 히어로 데이터 계산 ────────────────────────────────────
    @reactive.calc
    def _hero_data():
        nonlocal _initialized
        _price_signal.get()
        _daily_insert_signal.get()
        _position_signal.get()
        _ticker_signal.get()
        if _initialized and active_tab and active_tab.get() != "asset":
            return None

        from common.redis_store import get_all_prices
        prices = get_all_prices()

        fx_data = prices.get("USDKRW=X")
        usd_krw = float(fx_data["price"]) if fx_data else 1300.0
        usd_chg = float(fx_data["change_pct"]) if fx_data else 0.0

        raw_rows = _db_position_rows()
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

        rt          = calculate_exposure_and_ratios(pos_rows, usd_krw)
        total_asset = rt["total_asset"]

        summary_rows = _db_summary_rows()
        if not summary_rows:
            return None
        prev_asset = to_f(summary_rows[-1][1])
        asset_delta     = total_asset - prev_asset
        asset_delta_pct = (asset_delta / prev_asset) if prev_asset else 0.0

        # 미니차트용 샘플링
        all_assets = [to_f(r[1]) for r in summary_rows] + [total_asset]
        n = len(all_assets)
        if n > 100:
            step = n / 100
            sampled = [all_assets[int(i * step)] for i in range(100)]
            sampled[-1] = total_asset
        else:
            sampled = all_assets

        return {
            "total_asset":      total_asset,
            "asset_delta":      asset_delta,
            "asset_delta_pct":  asset_delta_pct,
            "usd_krw":          usd_krw,
            "usd_chg":          usd_chg,
            "chart_data":       sampled,
        }

    # ── 히어로 갱신 ──────────────────────────────────────────
    @reactive.effect
    async def _send_hero_update():
        nonlocal _initialized
        if _initialized and active_tab and active_tab.get() != "asset":
            return
        d = _hero_data()
        if not d:
            return

        def _arrow(v): return "+" if v > 0 else "-" if v < 0 else "–"
        def _fmt_ratio_pct(v): return f"{v * 100:+.2f}%"

        delta = d["asset_delta"]
        pct   = d["asset_delta_pct"]
        hero = {
            "total_asset": fmt_krw(d["total_asset"]),
            "delta_text":  f"{_arrow(delta)}{fmt_krw(abs(delta))}  {_fmt_ratio_pct(pct)}",
            "delta_val":   delta,
            "usd_text":    f"{d['usd_krw']:,.2f} {fmt_pct(d['usd_chg'])}",
            "usd_chg_val": d["usd_chg"],
        }
        chart_svg = _hero_line_svg(d["chart_data"])

        current = {
            "hero_text":     hero,
            "hero_chart_svg": chart_svg,
        }
        diff = diff_display(current, _last_display)
        if diff:
            await session.send_custom_message("asset_hero_update", diff)
        _initialized = True

    # 탭 활성화 시 서브탭 복원 트리거
    @reactive.effect
    async def _on_tab_active():
        if active_tab and active_tab.get() == "asset":
            await session.send_custom_message("restore_sub_tab", {})
        else:
            active_sub_tab.set(None)

    # ── 서브 모듈 서버 ────────────────────────────────────────
    dashboard_server("dashboard", active_tab=active_tab, active_sub_tab=active_sub_tab,
                     db_summary_rows=_db_summary_rows, db_position_rows=_db_position_rows)
    portfolio_server("portfolio", active_tab=active_tab, active_sub_tab=active_sub_tab)
    accounts_server("accounts",  active_tab=active_tab, active_sub_tab=active_sub_tab)