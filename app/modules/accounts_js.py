from app.modules.accounts_modals import modal_edit_position_js


def accounts_js(market_currency_map_js: str) -> str:
    """
    accounts 페이지 전체 JS — accounts_ui()에서 ui.tags.script()로 주입.
    market_currency_map_js: JSON 문자열 (Python에서 직렬화해서 전달)
    """
    return """
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
    // 열려있던 아코디언은 ac_acc_init이 오면 그때 열림
    if (window._acOpenId) {
      var accEl = document.getElementById('ac-acc-' + window._acOpenId);
      if (!accEl) window._acOpenId = null;
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

  // ── ac_acc_static_tick: static 필드만 patch ────────────────────────────
  Shiny.addCustomMessageHandler('ac_acc_static_tick', function(m) {
    Object.keys(m.positions || {}).forEach(function(key) {
      _applyOnePositionStatic(m.positions[key]);
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
      Shiny.setInputValue(window._acNs + '-card_clicked', 0, { priority: 'event' });
      return;
    }

    // 이전에 열려있던 아코디언 닫기
    if (window._acOpenId) {
      var prevEl = document.getElementById('ac-acc-' + window._acOpenId);
      if (prevEl) { prevEl.style.display = 'none'; prevEl.innerHTML = ''; }
    }

    window._acOpenId = acc_id;
    Shiny.setInputValue(window._acNs + '-card_clicked', acc_id, { priority: 'event' });
  };

  function _applyPositions(positions) {
    Object.values(positions).forEach(function(p) { _applyOnePositionFull(p); });
  }

  // ac_acc_init용: static + dynamic 전체 적용
  function _applyOnePositionFull(p) {
    _applyOnePositionStatic(p);
    _applyOnePosition(p);
  }

  // ac_acc_static_tick용: static 필드만 적용 (수신된 필드만 존재)
  function _applyOnePositionStatic(p) {
    if (p.name != null) {
      var nameEl = document.getElementById('ac-name-' + p.id);
      if (nameEl) nameEl.textContent = p.name;
    }

    if (p.leverage != null) {
      var levEl = document.getElementById('ac-lev-' + p.id);
      if (levEl) {
        levEl.textContent = 'x' + p.leverage;
        levEl.className   = 'lev-badge lev-x' + p.leverage;
        levEl.style.display = p.leverage > 1 ? '' : 'none';
      }
    }

    if (p.qty != null) {
      var qtyEl = document.getElementById('ac-qty-' + p.id);
      if (qtyEl) qtyEl.textContent = p.qty || '';
    }

    if (p.avgprice != null) {
      var avgEl = document.getElementById('ac-avgprice-' + p.id);
      if (avgEl) avgEl.textContent = p.avgprice || '';
    }

    if (p.status_dot != null || p.status_txt != null || p.status_cls != null) {
      var stEl = document.getElementById('ac-status-' + p.id);
      if (stEl) {
        stEl.textContent = p.status_dot ? p.status_dot + ' ' + p.status_txt : '';
        stEl.className   = 'ticker-status ' + (p.status_cls || '');
      }
    }

    // data-* 속성 갱신 (모달을 같은 세션에서 다시 열 때 최신값이 채워지도록)
    var amountEl = document.getElementById('ac-amount-' + p.id);
    if (amountEl) {
      var parentEl = amountEl.closest('[data-pos-id]');
      if (parentEl) {
        if (p.avg_price  !== undefined && p.avg_price  !== null) parentEl.setAttribute('data-avg-price', p.avg_price);
        if (p.cash_amount !== undefined && p.cash_amount !== null) parentEl.setAttribute('data-amount', p.cash_amount);
        if (p.name     != null) parentEl.setAttribute('data-name',     p.name);
        if (p.market   != null) parentEl.setAttribute('data-market',   p.market);
        if (p.leverage != null) parentEl.setAttribute('data-leverage', p.leverage);
        if (p.currency != null) parentEl.setAttribute('data-currency', p.currency);
        if (p.raw_qty  != null) parentEl.setAttribute('data-qty',      p.raw_qty);
      }
    }
  }

  // ac_acc_tick용: dynamic 필드만 적용 (수신된 필드만 존재)
  function _applyOnePosition(p) {
    if (p.amount != null) {
      var amountEl = document.getElementById('ac-amount-' + p.id);
      if (amountEl) amountEl.textContent = p.amount;
    }

    if (p.price != null || p.chg_css != null) {
      var priceEl = document.getElementById('ac-price-' + p.id);
      if (priceEl) {
        if (p.price   != null) { priceEl.textContent = p.price; priceEl.style.marginRight = p.price ? '4px' : '0'; }
        if (p.chg_css != null) priceEl.className = p.chg_css;
      }
    }

    if (p.chg != null || p.chg_css != null) {
      var chgEl = document.getElementById('ac-chg-' + p.id);
      if (chgEl) {
        if (p.chg     != null) chgEl.textContent = p.chg;
        if (p.chg_css != null) chgEl.className   = p.chg_css;
      }
    }

    if (p.pnl_amount != null || p.pnl_pct != null || p.pnl_css != null) {
      var pnlEl = document.getElementById('ac-pnl-' + p.id);
      if (pnlEl) {
        if (p.pnl_amount != null) pnlEl.dataset.pnlAmount = p.pnl_amount;
        if (p.pnl_pct    != null) pnlEl.dataset.pnlPct    = p.pnl_pct;
        if (p.pnl_css    != null) pnlEl.className          = p.pnl_css;
        pnlEl.textContent = (pnlEl.dataset.pnlAmount || '') + (pnlEl.dataset.pnlPct ? ' ' + pnlEl.dataset.pnlPct : '');
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
"""
