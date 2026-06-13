from shiny import ui, reactive, module
import subprocess
import sys
from app.db import get_db, get_usd_krw, get_config, get_market_currency
from app.price_signal import price_signal
from app.modules.components import fmt_krw, fmt_usd, fmt_pct, fmt_change
from scheduler.price_updater_common import get_market_status
from app.utils.display_diff import diff_display


# ── DAL ───────────────────────────────────────────────────────────────────────

def load_portfolio():
    from common.redis_store import get_all_prices

    prices = get_all_prices()

    usd_rate, usd_chg = get_usd_krw()
    usd_rate = usd_rate or 0

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.ticker, SUM(p.quantity) AS quantity,
                   t.name, t.market, t.leverage
            FROM positions p
            LEFT JOIN tickers t ON p.ticker = t.ticker
            LEFT JOIN accounts a ON p.account_id = a.id
            WHERE a.is_watch = false
            GROUP BY p.ticker, t.name, t.market, t.leverage
        """)
        db_rows = cur.fetchall()

        cur.execute("SELECT total_asset FROM daily_summary ORDER BY date DESC LIMIT 1")
        row = cur.fetchone()
        yesterday_total = float(row[0]) if row else 0.0
        cur.close()

    rows = []
    for ticker, qty, name, market, leverage in db_rows:
        p_data     = prices.get(ticker)
        price      = float(p_data["price"])      if p_data else 0.0
        change_pct = float(p_data["change_pct"]) if p_data else 0.0
        rows.append((ticker, qty, name, price, change_pct, market, leverage))

    return rows, usd_rate, usd_chg, yesterday_total


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _ticker_to_id(ticker: str) -> str:
    """ticker → DOM id 안전 문자열 (하이픈/캐럿 → 언더스코어)"""
    return ticker.replace("-", "_").replace("^", "_")


def _calc_amount(ticker, qty_f, price_f, market, usd_rate):
    if ticker == "KRW":                        return qty_f
    elif ticker == "USD":                      return qty_f * usd_rate
    elif get_market_currency(market) == "USD": return qty_f * price_f * usd_rate
    else:                                      return qty_f * price_f


def _sort_rows(rows, usd_rate):
    return sorted(
        rows,
        key=lambda r: (
            1 if r[0] in ("KRW", "USD") else 0,
            -_calc_amount(r[0], float(r[1] or 0), float(r[3] or 0), r[5], usd_rate)
        )
    )


def _build_row_skeleton(ticker, qty, name, market, leverage, usd_rate):
    """
    종목 구성 변경 시 1회 전송하는 골격 HTML.
    가변 값(amount, price, chg, status)은 id 달린 span으로 비워둠.
    """
    tid      = _ticker_to_id(ticker)
    is_cash  = ticker in ('KRW', 'USD')
    leverage = int(leverage) if leverage else 1
    qty_f    = float(qty or 0)

    lev_html = f'<span class="lev-badge lev-x{leverage}">x{leverage}</span>' if leverage > 1 else ""

    if ticker == 'KRW':
        display_name = "현금(KRW)"
        qty_str      = ""
        change_html  = ""
    elif ticker == 'USD':
        display_name = "현금(USD)"
        qty_str      = fmt_usd(qty_f)
        change_html  = ""
    else:
        display_name = name or ticker
        qty_str      = f"{qty_f:g}주"
        change_html  = (
            f'<div class="ticker-change">'
            f'<span id="pf-price-{tid}" style="margin-right:4px;"></span>'
            f'<span id="pf-chg-{tid}"></span>'
            f'</div>'
        )

    status_html = "" if is_cash else f'<span id="pf-status-{tid}" class="ticker-status"></span>'

    return (
        f'<div class="ticker-row" id="pf-row-{tid}">'
        f'  <div>'
        f'    <div class="lev-name-wrap">'
        f'      {lev_html}'
        f'      <span class="ticker-name">{display_name}</span>'
        f'      {status_html}'
        f'    </div>'
        f'    <div class="ticker-qty">{qty_str}</div>'
        f'  </div>'
        f'  <div>'
        f'    <div id="pf-amount-{tid}" class="ticker-amount"></div>'
        f'    {change_html}'
        f'  </div>'
        f'</div>'
    )


def _build_tick_values(ticker, qty, name, price, chg_pct, market, leverage, usd_rate):
    """
    시세 갱신 시마다 전송하는 값 dict.
    """
    tid      = _ticker_to_id(ticker)
    is_cash  = ticker in ('KRW', 'USD')
    leverage = int(leverage) if leverage else 1
    qty_f    = float(qty   or 0)
    price_f  = float(price or 0)
    chg_f    = float(chg_pct or 0)

    # amount
    amount = _calc_amount(ticker, qty_f, price_f, market, usd_rate)
    amount_str = fmt_krw(amount)

    # price / chg
    if is_cash:
        price_str = chg_str = chg_css = ""
    else:
        currency  = get_market_currency(market)
        price_str, chg_str, chg_css = fmt_change(price_f, chg_f, currency=currency)

    # status
    status_dot = status_text = status_cls = ""
    if not is_cash and market:
        status = get_market_status(market)
        dot_map = {
            "open":    ("●", "Open",       "status-open"),
            "pre":     ("●", "Pre",        "status-pre"),
            "after":   ("●", "After",      "status-after"),
            "closing": ("●", "Closing...", "status-closing"),
        }
        status_dot, status_text, status_cls = dot_map.get(status, ("○", "Closed", "status-closed"))

    return {
        "id":         tid,
        "amount":     amount_str,
        "price":      price_str,
        "chg":        chg_str,
        "chg_css":    chg_css,
        "status_dot": status_dot,
        "status_txt": status_text,
        "status_cls": status_cls,
    }


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def portfolio_ui():
    return ui.div(
        ui.tags.script("""
(function() {

  // ── pf_init: 종목 구성 변경 시 골격 통째 교체 ──────────────
  Shiny.addCustomMessageHandler('pf_init', function(m) {
    var el;

    el = document.getElementById('pf-total-asset');
    if (el) el.textContent = m.summary.total_asset;

    el = document.getElementById('pf-pnl');
    if (el) { el.textContent = m.summary.pnl_text; el.className = 'summary-delta ' + m.summary.pnl_class; }

    el = document.getElementById('pf-usd-wrap');
    if (el) el.style.display = m.summary.usd_html ? '' : 'none';
    el = document.getElementById('pf-usd-text');
    if (el) el.innerHTML = m.summary.usd_html;

    el = document.getElementById('pf-ticker-list');
    if (el) el.innerHTML = m.ticker_list_html;

    el = document.getElementById('pf-force-btn-wrap');
    if (el) el.style.display = m.show_force_btn ? '' : 'none';

    // 골격 교체 후 tick 값도 바로 반영
    _applyTickers(m.tickers);
  });

  // ── pf_tick: 변경된 key만 patch ─────────────────────────────
  Shiny.addCustomMessageHandler('pf_tick', function(m) {
    if (m.summary) {
      var el;
      el = document.getElementById('pf-total-asset');
      if (el) el.textContent = m.summary.total_asset;

      el = document.getElementById('pf-pnl');
      if (el) { el.textContent = m.summary.pnl_text; el.className = 'summary-delta ' + m.summary.pnl_class; }

      el = document.getElementById('pf-usd-wrap');
      if (el) el.style.display = m.summary.usd_html ? '' : 'none';
      el = document.getElementById('pf-usd-text');
      if (el) el.innerHTML = m.summary.usd_html;
    }

    // 나머지 key는 모두 ticker
    Object.keys(m).forEach(function(key) {
      if (key === 'summary') return;
      _applyOneTicker(m[key]);
    });
  });

  function _applyTickers(tickers) {
    Object.values(tickers).forEach(function(t) { _applyOneTicker(t); });
  }

  function _applyOneTicker(t) {
    var amountEl = document.getElementById('pf-amount-' + t.id);
    if (amountEl) amountEl.textContent = t.amount;

    var priceEl = document.getElementById('pf-price-' + t.id);
    if (priceEl) {
      priceEl.textContent  = t.price;
      priceEl.className    = t.chg_css;
      priceEl.style.marginRight = t.price ? '4px' : '0';
    }

    var chgEl = document.getElementById('pf-chg-' + t.id);
    if (chgEl) { chgEl.textContent = t.chg; chgEl.className = t.chg_css; }

    var stEl = document.getElementById('pf-status-' + t.id);
    if (stEl) {
      stEl.textContent = t.status_dot ? t.status_dot + ' ' + t.status_txt : '';
      stEl.className   = 'ticker-status ' + t.status_cls;
    }
  }

})();
        """),

        ui.div(
            {"class": "page-inner", "style": "position:relative;"},

            ui.div(
                {"class": "total-summary"},
                ui.div("포트폴리오", class_="summary-label"),
                ui.div("–", id="pf-total-asset", class_="summary-amount"),
                ui.div(
                    ui.span("–", id="pf-pnl", class_="summary-delta"),
                    ui.span(
                        {"id": "pf-usd-wrap", "style": "display:none;"},
                        ui.span({"id": "pf-usd-text", "class": "summary-usd"}),
                    ),
                    class_="summary-delta-row",
                ),
            ),

            ui.div(
                {"id": "pf-force-btn-wrap", "style": "display:none;"},
                ui.input_action_button("force_update", "↺", class_="force-update-btn"),
            ),

            ui.div({"id": "pf-ticker-list", "class": "ticker-list"}),
        ),

        class_="page-container",
    )


# ── Server ────────────────────────────────────────────────────────────────────
@module.server
def portfolio_server(input, output, session, active_tab: reactive.value = None):

    initialized = reactive.value(False)

    # 종목 구성 캐시 — ticker 목록이 바뀌면 pf_init 전송
    _last_tickers: list = []
    _last_display: dict = {}

    # ── 강제 시세 조회 모달 ───────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.force_update)
    def _show_force_modal():
        m = ui.modal(
            ui.div(
                ui.p("전체 종목 시세를 강제 조회합니다. 장외시간 종목도 포함됩니다."),
                ui.div(
                    ui.input_action_button("force_confirm", "확인", class_="btn-primary"),
                    ui.input_action_button("force_cancel", "취소", class_="btn-secondary"),
                    class_="modal-btn-row-half",
                    style="display:flex; gap:8px; margin-top:12px;",
                ),
                class_="modal-body-inner",
            ),
            title="강제 시세 조회",
            easy_close=True,
            footer=None,
        )
        ui.modal_show(m)

    @reactive.effect
    @reactive.event(input.force_confirm)
    def _do_force_update():
        ui.modal_remove()
        subprocess.Popen(
            [sys.executable, "scheduler/price_updater.py", "--force"],
            cwd="/home/ubuntu/asset-cloud"
        )

    @reactive.effect
    @reactive.event(input.force_cancel)
    def _cancel_force_update():
        ui.modal_remove()

    # ── 포트폴리오 갱신 ───────────────────────────────────────────────────────

    # ── 시세 수신 시 포트폴리오 갱신 ────────────────────────────────────────
    # price_signal 마다 Redis 시세를 새로 읽어 종목별 평가액·등락률을 갱신.
    # 종목 구성이 바뀌면 pf_init(골격 통째 교체), 아니면 pf_tick(변경 필드만 패치).
    # 탭 비활성 시 스킵: 보이지 않는 DOM을 패치하는 건 낭비이고,
    # 탭 활성화 순간 active_tab 이 "portfolio"로 바뀌면서 자동으로 재실행된다.
    @reactive.effect
    async def _send_update():
        nonlocal _last_tickers, _last_display
        price_signal.get()

        if initialized.get() and active_tab and active_tab.get() != "portfolio":
            return

        rows, usd_rate, usd_chg, yesterday_total = load_portfolio()

        # ── 총 평가액 합산 ────────────────────────────────
        total_asset = 0
        for ticker, qty, name, price, chg_pct, market, leverage in rows:
            qty_f   = float(qty   or 0)
            price_f = float(price or 0)
            total_asset += _calc_amount(ticker, qty_f, price_f, market, usd_rate)

        total_pnl = total_asset - yesterday_total
        pnl_pct   = (total_pnl / yesterday_total * 100) if yesterday_total else 0
        pnl_sign  = "+" if total_pnl >= 0 else ""
        pnl_text  = f"{pnl_sign}{fmt_krw(abs(total_pnl))} ({fmt_pct(pnl_pct)})"
        pnl_class = "positive" if total_pnl >= 0 else "negative"

        usd_html = ""
        if usd_rate and usd_chg is not None:
            usd_css  = "positive" if usd_chg >= 0 else "negative"
            usd_html = f'<span style="color:#888888;">USD </span><span class="{usd_css}">{usd_rate:,.2f} ({fmt_pct(usd_chg)})</span>'

        rows_sorted = _sort_rows(rows, usd_rate)

        # ── tick 값 (ticker → top-level key) ────────────────
        ticker_values = {
            ticker: _build_tick_values(ticker, qty, name, price, chg_pct, market, leverage, usd_rate)
            for ticker, qty, name, price, chg_pct, market, leverage in rows_sorted
        }

        summary = {
            "total_asset": fmt_krw(total_asset),
            "pnl_text":    pnl_text,
            "pnl_class":   pnl_class,
            "usd_html":    usd_html,
        }

        # ── 종목 구성 변경 감지 ───────────────────────────
        current_tickers = [r[0] for r in rows_sorted]
        structure_changed = (current_tickers != _last_tickers)

        if structure_changed:
            _last_tickers = current_tickers
            _last_display.clear()  # 골격 교체 시 diff 상태 리셋
            cfg        = get_config()
            show_force = int(cfg.get("interval", 1)) != 0
            ticker_list_html = "".join(
                _build_row_skeleton(ticker, qty, name, market, leverage, usd_rate)
                for ticker, qty, name, price, chg_pct, market, leverage in rows_sorted
            )
            await session.send_custom_message("pf_init", {
                "summary":          summary,
                "ticker_list_html": ticker_list_html,
                "show_force_btn":   show_force,
                "tickers":          ticker_values,
            })
        else:
            current = {"summary": summary, **ticker_values}
            diff = diff_display(current, _last_display)
            if diff:
                await session.send_custom_message("pf_tick", diff)
        initialized.set(True)