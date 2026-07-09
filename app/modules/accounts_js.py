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
    // 새로고침 후 상태 복원 로직이 "계좌 목록 렌더링 완료"를 알 수 있도록 알림
    document.dispatchEvent(new CustomEvent('ac:list_init'));
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
    // 새로고침 후 상태 복원 로직이 "이 계좌의 종목 목록 렌더링 완료"를 알 수 있도록 알림
    document.dispatchEvent(new CustomEvent('ac:acc_init', { detail: { acc_id: m.acc_id } }));
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

  // ── 끊김→새로고침 시 상태 저장/복원 ────────────────────────────────────
  // _editPosId, _editCurQty, _editCurAvg, _editMarket, _editCurrency 는
  // modal_edit_position_js()에서 선언됨 (같은 IIFE 스코프 내 접근 가능)

  function _val(id) {
    var el = document.getElementById(id);
    return el ? el.value : '';
  }
  function _setVal(id, v) {
    var el = document.getElementById(id);
    if (el && v !== undefined && v !== null && v !== '') el.value = v;
  }
  function _isModalOpen(id) {
    var el = document.getElementById(id);
    return !!(el && el.style.display !== 'none');
  }

  function _acSaveRestoreState() {
    var state = {};

    if (window._acOpenId) {
      state.openAccountId = window._acOpenId;
    }

    // 모달은 한 번에 하나만 뜰 수 있는 구조 (UI 상 중첩 없음)
    if (_isModalOpen('ac-modal-edit-position')) {
      var activeTabBtn = document.querySelector('.ac-tab-btn.ac-tab-active');
      var allTabBtns   = document.querySelectorAll('.ac-tab-btn');
      var tabIdx       = activeTabBtn ? Array.prototype.indexOf.call(allTabBtns, activeTabBtn) : 0;
      var tabName      = ['info', 'buy', 'sell'][tabIdx] || 'info';

      state.modal = {
        type: 'edit-position',
        posId: _editPosId,
        tab: tabName,
        fields: {
          name:       _val('ac-edit-pos-name'),
          market:     _val('ac-edit-pos-market'),
          leverage:   _val('ac-edit-pos-leverage'),
          qty:        _val('ac-edit-pos-qty'),
          avg_price:  _val('ac-edit-pos-avg-price'),
          buy_qty:    _val('ac-buy-qty'),
          buy_price:  _val('ac-buy-price'),
          sell_qty:   _val('ac-sell-qty'),
          sell_price: _val('ac-sell-price'),
        },
      };
    } else if (_isModalOpen('ac-modal-edit-cash')) {
      state.modal = {
        type: 'edit-cash',
        posId: _editCashId,
        fields: {
          cash_type: _val('ac-edit-cash-type'),
          amount:    _val('ac-edit-cash-amount'),
        },
      };
    } else if (_isModalOpen('ac-modal-add-position')) {
      state.modal = {
        type: 'add-position',
        fields: {
          ticker:    _val('ac-new-pos-ticker'),
          name:      _val('ac-new-pos-name'),
          market:    _val('ac-new-pos-market'),
          leverage:  _val('ac-new-pos-leverage'),
          qty:       _val('ac-new-pos-qty'),
          avg_price: _val('ac-new-pos-avg-price'),
        },
      };
    } else if (_isModalOpen('ac-modal-add-cash')) {
      state.modal = {
        type: 'add-cash',
        fields: {
          cash_type: _val('ac-new-cash-type'),
          amount:    _val('ac-new-cash-amount'),
        },
      };
    } else if (_isModalOpen('ac-modal-add-account')) {
      var watchEl = document.getElementById('ac-new-account-is-watch');
      state.modal = {
        type: 'add-account',
        fields: {
          name:     _val('ac-new-account-name'),
          alias:    _val('ac-new-account-alias'),
          is_watch: watchEl ? watchEl.checked : false,
        },
      };
    }

    if (Object.keys(state).length === 0) return null;
    return state;
  }

  // 계좌 목록이 이미 렌더링돼 있으면 즉시, 아니면 ac:list_init 이벤트를 1회 대기 후 실행
  function _withAccountList(cb) {
    var listEl = document.getElementById('ac-account-list');
    if (listEl && listEl.innerHTML.trim() !== '') {
      cb();
      return;
    }
    document.addEventListener('ac:list_init', function handler() {
      document.removeEventListener('ac:list_init', handler);
      cb();
    });
  }

  // 해당 계좌의 종목 목록이 렌더링될 때까지 ac:acc_init 이벤트 대기
  function _withAccPositions(accId, cb) {
    document.addEventListener('ac:acc_init', function handler(e) {
      if (e.detail && e.detail.acc_id === accId) {
        document.removeEventListener('ac:acc_init', handler);
        cb();
      }
    });
  }

  function _acOpenModalFromState(state) {
    var m = state.modal;
    if (!m) return;

    if (m.type === 'add-account') {
      acShowModal('ac-modal-add-account');
      _setVal('ac-new-account-name', m.fields.name);
      _setVal('ac-new-account-alias', m.fields.alias);
      var watchEl = document.getElementById('ac-new-account-is-watch');
      if (watchEl) watchEl.checked = !!m.fields.is_watch;
      return;
    }

    // 아래 타입들은 계좌가 열려있는 상태에서만 의미가 있음
    if (!state.openAccountId) return;

    if (m.type === 'add-position') {
      acShowModal('ac-modal-add-position');
      _setVal('ac-new-pos-ticker', m.fields.ticker);
      _setVal('ac-new-pos-name', m.fields.name);
      _setVal('ac-new-pos-market', m.fields.market);
      _setVal('ac-new-pos-leverage', m.fields.leverage);
      _setVal('ac-new-pos-qty', m.fields.qty);
      _setVal('ac-new-pos-avg-price', m.fields.avg_price);
      acUpdateAddPreview();
      return;
    }

    if (m.type === 'add-cash') {
      acShowModal('ac-modal-add-cash');
      _setVal('ac-new-cash-type', m.fields.cash_type);
      _setVal('ac-new-cash-amount', m.fields.amount);
      return;
    }

    // edit-position / edit-cash 는 실제 row(data-pos-id)가 DOM에 있어야 함
    var row = document.querySelector('[data-pos-id="' + m.posId + '"]');
    if (!row) return; // 그 사이 삭제/변경됐으면 조용히 포기 (stale 데이터로 덮어쓰지 않음)

    if (m.type === 'edit-position') {
      window.acOpenEditPositionModal(row);
      if (m.tab && m.tab !== 'info') acSwitchTab(m.tab);
      _setVal('ac-edit-pos-name', m.fields.name);
      _setVal('ac-edit-pos-market', m.fields.market);
      _setVal('ac-edit-pos-leverage', m.fields.leverage);
      _setVal('ac-edit-pos-qty', m.fields.qty);
      _setVal('ac-edit-pos-avg-price', m.fields.avg_price);
      _setVal('ac-buy-qty', m.fields.buy_qty);
      _setVal('ac-buy-price', m.fields.buy_price);
      _setVal('ac-sell-qty', m.fields.sell_qty);
      _setVal('ac-sell-price', m.fields.sell_price);
      if (m.tab === 'buy')  acUpdateBuyPreview();
      if (m.tab === 'sell') acUpdateSellPreview();
    } else if (m.type === 'edit-cash') {
      window.acOpenEditCashModal(row);
      _setVal('ac-edit-cash-type', m.fields.cash_type);
      _setVal('ac-edit-cash-amount', m.fields.amount);
    }
  }

  function _acRestoreState(state) {
    if (!state) return;
    var accId = state.openAccountId;

    if (!accId) {
      // 열려있던 계좌가 없으면 add-account 모달만 있을 수 있음 — 바로 처리
      _acOpenModalFromState(state);
      return;
    }

    _withAccountList(function() {
      _withAccPositions(accId, function() {
        _acOpenModalFromState(state);
      });
      window.acToggleCard(accId);
    });
  }

  window.registerStateRestore('accounts', _acSaveRestoreState, _acRestoreState);

})();
"""