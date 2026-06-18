import re

import yfinance as yf
from shiny import ui, module, reactive

from app.modules.accounts_DAL import (
    fetch_accounts_summary, calc_accounts_summary,
    fetch_account_details, calc_account_details,
    execute_buy, execute_sell,
)
from app.modules.accounts_helpers import (
    _build_account_card_skeleton, _build_account_card_values,
    _build_position_row_skeleton, _build_position_row_values,
    _build_summary_html,
)
from app.modules.accounts_modals import modal_edit_position_html, modal_edit_position_js
from app.db import get_db, get_usd_krw, get_market_map, get_market_label, get_market_currency
from app.modules.components import fmt_krw, fmt_usd, fmt_pct, fmt_pnl, fmt_change, build_summary_header_dom
from app.price_signal import price_signal, daily_insert_signal
from scheduler.price_updater_common import get_market_status
from app.utils.display_diff import diff_display


def _notify_position_changed():
    try:
        from common.redis_store import recalc_today_row, publish_position_changed
        recalc_today_row()
        publish_position_changed()
    except Exception as e:
        print(f"[accounts] position_changed 신호 발행 실패 (무시): {e}")


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def accounts_ui():
    market_options = "".join(
        f'<option value="{m}">{m} ({get_market_label(m)})</option>'
        for m in get_market_map()
    )

    return ui.div(
        ui.tags.script("""
(function() {

  // ── id 조회 헬퍼 ───────────────────────────────────────────────────────
  window.acGetEl = function(id) {
    if (!id) return null;
    return document.getElementById(id) || document.querySelector('[id$="' + id + '"]');
  };

  function setDisplay(id, val) {
    var el = acGetEl(id);
    if (el && el.style) el.style.display = val;
  }
  function setText(id, text) {
    var el = acGetEl(id);
    if (el) el.textContent = text;
  }
  function setHtml(id, html) {
    var el = acGetEl(id);
    if (el) el.innerHTML = html;
  }

  // ── 모달 show/hide ─────────────────────────────────────────────────────
  window.acShowModal = function(id) { setDisplay(id, ''); };
  window.acHideModal = function(id) { setDisplay(id, 'none'); };

  // ── ac_list_init ───────────────────────────────────────────────────────
  Shiny.addCustomMessageHandler('ac_list_init', function(m) {
    _applySummary(m.summary);
    setText('ac-summary-label', m.summary.label);
    setHtml('ac-account-list', m.account_list_html);
    setDisplay('ac-back-btn', 'none');
    setDisplay('ac-list-view', '');
    setDisplay('ac-detail-view', 'none');
    _applyAccountCards(m.cards);
  });

  // ── ac_list_tick ───────────────────────────────────────────────────────
  Shiny.addCustomMessageHandler('ac_list_tick', function(m) {
    if (m.summary) {
      _applySummary(m.summary);
      setText('ac-summary-label', m.summary.label);
    }
    Object.keys(m).forEach(function(key) {
      if (key === 'summary') return;
      _applyOneCard(m[key]);
    });
  });

  function _applyAccountCards(cards) {
    Object.values(cards).forEach(function(c) { _applyOneCard(c); });
  }
  function _applyOneCard(c) {
    var totalEl = document.getElementById('ac-card-total-' + c.id);
    if (totalEl) totalEl.textContent = c.total;
    var pnlEl = document.getElementById('ac-card-pnl-' + c.id);
    if (pnlEl) { pnlEl.textContent = c.pnl_text; pnlEl.className = c.pnl_class; }
    var cashEl = document.getElementById('ac-card-cash-' + c.id);
    if (cashEl) cashEl.textContent = c.cash;
  }

  // ── ac_detail_init ─────────────────────────────────────────────────────
  Shiny.addCustomMessageHandler('ac_detail_init', function(m) {
    _applySummary(m.summary);
    setText('ac-summary-label', m.title);
    setHtml('ac-position-list', m.position_list_html);
    setDisplay('ac-back-btn', 'inline-block');
    setDisplay('ac-list-view', 'none');
    setDisplay('ac-detail-view', '');
    _applyPositions(m.positions);
  });

  // ── ac_detail_tick ─────────────────────────────────────────────────────
  Shiny.addCustomMessageHandler('ac_detail_tick', function(m) {
    if (m.summary) _applySummary(m.summary);
    Object.keys(m).forEach(function(key) {
      if (key === 'summary') return;
      _applyOnePosition(m[key]);
    });
  });

  function _applyPositions(positions) {
    Object.values(positions).forEach(function(p) { _applyOnePosition(p); });
  }
  function _applyOnePosition(p) {
    var amountEl = document.getElementById('ac-amount-' + p.id);
    if (amountEl) amountEl.textContent = p.amount;
    var priceEl = document.getElementById('ac-price-' + p.id);
    if (priceEl) {
      priceEl.textContent = p.price;
      priceEl.className   = p.chg_css;
      priceEl.style.marginRight = p.price ? '4px' : '0';
    }
    var chgEl = document.getElementById('ac-chg-' + p.id);
    if (chgEl) { chgEl.textContent = p.chg; chgEl.className = p.chg_css; }
    var avgEl = document.getElementById('ac-avgprice-' + p.id);
    if (avgEl) avgEl.textContent = p.avgprice || '';

    var pnlEl = document.getElementById('ac-pnlpct-' + p.id);
    if (pnlEl) { pnlEl.textContent = p.pnlpct || ''; pnlEl.className = p.pnlpct_css || ''; }

    var stEl = document.getElementById('ac-status-' + p.id);
    if (stEl) {
      stEl.textContent = p.status_dot ? p.status_dot + ' ' + p.status_txt : '';
      stEl.className   = 'ticker-status ' + p.status_cls;
    }
    // ── data-* 속성 갱신 (모달 오픈 시 최신값 반영) ──
    if (amountEl) {
      var parentEl = amountEl.closest('[data-pos-id]');
      if (parentEl) {
        if (p.avg_price !== undefined && p.avg_price !== null) {
          parentEl.setAttribute('data-avg-price', p.avg_price);
        }
        if (p.cash_amount !== undefined && p.cash_amount !== null) {
          parentEl.setAttribute('data-amount', p.cash_amount);
        }
      }
    }
  }

  function _applySummary(s) {
    setText('ac-summary-total', s.total);
    var pnlEl = acGetEl('ac-summary-pnl');
    if (pnlEl) { pnlEl.textContent = s.pnl_text; pnlEl.className = 'summary-delta ' + s.pnl_class; }
    setDisplay('ac-usd-wrap', s.usd_text ? 'flex' : 'none');
    var usdEl = acGetEl('ac-usd-text');
    if (usdEl) { usdEl.textContent = s.usd_text; usdEl.className = s.usd_css; }
  }

  // ── 현금 모달 ──────────────────────────────────────────────────────────
  var _editCashId = null;

  window.acOpenEditCashModal = function(el) {
    _editCashId = parseInt(el.getAttribute('data-pos-id'));
    var tEl = document.getElementById('ac-edit-cash-type');
    if (tEl) tEl.value = el.getAttribute('data-ticker');
    var aEl = document.getElementById('ac-edit-cash-amount');
    if (aEl) aEl.value = el.getAttribute('data-amount');
    acShowModal('ac-modal-edit-cash');
  };

  window.acTriggerEditCashSave = function() {
    var tEl = document.getElementById('ac-edit-cash-type');
    var aEl = document.getElementById('ac-edit-cash-amount');
    Shiny.setInputValue('accounts-btn_confirm_edit_cash', {
      pos_id:    _editCashId,
      cash_type: tEl ? tEl.value : 'KRW',
      amount:    aEl ? (parseFloat(aEl.value) || 0) : 0,
    }, {priority: 'event'});
    acHideModal('ac-modal-edit-cash');
  };

  window.acTriggerCashDelete = function() {
    if (confirm('현금을 삭제하시겠습니까?')) {
      Shiny.setInputValue('accounts-confirm_delete_cash',
        { pos_id: _editCashId }, {priority: 'event'});
      acHideModal('ac-modal-edit-cash');
    }
  };

  // ── 티커 자동조회 ──────────────────────────────────────────────────────
  window.acLookupTicker = function() {
    var tickerEl = document.getElementById('ac-new-pos-ticker');
    var ticker = tickerEl ? tickerEl.value.trim() : '';
    if (!ticker) return;
    var btn = document.getElementById('ac-new-pos-lookup-btn');
    if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
    Shiny.setInputValue('accounts-lookup_ticker', { ticker: ticker }, {priority: 'event'});
  };

  Shiny.addCustomMessageHandler('ac_ticker_lookup_result', function(m) {
    var btn = document.getElementById('ac-new-pos-lookup-btn');
    if (btn) { btn.textContent = '🔍'; btn.disabled = false; }
    if (m.name) {
      var nameEl = document.getElementById('ac-new-pos-name');
      if (nameEl) nameEl.value = m.name;
      if (m.market) {
        var marketEl = document.getElementById('ac-new-pos-market');
        if (marketEl) marketEl.value = m.market;
      }
    } else {
      alert('종목명을 찾지 못했습니다: ' + m.ticker);
    }
  });

""" + modal_edit_position_js() + """
})();
        """),

        # ── Summary 헤더 ──────────────────────────────────────────────────────
        build_summary_header_dom(
            id_prefix        = "ac",
            label_text       = "총자산",
            back_btn_onclick = "Shiny.setInputValue('accounts-btn_back', Math.random(), {priority: 'event'});",
        ),

        # ── 계좌 목록 화면 ────────────────────────────────────────────────────
        ui.div(
            {"id": "ac-list-view"},
            ui.div(
                ui.h4("계좌 목록", class_="section-heading"),
                ui.div({"id": "ac-account-list", "class": "ticker-list"}),
                ui.tags.button(
                    "+ 계좌 추가",
                    class_="btn-add",
                    onclick="acShowModal('ac-modal-add-account');",
                ),
                class_="page-inner",
            ),
        ),

        # ── 계좌 상세 화면 ────────────────────────────────────────────────────
        ui.div(
            {"id": "ac-detail-view", "style": "display:none;"},
            ui.div(
                ui.div({"id": "ac-position-list"}),
                ui.div(
                    ui.tags.button(
                        "+ 종목 추가",
                        class_="btn-add",
                        onclick="acShowModal('ac-modal-add-position');",
                    ),
                    ui.tags.button(
                        "+ 현금 추가",
                        class_="btn-add",
                        onclick="acShowModal('ac-modal-add-cash');",
                    ),
                    ui.tags.button(
                        "계좌 삭제",
                        class_="btn-account-delete-bottom",
                        onclick="if(confirm('계좌를 삭제하시겠습니까?')) Shiny.setInputValue('accounts-confirm_delete_account', Math.random(), {priority: 'event'});",
                    ),
                    style="margin-top:20px;",
                ),
                class_="page-inner",
            ),
        ),

        # ── 모달: 계좌 추가 ───────────────────────────────────────────────────
        ui.div(
            ui.div(
                ui.div(
                    ui.h4("계좌 추가", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon",
                            onclick="acHideModal('ac-modal-add-account');"),
                    class_="modal-header-row",
                ),
                ui.div(ui.tags.label("계좌명"),
                       ui.tags.input(id="ac-new-account-name", type="text",
                                     placeholder="예) 키움증권", class_="form-control")),
                ui.div(ui.tags.label("별명 (선택)"),
                       ui.tags.input(id="ac-new-account-alias", type="text",
                                     placeholder="예) 키움", class_="form-control")),
                ui.div(
                    ui.tags.input(id="ac-new-account-is-watch", type="checkbox", value="false"),
                    ui.tags.label("감시 계좌 (내 자산 아님)",
                                  **{"for": "ac-new-account-is-watch"}),
                    style="display:flex; align-items:center; gap:8px;",
                ),
                ui.tags.button(
                    "추가", class_="btn-add",
                    onclick=(
                        "Shiny.setInputValue('accounts-btn_confirm_add', {"
                        "  name: document.getElementById('ac-new-account-name').value,"
                        "  alias: document.getElementById('ac-new-account-alias').value,"
                        "  is_watch: document.getElementById('ac-new-account-is-watch').checked"
                        "}, {priority: 'event'});"
                        "acHideModal('ac-modal-add-account');"
                    ),
                ),
                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            id="ac-modal-add-account",
            class_="modal-overlay",
            style="display:none;",
            onclick="acHideModal('ac-modal-add-account');",
        ),

        # ── 모달: 종목 추가 ───────────────────────────────────────────────────
        ui.div(
            ui.div(
                ui.div(
                    ui.h4("종목 추가", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon",
                            onclick="acHideModal('ac-modal-add-position');"),
                    class_="modal-header-row",
                ),
                ui.div(
                    ui.tags.label("티커"),
                    ui.div(
                        ui.tags.input(id="ac-new-pos-ticker", type="text",
                                      placeholder="예) AAPL", class_="form-control",
                                      style="flex:1;",
                                      oninput="this.value=this.value.toUpperCase();"),
                        ui.tags.button("🔍", id="ac-new-pos-lookup-btn",
                                       style="margin-left:6px; padding:0; font-size:18px; background:none; border:none; outline:none; cursor:pointer; line-height:1; -webkit-appearance:none;",
                                       onclick="acLookupTicker();"),
                        style="display:flex; align-items:center;",
                    ),
                ),
                ui.div(ui.tags.label("종목명"),
                       ui.tags.input(id="ac-new-pos-name", type="text",
                                     placeholder="예) 애플", class_="form-control")),
                ui.div(ui.tags.label("시장"),
                       ui.tags.select(ui.HTML(market_options),
                                      id="ac-new-pos-market", class_="form-control")),
                ui.div(ui.tags.label("레버리지"),
                       ui.tags.select(
                           ui.tags.option("x1", value="1"),
                           ui.tags.option("x2", value="2"),
                           ui.tags.option("x3", value="3"),
                           id="ac-new-pos-leverage", class_="form-control",
                       )),
                ui.div(ui.tags.label("수량"),
                       ui.tags.input(id="ac-new-pos-qty", type="number",
                                     value="0", min="0", step="any", class_="form-control")),
                ui.tags.button(
                    "추가", class_="btn-add",
                    onclick=(
                        "Shiny.setInputValue('accounts-btn_confirm_add_position', {"
                        "  name: document.getElementById('ac-new-pos-name').value,"
                        "  ticker: document.getElementById('ac-new-pos-ticker').value,"
                        "  market: document.getElementById('ac-new-pos-market').value,"
                        "  leverage: document.getElementById('ac-new-pos-leverage').value,"
                        "  qty: parseFloat(document.getElementById('ac-new-pos-qty').value) || 0"
                        "}, {priority: 'event'});"
                        "acHideModal('ac-modal-add-position');"
                    ),
                ),
                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            id="ac-modal-add-position",
            class_="modal-overlay",
            style="display:none;",
            onclick="acHideModal('ac-modal-add-position');",
        ),

        # ── 모달: 현금 추가 ───────────────────────────────────────────────────
        ui.div(
            ui.div(
                ui.div(
                    ui.h4("현금 추가", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon",
                            onclick="acHideModal('ac-modal-add-cash');"),
                    class_="modal-header-row",
                ),
                ui.div(ui.tags.label("통화"),
                       ui.tags.select(
                           ui.tags.option("KRW (원화)", value="KRW"),
                           ui.tags.option("USD (달러)", value="USD"),
                           id="ac-new-cash-type", class_="form-control",
                       )),
                ui.div(ui.tags.label("금액"),
                       ui.tags.input(id="ac-new-cash-amount", type="number",
                                     value="0", min="0", step="any", class_="form-control")),
                ui.tags.button(
                    "추가", class_="btn-add",
                    onclick=(
                        "Shiny.setInputValue('accounts-btn_confirm_add_cash', {"
                        "  cash_type: document.getElementById('ac-new-cash-type').value,"
                        "  amount: parseFloat(document.getElementById('ac-new-cash-amount').value) || 0"
                        "}, {priority: 'event'});"
                        "acHideModal('ac-modal-add-cash');"
                    ),
                ),
                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            id="ac-modal-add-cash",
            class_="modal-overlay",
            style="display:none;",
            onclick="acHideModal('ac-modal-add-cash');",
        ),

        # ── 모달: 종목 수정 (탭 구조) ─────────────────────────────────────────
        modal_edit_position_html(market_options),

        # ── 모달: 현금 수정 ───────────────────────────────────────────────────
        ui.div(
            ui.div(
                ui.div(
                    ui.h4("현금 수정", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon",
                            onclick="acHideModal('ac-modal-edit-cash');"),
                    class_="modal-header-row",
                ),
                ui.div(ui.tags.label("통화"),
                       ui.tags.select(
                           ui.tags.option("KRW (원화)", value="KRW"),
                           ui.tags.option("USD (달러)", value="USD"),
                           id="ac-edit-cash-type", class_="form-control",
                       )),
                ui.div(ui.tags.label("금액"),
                       ui.tags.input(id="ac-edit-cash-amount", type="number",
                                     min="0", step="any", class_="form-control")),
                ui.tags.button("저장", class_="btn-add",
                               onclick="acTriggerEditCashSave();"),
                ui.tags.button("현금 삭제", class_="btn-modal-delete-bottom",
                               onclick="event.stopPropagation(); acTriggerCashDelete();"),
                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            id="ac-modal-edit-cash",
            class_="modal-overlay",
            style="display:none;",
            onclick="acHideModal('ac-modal-edit-cash');",
        ),

        class_="page-container",
    )


# ── Server ────────────────────────────────────────────────────────────────────

@module.server
def accounts_server(input, output, session, active_tab: reactive.value = None):

    initialized      = reactive.value(False)
    selected_account = reactive.value(None)
    refresh          = reactive.value(0)

    ns_str = session.ns("_")[:-1]  # "accounts-" 접두사

    _last_accounts:  list = []
    _last_positions: list = []
    _last_display:   dict = {}

    # ── DB 캐시 (price_signal 비의존, 구조만) ───────────────────────────────

    @reactive.calc
    def _db_accounts():
        refresh()
        return fetch_accounts_summary()  # 시세 없음, 구조만

    @reactive.calc
    def _db_detail():
        refresh()
        acc_id = selected_account()
        if acc_id is None:
            return None
        return fetch_account_details(acc_id)  # 시세 없음, 구조만

    # ── 화면 갱신 ─────────────────────────────────────────────────────────────

    @reactive.effect
    async def _send_update():
        nonlocal _last_accounts, _last_positions, _last_display
        price_signal.get()
        daily_insert_signal.get()

        if initialized.get() and active_tab and active_tab.get() != "accounts":
            return

        usd_rate_val, usd_chg = get_usd_krw()
        acc_id = selected_account()

        if acc_id is None:
            from common.redis_store import get_all_prices
            prices   = get_all_prices()
            accounts = calc_accounts_summary(_db_accounts(), prices, usd_rate_val)
            # accounts: [(id, name, alias, total, cash, is_watch, prev_total), ...]
            normal   = [a for a in accounts if not a[5]]
            watch    = [a for a in accounts if a[5]]

            total_sum       = sum(a[3] for a in normal)
            yesterday_total = sum(a[6] for a in normal)
            pnl_sum         = total_sum - yesterday_total
            pnl_pct_sum     = (pnl_sum / yesterday_total * 100) if yesterday_total > 0 else 0
            summary = _build_summary_html("총자산", total_sum, pnl_sum, pnl_pct_sum,
                                          usd_rate_val, usd_chg)

            card_values = {str(a[0]): _build_account_card_values(a) for a in accounts}
            current_accounts = [a[0] for a in accounts]
            structure_changed = (current_accounts != _last_accounts)

            if structure_changed:
                _last_accounts = current_accounts
                _last_display.clear()
                if normal:
                    skeleton_html = "".join(
                        _build_account_card_skeleton(a, ns_str) for a in normal
                    )
                else:
                    skeleton_html = '<p style="color:#888; padding:16px 0;">등록된 계좌가 없습니다.</p>'
                if watch:
                    skeleton_html += '<h4 class="section-heading">감시 계좌</h4>'
                    skeleton_html += "".join(
                        _build_account_card_skeleton(a, ns_str) for a in watch
                    )
                await session.send_custom_message("ac_list_init", {
                    "summary":           summary,
                    "account_list_html": skeleton_html,
                    "cards":             card_values,
                })
            else:
                current = {"summary": summary, **card_values}
                diff = diff_display(current, _last_display)
                if diff:
                    await session.send_custom_message("ac_list_tick", diff)

        else:
            db_detail = _db_detail()
            if db_detail is None:
                return
            acc_row, db_rows = db_detail

            from common.redis_store import get_all_prices
            prices = get_all_prices()
            acc, positions, usd_rate = calc_account_details(acc_row, db_rows, prices, usd_rate_val)

            prev_total = float(acc[3])
            total_sum  = 0
            for pos in positions:
                _, ticker, qty, _, price, _, t_market, _, _ = pos
                qty_f   = float(qty   or 0)
                price_f = float(price or 0)
                rate = usd_rate if (get_market_currency(t_market) == "USD" or ticker == "USD") else 1
                amt  = qty_f * (price_f if ticker not in ('KRW', 'USD') else 1) * rate
                total_sum += amt

            pnl_sum     = total_sum - prev_total
            pnl_pct_sum = (pnl_sum / prev_total * 100) if prev_total > 0 else 0
            title       = acc[0] + (f" ({acc[1]})" if acc[1] else "")
            summary     = _build_summary_html("계좌자산", total_sum, pnl_sum, pnl_pct_sum,
                                              usd_rate_val, usd_chg)

            pos_values = {str(p[0]): _build_position_row_values(p, usd_rate) for p in positions}
            current_positions = [p[0] for p in positions]
            structure_changed = (current_positions != _last_positions)

            if structure_changed:
                _last_positions = current_positions
                _last_display.clear()
                if positions:
                    skeleton_html = "".join(
                        _build_position_row_skeleton(p, ns_str) for p in positions
                    )
                else:
                    skeleton_html = '<p style="color:#888; padding:16px;">종목이 없습니다.</p>'
                await session.send_custom_message("ac_detail_init", {
                    "summary":            summary,
                    "title":              title,
                    "position_list_html": skeleton_html,
                    "positions":          pos_values,
                })
            else:
                current = {"summary": summary, **pos_values}
                diff = diff_display(current, _last_display)
                if diff:
                    await session.send_custom_message("ac_detail_tick", diff)

        initialized.set(True)

    # ── 계좌 카드 클릭 ────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.card_clicked)
    def _handle_card_click():
        nonlocal _last_display, _last_positions
        acc_id = input.card_clicked()
        if acc_id is not None:
            _last_display.clear()
            _last_positions = []
            selected_account.set(acc_id)

    # ── 뒤로가기 ──────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_back)
    def _go_back():
        nonlocal _last_display, _last_accounts
        _last_display.clear()
        _last_accounts = []
        selected_account.set(None)

    # ── 티커 자동조회 ─────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.lookup_ticker)
    async def _lookup_ticker():
        payload = input.lookup_ticker()
        ticker  = str(payload.get("ticker", "")).strip().upper()
        if not ticker:
            return

        is_kr = bool(re.search(r'\d', ticker)) and '.' not in ticker

        def _fetch(t):
            try:
                info = yf.Ticker(t).info
                name = info.get("longName") or info.get("shortName") or ""
                return name, info.get("exchange", ""), info.get("quoteType", "")
            except Exception:
                return "", "", ""

        if is_kr:
            name, exchange, qtype = _fetch(ticker + ".KS")
            if not name:
                name, exchange, qtype = _fetch(ticker + ".KQ")
        else:
            name, exchange, qtype = _fetch(ticker)

        exchange_map = {
            "KSC": "KR", "KOE": "KR",
            "NMS": "NAS", "NGM": "NAS", "NCM": "NAS",
            "NYQ": "NYS",
            "ASE": "AMS",
            "NIM": "INDEX",
        }
        if qtype == "CRYPTOCURRENCY":
            market = "CRYPTO"
        elif qtype == "INDEX":
            market = "INDEX"
        else:
            market = exchange_map.get(exchange, "")

        await session.send_custom_message("ac_ticker_lookup_result", {
            "ticker": ticker,
            "name":   name,
            "market": market,
        })

    # ── 계좌 추가 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_add)
    def _add_account():
        payload  = input.btn_confirm_add()
        if not payload:
            return
        name     = str(payload.get("name", "")).strip()
        alias    = str(payload.get("alias", "")).strip() or None
        is_watch = bool(payload.get("is_watch", False))
        if not name:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO accounts (name, alias, is_watch) VALUES (%s, %s, %s)",
                (name, alias, is_watch)
            )
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 계좌 삭제 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.confirm_delete_account)
    def _delete_account():
        acc_id = selected_account()
        if acc_id is None:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM accounts WHERE id = %s", (acc_id,))
            conn.commit()
            cur.close()
        selected_account.set(None)
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 종목 추가 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_add_position)
    def _add_position():
        payload  = input.btn_confirm_add_position()
        if not payload:
            return
        name     = str(payload.get("name", "")).strip()
        ticker   = str(payload.get("ticker", "")).strip().upper()
        market   = str(payload.get("market", ""))
        leverage = int(payload.get("leverage", 1))
        qty      = float(payload.get("qty", 0))
        acc_id   = selected_account()
        if not ticker or not acc_id:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT ticker FROM tickers WHERE ticker = %s", (ticker,))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO tickers (ticker, name, market, leverage, is_manual) "
                    "VALUES (%s, %s, %s, %s, false)",
                    (ticker, name or ticker, market, leverage)
                )
            cur.execute(
                "INSERT INTO positions (account_id, ticker, quantity) VALUES (%s, %s, %s)",
                (acc_id, ticker, qty)
            )
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 현금 추가 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_add_cash)
    def _add_cash():
        payload   = input.btn_confirm_add_cash()
        if not payload:
            return
        cash_type = str(payload.get("cash_type", "KRW"))
        amount    = float(payload.get("amount", 0))
        acc_id    = selected_account()
        if not acc_id:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO positions (account_id, ticker, quantity) VALUES (%s, %s, %s)",
                (acc_id, cash_type, amount)
            )
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 종목 수정 (정보 탭 저장) ──────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_edit_position)
    def _edit_position():
        payload   = input.btn_confirm_edit_position()
        pos_id    = payload.get("pos_id")
        if not pos_id:
            return
        name      = str(payload.get("name", "")).strip()
        market    = str(payload.get("market", ""))
        leverage  = int(payload.get("leverage", 1))
        qty       = float(payload.get("qty", 0))
        avg_price = payload.get("avg_price")  # None 허용
        if avg_price is not None:
            avg_price = float(avg_price)

        with get_db() as conn:
            cur = conn.cursor()
            if avg_price is not None:
                cur.execute(
                    "UPDATE positions SET quantity = %s, avg_price = %s WHERE id = %s",
                    (qty, avg_price, pos_id)
                )
            else:
                cur.execute(
                    "UPDATE positions SET quantity = %s WHERE id = %s",
                    (qty, pos_id)
                )
            cur.execute("""
                UPDATE tickers SET name = %s, market = %s, leverage = %s
                WHERE ticker = (SELECT ticker FROM positions WHERE id = %s)
            """, (name, market, leverage, pos_id))
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 매수 ──────────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_buy)
    def _buy():
        payload = input.btn_confirm_buy()
        pos_id  = payload.get("pos_id")
        qty     = float(payload.get("qty", 0))
        price   = float(payload.get("price", 0))
        if not pos_id or qty <= 0 or price <= 0:
            return
        usd_markets = {m for m, v in get_market_map().items() if v.get("currency") == "USD"}
        try:
            execute_buy(pos_id, qty, price, usd_markets)
        except Exception as e:
            print(f"[accounts] 매수 처리 오류: {e}")
            return
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 매도 ──────────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_sell)
    def _sell():
        payload = input.btn_confirm_sell()
        pos_id  = payload.get("pos_id")
        qty     = float(payload.get("qty", 0))
        price   = float(payload.get("price", 0))
        if not pos_id or qty <= 0 or price <= 0:
            return
        usd_markets = {m for m, v in get_market_map().items() if v.get("currency") == "USD"}
        try:
            execute_sell(pos_id, qty, price, usd_markets)
        except ValueError as e:
            print(f"[accounts] 매도 처리 오류: {e}")
            return
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 종목 삭제 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.confirm_delete_position)
    def _delete_position():
        payload = input.confirm_delete_position()
        pos_id  = payload.get("pos_id") if isinstance(payload, dict) else None
        if not pos_id:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT ticker FROM positions WHERE id = %s", (pos_id,))
            row    = cur.fetchone()
            ticker = row[0] if row else None
            cur.execute("DELETE FROM positions WHERE id = %s", (pos_id,))
            if ticker:
                cur.execute(
                    "SELECT 1 FROM tickers WHERE ticker = %s AND is_manual = false", (ticker,)
                )
                if cur.fetchone():
                    cur.execute("SELECT COUNT(*) FROM positions WHERE ticker = %s", (ticker,))
                    if cur.fetchone()[0] == 0:
                        cur.execute("DELETE FROM tickers WHERE ticker = %s", (ticker,))
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 현금 수정 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_edit_cash)
    def _edit_cash():
        payload   = input.btn_confirm_edit_cash()
        pos_id    = payload.get("pos_id")
        if not pos_id:
            return
        cash_type = str(payload.get("cash_type", "KRW"))
        amount    = float(payload.get("amount", 0))
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE positions SET ticker = %s, quantity = %s WHERE id = %s",
                (cash_type, amount, pos_id)
            )
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 현금 삭제 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.confirm_delete_cash)
    def _delete_cash():
        payload = input.confirm_delete_cash()
        pos_id  = payload.get("pos_id") if isinstance(payload, dict) else None
        if not pos_id:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM positions WHERE id = %s", (pos_id,))
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_position_changed()