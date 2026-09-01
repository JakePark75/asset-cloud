def settings_js() -> str:
    """
    설정(시세조회간격/티커) 페이지 JS — settings_ui()에서 ui.tags.script()로 주입.
    """
    return """
(function() {

  var _stDynamicCache = {}; // ticker id -> {price, chg, chg_css} 마지막 값 캐시 (부분 diff 병합용)

  // ── 갱신시각 카운트업 (서버 재통신 없이 클라이언트에서 매초 재계산) ─────
  // portfolio_js.py/accounts_js.py의 동일 로직과 같은 이유 — 서버는 값이 실제로
  // 바뀔 때만 raw epoch(t.updated_at)을 보낸다. 그 값을 레지스트리에 저장해두고,
  // 1초 인터벌이 모든 등록된 DOM에 대해 "지금 - updated_at"을 재계산해서
  // 텍스트만 갱신한다.
  var _stUpdatedAt = {};

  function _stFmtElapsed(updatedAt) {
    if (updatedAt == null) return '-';
    var elapsed = Math.floor(Date.now() / 1000 - updatedAt);
    if (elapsed < 0) elapsed = 0;
    if (elapsed < 60)    return elapsed + 's';
    if (elapsed < 3600)  return Math.floor(elapsed / 60) + 'm';
    if (elapsed < 86400) return Math.floor(elapsed / 3600) + 'h';
    return Math.floor(elapsed / 86400) + 'd';
  }

  function _stSetUpdated(domId, updatedAt) {
    _stUpdatedAt[domId] = updatedAt;
    var el = document.getElementById(domId);
    if (el) el.textContent = _stFmtElapsed(updatedAt);
  }

  setInterval(function() {
    Object.keys(_stUpdatedAt).forEach(function(domId) {
      var el = document.getElementById(domId);
      if (el) el.textContent = _stFmtElapsed(_stUpdatedAt[domId]);
    });
  }, 1000);

  // ── 시세 갱신 시 가격박스 깜빡임(테두리) ────────────────────
  // portfolio_js.py의 _pfFlashPriceBox와 동일한 이유/방식.
  // 여기선 가격+등락률이 이미 st-change-{id} 하나에 합쳐져 있으므로 그 요소 자체가 대상.
  // 색은 그 안에 렌더링된 등락 span(class=chg_css)의 실제 계산된 색을 그대로 사용한다.
  var _stFlashTimers = {};

  function _stFlashChangeBox(id) {
    var box = document.getElementById('st-change-' + id);
    if (!box) return;
    var innerSpan = box.querySelector('span');
    var color = innerSpan ? getComputedStyle(innerSpan).color : '';

    if (_stFlashTimers[id]) clearTimeout(_stFlashTimers[id]);

    box.style.outline = '1px solid ' + (color || 'currentColor');
    box.style.outlineOffset = '1px';

    _stFlashTimers[id] = setTimeout(function() {
      box.style.outline = '';
      box.style.outlineOffset = '';
      delete _stFlashTimers[id];
    }, 500);
  }

  // ── 티커 드래그 정렬 (SortableJS) ────────────────────────────────────────
  // st_init이 #st-ticker-list의 innerHTML을 통째로 교체하므로, 이전 SortableJS
  // 인스턴스가 붙어있던 DOM 노드가 매번 파괴된다. portfolio_js.py(pf_init)와
  // 동일한 이유로 st_init이 올 때마다 재초기화한다.
  // 설정 화면은 asset_ui(#asset-root) 밖의 완전히 독립된 최상위 탭이므로,
  // portfolio_js.py에 있는 #asset-root 기반 iOS 자동스크롤 transform
  // 워크어라운드는 적용 대상 DOM 자체가 없어 넣지 않는다(2026-09 asset.py 확인 완료).
  // window.Sortable/_sortableLoading은 portfolio_js.py와 이름을 맞춰 공유되며,
  // 두 스크립트가 같은 페이지에 있어도 CDN 스크립트가 중복 로드되지 않는다.
  function _stLoadSortable(cb) {
    if (window.Sortable) { cb(); return; }
    if (window._sortableLoading) {
      window._sortableLoading.push(cb);
      return;
    }
    window._sortableLoading = [cb];
    var s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.7/Sortable.min.js';
    s.onload = function() {
      var queue = window._sortableLoading;
      window._sortableLoading = null;
      queue.forEach(function(fn) { fn(); });
    };
    document.head.appendChild(s);
  }

  var _stSortable = null;

  function _stInitSortable() {
    var el = document.getElementById('st-ticker-list-normal');
    if (!el) return;
    if (_stSortable) { _stSortable.destroy(); _stSortable = null; }
    _stLoadSortable(function() {
      var el2 = document.getElementById('st-ticker-list-normal');
      if (!el2) return;
      _stSortable = Sortable.create(el2, {
        delay: 150,
        delayOnTouchOnly: true,
        animation: 150,
        dataIdAttr: 'data-ticker',
        onEnd: function() {
          var order = _stSortable.toArray();
          Shiny.setInputValue('settings-ticker_reorder', order, { priority: 'event' });
        },
      });
    });
  }

  Shiny.addCustomMessageHandler('st_init', function(m) {
    _stDynamicCache = {};

    var listEl = document.getElementById('st-ticker-list');
    if (listEl) listEl.innerHTML = m.ticker_list_html || '<p style="color:#888; padding:8px 0;">등록된 티커가 없습니다.</p>';

    _applyTickers(m.tickers);
    _stInitSortable();
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

  // ── 티커 자동조회 ──────────────────────────────────────────
  window.stLookupTicker = function() {
    var tickerEl = document.getElementById('st-new-ticker');
    var ticker = tickerEl ? tickerEl.value.trim() : '';
    if (!ticker) return;
    var btn = document.getElementById('st-new-ticker-lookup-btn');
    if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
    Shiny.setInputValue('settings-lookup_ticker', { ticker: ticker }, {priority: 'event'});
  };

  // 레버리지는 서버(KIS 응답)가 명확히 판단 가능한 경우(1/2/3배)에만 m.leverage로 전달됨.
  // 그 외(비정수 배수, yf 폴백 등)에는 leverage 필드 자체가 없으므로 select 값을
  // 건드리지 않고 사용자가 직접 고르도록 둔다. (accounts_js.py와 동일한 정책)
  Shiny.addCustomMessageHandler('st_ticker_lookup_result', function(m) {
    var btn = document.getElementById('st-new-ticker-lookup-btn');
    if (btn) { btn.textContent = '🔍'; btn.disabled = false; }
    if (m.name) {
      var nameEl = document.getElementById('st-new-ticker-name');
      if (nameEl) nameEl.value = m.name;
      if (m.market) {
        var marketEl = document.getElementById('st-new-ticker-market');
        if (marketEl) marketEl.value = m.market;
      }
      if (m.leverage != null) {
        var levEl = document.getElementById('st-new-ticker-leverage');
        if (levEl) levEl.value = String(m.leverage);
      }
    } else {
      alert('종목명을 찾지 못했습니다: ' + m.ticker);
    }
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

  // ── 서버 관리 ────────────────────────────────────────────
  window.stShowServerModal = function() {
    document.getElementById('st-server-list-view').style.display = '';
    document.getElementById('st-server-log-view').style.display = 'none';
    document.getElementById('st-server-modal-overlay').style.display = '';
  };

  window.stHideServerModal = function() {
    document.getElementById('st-server-modal-overlay').style.display = 'none';
    stBackToServiceList();
    var box = document.getElementById('st-server-modal-box');
    if (box) {
      box.style.position = '';
      box.style.left = '';
      box.style.top = '';
      box.style.margin = '';
    }
  };

  window.stRestartService = function(svc) {
    Shiny.setInputValue('settings-btn_restart_service', svc, {priority: 'event'});
  };

  window.stViewLog = function(svc) {
    var logView = document.getElementById('st-server-log-view');
    logView.dataset.service = svc;
    document.getElementById('st-log-title').textContent = svc + ' 로그';
    document.getElementById('st-log-content').textContent = '불러오는 중...';
    document.getElementById('st-server-list-view').style.display = 'none';
    logView.style.display = '';
    Shiny.setInputValue('settings-btn_view_log', svc, {priority: 'event'});
  };

  window.stBackToServiceList = function() {
    var logView = document.getElementById('st-server-log-view');
    logView.dataset.service = '';
    logView.style.display = 'none';
    document.getElementById('st-server-list-view').style.display = '';
    Shiny.setInputValue('settings-btn_close_log', Date.now(), {priority: 'event'});
  };

  // 서버관리 모달 드래그 이동 — 헤더(.modal-header-row)가 드래그 핸들.
  // Pointer Events로 마우스/터치를 하나의 이벤트 체계로 처리(별도 mousedown/touchstart 분기 불필요).
  // 최초 드래그 시작 시 base.css의 flex 중앙정렬(position 미지정)에서
  // position:fixed + left/top(px) 고정 좌표로 전환한다. (다른 모달의 .modal-box는 영향 없음 — id로 한정)
  var _stDragState = null; // {offsetX, offsetY, pointerId}

  window.stStartDragServerModal = function(evt) {
    var box = document.getElementById('st-server-modal-box');
    if (!box) return;
    var rect = box.getBoundingClientRect();
    box.style.position = 'fixed';
    box.style.left = rect.left + 'px';
    box.style.top = rect.top + 'px';
    box.style.margin = '0';

    _stDragState = {
      offsetX: evt.clientX - rect.left,
      offsetY: evt.clientY - rect.top,
      pointerId: evt.pointerId,
    };
    evt.target.setPointerCapture(evt.pointerId);
  };

  document.addEventListener('pointermove', function(evt) {
    if (!_stDragState || evt.pointerId !== _stDragState.pointerId) return;
    var box = document.getElementById('st-server-modal-box');
    if (!box) return;
    box.style.left = (evt.clientX - _stDragState.offsetX) + 'px';
    box.style.top  = (evt.clientY - _stDragState.offsetY) + 'px';
  });

  document.addEventListener('pointerup', function(evt) {
    if (!_stDragState || evt.pointerId !== _stDragState.pointerId) return;
    _stDragState = null;
  });

  document.addEventListener('pointercancel', function(evt) {
    if (!_stDragState || evt.pointerId !== _stDragState.pointerId) return;
    _stDragState = null;
  });

  // 폴링 결과 반영 — 화면 전환 후 늦게 도착한 응답은 dataset.service 불일치로 무시됨.
  // append=false(최초 오픈)는 통째로 교체, append=true(증분분)는 기존 내용 뒤에 이어붙임.
  Shiny.addCustomMessageHandler('st_log_update', function(m) {
    var logView = document.getElementById('st-server-log-view');
    if (!logView || logView.dataset.service !== m.service) return;
    var el = document.getElementById('st-log-content');
    if (m.append) {
      el.textContent += (el.textContent ? '\\n' : '') + m.log;
    } else {
      el.textContent = m.log;
    }
    el.scrollTop = el.scrollHeight;
  });

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

  // st_tick용: dynamic 필드만 (수신된 필드만 존재) — 부분 필드를 캐시에 병합해서 렌더링.
  // diff_display_split이 변경된 필드만 보내므로(price만 오거나 chg만 오는 경우 있음),
  // 수신 필드만으로 innerHTML을 다시 쓰면 안 온 필드가 사라진다. 마지막 값을 캐시해두고 병합한다.
  function _applyOneTickerDynamic(t) {
    // updated_at: 값이 바뀐 티커만 diff에 실려 오므로 필드 유무(!== undefined)로
    // "이번에 갱신됐는지"를 판단한다. null도 유효한 값(=아직 갱신 이력 없음, '-' 표시)
    // 이라 다른 필드들과 달리 != null 체크를 쓰면 안 된다.
    var shouldFlash = (t.updated_at !== undefined);
    if (shouldFlash) {
      _stSetUpdated('st-updated-' + t.id, t.updated_at);
    }

    if (t.price == null && t.chg == null && t.chg_css == null) {
      if (shouldFlash) _stFlashChangeBox(t.id);
      return;
    }

    var cache = _stDynamicCache[t.id] || {};
    if (t.price != null)   cache.price   = t.price;
    if (t.chg != null)     cache.chg     = t.chg;
    if (t.chg_css != null) cache.chg_css = t.chg_css;
    _stDynamicCache[t.id] = cache;

    var chgEl = document.getElementById('st-change-' + t.id);
    if (!chgEl) return;

    if (cache.chg) {
      chgEl.innerHTML =
        (cache.price ? '<span class="' + cache.chg_css + '" style="margin-right:4px;">' + cache.price + '</span>' : '') +
        '<span class="' + cache.chg_css + '">' + cache.chg + '</span>';
    } else {
      chgEl.innerHTML = '';
    }

    if (shouldFlash) _stFlashChangeBox(t.id);
  }

})();
"""