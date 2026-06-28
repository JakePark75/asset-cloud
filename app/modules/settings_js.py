def settings_js() -> str:
    """
    설정(시세조회간격/티커) 페이지 JS — settings_ui()에서 ui.tags.script()로 주입.
    """
    return """
(function() {

  Shiny.addCustomMessageHandler('st_init', function(m) {
    var toggle = document.getElementById('st-realtime-toggle');
    if (toggle) toggle.checked = (m.interval === 0);

    var listEl = document.getElementById('st-ticker-list');
    if (listEl) listEl.innerHTML = m.ticker_list_html || '<p style="color:#888; padding:8px 0;">등록된 티커가 없습니다.</p>';

    _applyTickers(m.tickers);
    _applyAutoTickerVisibility();
  });

  // ── st_tick: dynamic 필드만 patch ──────────────────────────
  Shiny.addCustomMessageHandler('st_tick', function(m) {
    Object.keys(m).forEach(function(key) {
      _applyOneTickerDynamic(m[key]);
    });
  });

  // ── st_static_tick: static 필드만 patch ─────────────────────
  Shiny.addCustomMessageHandler('st_static_tick', function(m) {
    Object.keys(m).forEach(function(key) {
      _applyOneTickerStatic(m[key]);
    });
  });

  // ── 자동 티커 숨김/표시 ───────────────────────────────────
  function _applyAutoTickerVisibility() {
    var btn = document.getElementById('st-auto-ticker-toggle');
    if (!btn) return;
    var hidden = btn.dataset.hidden === '1';
    var rows = document.querySelectorAll('#st-ticker-list .ticker-row[data-auto]');
    rows.forEach(function(r) { r.style.display = hidden ? 'none' : ''; });
  }

  window.stToggleAutoTickers = function() {
    var btn = document.getElementById('st-auto-ticker-toggle');
    var hidden = btn.dataset.hidden === '1';
    var nextHidden = !hidden;
    btn.dataset.hidden = nextHidden ? '1' : '0';
    btn.style.color = nextHidden ? '#888' : '#00c073';
    btn.textContent = nextHidden ? '자동 숨김' : '자동 표시';
    _applyAutoTickerVisibility();
    Shiny.setInputValue('settings-auto_hidden', nextHidden ? '1' : '0');
  };

  // 초기 상태(자동 숨김=1) 서버에 동기화
  document.addEventListener('shiny:connected', function() {
    Shiny.setInputValue('settings-auto_hidden', '1');
  });

  // ── 티커 추가 모달 ────────────────────────────────────────
  window.stShowModal = function() {
    document.getElementById('st-modal-overlay').style.display = '';
  };
  window.stHideModal = function() {
    document.getElementById('st-modal-overlay').style.display = 'none';
    ['st-new-ticker', 'st-new-ticker-name'].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.value = '';
    });
    var lev = document.getElementById('st-new-ticker-leverage');
    if (lev) lev.value = '1';
    var mkt = document.getElementById('st-new-ticker-market');
    if (mkt) mkt.selectedIndex = 0;
  };

  // st_init용: static+dynamic 전체 적용
  function _applyTickers(tickers) {
    Object.values(tickers).forEach(function(t) { _applyOneTickerFull(t); });
  }

  function _applyOneTickerFull(t) {
    var row = document.getElementById('st-row-' + t.id);
    if (!row) return;
    _applyOneTickerStatic(t);
    _applyOneTickerDynamic(t);
  }

  // st_static_tick용: static 필드만 (수신된 필드만 존재)
  function _applyOneTickerStatic(t) {
    var row = document.getElementById('st-row-' + t.id);
    if (row && row.dataset.auto && row.style.display === 'none') return;

    if (t.name != null) {
      var nameEl = document.getElementById('st-name-' + t.id);
      if (nameEl) nameEl.textContent = t.name;
    }

    if (t.leverage != null) {
      var levEl = document.getElementById('st-lev-' + t.id);
      if (levEl) {
        levEl.textContent = 'x' + t.leverage;
        levEl.className   = 'lev-badge lev-x' + t.leverage;
        levEl.style.display = t.leverage > 1 ? '' : 'none';
      }
    }

    if (t.market != null) {
      var marketEl = document.getElementById('st-market-' + t.id);
      if (marketEl) marketEl.textContent = t.market;
    }

    if (t.status_dot != null || t.status_txt != null || t.status_cls != null) {
      var stEl = document.getElementById('st-status-' + t.id);
      if (stEl) {
        stEl.textContent = t.status_dot ? t.status_dot + ' ' + t.status_txt : '';
        stEl.className   = 'ticker-status ' + (t.status_cls || '');
      }
    }
  }

  // st_tick용: dynamic 필드만 (수신된 필드만 존재)
  function _applyOneTickerDynamic(t) {
    var row = document.getElementById('st-row-' + t.id);
    if (row && row.dataset.auto && row.style.display === 'none') return;

    if (t.price != null || t.chg != null || t.chg_css != null) {
      var chgEl = document.getElementById('st-change-' + t.id);
      if (chgEl) {
        if (t.chg) {
          chgEl.innerHTML =
            (t.price ? '<span class="' + t.chg_css + '" style="margin-right:4px;">' + t.price + '</span>' : '') +
            '<span class="' + t.chg_css + '">' + t.chg + '</span>';
        } else {
          chgEl.innerHTML = '';
        }
      }
    }
  }

})();
"""
