import json
import re

import yfinance as yf
from shiny import ui, module, reactive

from app.modules.accounts_DAL import (
    fetch_accounts_summary, calc_accounts_summary,
    fetch_account_details, calc_account_details,
    execute_buy, execute_sell,
)
# fetch_account_details / calc_account_details: 아코디언 종목 목록 조회에 재활용
from app.modules.accounts_helpers import (
    _build_account_card_skeleton, _build_account_card_values,
    _build_position_row_skeleton, _build_position_row_values,
)
from app.modules.accounts_modals import modal_edit_position_html, modal_edit_position_js
from app.db import get_db, get_usd_krw, get_market_map, get_market_label, get_market_currency
from app.modules.components import fmt_krw, fmt_usd, fmt_pct, fmt_pnl, fmt_change
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


def _notify_ticker_changed():
    try:
        from common.redis_store import publish_ticker_changed
        publish_ticker_changed()
    except Exception as e:
        print(f"[accounts] ticker_changed 신호 발행 실패 (무시): {e}")


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def accounts_ui():
    market_map = get_market_map()
    market_options = "".join(
        f'<option value="{m}">{m} ({get_market_label(m)})</option>'
        for m in market_map
    )
    # market → currency 매핑 (JS에서 종목 추가 모달 preview currency 결정에 사용)
    market_currency_map_js = json.dumps(
        {m: v.get("currency", "KRW") for m, v in market_map.items()}
    )

    return ui.div(
        ui.tags.script("""
(function() {

  // ── market → currency 매핑 (Python에서 주입) ──────────────────────────
  var _marketCurrencyMap = """ + market_currency_map_js + """;

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
    setHtml('ac-account-list', m.account_list_html);
    // 열려있던 아코디언이 있으면 재오픈 표시 (내용은 ac_acc_init이 채움)
    if (window._acOpenId) {
      var accEl = document.getElementById('ac-acc-' + window._acOpenId);
      if (accEl) {
        accEl.style.display = '';
        accEl.innerHTML = '<div class="pf-acc-loading">불러오는 중...</div>';
      } else {
        window._acOpenId = null;
      }
    }
    _applyAccountCards(m.cards);
  });

  // ── ac_list_tick ───────────────────────────────────────────────────────
  Shiny.addCustomMessageHandler('ac_list_tick', function(m) {
    Object.keys(m).forEach(function(key) {
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

  // ── ac_acc_init: 아코디언 내용 통째 교체 ──────────────────────────────
  Shiny.addCustomMessageHandler('ac_acc_init', function(m) {
    var el = document.getElementById('ac-acc-' + m.acc_id);
    if (el) {
      el.innerHTML = m.position_list_html;
      el.style.display = '';
    }
    _applyPositions(m.positions);
  });

  // ── ac_acc_tick: 아코디언 변경값만 patch ──────────────────────────────
  Shiny.addCustomMessageHandler('ac_acc_tick', function(m) {
    Object.keys(m.positions || {}).forEach(function(key) {
      _applyOnePosition(m.positions[key]);
    });
  });

  // ── 아코디언 토글 (한번에 하나만 열림) ────────────────────────────────
  window.acToggleCard = function(acc_id) {
    var el = document.getElementById('ac-acc-' + acc_id);
    if (!el) return;

    if (window._acOpenId === acc_id) {
      // 닫기
      el.style.display = 'none';
      el.innerHTML = '';
      window._acOpenId = null;
      Shiny.setInputValue(window._acNs + '-card_clicked', null, { priority: 'event' });
      return;
    }

    // 이전에 열려있던 아코디언 닫기
    if (window._acOpenId) {
      var prevEl = document.getElementById('ac-acc-' + window._acOpenId);
      if (prevEl) { prevEl.style.display = 'none'; prevEl.innerHTML = ''; }
    }

    window._acOpenId = acc_id;
    el.style.display = '';
    el.innerHTML = '<div class="pf-acc-loading">불러오는 중...</div>';
    Shiny.setInputValue(window._acNs + '-card_clicked', acc_id, { priority: 'event' });
  };

  function _applyPositions(positions) {
    Object.values(positions).forEach(function(p) { _applyOnePosition(p); });
  }
  function _applyOnePosition(p) {
    var nameEl = document.getElementById('ac-name-' + p.id);
    if (nameEl && p.name != null) nameEl.textContent = p.name;

    var levEl = document.getElementById('ac-lev-' + p.id);
    if (levEl && p.leverage != null) {
      levEl.textContent = 'x' + p.leverage;
      levEl.className   = 'lev-badge lev-x' + p.leverage;
      levEl.style.display = p.leverage > 1 ? '' : 'none';
    }

    var qtyEl = document.getElementById('ac-qty-' + p.id);
    if (qtyEl) qtyEl.textContent = p.qty || '';

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

    var pnlEl = document.getElementById('ac-pnl-' + p.id);
    if (pnlEl) { pnlEl.textContent = p.pnl || ''; pnlEl.className = p.pnl_css || ''; }

    var stEl = document.getElementById('ac-status-' + p.id);
    if (stEl) {
      stEl.textContent = p.status_dot ? p.status_dot + ' ' + p.status_txt : '';
      stEl.className   = 'ticker-status ' + p.status_cls;
    }
    // ── data-* 속성 갱신 (모달을 같은 세션에서 다시 열 때 최신값이 채워지도록) ──
    if (amountEl) {
      var parentEl = amountEl.closest('[data-pos-id]');
      if (parentEl) {
        if (p.avg_price !== undefined && p.avg_price !== null) {
          parentEl.setAttribute('data-avg-price', p.avg_price);
        }
        if (p.cash_amount !== undefined && p.cash_amount !== null) {
          parentEl.setAttribute('data-amount', p.cash_amount);
        }
        if (p.name != null)     parentEl.setAttribute('data-name', p.name);
        if (p.market != null)   parentEl.setAttribute('data-market', p.market);
        if (p.leverage != null) parentEl.setAttribute('data-leverage', p.leverage);
        if (p.currency != null) parentEl.setAttribute('data-currency', p.currency);
        if (p.raw_qty != null)  parentEl.setAttribute('data-qty', p.raw_qty);
      }
    }
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
    Shiny.setInputValue(window._acNs + '-btn_confirm_edit_cash', {
      pos_id:    _editCashId,
      cash_type: tEl ? tEl.value : 'KRW',
      amount:    aEl ? (parseFloat(aEl.value) || 0) : 0,
    }, {priority: 'event'});
    acHideModal('ac-modal-edit-cash');
  };

  window.acTriggerCashDelete = function() {
    if (confirm('현금을 삭제하시겠습니까?')) {
      Shiny.setInputValue(window._acNs + '-confirm_delete_cash',
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
    Shiny.setInputValue(window._acNs + '-lookup_ticker', { ticker: ticker, source: 'add' }, {priority: 'event'});
  };

  window.acLookupTickerEdit = function() {
    var tickerEl = document.getElementById('ac-edit-pos-ticker');
    var ticker = tickerEl ? tickerEl.textContent.trim() : '';
    if (!ticker) return;
    var btn = document.getElementById('ac-edit-pos-lookup-btn');
    if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
    Shiny.setInputValue(window._acNs + '-lookup_ticker', { ticker: ticker, source: 'edit' }, {priority: 'event'});
  };

  // ── 종목명으로 레버리지 배수 추론 ────────────────────────────────────────
  function _inferLeverage(name) {
    if (!name) return null;
    if (/3X|3x|UltraPro/i.test(name)) return '3';
    if (/2X|2x|Ultra/i.test(name))    return '2';
    return null;
  }

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
      var lev = _inferLeverage(m.name);
      if (lev) {
        var levEl = document.getElementById('ac-new-pos-leverage');
        if (levEl) levEl.value = lev;
      }
      // 조회 후 시장이 바뀌었을 수 있으므로 preview 갱신
      acUpdateAddPreview();
    } else {
      alert('종목명을 찾지 못했습니다: ' + m.ticker);
    }
  });

  Shiny.addCustomMessageHandler('ac_ticker_lookup_result_edit', function(m) {
    var btn = document.getElementById('ac-edit-pos-lookup-btn');
    if (btn) { btn.textContent = '🔄'; btn.disabled = false; }
    if (m.name) {
      var nameEl = document.getElementById('ac-edit-pos-name');
      if (nameEl) nameEl.value = m.name;
      if (m.market) {
        var marketEl = document.getElementById('ac-edit-pos-market');
        if (marketEl) marketEl.value = m.market;
      }
      var lev = _inferLeverage(m.name);
      if (lev) {
        var levEl = document.getElementById('ac-edit-pos-leverage');
        if (levEl) levEl.value = lev;
      }
    } else {
      alert('종목명을 찾지 못했습니다: ' + m.ticker);
    }
  });

  // ── 종목 추가 모달 — preview ───────────────────────────────────────────
  window.acUpdateAddPreview = function() {
    var marketEl = document.getElementById('ac-new-pos-market');
    var market   = marketEl ? marketEl.value : '';
    var cur      = _marketCurrencyMap[market] || 'KRW';

    var previewBox = document.getElementById('ac-add-preview-box');

    // INDEX(NUM)는 현금 개념 없음 → preview 숨김
    if (cur === 'NUM') {
      if (previewBox) previewBox.style.display = 'none';
      return;
    }
    if (previewBox) previewBox.style.display = '';

    var qty      = parseFloat(document.getElementById('ac-new-pos-qty')       ? document.getElementById('ac-new-pos-qty').value       : 0) || 0;
    var avgPrice = parseFloat(document.getElementById('ac-new-pos-avg-price') ? document.getElementById('ac-new-pos-avg-price').value : 0) || 0;
    var cost     = qty * avgPrice;
    var cashHeld = _getCashAmount(cur);

    var cashLabel = document.getElementById('ac-add-preview-cash-label');
    if (cashLabel) cashLabel.textContent = '보유현금(' + cur + ')';
    var cashEl = document.getElementById('ac-add-preview-cash');
    if (cashEl) {
      cashEl.textContent = _fmtNum(cashHeld, cur);
      cashEl.className   = 'ac-preview-value' + (cost > cashHeld ? ' negative' : '');
    }

    var costLabel = document.getElementById('ac-add-preview-cost-label');
    if (costLabel) costLabel.textContent = '매수금액(' + cur + ')';
    var costEl = document.getElementById('ac-add-preview-cost');
    if (costEl) {
      costEl.textContent = cost > 0 ? ('-' + _fmtNum(cost, cur)) : '-';
      costEl.className   = 'ac-preview-value negative' + (cost > cashHeld && cost > 0 ? ' ac-preview-over' : '');
    }

    var remainEl = document.getElementById('ac-add-preview-remain');
    if (remainEl) {
      var remain = cashHeld - cost;
      remainEl.textContent = _fmtNum(remain, cur);
      remainEl.className   = 'ac-preview-value' + (remain < 0 ? ' negative' : '');
    }
  };

  // ── 종목 추가 확인 트리거 ────────────────────────────────────────────────
  window.acTriggerAddPosition = function() {
    var tickerEl  = document.getElementById('ac-new-pos-ticker');
    var nameEl    = document.getElementById('ac-new-pos-name');
    var marketEl  = document.getElementById('ac-new-pos-market');
    var leverageEl= document.getElementById('ac-new-pos-leverage');
    var qtyEl     = document.getElementById('ac-new-pos-qty');
    var avgEl     = document.getElementById('ac-new-pos-avg-price');

    var market  = marketEl  ? marketEl.value  : '';
    var cur     = _marketCurrencyMap[market] || 'KRW';
    var qty     = parseFloat(qtyEl  ? qtyEl.value  : 0) || 0;
    var avg     = parseFloat(avgEl  ? avgEl.value  : 0) || 0;
    var cost    = qty * avg;

    // NUM 마켓(지수)은 현금 개념 없음 → 경고 없이 통과
    if (cur !== 'NUM' && cost > 0) {
      var cashHeld = _getCashAmount(cur);
      if (cost > cashHeld) {
        alert('매수금액(' + cost.toLocaleString('ko-KR', {maximumFractionDigits:4}) + ' ' + cur + ')이 보유현금(' + cashHeld.toLocaleString('ko-KR', {maximumFractionDigits:4}) + ' ' + cur + ')을 초과합니다.');
        return;
      }
    }

    Shiny.setInputValue(window._acNs + '-btn_confirm_add_position', {
      name:     nameEl     ? nameEl.value     : '',
      ticker:   tickerEl   ? tickerEl.value   : '',
      market:   market,
      leverage: leverageEl ? leverageEl.value : '1',
      qty:      qty,
      avg_price: avg || null,
    }, {priority: 'event'});
    acHideModal('ac-modal-add-position');
  };

  // _fmtNum / _getCashAmount 는 modal_edit_position_js() 에서 정의됨 (같은 IIFE 내)

""" + modal_edit_position_js() + """
})();
        """),

        # ── 계좌 목록 화면 ────────────────────────────────────────────────────
        ui.div(
            {"id": "ac-list-view"},
            ui.div(
                ui.div({"id": "ac-account-list", "class": "ticker-list"}),
                ui.tags.button(
                    "+ 계좌 추가",
                    class_="btn-add",
                    onclick="acShowModal('ac-modal-add-account');",
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
                        "Shiny.setInputValue(window._acNs + '-btn_confirm_add', {"
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
                                      id="ac-new-pos-market", class_="form-control",
                                      onchange="acUpdateAddPreview();")),
                ui.div(ui.tags.label("레버리지"),
                       ui.tags.select(
                           ui.tags.option("x1", value="1"),
                           ui.tags.option("x2", value="2"),
                           ui.tags.option("x3", value="3"),
                           id="ac-new-pos-leverage", class_="form-control",
                       )),
                ui.div(ui.tags.label("수량"),
                       ui.tags.input(id="ac-new-pos-qty", type="number",
                                     value="0", min="0", step="any", class_="form-control",
                                     oninput="acUpdateAddPreview();")),
                ui.div(ui.tags.label("매수 평단가"),
                       ui.tags.input(id="ac-new-pos-avg-price", type="number",
                                     min="0", step="any", placeholder="미입력 시 미설정",
                                     class_="form-control",
                                     oninput="acUpdateAddPreview();")),
                # ── 미리보기 ──────────────────────────────────────────────────
                ui.div(
                    ui.div(
                        ui.span("", id="ac-add-preview-cash-label", class_="ac-preview-label"),
                        ui.span("", id="ac-add-preview-cash", class_="ac-preview-value"),
                    ),
                    ui.div(
                        ui.span("", id="ac-add-preview-cost-label", class_="ac-preview-label"),
                        ui.span("", id="ac-add-preview-cost", class_="ac-preview-value negative"),
                    ),
                    ui.div(
                        ui.span("매수 후 잔여현금", class_="ac-preview-label"),
                        ui.span("", id="ac-add-preview-remain", class_="ac-preview-value"),
                    ),
                    id="ac-add-preview-box",
                    class_="ac-preview-box",
                ),
                ui.tags.button(
                    "추가", class_="btn-add",
                    onclick="acTriggerAddPosition();",
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
                        "Shiny.setInputValue(window._acNs + '-btn_confirm_add_cash', {"
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
def accounts_server(input, output, session, active_tab: reactive.value = None,
                    active_sub_tab: reactive.value = None):

    ns_str = session.ns("_")[:-1]  # "asset-accounts" 등 실제 prefix

    _initialized = False  # 일반 변수: effect 자기-재트리거 방지
    open_account = reactive.value(None)
    refresh      = reactive.value(0)

    _last_accounts:  list = []
    _last_list_disp: dict = {}   # 계좌 목록 diff 캐시
    _last_positions: dict = {}   # open_account → [pos_id, ...] (아코디언 구조 변경 감지)
    _last_acc_disp:  dict = {}   # 아코디언 종목 diff 캐시

    # ── DB 캐시 (price_signal 비의존, 구조만) ───────────────────────────────

    @reactive.calc
    def _db_accounts():
        refresh()
        return fetch_accounts_summary()  # 시세 없음, 구조만

    @reactive.calc
    def _db_account_positions():
        refresh()
        acc_id = open_account()
        if acc_id is None:
            return None
        return fetch_account_details(acc_id)  # 시세 없음, 구조만

    # ── 아코디언 하단 버튼 HTML 생성 ────────────────────────────────────────

    def _build_accordion_footer(acc_id: int) -> str:
        return (
            f'<div style="margin-top:12px; display:flex; gap:8px; flex-wrap:wrap;">'
            f'  <button class="btn-add" '
            f'    onclick="acShowModal(\'ac-modal-add-position\'); acUpdateAddPreview();">'
            f'    + 종목 추가</button>'
            f'  <button class="btn-add" '
            f'    onclick="acShowModal(\'ac-modal-add-cash\');">'
            f'    + 현금 추가</button>'
            f'  <button class="btn-account-delete-bottom" '
            f'    onclick="if(confirm(\'계좌를 삭제하시겠습니까?\')) '
            f'Shiny.setInputValue(window._acNs + \'-confirm_delete_account\', '
            f'Math.random(), {{priority: \'event\'}});">'
            f'    계좌 삭제</button>'
            f'</div>'
        )

    # ── 화면 갱신 ─────────────────────────────────────────────────────────────

    @reactive.effect
    async def _send_update():
        nonlocal _last_accounts, _last_list_disp, _last_positions, _last_acc_disp
        nonlocal _initialized
        price_signal.get()
        daily_insert_signal.get()
        acc_id = open_account()  # 탭 가드 전에 의존성 등록

        tab = active_sub_tab if active_sub_tab is not None else active_tab
        if _initialized and tab and tab.get() != "accounts":
            return

        usd_rate_val, usd_chg = get_usd_krw()

        # ── 계좌 목록 갱신 ──────────────────────────────────────────────────
        from common.redis_store import get_all_prices
        prices   = get_all_prices()
        accounts = calc_accounts_summary(_db_accounts(), prices, usd_rate_val)
        normal   = [a for a in accounts if not a[5]]
        watch    = [a for a in accounts if a[5]]

        card_values = {str(a[0]): _build_account_card_values(a) for a in accounts}
        current_accounts = [a[0] for a in accounts]
        structure_changed = (current_accounts != _last_accounts)

        if structure_changed:
            _last_accounts = current_accounts
            _last_list_disp.clear()
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
                "account_list_html": skeleton_html,
                "cards":             card_values,
            })
        else:
            diff = diff_display(card_values, _last_list_disp)
            if diff:
                await session.send_custom_message("ac_list_tick", diff)

        _last_list_disp.update(card_values)

        # ── 아코디언 종목 갱신 (열려있을 때만) ─────────────────────────────
        if acc_id is not None:
            db_detail = _db_account_positions()
            if db_detail is None:
                return
            acc_row, db_rows = db_detail

            acc, positions, usd_rate = calc_account_details(acc_row, db_rows, prices, usd_rate_val)

            pos_values = {str(p[0]): _build_position_row_values(p, usd_rate) for p in positions}
            current_pos_ids = [p[0] for p in positions]
            pos_structure_changed = (_last_positions.get(acc_id) != current_pos_ids)

            if pos_structure_changed:
                _last_positions[acc_id] = current_pos_ids
                _last_acc_disp.clear()
                if positions:
                    skeleton_html = "".join(
                        _build_position_row_skeleton(p, ns_str) for p in positions
                    )
                else:
                    skeleton_html = '<p style="color:#888; padding:16px;">종목이 없습니다.</p>'
                skeleton_html += _build_accordion_footer(acc_id)
                await session.send_custom_message("ac_acc_init", {
                    "acc_id":             acc_id,
                    "position_list_html": skeleton_html,
                    "positions":          pos_values,
                })
            else:
                diff = diff_display(pos_values, _last_acc_disp)
                if diff:
                    await session.send_custom_message("ac_acc_tick", {
                        "positions": diff,
                    })

            _last_acc_disp.update(pos_values)

        _initialized = True

    # ── 계좌 카드 클릭 (아코디언 토글) ──────────────────────────────────────

    @reactive.effect
    @reactive.event(input.card_clicked)
    def _handle_card_click():
        nonlocal _last_acc_disp, _last_positions
        acc_id = input.card_clicked()
        if acc_id is None:
            # 닫기
            _last_acc_disp.clear()
            open_account.set(None)
        else:
            _last_acc_disp.clear()
            _last_positions.pop(acc_id, None)
            open_account.set(acc_id)

    # ── 티커 자동조회 ─────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.lookup_ticker)
    async def _lookup_ticker():
        payload = input.lookup_ticker()
        ticker  = str(payload.get("ticker", "")).strip().upper()
        source  = str(payload.get("source", "add"))  # "add" | "edit"
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
            "PCX": "ARC",
            "ASE": "AMS",
            "NIM": "INDEX",
        }
        if qtype == "CRYPTOCURRENCY":
            market = "CRYPTO"
        elif qtype == "INDEX":
            market = "INDEX"
        else:
            market = exchange_map.get(exchange, "")

        channel = "ac_ticker_lookup_result" if source == "add" else "ac_ticker_lookup_result_edit"
        await session.send_custom_message(channel, {
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
        acc_id = open_account()
        if acc_id is None:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM accounts WHERE id = %s", (acc_id,))
            conn.commit()
            cur.close()
        open_account.set(None)
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
        qty       = float(payload.get("qty", 0))
        avg_price = payload.get("avg_price")
        if avg_price is not None:
            avg_price = float(avg_price)
        acc_id    = open_account()
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
            if avg_price is not None:
                cur.execute(
                    "INSERT INTO positions (account_id, ticker, quantity, avg_price) VALUES (%s, %s, %s, %s)",
                    (acc_id, ticker, qty, avg_price)
                )
            else:
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
        acc_id    = open_account()
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
        _notify_ticker_changed()  # tickers 메타데이터(이름/시장/레버리지) 변경 → settings 화면 갱신

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