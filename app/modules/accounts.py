from shiny import ui, module, reactive

from app.modules.accounts_DAL import fetch_accounts_summary, fetch_account_details
from app.db import get_connection, get_db, get_usd_krw, get_config, get_market_currency, get_market_map, get_market_label
from app.modules.components import fmt_krw, fmt_usd, fmt_pct, fmt_pnl, fmt_change
from app.price_signal import price_signal, daily_insert_signal
from scheduler.price_updater_common import get_market_status
from app.utils.display_diff import diff_display


def _notify_price_updated():
    try:
        conn = get_connection()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("NOTIFY price_updated")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[accounts] NOTIFY price_updated 실패 (무시): {e}")

    try:
        from common.redis_store import recalc_today_row
        recalc_today_row()
    except Exception as e:
        print(f"[accounts] recalc_today_row 실패 (무시): {e}")


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _ticker_to_id(ticker: str) -> str:
    return ticker.replace("-", "_").replace("^", "_").replace("=", "_")


def _build_account_card_skeleton(acc, ns_str):
    """계좌 카드 골격 HTML — 구성 변경 시 1회 전송"""
    a_id, name, alias, total, cash, is_watch, prev_total = acc
    alias_str = f" ({alias})" if alias else ""
    return (
        f'<div class="asset-card" id="ac-card-{a_id}" '
        f'onclick="Shiny.setInputValue(\'{ns_str}card_clicked\', {a_id}, {{priority: \'event\'}});">'
        f'  <div>'
        f'    <span class="ticker-name">{name}</span>'
        f'    <span class="account-alias">{alias_str}</span>'
        f'  </div>'
        f'  <div>'
        f'    <div class="amount-large" id="ac-card-total-{a_id}"></div>'
        f'    <div class="card-pnl-row">'
        f'      <span id="ac-card-pnl-{a_id}" class="summary-delta"></span>'
        f'      <span class="card-cash-label">현금 <span id="ac-card-cash-{a_id}"></span></span>'
        f'    </div>'
        f'  </div>'
        f'</div>'
    )

def _build_account_card_values(acc):
    """계좌 카드 가변값 dict — 매 tick diff 비교용"""
    a_id, name, alias, total, cash, is_watch, prev_total = acc
    pnl = total - prev_total
    pnl_pct = (pnl / prev_total * 100) if prev_total > 0 else 0
    pnl_text, pnl_class = fmt_pnl(pnl, pnl_pct)
    return {
        "id":        a_id,
        "total":     fmt_krw(total),
        "pnl_text":  pnl_text,
        "pnl_class": pnl_class,
        "cash":      fmt_krw(cash),
    }

def _build_position_row_skeleton(pos, ns_str):
    """종목 행 골격 HTML — 구성 변경 시 1회 전송"""
    pos_id, ticker, qty, tname, price, chg_pct, t_market, leverage = pos
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
        display_name = tname or ticker
        qty_str      = f"{qty_f:g}주"
        change_html  = (
            f'<div class="ticker-change">'
            f'<span id="ac-price-{pos_id}" style="margin-right:4px;"></span>'
            f'<span id="ac-chg-{pos_id}"></span>'
            f'</div>'
        )

    status_html = "" if is_cash else f'<span id="ac-status-{pos_id}" class="ticker-status"></span>'

    if is_cash:
        onclick_js = "acOpenEditCashModal(this);"
        data_attrs = f'data-pos-id="{pos_id}" data-ticker="{ticker}" data-amount="{qty_f}"'
    else:
        data_attrs = (
            f'data-pos-id="{pos_id}" data-ticker="{ticker}" '
            f'data-name="{tname or ""}" data-market="{t_market or "KR"}" '
            f'data-leverage="{leverage}" data-qty="{qty_f}"'
        )
        onclick_js = "acOpenEditPositionModal(this);"

    return (
        f'<div style="cursor:pointer;" onclick="{onclick_js}" {data_attrs}>'
        f'  <div class="ticker-row" id="ac-row-{pos_id}">'
        f'    <div>'
        f'      <div class="lev-name-wrap">'
        f'        {lev_html}'
        f'        <span class="ticker-name">{display_name}</span>'
        f'        {status_html}'
        f'      </div>'
        f'      <div class="ticker-qty">{qty_str}</div>'
        f'    </div>'
        f'    <div>'
        f'      <div class="ticker-amount" id="ac-amount-{pos_id}"></div>'
        f'      {change_html}'
        f'    </div>'
        f'  </div>'
        f'</div>'
    )

def _build_position_row_values(pos, usd_rate):
    """종목 행 가변값 dict — 매 tick diff 비교용"""
    pos_id, ticker, qty, tname, price, chg_pct, t_market, leverage = pos
    is_cash  = ticker in ('KRW', 'USD')
    leverage = int(leverage) if leverage else 1
    qty_f    = float(qty   or 0)
    price_f  = float(price or 0)
    chg_f    = float(chg_pct or 0)

    if ticker == 'KRW':
        amount_str = fmt_krw(qty_f)
    elif ticker == 'USD':
        amount_str = fmt_krw(qty_f * usd_rate)
    else:
        currency   = get_market_currency(t_market)
        rate       = usd_rate if currency == "USD" else 1
        amount_str = fmt_krw(qty_f * price_f * rate)

    if is_cash:
        price_str = chg_str = chg_css = ""
    else:
        currency = get_market_currency(t_market)
        price_str, chg_str, chg_css = fmt_change(price_f, chg_f, currency=currency)

    status_dot = status_text = status_cls = ""
    if not is_cash and t_market:
        status = get_market_status(t_market)
        dot_map = {
            "open":    ("●", "Open",       "status-open"),
            "pre":     ("●", "Pre",        "status-pre"),
            "after":   ("●", "After",      "status-after"),
            "closing": ("●", "Closing...", "status-closing"),
        }
        status_dot, status_text, status_cls = dot_map.get(status, ("○", "Closed", "status-closed"))

    return {
        "id":         pos_id,
        "amount":     amount_str,
        "price":      price_str,
        "chg":        chg_str,
        "chg_css":    chg_css,
        "status_dot": status_dot,
        "status_txt": status_text,
        "status_cls": status_cls,
    }


def _build_summary_html(label, total_asset, pnl, pnl_pct, usd_rate=None, usd_chg=None):
    """summary header HTML"""
    pnl_text, pnl_class = fmt_pnl(pnl, pnl_pct)
    usd_html = ""
    if usd_rate and usd_chg is not None:
        usd_css  = "positive" if usd_chg >= 0 else "negative"
        usd_html = (
            f'<span style="color:#888888;">USD </span>'
            f'<span class="{usd_css}">{usd_rate:,.2f} ({fmt_pct(usd_chg)})</span>'
        )
    return {
        "label":      label,
        "total":      fmt_krw(total_asset),
        "pnl_text":   pnl_text,
        "pnl_class":  pnl_class,
        "usd_html":   usd_html,
    }


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def accounts_ui():
    market_choices = {m: f"{m} ({get_market_label(m)})" for m in get_market_map()}
    market_options = "".join(f'<option value="{m}">{m} ({get_market_label(m)})</option>' for m in get_market_map())

    return ui.div(
        ui.tags.script("""
(function() {

  // ── id 조회 헬퍼: Shiny 모듈 네임스페이스 접두사 방어 ──────
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

  // ── ac_list_init: 계좌 구성 변경 시 골격 통째 교체 ──────────
  Shiny.addCustomMessageHandler('ac_list_init', function(m) {
    _applySummary(m.summary);
    setText('ac-summary-label', m.summary.label);
    setHtml('ac-account-list', m.account_list_html);
    setDisplay('ac-back-btn', 'none');
    setDisplay('ac-list-view', '');
    setDisplay('ac-detail-view', 'none');
    _applyAccountCards(m.cards);
  });

  // ── ac_list_tick: 변경된 key만 patch ─────────────────────────
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
    if (pnlEl) { pnlEl.textContent = c.pnl_text; pnlEl.className = 'summary-delta ' + c.pnl_class; }
    var cashEl = document.getElementById('ac-card-cash-' + c.id);
    if (cashEl) cashEl.textContent = c.cash;
  }

  // ── ac_detail_init: 종목 구성 변경 시 골격 통째 교체 ─────────
  Shiny.addCustomMessageHandler('ac_detail_init', function(m) {
    _applySummary(m.summary);
    setText('ac-summary-label', m.title);
    setHtml('ac-position-list', m.position_list_html);
    setDisplay('ac-back-btn', 'inline-block');
    setDisplay('ac-list-view', 'none');
    setDisplay('ac-detail-view', '');
    _applyPositions(m.positions);
  });

  // ── ac_detail_tick: 변경된 key만 patch ───────────────────────
  Shiny.addCustomMessageHandler('ac_detail_tick', function(m) {
    if (m.summary) {
      _applySummary(m.summary);
    }
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

    var stEl = document.getElementById('ac-status-' + p.id);
    if (stEl) {
      stEl.textContent = p.status_dot ? p.status_dot + ' ' + p.status_txt : '';
      stEl.className   = 'ticker-status ' + p.status_cls;
    }
  }

  function _applySummary(s) {
    setText('ac-summary-total', s.total);
    var pnlEl = acGetEl('ac-summary-pnl');
    if (pnlEl) { pnlEl.textContent = s.pnl_text; pnlEl.className = 'summary-delta ' + s.pnl_class; }
    setDisplay('ac-usd-wrap', s.usd_html ? '' : 'none');
    setHtml('ac-usd-text', s.usd_html);
  }

  // ── 모달 show/hide ─────────────────────────────────────────
  window.acShowModal = function(id) { setDisplay(id, ''); };
  window.acHideModal = function(id) { setDisplay(id, 'none'); };

  // ── 종목/현금 클릭 → data-* 읽어 모달 오픈 (서버 왕복 없음) ─
  var _editPosId  = null;
  var _editCashId = null;

  window.acOpenEditPositionModal = function(el) {
    _editPosId = parseInt(el.getAttribute('data-pos-id'));
    var tEl = acGetEl('ac-edit-pos-ticker');  if (tEl) tEl.textContent = el.getAttribute('data-ticker');
    var nEl = acGetEl('ac-edit-pos-name');    if (nEl) nEl.value       = el.getAttribute('data-name');
    var mEl = acGetEl('ac-edit-pos-market');  if (mEl) mEl.value       = el.getAttribute('data-market');
    var lEl = acGetEl('ac-edit-pos-leverage'); if (lEl) lEl.value      = el.getAttribute('data-leverage');
    var qEl = acGetEl('ac-edit-pos-qty');     if (qEl) qEl.value       = el.getAttribute('data-qty');
    acShowModal('ac-modal-edit-position');
  };

  window.acOpenEditCashModal = function(el) {
    _editCashId = parseInt(el.getAttribute('data-pos-id'));
    var tEl = acGetEl('ac-edit-cash-type');   if (tEl) tEl.value = el.getAttribute('data-ticker');
    var aEl = acGetEl('ac-edit-cash-amount'); if (aEl) aEl.value = el.getAttribute('data-amount');
    acShowModal('ac-modal-edit-cash');
  };

  // ── 종목 수정/삭제 트리거 ──────────────────────────────────
  window.acTriggerEditPositionSave = function() {
    var nEl = acGetEl('ac-edit-pos-name');
    var mEl = acGetEl('ac-edit-pos-market');
    var lEl = acGetEl('ac-edit-pos-leverage');
    var qEl = acGetEl('ac-edit-pos-qty');
    Shiny.setInputValue('accounts-btn_confirm_edit_position', {
      pos_id:   _editPosId,
      name:     nEl ? nEl.value : '',
      market:   mEl ? mEl.value : 'KR',
      leverage: lEl ? lEl.value : '1',
      qty:      qEl ? (parseFloat(qEl.value) || 0) : 0
    }, {priority: 'event'});
    acHideModal('ac-modal-edit-position');
  };

  window.acTriggerPositionDelete = function() {
    if (confirm('종목을 삭제하시겠습니까?')) {
      Shiny.setInputValue('accounts-confirm_delete_position', { pos_id: _editPosId }, {priority: 'event'});
      acHideModal('ac-modal-edit-position');
    }
  };

  // ── 현금 수정/삭제 트리거 ──────────────────────────────────
  window.acTriggerEditCashSave = function() {
    var tEl = acGetEl('ac-edit-cash-type');
    var aEl = acGetEl('ac-edit-cash-amount');
    Shiny.setInputValue('accounts-btn_confirm_edit_cash', {
      pos_id:    _editCashId,
      cash_type: tEl ? tEl.value : 'KRW',
      amount:    aEl ? (parseFloat(aEl.value) || 0) : 0
    }, {priority: 'event'});
    acHideModal('ac-modal-edit-cash');
  };

  window.acTriggerCashDelete = function() {
    if (confirm('현금을 삭제하시겠습니까?')) {
      Shiny.setInputValue('accounts-confirm_delete_cash', { pos_id: _editCashId }, {priority: 'event'});
      acHideModal('ac-modal-edit-cash');
    }
  };

  // ── 티커 → 종목명 자동조회 ────────────────────────────────
  window.acLookupTicker = function() {
    var tickerEl = acGetEl('ac-new-pos-ticker');
    var ticker = tickerEl ? tickerEl.value.trim() : '';
    if (!ticker) return;
    var btn = acGetEl('ac-new-pos-lookup-btn');
    if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
    Shiny.setInputValue('accounts-lookup_ticker', { ticker: ticker }, {priority: 'event'});
  };

  Shiny.addCustomMessageHandler('ac_ticker_lookup_result', function(m) {
    var btn = acGetEl('ac-new-pos-lookup-btn');
    if (btn) { btn.textContent = '🔍'; btn.disabled = false; }
    if (m.name) {
      var nameEl = acGetEl('ac-new-pos-name');
      if (nameEl) nameEl.value = m.name;
      if (m.market) {
        var marketEl = acGetEl('ac-new-pos-market');
        if (marketEl) marketEl.value = m.market;
      }
    } else {
      alert('종목명을 찾지 못했습니다: ' + m.ticker);
    }
  });

})();
        """),

        # ── Summary 헤더 (목록/상세 공용) ─────────────────────────────────────
        ui.div(
            {"class": "total-summary"},
            ui.div(
                ui.tags.button(
                    "‹",
                    id="ac-back-btn",
                    class_="summary-label",
                    style="display:none; background:none; border:none; padding:0; margin-right:6px; cursor:pointer; vertical-align:middle; line-height:1; font-family:inherit;",
                    onclick="Shiny.setInputValue('accounts-btn_back', Math.random(), {priority: 'event'});",
                ),
                ui.span("총자산", id="ac-summary-label", class_="summary-label", style="vertical-align:middle;"),
                style="display:flex; align-items:center; height:20px; margin-bottom:4px;",
            ),
            ui.div("–", id="ac-summary-total",  class_="summary-amount"),
            ui.div(
                ui.span("–", id="ac-summary-pnl", class_="summary-delta"),
                ui.span(
                    {"id": "ac-usd-wrap", "style": "display:none;"},
                    ui.span({"id": "ac-usd-text", "class": "summary-usd"}),
                ),
                class_="summary-delta-row",
            ),
        ),

        # ── 계좌 목록 화면 ────────────────────────────────────────────────────
        ui.div(
            {"id": "ac-list-view"},
            ui.div(
                ui.h4("계좌 목록", class_="section-heading"),
                ui.div({"id": "ac-account-list"}),
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
                    ui.span("✕", class_="modal-close-icon", onclick="acHideModal('ac-modal-add-account');"),
                    class_="modal-header-row",
                ),
                ui.div(ui.tags.label("계좌명"), ui.tags.input(id="ac-new-account-name", type="text", placeholder="예) 키움증권", class_="form-control")),
                ui.div(ui.tags.label("별명 (선택)"), ui.tags.input(id="ac-new-account-alias", type="text", placeholder="예) 키움", class_="form-control")),
                ui.div(
                    ui.tags.input(id="ac-new-account-is-watch", type="checkbox", value="false"),
                    ui.tags.label("감시 계좌 (내 자산 아님)", **{"for": "ac-new-account-is-watch"}),
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
                    ui.span("✕", class_="modal-close-icon", onclick="acHideModal('ac-modal-add-position');"),
                    class_="modal-header-row",
                ),
                ui.div(
                    ui.tags.label("티커"),
                    ui.div(
                        ui.tags.input(id="ac-new-pos-ticker", type="text", placeholder="예) AAPL",
                            class_="form-control", style="flex:1;",
                            oninput="this.value=this.value.toUpperCase();",
                        ),
                        ui.tags.button("🔍", id="ac-new-pos-lookup-btn",
                            style="margin-left:6px; padding:0; font-size:18px; background:none; border:none; outline:none; cursor:pointer; line-height:1; -webkit-appearance:none;",
                            onclick="acLookupTicker();",
                        ),
                        style="display:flex; align-items:center;",
                    ),
                ),
                ui.div(ui.tags.label("종목명"), ui.tags.input(id="ac-new-pos-name", type="text", placeholder="예) 애플", class_="form-control")),
                ui.div(ui.tags.label("시장"),   ui.tags.select(ui.HTML(market_options), id="ac-new-pos-market", class_="form-control")),
                ui.div(ui.tags.label("레버리지"),
                    ui.tags.select(
                        ui.tags.option("x1", value="1"),
                        ui.tags.option("x2", value="2"),
                        ui.tags.option("x3", value="3"),
                        id="ac-new-pos-leverage", class_="form-control",
                    ),
                ),
                ui.div(ui.tags.label("수량"), ui.tags.input(id="ac-new-pos-qty", type="number", value="0", min="0", step="any", class_="form-control")),
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
                    ui.span("✕", class_="modal-close-icon", onclick="acHideModal('ac-modal-add-cash');"),
                    class_="modal-header-row",
                ),
                ui.div(ui.tags.label("통화"),
                    ui.tags.select(
                        ui.tags.option("KRW (원화)", value="KRW"),
                        ui.tags.option("USD (달러)", value="USD"),
                        id="ac-new-cash-type", class_="form-control",
                    ),
                ),
                ui.div(ui.tags.label("금액"), ui.tags.input(id="ac-new-cash-amount", type="number", value="0", min="0", step="any", class_="form-control")),
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

        # ── 모달: 종목 수정 ───────────────────────────────────────────────────
        ui.div(
            ui.div(
                ui.div(
                    ui.h4("종목 수정", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon", onclick="acHideModal('ac-modal-edit-position');"),
                    class_="modal-header-row",
                ),
                ui.p("", id="ac-edit-pos-ticker", class_="ticker-readonly"),
                ui.div(ui.tags.label("종목명"),   ui.tags.input(id="ac-edit-pos-name", type="text", class_="form-control")),
                ui.div(ui.tags.label("시장"),     ui.tags.select(ui.HTML(market_options), id="ac-edit-pos-market", class_="form-control")),
                ui.div(ui.tags.label("레버리지"),
                    ui.tags.select(
                        ui.tags.option("x1", value="1"),
                        ui.tags.option("x2", value="2"),
                        ui.tags.option("x3", value="3"),
                        id="ac-edit-pos-leverage", class_="form-control",
                    ),
                ),
                ui.div(ui.tags.label("수량"), ui.tags.input(id="ac-edit-pos-qty", type="number", min="0", step="any", class_="form-control")),
                ui.tags.button("저장", class_="btn-add", onclick="acTriggerEditPositionSave();"),
                ui.tags.button("종목 삭제", class_="btn-modal-delete-bottom", onclick="event.stopPropagation(); acTriggerPositionDelete();"),
                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            id="ac-modal-edit-position",
            class_="modal-overlay",
            style="display:none;",
            onclick="acHideModal('ac-modal-edit-position');",
        ),

        # ── 모달: 현금 수정 ───────────────────────────────────────────────────
        ui.div(
            ui.div(
                ui.div(
                    ui.h4("현금 수정", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon", onclick="acHideModal('ac-modal-edit-cash');"),
                    class_="modal-header-row",
                ),
                ui.div(ui.tags.label("통화"),
                    ui.tags.select(
                        ui.tags.option("KRW (원화)", value="KRW"),
                        ui.tags.option("USD (달러)", value="USD"),
                        id="ac-edit-cash-type", class_="form-control",
                    ),
                ),
                ui.div(ui.tags.label("금액"), ui.tags.input(id="ac-edit-cash-amount", type="number", min="0", step="any", class_="form-control")),
                ui.tags.button("저장", class_="btn-add", onclick="acTriggerEditCashSave();"),
                ui.tags.button("현금 삭제", class_="btn-modal-delete-bottom", onclick="event.stopPropagation(); acTriggerCashDelete();"),
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

    selected_account = reactive.value(None)
    refresh          = reactive.value(0)

    ns_str = session.ns("_")[:-1]  # "accounts-" 접두사

    _last_accounts: list = []   # 계좌 구성 캐시
    _last_positions: list = []  # 종목 구성 캐시
    _last_display: dict = {}    # diff 상태

    # ── 화면 갱신 ─────────────────────────────────────────────────────────────

    # ── 시세/daily insert 수신 시 계좌 화면 갱신 ────────────────────────────
    # price_signal 마다 Redis 시세를 새로 읽어 계좌별 평가액·등락률을 갱신.
    # daily_insert_signal 수신 시 prev_total_asset 이 갱신됐으므로 DB를 새로 읽어야
    # 전일대비 손익이 정확해진다.
    # 계좌 목록/상세 구성이 바뀌면 init(골격 통째 교체), 아니면 tick(변경 필드만 패치).
    # 탭 비활성 시 스킵: 보이지 않는 DOM을 패치하는 건 낭비이고,
    # 탭 활성화 순간 active_tab 이 "accounts"로 바뀌면서 자동으로 재실행된다.
    @reactive.effect
    async def _send_update():
        nonlocal _last_accounts, _last_positions, _last_display
        price_signal.get()
        daily_insert_signal.get()
        refresh()

        if active_tab and active_tab.get() != "accounts":
            return

        usd_rate_val, usd_chg = get_usd_krw()
        acc_id = selected_account()

        if acc_id is None:
            # ── 계좌 목록 화면 ────────────────────────────────
            accounts = fetch_accounts_summary()
            normal   = [a for a in accounts if not a[5]]
            watch    = [a for a in accounts if a[5]]

            total_sum       = sum(a[3] for a in normal)
            yesterday_total = sum(a[6] for a in normal)
            pnl_sum         = total_sum - yesterday_total
            pnl_pct_sum     = (pnl_sum / yesterday_total * 100) if yesterday_total > 0 else 0
            summary = _build_summary_html("총자산", total_sum, pnl_sum, pnl_pct_sum, usd_rate_val, usd_chg)

            # 계좌별 값 dict (account_id → values)
            card_values = {str(a[0]): _build_account_card_values(a) for a in accounts}

            current_accounts = [a[0] for a in accounts]
            structure_changed = (current_accounts != _last_accounts)

            if structure_changed:
                _last_accounts = current_accounts
                _last_display.clear()
                # 골격 HTML 생성
                if normal:
                    skeleton_html = "".join(_build_account_card_skeleton(a, ns_str) for a in normal)
                else:
                    skeleton_html = '<p style="color:#888; padding:16px 0;">등록된 계좌가 없습니다.</p>'
                if watch:
                    skeleton_html += '<h4 class="section-heading">감시 계좌</h4>'
                    skeleton_html += "".join(_build_account_card_skeleton(a, ns_str) for a in watch)

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
            # ── 계좌 상세 화면 ────────────────────────────────
            acc, positions, usd_rate = fetch_account_details(acc_id)

            prev_total  = float(acc[3])
            total_sum   = 0
            for pos in positions:
                _, ticker, qty, _, price, _, t_market, _ = pos
                qty_f   = float(qty   or 0)
                price_f = float(price or 0)
                rate = usd_rate if (get_market_currency(t_market) == "USD" or ticker == "USD") else 1
                amt  = qty_f * (price_f if ticker not in ('KRW', 'USD') else 1) * rate
                total_sum += amt

            pnl_sum     = total_sum - prev_total
            pnl_pct_sum = (pnl_sum / prev_total * 100) if prev_total > 0 else 0
            title       = acc[0] + (f" ({acc[1]})" if acc[1] else "")
            summary     = _build_summary_html("계좌자산", total_sum, pnl_sum, pnl_pct_sum, usd_rate_val, usd_chg)

            # 종목별 값 dict (pos_id → values)
            pos_values = {str(p[0]): _build_position_row_values(p, usd_rate) for p in positions}

            current_positions = [p[0] for p in positions]
            structure_changed = (current_positions != _last_positions)

            if structure_changed:
                _last_positions = current_positions
                _last_display.clear()
                if positions:
                    skeleton_html = "".join(_build_position_row_skeleton(p, ns_str) for p in positions)
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

    # ── 계좌 카드 클릭 → 상세 이동 ───────────────────────────────────────────

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

    # ── 티커 → 종목명 조회 ───────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.lookup_ticker)
    async def _lookup_ticker():
        import yfinance as yf
        import re
        payload = input.lookup_ticker()
        ticker  = str(payload.get("ticker", "")).strip().upper()
        if not ticker:
            return

        # KR 종목 판별: 숫자 포함 + '.' 없음
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

        # 시장 매핑
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
        payload = input.btn_confirm_add()
        if not payload:
            return
        name     = str(payload.get("name", "")).strip()
        alias    = str(payload.get("alias", "")).strip() or None
        is_watch = bool(payload.get("is_watch", False))
        if not name:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO accounts (name, alias, is_watch) VALUES (%s, %s, %s)", (name, alias, is_watch))
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_price_updated()

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
        _notify_price_updated()

    # ── 종목 추가 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_add_position)
    def _add_position():
        payload = input.btn_confirm_add_position()
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
                    "INSERT INTO tickers (ticker, name, market, leverage, is_manual) VALUES (%s, %s, %s, %s, false)",
                    (ticker, name or ticker, market, leverage)
                )
            cur.execute("INSERT INTO positions (account_id, ticker, quantity) VALUES (%s, %s, %s)", (acc_id, ticker, qty))
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_price_updated()

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
            cur.execute("INSERT INTO positions (account_id, ticker, quantity) VALUES (%s, %s, %s)", (acc_id, cash_type, amount))
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_price_updated()

    # ── 종목 수정 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_edit_position)
    def _edit_position():
        payload  = input.btn_confirm_edit_position()
        pos_id   = payload.get("pos_id")
        if not pos_id:
            return
        name     = str(payload.get("name", "")).strip()
        market   = str(payload.get("market", ""))
        leverage = int(payload.get("leverage", 1))
        qty      = float(payload.get("qty", 0))
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE positions SET quantity = %s WHERE id = %s", (qty, pos_id))
            cur.execute("""
                UPDATE tickers SET name = %s, market = %s, leverage = %s
                WHERE ticker = (SELECT ticker FROM positions WHERE id = %s)
            """, (name, market, leverage, pos_id))
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_price_updated()

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
            # 삭제 전 ticker 조회
            cur.execute("SELECT ticker FROM positions WHERE id = %s", (pos_id,))
            row    = cur.fetchone()
            ticker = row[0] if row else None
            # position 삭제
            cur.execute("DELETE FROM positions WHERE id = %s", (pos_id,))
            # 수동 추가 아니고 다른 계좌에도 없으면 ticker도 삭제
            if ticker:
                cur.execute("SELECT 1 FROM tickers WHERE ticker = %s AND is_manual = false", (ticker,))
                if cur.fetchone():
                    cur.execute("SELECT COUNT(*) FROM positions WHERE ticker = %s", (ticker,))
                    if cur.fetchone()[0] == 0:
                        cur.execute("DELETE FROM tickers WHERE ticker = %s", (ticker,))
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_price_updated()

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
            cur.execute("UPDATE positions SET ticker = %s, quantity = %s WHERE id = %s", (cash_type, amount, pos_id))
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_price_updated()

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
        _notify_price_updated()