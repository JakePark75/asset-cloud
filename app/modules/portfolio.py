from shiny import ui, reactive, module
import subprocess
import sys
from app.db import get_db, get_usd_krw, get_config, get_market_currency
from app.price_signal import price_signal, position_signal, ticker_signal
from app.modules.components import (
    build_ticker_row_skeleton, build_ticker_row_values,
    build_account_row_skeleton, build_account_row_values,
)
from scheduler.price_updater_common import get_market_status
from app.utils.display_diff import diff_display, diff_display_split


# ── DAL ───────────────────────────────────────────────────────────────────────

def load_portfolio(db_rows):
    from common.redis_store import get_all_prices

    prices = get_all_prices()
    rows   = []
    for ticker, qty, name, market, leverage, avg_price in db_rows:
        p_data     = prices.get(ticker)
        price      = float(p_data["price"])      if p_data else 0.0
        change_pct = float(p_data["change_pct"]) if p_data else 0.0
        rows.append((ticker, qty, name, price, change_pct, market, leverage, avg_price))

    return rows


def load_watch_only(db_rows):
    """
    감시계좌에만 존재하고 비감시계좌 보유가 없는 ticker(감시종목).
    가격은 일반 종목과 동일하게 Redis에서 주입. qty/avg_price는 보유가 없으므로 항상 0/None.
    """
    from common.redis_store import get_all_prices

    prices = get_all_prices()
    rows   = []
    for ticker, name, market, leverage in db_rows:
        p_data     = prices.get(ticker)
        price      = float(p_data["price"])      if p_data else 0.0
        change_pct = float(p_data["change_pct"]) if p_data else 0.0
        rows.append((ticker, 0, name, price, change_pct, market, leverage, None))

    return rows


def load_ticker_accounts(ticker: str, db_rows, usd_rate: float):
    """
    특정 ticker 보유 계좌 목록 (아코디언 내용).
    가격은 Redis에서 읽어 주입 (position_signal 구독 캐시 기반, price_signal 무관).
    반환: (acc_rows, price, chg_pct)
      acc_rows: [(acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, price, chg_pct, ticker_name), ...]
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
    return result


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


def _sort_watch_rows(rows):
    """감시종목 정렬 — 보유 자산이 없어 금액 기준 정렬이 의미 없으므로 이름/ticker 기준 고정 정렬.
    (정렬 기준이 매 갱신마다 흔들리면 structure_changed가 불필요하게 자주 True가 됨)"""
    return sorted(rows, key=lambda r: (r[2] or r[0]))


def _build_pf_row_skeleton(ticker, qty, name, market, leverage, avg_price=None):
    """포트폴리오 종목 행 골격 + 아코디언 컨테이너(빈 채로, 기본 display:none)"""
    tid      = _ticker_to_id(ticker)
    qty_f    = float(qty or 0)
    leverage = int(leverage) if leverage else 1
    is_cash  = ticker in ('KRW', 'USD')

    if ticker == 'KRW':
        display_name = "현금(KRW)"
        qty_fixed    = ""
    elif ticker == 'USD':
        display_name = "현금(USD)"
        qty_fixed    = None  # 1행 구조 — 달러 잔액은 amount_str에 통합 표시
    else:
        display_name = name or ticker
        qty_fixed    = None  # span으로 비워둠 (tick에서 채움)

    onclick_attr = (
        "" if is_cash
        else f"pfToggleTicker('{ticker}', '{tid}');"
    )

    row_html = build_ticker_row_skeleton(
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

    if is_cash:
        return row_html

    accordion_html = f'<div class="subtab-accordion" id="pf-acc-{tid}" style="display:none;"></div>'
    return row_html + accordion_html


def _build_pf_tick_values(ticker, qty, name, price, chg_pct, market, leverage, usd_rate, avg_price=None, is_watch_only=False):
    """포트폴리오 종목 tick 값"""
    tid     = _ticker_to_id(ticker)
    qty_f   = float(qty   or 0)
    price_f = float(price or 0)

    amount = _calc_amount(ticker, qty_f, price_f, market, usd_rate)

    if ticker == 'KRW':
        display_name = "현금(KRW)"
    elif ticker == 'USD':
        display_name = "현금(USD)"
    else:
        display_name = name or ticker

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
        name                   = display_name,
        leverage               = leverage,
        usd_rate               = usd_rate,
        qty_in_values          = True,
        is_watch_only          = is_watch_only,
    )


def _build_drilldown_row_skeleton(acc_id, acc_name, alias, qty):
    """아코디언 내부 계좌 행 골격 — 계좌명·수량만."""
    qty_f         = float(qty or 0)
    display_name  = acc_name + (f" ({alias})" if alias else "")
    qty_text      = f"≈{qty_f:.2f}주" if qty_f != int(qty_f) else f"{qty_f:g}주"

    return build_account_row_skeleton(
        display_name = display_name,
        qty_text     = qty_text,
        row_id       = str(acc_id),
        id_prefix    = "pfd",
    )


def _build_drilldown_row_values(acc_id, ticker, qty, avg_price, price, market, usd_rate):
    """아코디언 내부 계좌 행 tick 값 — 평가금액 + 이 계좌 포지션의 손익액/수익률"""
    qty_f   = float(qty       or 0)
    avg_f   = float(avg_price or 0)
    price_f = float(price     or 0)

    amount     = _calc_amount(ticker, qty_f, price_f, market, usd_rate)
    cost_basis = _calc_amount(ticker, qty_f, avg_f,   market, usd_rate)
    pnl_amount = amount - cost_basis
    pnl_pct    = ((price_f - avg_f) / avg_f * 100) if avg_f else 0.0

    return build_account_row_values(
        avg_price  = avg_f,
        amount     = amount,
        pnl_amount = pnl_amount,
        pnl_pct    = pnl_pct,
        currency   = get_market_currency(market),
        row_id     = str(acc_id),
    )


def _build_accordion_html(acc_rows):
    """아코디언 내부 계좌 목록 HTML (헤더 없음 — 종목 행 자체에 가격/손익 이미 표시됨)"""
    normal = [r for r in acc_rows if not r[3]]
    watch  = [r for r in acc_rows if r[3]]

    def _section(rows_subset):
        return "".join(
            _build_drilldown_row_skeleton(acc_id, acc_name, alias, qty)
            for acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, p, c, _
            in rows_subset
        )

    html = _section(normal)
    if watch:
        html += '<h4 class="section-heading">감시 계좌</h4>'
        html += _section(watch)
    return html


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def portfolio_ui():
    return ui.div(
        ui.tags.script("""
(function() {

  // ── pf_init: 종목 구성 변경 시 골격 통째 교체 ──────────────
  Shiny.addCustomMessageHandler('pf_init', function(m) {
    var el = document.getElementById('pf-ticker-list');
    if (el) el.innerHTML = m.ticker_list_html;

    el = document.getElementById('pf-force-btn-wrap');
    if (el) el.style.display = m.show_force_btn ? '' : 'none';

    // 열려있던 아코디언은 pf_acc_init/tick 이 오면 그때 열림
    if (window._pfOpenTid) {
      var accEl = document.getElementById('pf-acc-' + window._pfOpenTid);
      if (!accEl) window._pfOpenTid = null;
    }

    _applyTickers(m.tickers);
  });

  // ── pf_tick: 변경된 key만 patch ─────────────────────────────
  Shiny.addCustomMessageHandler('pf_tick', function(m) {
    Object.keys(m).forEach(function(key) {
      _applyOneTicker(m[key]);
    });
  });

  // ── pf_static_tick: static 필드만 patch (종목명/레버리지/수량/평단/시장상태) ──
  Shiny.addCustomMessageHandler('pf_static_tick', function(m) {
    Object.keys(m).forEach(function(key) {
      _applyOneTickerStatic(m[key]);
    });
  });

  // ── pf_acc_init: 아코디언 내용 통째 교체 ────────────────────
  Shiny.addCustomMessageHandler('pf_acc_init', function(m) {
    var el = document.getElementById('pf-acc-' + m.tid);
    if (el) {
      el.innerHTML = m.account_list_html;
      el.style.display = '';
    }
    _applyDrilldownRows(m.rows);
  });

  // ── pf_acc_tick: 아코디언 변경값만 patch ─────────────────────
  Shiny.addCustomMessageHandler('pf_acc_tick', function(m) {
    Object.keys(m.rows || {}).forEach(function(key) {
      _applyOneDrilldownRow(m.rows[key]);
    });
  });

  // ── 아코디언 토글 (한번에 하나만 열림) ────────────────────────
  window.pfToggleTicker = function(ticker, tid) {
    var el = document.getElementById('pf-acc-' + tid);
    if (!el) return;

    if (window._pfOpenTid === tid) {
      // 닫기
      el.style.display = 'none';
      el.innerHTML = '';
      window._pfOpenTid = null;
      Shiny.setInputValue(window._pfNs + '-ticker_clicked', { ticker: null }, { priority: 'event' });
      return;
    }

    // 이전에 열려있던 아코디언 닫기
    if (window._pfOpenTid) {
      var prevEl = document.getElementById('pf-acc-' + window._pfOpenTid);
      if (prevEl) { prevEl.style.display = 'none'; prevEl.innerHTML = ''; }
    }

    window._pfOpenTid = tid;
    Shiny.setInputValue(window._pfNs + '-ticker_clicked', { ticker: ticker }, { priority: 'event' });
  };

  // pf_init용: static + dynamic 전체 적용
  function _applyTickers(tickers) {
    Object.values(tickers).forEach(function(t) { _applyOneTickerFull(t); });
  }

  function _applyOneTickerFull(t) {
    var nameEl = document.getElementById('pf-name-' + t.id);
    if (nameEl && t.name != null) nameEl.textContent = t.name;

    var levEl = document.getElementById('pf-lev-' + t.id);
    if (levEl && t.leverage != null) {
      levEl.textContent = 'x' + t.leverage;
      levEl.className   = 'lev-badge lev-x' + t.leverage;
      levEl.style.display = t.leverage > 1 ? '' : 'none';
    }

    var qtyEl = document.getElementById('pf-qty-' + t.id);
    if (qtyEl) qtyEl.textContent = t.qty || '';

    var avgEl = document.getElementById('pf-avgprice-' + t.id);
    if (avgEl) avgEl.textContent = t.avgprice || '';

    var stEl = document.getElementById('pf-status-' + t.id);
    if (stEl) {
      stEl.textContent = t.status_dot ? t.status_dot + ' ' + t.status_txt : '';
      stEl.className   = 'ticker-status ' + t.status_cls;
    }

    _applyOneTicker(t);
  }

  // pf_static_tick용: static 필드만 적용 (수신된 필드만 존재)
  function _applyOneTickerStatic(t) {
    if (t.name != null) {
      var nameEl = document.getElementById('pf-name-' + t.id);
      if (nameEl) nameEl.textContent = t.name;
    }

    if (t.leverage != null) {
      var levEl = document.getElementById('pf-lev-' + t.id);
      if (levEl) {
        levEl.textContent = 'x' + t.leverage;
        levEl.className   = 'lev-badge lev-x' + t.leverage;
        levEl.style.display = t.leverage > 1 ? '' : 'none';
      }
    }

    if (t.qty != null) {
      var qtyEl = document.getElementById('pf-qty-' + t.id);
      if (qtyEl) qtyEl.textContent = t.qty || '';
    }

    if (t.avgprice != null) {
      var avgEl = document.getElementById('pf-avgprice-' + t.id);
      if (avgEl) avgEl.textContent = t.avgprice || '';
    }

    if (t.status_dot != null || t.status_txt != null || t.status_cls != null) {
      var stEl = document.getElementById('pf-status-' + t.id);
      if (stEl) {
        stEl.textContent = t.status_dot ? t.status_dot + ' ' + t.status_txt : '';
        stEl.className   = 'ticker-status ' + (t.status_cls || '');
      }
    }
  }

  // pf_tick용: dynamic 필드만 적용 (수신된 필드만 존재)
  function _applyOneTicker(t) {
    if (t.amount != null) {
      var amountEl = document.getElementById('pf-amount-' + t.id);
      if (amountEl) amountEl.textContent = t.amount;
    }

    if (t.price != null || t.chg_css != null) {
      var priceEl = document.getElementById('pf-price-' + t.id);
      if (priceEl) {
        if (t.price != null)   { priceEl.textContent = t.price; priceEl.style.marginRight = t.price ? '4px' : '0'; }
        if (t.chg_css != null)   priceEl.className = t.chg_css;
      }
    }

    if (t.chg != null || t.chg_css != null) {
      var chgEl = document.getElementById('pf-chg-' + t.id);
      if (chgEl) {
        if (t.chg != null)     chgEl.textContent = t.chg;
        if (t.chg_css != null) chgEl.className   = t.chg_css;
      }
    }

    if (t.pnl_amount != null || t.pnl_pct != null || t.pnl_css != null) {
      var pnlEl = document.getElementById('pf-pnl-' + t.id);
      if (pnlEl) {
        if (t.pnl_amount != null) pnlEl.dataset.pnlAmount = t.pnl_amount;
        if (t.pnl_pct    != null) pnlEl.dataset.pnlPct    = t.pnl_pct;
        if (t.pnl_css    != null) pnlEl.className          = t.pnl_css;
        pnlEl.textContent = (pnlEl.dataset.pnlAmount || '') + (pnlEl.dataset.pnlPct ? ' ' + pnlEl.dataset.pnlPct : '');
      }
    }
  }

  function _applyDrilldownRows(rows) {
    Object.values(rows).forEach(function(r) { _applyOneDrilldownRow(r); });
  }

  function _applyOneDrilldownRow(r) {
    var amountEl = document.getElementById('pfd-amount-' + r.id);
    if (amountEl) amountEl.textContent = r.amount;

    var avgEl = document.getElementById('pfd-avgprice-' + r.id);
    if (avgEl) avgEl.textContent = r.avgprice || '';

    var pnlEl = document.getElementById('pfd-pnl-' + r.id);
    if (pnlEl) { pnlEl.textContent = r.pnl_text; pnlEl.className = r.pnl_css; }
  }

})();
        """),

        ui.div(
            {"class": "page-inner", "style": "position:relative;"},

            # ── 강제 조회 버튼 ────────────────────────────────────────────────
            ui.div(
                {"id": "pf-force-btn-wrap", "style": "display:none;"},
                ui.input_action_button("force_update", "↺", class_="force-update-btn"),
            ),

            # ── 포트폴리오 목록 (아코디언 포함) ───────────────────────────────
            ui.div(
                {"id": "pf-ticker-list", "class": "ticker-list"},
            ),
        ),

        class_="page-container",
    )


# ── Server ────────────────────────────────────────────────────────────────────

@module.server
def portfolio_server(input, output, session, active_tab: reactive.value = None,
                     active_sub_tab: reactive.value = None):

    ns_str = session.ns("_")[:-1]

    _initialized = False  # 일반 변수: effect 자기-재트리거 방지
    open_ticker  = reactive.value(None)  # None: 아코디언 닫힘, str: 해당 ticker 아코디언 열림 (한번에 하나만)

    _last_tickers:     list      = []
    _last_display:     dict      = {}
    _last_open_ticker: str | None = None
    _last_dd_accounts: list      = []
    _last_dd_display:  dict      = {}

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
    def _db_watch_only_tickers():
        """
        감시계좌(is_watch=true)에는 존재하지만 비감시계좌(is_watch=false) 보유가 전혀 없는 ticker.
        position_signal / ticker_signal 시에만 재조회. 보유 0이므로 avg_price 없음.
        """
        position_signal.get()
        ticker_signal.get()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT p.ticker, t.name, t.market, t.leverage
                FROM positions p
                JOIN accounts a ON p.account_id = a.id
                LEFT JOIN tickers t ON p.ticker = t.ticker
                WHERE a.is_watch = true
                  AND p.ticker NOT IN (
                      SELECT p2.ticker
                      FROM positions p2
                      JOIN accounts a2 ON p2.account_id = a2.id
                      WHERE a2.is_watch = false
                  )
            """)
            rows = cur.fetchall()
            cur.close()
        return rows

    @reactive.calc
    def _db_ticker_accounts():
        """열려있는 아코디언의 ticker 보유 계좌 목록 — position_signal / ticker_signal 시에만 재조회.
        open_ticker가 None이면 빈 리스트 (불필요한 쿼리 방지)."""
        position_signal.get()
        ticker_signal.get()
        ticker = open_ticker.get()
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

    # ── 종목 클릭 → 아코디언 토글 ───────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.ticker_clicked)
    def _handle_ticker_click():
        nonlocal _last_dd_accounts, _last_dd_display
        payload = input.ticker_clicked()
        ticker  = payload.get("ticker") if payload else None
        if not ticker:
            # 닫기 — 상태 초기화
            _last_dd_accounts.clear()
            _last_dd_display.clear()
        open_ticker.set(ticker)

    # ── 화면 갱신 ─────────────────────────────────────────────────────────────

    @reactive.effect
    async def _send_update():
        nonlocal _last_tickers, _last_display
        nonlocal _last_open_ticker, _last_dd_accounts, _last_dd_display
        nonlocal _initialized

        price_signal.get()
        position_signal.get()
        ticker_signal.get()
        cur_open_ticker = open_ticker.get()  # 탭 가드 전에 의존성 등록

        tab = active_sub_tab if active_sub_tab is not None else active_tab
        if _initialized and tab and tab.get() != "portfolio":
            return

        usd_rate, usd_chg = get_usd_krw()
        usd_rate = usd_rate or 0

        # ── 포트폴리오 목록 (항상 갱신) ────────────────────────────────────
        rows = load_portfolio(_db_portfolio_rows())
        watch_rows = load_watch_only(_db_watch_only_tickers())

        rows_sorted  = _sort_rows(rows, usd_rate)
        watch_sorted = _sort_watch_rows(watch_rows)

        ticker_values = {
            t: _build_pf_tick_values(t, qty, name, price, chg_pct, market, leverage, usd_rate, avg_price)
            for t, qty, name, price, chg_pct, market, leverage, avg_price in rows_sorted
        }
        ticker_values.update({
            t: _build_pf_tick_values(t, qty, name, price, chg_pct, market, leverage, usd_rate, avg_price, is_watch_only=True)
            for t, qty, name, price, chg_pct, market, leverage, avg_price in watch_sorted
        })

        current_tickers   = [r[0] for r in rows_sorted] + [r[0] for r in watch_sorted]
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
            if watch_sorted:
                ticker_list_html += '<h4 class="section-heading">감시종목</h4>'
                ticker_list_html += "".join(
                    _build_pf_row_skeleton(t, qty, name, market, leverage, avg_price)
                    for t, qty, name, price, chg_pct, market, leverage, avg_price in watch_sorted
                )
            # pf_init: static(이름/레버리지/수량/평단/상태) + dynamic(가격/평가금액/손익) 모두 전송
            await session.send_custom_message("pf_init", {
                "ticker_list_html": ticker_list_html,
                "show_force_btn":   show_force,
                "tickers": {t: {**v["static"], **v["dynamic"]} for t, v in ticker_values.items()},
            })
        else:
            # pf_tick: dynamic 필드 단위 diff / pf_static_tick: static 필드 단위 diff
            dyn_diff, sta_diff = diff_display_split(ticker_values, _last_display)
            if dyn_diff:
                await session.send_custom_message("pf_tick", dyn_diff)
            if sta_diff:
                await session.send_custom_message("pf_static_tick", sta_diff)

        # ── 아코디언 (열려있는 종목이 있을 때만 추가 계산) ───────────────────
        if cur_open_ticker:
            tid = _ticker_to_id(cur_open_ticker)
            db_rows = _db_ticker_accounts()
            acc_rows = load_ticker_accounts(cur_open_ticker, db_rows, usd_rate)

            current_accounts = [r[0] for r in acc_rows]
            ticker_switched   = (cur_open_ticker != _last_open_ticker)
            acc_structure_changed = ticker_switched or (current_accounts != _last_dd_accounts)

            row_values = {
                str(acc_id): _build_drilldown_row_values(
                    acc_id, cur_open_ticker, qty, avg_price, p, market, usd_rate
                )
                for acc_id, _, _, _, qty, avg_price, market, leverage, p, c, _ in acc_rows
            }

            if acc_structure_changed:
                _last_dd_accounts = current_accounts
                _last_dd_display.clear()
                await session.send_custom_message("pf_acc_init", {
                    "tid":               tid,
                    "account_list_html": _build_accordion_html(acc_rows),
                    "rows":              row_values,
                })
            else:
                diff = diff_display(row_values, _last_dd_display)
                if diff:
                    await session.send_custom_message("pf_acc_tick", {"rows": diff})

            _last_open_ticker = cur_open_ticker
        else:
            _last_open_ticker = None

        _initialized = True