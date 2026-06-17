from shiny import ui, reactive, module
import subprocess
import sys
from app.db import get_db, get_usd_krw, get_config, get_market_currency
from app.price_signal import price_signal, position_signal, ticker_signal
from app.modules.components import (
    fmt_krw, fmt_usd, fmt_pct, fmt_change, fmt_pnl,
    build_ticker_row_skeleton, build_ticker_row_values,
)
from scheduler.price_updater_common import get_market_status
from app.utils.display_diff import diff_display


# ── DAL ───────────────────────────────────────────────────────────────────────

def load_portfolio(db_rows, yesterday_total):
    from common.redis_store import get_all_prices

    prices = get_all_prices()
    rows   = []
    for ticker, qty, name, market, leverage, avg_price in db_rows:
        p_data     = prices.get(ticker)
        price      = float(p_data["price"])      if p_data else 0.0
        change_pct = float(p_data["change_pct"]) if p_data else 0.0
        rows.append((ticker, qty, name, price, change_pct, market, leverage, avg_price))

    return rows, yesterday_total


def load_ticker_accounts(ticker: str, db_rows, usd_rate: float):
    """
    특정 ticker 보유 계좌 목록.
    가격은 Redis에서 읽어 주입 (position_signal 구독 캐시 기반, price_signal 무관).
    반환: (acc_rows, price, chg_pct)
      acc_rows: [(acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, price, chg_pct), ...]
    """
    from common.redis_store import get_all_prices
    prices  = get_all_prices()
    p_data  = prices.get(ticker)
    price   = float(p_data["price"])      if p_data else 0.0
    chg_pct = float(p_data["change_pct"]) if p_data else 0.0

    result = [
        (acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, price, chg_pct, ticker_name)
        for acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, ticker_name in db_rows
    ]
    return result, price, chg_pct


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _ticker_to_id(ticker: str) -> str:
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


def _build_pf_row_skeleton(ticker, qty, name, market, leverage, avg_price=None):
    """포트폴리오 종목 행 골격"""
    tid      = _ticker_to_id(ticker)
    qty_f    = float(qty or 0)
    leverage = int(leverage) if leverage else 1
    is_cash  = ticker in ('KRW', 'USD')

    if ticker == 'KRW':
        display_name = "현금(KRW)"
        qty_fixed    = ""
    elif ticker == 'USD':
        display_name = "현금(USD)"
        qty_fixed    = fmt_usd(qty_f)
    else:
        display_name = name or ticker
        qty_fixed    = None  # span으로 비워둠 (tick에서 채움)

    onclick_attr = (
        "" if is_cash
        else f"pfOpenTickerDrilldown('{ticker}', '{display_name}');"
    )

    return build_ticker_row_skeleton(
        ticker       = ticker,
        display_name = display_name,
        market       = market,
        leverage     = leverage,
        id_prefix    = "pf",
        row_id       = tid,
        qty_fixed    = qty_fixed,
        onclick_attr = onclick_attr,
        data_attrs   = "",
    )


def _build_pf_tick_values(ticker, qty, name, price, chg_pct, market, leverage, usd_rate, avg_price=None):
    """포트폴리오 종목 tick 값"""
    tid     = _ticker_to_id(ticker)
    qty_f   = float(qty   or 0)
    price_f = float(price or 0)

    amount = _calc_amount(ticker, qty_f, price_f, market, usd_rate)

    return build_ticker_row_values(
        ticker                 = ticker,
        amount                 = amount,
        qty                    = qty,
        price                  = price,
        chg_pct                = chg_pct,
        market                 = market,
        avg_price              = avg_price,
        id_prefix              = "pf",
        row_id                 = tid,
        get_market_currency_fn = get_market_currency,
        get_market_status_fn   = get_market_status,
        qty_in_values          = True,
    )


def _build_drilldown_row_skeleton(acc_id, ticker, acc_name, alias, is_watch, qty, avg_price, market, leverage):
    """드릴다운 계좌 행 골격 — 종목명 자리에 계좌명"""
    qty_f        = float(qty or 0)
    leverage     = int(leverage) if leverage else 1
    display_name = acc_name + (f" ({alias})" if alias else "")
    qty_fixed    = f"≈{qty_f:.2f}주" if qty_f != int(qty_f) else f"{qty_f:g}주"

    return build_ticker_row_skeleton(
        ticker       = ticker,
        display_name = display_name,
        market       = market,
        leverage     = leverage,
        id_prefix    = "pfd",
        row_id       = str(acc_id),
        qty_fixed    = qty_fixed,
        onclick_attr = "",
        data_attrs   = "",
    )


def _build_drilldown_row_values(acc_id, ticker, qty, avg_price, price, chg_pct, market, leverage, usd_rate):
    """드릴다운 계좌 행 tick 값"""
    qty_f   = float(qty   or 0)
    price_f = float(price or 0)
    # 드릴다운 행은 ticker 원천값 기반으로 is_cash 판단 (components 내부)
    amount = _calc_amount(ticker, qty_f, price_f, market, usd_rate)

    return build_ticker_row_values(
        ticker                 = ticker,
        amount                 = amount,
        qty                    = qty,
        price                  = price,
        chg_pct                = chg_pct,
        market                 = market,
        avg_price              = avg_price,
        id_prefix              = "pfd",
        row_id                 = str(acc_id),
        get_market_currency_fn = get_market_currency,
        get_market_status_fn   = get_market_status,
        qty_in_values          = False,
    )


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
    if (el) el.style.display = m.summary.usd_text ? 'flex' : 'none';
    el = document.getElementById('pf-usd-text');
    if (el) { el.textContent = m.summary.usd_text; el.className = m.summary.usd_css; }

    el = document.getElementById('pf-ticker-list');
    if (el) el.innerHTML = m.ticker_list_html;

    el = document.getElementById('pf-force-btn-wrap');
    if (el) el.style.display = m.show_force_btn ? '' : 'none';

    el = document.getElementById('pf-header-price-wrap');
    if (el) el.style.display = 'none';

    el = document.getElementById('pf-summary-label');
    if (el) el.textContent = '포트폴리오';

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
      if (el) el.style.display = m.summary.usd_text ? 'flex' : 'none';
      el = document.getElementById('pf-usd-text');
      if (el) { el.textContent = m.summary.usd_text; el.className = m.summary.usd_css; }
    }
    Object.keys(m).forEach(function(key) {
      if (key === 'summary') return;
      _applyOneTicker(m[key]);
    });
  });

  // ── pfd_init: 드릴다운 계좌 목록 초기화 ────────────────────
  Shiny.addCustomMessageHandler('pfd_init', function(m) {
    var el;

    el = document.getElementById('pf-total-asset');
    if (el) el.textContent = m.summary.total_asset;
    el = document.getElementById('pf-pnl');
    if (el) { el.textContent = m.summary.pnl_text; el.className = 'summary-delta ' + m.summary.pnl_class; }
    el = document.getElementById('pf-usd-wrap');
    if (el) el.style.display = 'none';

    el = document.getElementById('pf-summary-label');
    if (el) el.textContent = m.ticker_name;

    el = document.getElementById('pf-drilldown-list');
    if (el) el.innerHTML = m.account_list_html;

    el = document.getElementById('pf-header-price-wrap');
    if (el) el.style.display = '';
    el = document.getElementById('pf-header-price');
    if (el) { el.textContent = m.summary.price_text; el.className = m.summary.chg_css; }
    el = document.getElementById('pf-header-chg');
    if (el) { el.textContent = m.summary.chg_text; el.className = m.summary.chg_css; }

    document.getElementById('pf-list-view').style.display      = 'none';
    document.getElementById('pf-drilldown-view').style.display = '';
    document.getElementById('pf-back-btn').style.display       = 'inline-block';
    document.getElementById('pf-force-btn-wrap').style.display = 'none';

    _applyDrilldownRows(m.rows);
  });

  // ── pfd_tick: 드릴다운 변경값만 patch ──────────────────────
  Shiny.addCustomMessageHandler('pfd_tick', function(m) {
    if (m.summary) {
      var el;
      el = document.getElementById('pf-total-asset');
      if (el) el.textContent = m.summary.total_asset;
      el = document.getElementById('pf-pnl');
      if (el) { el.textContent = m.summary.pnl_text; el.className = 'summary-delta ' + m.summary.pnl_class; }
      el = document.getElementById('pf-header-price');
      if (el) { el.textContent = m.summary.price_text; el.className = m.summary.chg_css; }
      el = document.getElementById('pf-header-chg');
      if (el) { el.textContent = m.summary.chg_text; el.className = m.summary.chg_css; }
    }
    Object.keys(m).forEach(function(key) {
      if (key === 'summary') return;
      _applyOneDrilldownRow(m[key]);
    });
  });

  // ── 드릴다운 열기 ────────────────────────────────────────────
  window.pfOpenTickerDrilldown = function(ticker, name) {
    Shiny.setInputValue('portfolio-ticker_clicked', { ticker: ticker, name: name }, { priority: 'event' });
  };

  // ── 뒤로가기 ─────────────────────────────────────────────────
  window.pfGoBack = function() {
    document.getElementById('pf-list-view').style.display      = '';
    document.getElementById('pf-drilldown-view').style.display = 'none';
    document.getElementById('pf-back-btn').style.display       = 'none';
    document.getElementById('pf-header-price-wrap').style.display = 'none';
    var labelEl = document.getElementById('pf-summary-label');
    if (labelEl) labelEl.textContent = '포트폴리오';
    Shiny.setInputValue('portfolio-go_back', Math.random(), { priority: 'event' });
  };

  function _applyTickers(tickers) {
    Object.values(tickers).forEach(function(t) { _applyOneTicker(t); });
  }

  function _applyOneTicker(t) {
    var amountEl = document.getElementById('pf-amount-' + t.id);
    if (amountEl) amountEl.textContent = t.amount;

    var qtyEl = document.getElementById('pf-qty-' + t.id);
    if (qtyEl) qtyEl.textContent = t.qty || '';

    var priceEl = document.getElementById('pf-price-' + t.id);
    if (priceEl) {
      priceEl.textContent  = t.price;
      priceEl.className    = t.chg_css;
      priceEl.style.marginRight = t.price ? '4px' : '0';
    }

    var chgEl = document.getElementById('pf-chg-' + t.id);
    if (chgEl) { chgEl.textContent = t.chg; chgEl.className = t.chg_css; }

    var avgEl = document.getElementById('pf-avgprice-' + t.id);
    if (avgEl) avgEl.textContent = t.avgprice || '';

    var pnlEl = document.getElementById('pf-pnlpct-' + t.id);
    if (pnlEl) { pnlEl.textContent = t.pnlpct || ''; pnlEl.className = t.pnlpct_css || ''; }

    var stEl = document.getElementById('pf-status-' + t.id);
    if (stEl) {
      stEl.textContent = t.status_dot ? t.status_dot + ' ' + t.status_txt : '';
      stEl.className   = 'ticker-status ' + t.status_cls;
    }
  }

  function _applyDrilldownRows(rows) {
    Object.values(rows).forEach(function(r) { _applyOneDrilldownRow(r); });
  }

  function _applyOneDrilldownRow(r) {
    var amountEl = document.getElementById('pfd-amount-' + r.id);
    if (amountEl) amountEl.textContent = r.amount;

    var priceEl = document.getElementById('pfd-price-' + r.id);
    if (priceEl) {
      priceEl.textContent = r.price;
      priceEl.className   = r.chg_css;
      priceEl.style.marginRight = r.price ? '4px' : '0';
    }

    var chgEl = document.getElementById('pfd-chg-' + r.id);
    if (chgEl) { chgEl.textContent = r.chg; chgEl.className = r.chg_css; }

    var avgEl = document.getElementById('pfd-avgprice-' + r.id);
    if (avgEl) avgEl.textContent = r.avgprice || '';

    var pnlEl = document.getElementById('pfd-pnlpct-' + r.id);
    if (pnlEl) { pnlEl.textContent = r.pnlpct || ''; pnlEl.className = r.pnlpct_css || ''; }

    var stEl = document.getElementById('pfd-status-' + r.id);
    if (stEl) {
      stEl.textContent = r.status_dot ? r.status_dot + ' ' + r.status_txt : '';
      stEl.className   = 'ticker-status ' + r.status_cls;
    }
  }

})();
        """),

        ui.div(
            {"class": "page-inner", "style": "position:relative;"},

            # ── 공통 헤더 ─────────────────────────────────────────────────────
            ui.div(
                {"class": "total-summary"},
                ui.div(
                    ui.tags.button(
                        "‹",
                        id="pf-back-btn",
                        class_="summary-label",
                        style="display:none; background:none; border:none; padding:0; margin-right:6px; cursor:pointer; vertical-align:middle; line-height:1; font-family:inherit;",
                        onclick="pfGoBack();",
                    ),
                    ui.span("포트폴리오", id="pf-summary-label", class_="summary-label",
                            style="vertical-align:middle;"),
                    style="display:flex; align-items:center; height:20px; margin-bottom:4px;",
                ),
                ui.div("–", id="pf-total-asset", class_="summary-amount"),
                ui.div(
                    ui.span("–", id="pf-pnl", class_="summary-delta"),
                    ui.span(
                        {"id": "pf-usd-wrap", "style": "display:none; margin-left:auto; align-items:baseline; gap:4px;"},
                        ui.span("USD", style="font-size:11px; color:#888888;"),
                        ui.span("–", id="pf-usd-text", style="font-size:13px;"),
                    ),
                    ui.span(
                        {"id": "pf-header-price-wrap", "style": "display:none; margin-left:auto;"},
                        ui.span("", id="pf-header-price", style="margin-right:4px;"),
                        ui.span("", id="pf-header-chg"),
                    ),
                    class_="summary-delta-row",
                ),
            ),

            # ── 강제 조회 버튼 ────────────────────────────────────────────────
            ui.div(
                {"id": "pf-force-btn-wrap", "style": "display:none;"},
                ui.input_action_button("force_update", "↺", class_="force-update-btn"),
            ),

            # ── 포트폴리오 목록 뷰 ────────────────────────────────────────────
            ui.div(
                {"id": "pf-list-view"},
                ui.div({"id": "pf-ticker-list", "class": "ticker-list"}),
            ),

            # ── 드릴다운 뷰 (종목별 계좌 목록) ───────────────────────────────
            ui.div(
                {"id": "pf-drilldown-view", "style": "display:none;"},
                ui.div({"id": "pf-drilldown-list", "class": "ticker-list"}),
            ),
        ),

        class_="page-container",
    )


# ── Server ────────────────────────────────────────────────────────────────────

@module.server
def portfolio_server(input, output, session, active_tab: reactive.value = None):

    initialized     = reactive.value(False)
    selected_ticker = reactive.value(None)  # None: 목록 뷰, str: 드릴다운 뷰

    _last_tickers:     list = []
    _last_display:     dict = {}
    _last_dd_accounts: list = []
    _last_dd_display:  dict = {}

    # ── DB 캐시 ──────────────────────────────────────────────────────────────

    @reactive.calc
    def _db_portfolio_rows():
        """positions + tickers JOIN — position_signal / ticker_signal 시에만 재조회"""
        position_signal.get()
        ticker_signal.get()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.ticker, SUM(p.quantity) AS quantity,
                       t.name, t.market, t.leverage,
                       SUM(p.quantity * p.avg_price) / NULLIF(SUM(p.quantity), 0) AS avg_price
                FROM positions p
                LEFT JOIN tickers t ON p.ticker = t.ticker
                LEFT JOIN accounts a ON p.account_id = a.id
                WHERE a.is_watch = false
                GROUP BY p.ticker, t.name, t.market, t.leverage
            """)
            rows = cur.fetchall()
            cur.close()
        return rows

    @reactive.calc
    def _db_yesterday_total():
        """daily_summary 최신 1행 — daily_insert_signal 시에만 재조회"""
        from app.price_signal import daily_insert_signal
        daily_insert_signal.get()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT total_asset FROM daily_summary ORDER BY date DESC LIMIT 1")
            row = cur.fetchone()
            cur.close()
        return float(row[0]) if row else 0.0

    @reactive.calc
    def _db_ticker_accounts():
        """선택된 ticker 보유 계좌 목록 — position_signal / ticker_signal 시에만 재조회"""
        position_signal.get()
        ticker_signal.get()
        ticker = selected_ticker.get()
        if not ticker:
            return []
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT a.id, a.name, a.alias, a.is_watch,
                       p.quantity, p.avg_price,
                       t.market, t.leverage, t.name AS ticker_name
                FROM positions p
                JOIN accounts a ON p.account_id = a.id
                LEFT JOIN tickers t ON p.ticker = t.ticker
                WHERE p.ticker = %s
                ORDER BY a.is_watch ASC, a.name ASC
            """, (ticker,))
            rows = cur.fetchall()
            cur.close()
        return rows

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

    # ── 종목 클릭 → 드릴다운 ────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.ticker_clicked)
    def _handle_ticker_click():
        nonlocal _last_dd_accounts, _last_dd_display
        payload = input.ticker_clicked()
        ticker  = payload.get("ticker") if payload else None
        if ticker:
            _last_dd_accounts = []
            _last_dd_display.clear()
            selected_ticker.set(ticker)

    # ── 뒤로가기 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.go_back)
    def _handle_go_back():
        nonlocal _last_tickers, _last_display
        _last_tickers = []
        _last_display.clear()
        selected_ticker.set(None)

    # ── 화면 갱신 ─────────────────────────────────────────────────────────────

    @reactive.effect
    async def _send_update():
        nonlocal _last_tickers, _last_display, _last_dd_accounts, _last_dd_display
        price_signal.get()
        position_signal.get()
        ticker_signal.get()

        if initialized.get() and active_tab and active_tab.get() != "portfolio":
            return

        usd_rate, usd_chg = get_usd_krw()
        usd_rate = usd_rate or 0

        ticker = selected_ticker.get()

        if ticker is None:
            # ── 포트폴리오 목록 뷰 ────────────────────────────────────────
            rows, yesterday_total = load_portfolio(
                _db_portfolio_rows(), _db_yesterday_total()
            )

            total_asset = sum(
                _calc_amount(t, float(qty or 0), float(price or 0), market, usd_rate)
                for t, qty, name, price, chg_pct, market, leverage, avg_price in rows
            )

            total_pnl = total_asset - yesterday_total
            pnl_pct   = (total_pnl / yesterday_total * 100) if yesterday_total else 0
            pnl_text, pnl_class = fmt_pnl(total_pnl, pnl_pct)

            usd_text = ""
            if usd_rate and usd_chg is not None:
                usd_text = f'{usd_rate:,.2f} ({fmt_pct(usd_chg)})'

            rows_sorted   = _sort_rows(rows, usd_rate)
            ticker_values = {
                t: _build_pf_tick_values(t, qty, name, price, chg_pct, market, leverage, usd_rate, avg_price)
                for t, qty, name, price, chg_pct, market, leverage, avg_price in rows_sorted
            }
            usd_css = (
                "positive" if usd_chg is not None and usd_chg > 0
                else "negative" if usd_chg is not None and usd_chg < 0
                else "neutral"
            ) if usd_rate and usd_chg is not None else ""
            summary = {
                "total_asset": fmt_krw(total_asset),
                "pnl_text":    pnl_text,
                "pnl_class":   pnl_class,
                "usd_text":    usd_text,
                "usd_css":     usd_css,
            }

            current_tickers   = [r[0] for r in rows_sorted]
            structure_changed = (current_tickers != _last_tickers)

            if structure_changed:
                _last_tickers = current_tickers
                _last_display.clear()
                cfg        = get_config()
                show_force = int(cfg.get("interval", 1)) != 0
                ticker_list_html = "".join(
                    _build_pf_row_skeleton(t, qty, name, market, leverage, avg_price)
                    for t, qty, name, price, chg_pct, market, leverage, avg_price in rows_sorted
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

        else:
            # ── 드릴다운 뷰 (ticker 보유 계좌 목록) ──────────────────────
            db_rows = _db_ticker_accounts()
            acc_rows, price, chg_pct = load_ticker_accounts(ticker, db_rows, usd_rate)

            normal = [r for r in acc_rows if not r[3]]
            watch  = [r for r in acc_rows if r[3]]

            # 드릴다운 헤더: 비감시 계좌만 합산 (감시계좌는 보유자산이 아니므로 제외 — 목록 뷰와 동일 기준)
            total_asset = sum(
                _calc_amount(ticker, float(qty or 0), float(p or 0), market, usd_rate)
                for _, _, _, _, qty, _, market, leverage, p, _, _ in normal
            )
            cost_basis = sum(
                _calc_amount(ticker, float(qty or 0), float(avg_price or 0), market, usd_rate)
                for _, _, _, _, qty, avg_price, market, leverage, p, _, _ in normal
            )
            sum_qty = sum(float(qty or 0) for _, _, _, _, qty, _, _, _, _, _, _ in normal)
            weighted_avg_price = (
                sum(
                    float(qty or 0) * float(avg_price or 0)
                    for _, _, _, _, qty, avg_price, _, _, _, _, _ in normal
                ) / sum_qty
            ) if sum_qty else 0.0

            pnl_amount = total_asset - cost_basis
            pnl_pct    = (
                (price - weighted_avg_price) / weighted_avg_price * 100
            ) if weighted_avg_price else 0.0
            pnl_text, pnl_class = fmt_pnl(pnl_amount, pnl_pct)

            ticker_market = acc_rows[0][6] if acc_rows else None
            currency = get_market_currency(ticker_market) if ticker_market else None
            price_text, chg_text, chg_css = fmt_change(price, chg_pct, currency=currency)

            summary = {
                "total_asset": fmt_krw(total_asset),
                "pnl_text":    pnl_text,
                "pnl_class":   pnl_class,
                "price_text":  price_text,
                "chg_text":    chg_text,
                "chg_css":     chg_css,
            }

            row_values = {
                str(acc_id): _build_drilldown_row_values(
                    acc_id, ticker, qty, avg_price, p, c, market, leverage, usd_rate
                )
                for acc_id, _, _, _, qty, avg_price, market, leverage, p, c, _ in acc_rows
            }

            current_accounts  = [r[0] for r in acc_rows]
            structure_changed = (current_accounts != _last_dd_accounts)

            # ticker 표시명 — DB 쿼리 결과에서 직접 추출
            ticker_name = acc_rows[0][10] or ticker if acc_rows else ticker

            if structure_changed:
                _last_dd_accounts = current_accounts
                _last_dd_display.clear()

                def _section(rows_subset):
                    return "".join(
                        _build_drilldown_row_skeleton(
                            acc_id, ticker, acc_name, alias, is_watch, qty, avg_price, market, leverage
                        )
                        for acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, p, c, _
                        in rows_subset
                    )

                html = _section(normal)
                if watch:
                    html += '<h4 class="section-heading">감시 계좌</h4>'
                    html += _section(watch)

                await session.send_custom_message("pfd_init", {
                    "summary":           summary,
                    "ticker_name":       ticker_name,
                    "account_list_html": html,
                    "rows":              row_values,
                })
            else:
                current = {"summary": summary, **row_values}
                diff = diff_display(current, _last_dd_display)
                if diff:
                    await session.send_custom_message("pfd_tick", diff)

        initialized.set(True)