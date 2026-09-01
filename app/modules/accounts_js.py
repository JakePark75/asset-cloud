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

  // ── 갱신시각 카운트업 (서버 재통신 없이 클라이언트에서 매초 재계산) ─────
  // portfolio_js.py의 동일 로직과 같은 이유 — 서버는 값이 실제로 바뀔 때만
  // raw epoch(p.updated_at)을 보낸다. 그 값을 레지스트리에 저장해두고,
  // 1초 인터벌이 모든 등록된 DOM에 대해 "지금 - updated_at"을 재계산해서
  // 텍스트만 갱신한다.
  var _acUpdatedAt = {};

  function _acFmtElapsed(updatedAt) {
    if (updatedAt == null) return '-';
    var elapsed = Math.floor(Date.now() / 1000 - updatedAt);
    if (elapsed < 0) elapsed = 0;
    if (elapsed < 60)    return elapsed + 's';
    if (elapsed < 3600)  return Math.floor(elapsed / 60) + 'm';
    if (elapsed < 86400) return Math.floor(elapsed / 3600) + 'h';
    return Math.floor(elapsed / 86400) + 'd';
  }

  function _acSetUpdated(domId, updatedAt) {
    _acUpdatedAt[domId] = updatedAt;
    var el = document.getElementById(domId);
    if (el) el.textContent = _acFmtElapsed(updatedAt);
  }

  setInterval(function() {
    Object.keys(_acUpdatedAt).forEach(function(domId) {
      var el = document.getElementById(domId);
      if (el) el.textContent = _acFmtElapsed(_acUpdatedAt[domId]);
    });
  }, 1000);

  // ── 시세 갱신 시 가격박스 깜빡임(테두리) ────────────────────
  // portfolio_js.py의 _pfFlashPriceBox와 동일한 이유/방식.
  var _acFlashTimers = {};

  function _acFlashPriceBox(id) {
    var box = document.getElementById('ac-pricebox-' + id);
    if (!box) return;
    var chgEl = document.getElementById('ac-chg-' + id);
    var color = chgEl ? getComputedStyle(chgEl).color : '';

    if (_acFlashTimers[id]) clearTimeout(_acFlashTimers[id]);

    box.style.outline = '1px solid ' + (color || 'currentColor');
    box.style.outlineOffset = '1px';

    _acFlashTimers[id] = setTimeout(function() {
      box.style.outline = '';
      box.style.outlineOffset = '';
      delete _acFlashTimers[id];
    }, 500);
  }

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
    _acInitSortable();
    // 새로고침 후 상태 복원 로직이 "계좌 목록 렌더링 완료"를 알 수 있도록 알림
    document.dispatchEvent(new CustomEvent('ac:list_init'));
  });

  // ── 계좌 카드 드래그 정렬 (SortableJS) ────────────────────────────────────
  // ac_list_init은 #ac-account-list의 innerHTML을 통째로 교체하므로, 이전
  // SortableJS 인스턴스가 붙어있던 DOM 노드가 매번 파괴된다. 따라서 innerHTML
  // 교체가 일어날 때마다(즉 ac_list_init이 올 때마다) 재초기화가 필요하다.
  function _loadSortable(cb) {
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

  var _acSortable = null;
  var _acSettleToken = 0; // dragEnd 이후 실제 스크롤 수렴을 기다리는 루프(현재 비활성화)의
                          // 유효성 토큰. 재활성화 시 이전 드래그의 settle 루프가 뒤늦게
                          // transform을 건드리지 못하게 막는 용도.

  function _acInitSortable() {
    var el = document.getElementById('ac-account-list-normal');
    if (!el) return;
    if (_acSortable) { _acSortable.destroy(); _acSortable = null; }
    _loadSortable(function() {
      // innerHTML 교체 중 이미 다른 노드로 바뀌었을 수 있으니 재조회
      var el2 = document.getElementById('ac-account-list-normal');
      if (!el2) return;
      _acSortable = Sortable.create(el2, {
        delay: 150,
        delayOnTouchOnly: true,
        animation: 150,
        dataIdAttr: 'data-account-id',
        fallbackOnBody: true,   // ghost를 document.body에 직접 부착 — transform 대상의
                                 // 후손이 되지 않도록 containing block 재해석 문제를 회피한다.
                                 // (portfolio.py와 동일 — 세션9 검증분 이식)
        scroll: true,
        scrollSensitivity: 150,  // px, 화면 가장자리로부터 이 거리 안이면 스크롤 시작
        scrollSpeed: 15,         // px/frame(24ms tick), scrollFn에 전달되는 offset의 스케일
                                 // (portfolio.py와 동일값으로 수정 — 기존 400은 이식 시
                                 // 남아있던 구값으로 추정, 별도 검증은 안 됨)

        // ── transform 기반 자체 오토스크롤 ────────────────────────────────
        // portfolio.py(포트폴리오 탭)와 완전히 동일한 상위 구조(#asset-root
        // 아래 히어로/서브탭 네비바 공유)를 쓰는 페이지이므로, 포트폴리오에서
        // 세션5~9에 걸쳐 진단·검증한 것과 동일한 원인·동일한 해결 방식을 그대로
        // 적용함(별도 재검증 없이 이식 — 계좌 목록에서 다른 양상이 나오면 그때
        // 별도 진단 필요).
        //
        // 원인 요약: iOS Safari가 터치 활성 중 실제 scrollTop 반영을 지연시킴
        // → SortableJS 네이티브 스크롤(scroll:true 기본 동작) 대신 offsetY를
        // #asset-root의 transform: translateY()에 누적 반영.
        //
        // 대상이 #ac-account-list가 아니라 #asset-root인 이유: 히어로(.db-hero)와
        // 서브탭 네비바(.asset-sub-tabbar)가 #ac-account-list와 형제 관계가
        // 아니라 훨씬 상위(#asset-root)의 형제 요소로 존재함(포트폴리오와 동일
        // 구조로 확인됨). 리스트에만 transform을 걸면 그 위에 겹쳐 그려짐.
        //
        // 위 방향 오토스크롤 잠금 해제용 1px 실스크롤 동기화 로직도 포트폴리오와
        // 동일한 이유(AutoScroll.js의 vy 계산이 `!!scrollPosY`를 요구)로 필요함.
        scrollFn: function(offsetX, offsetY, originalEvent, touchEvt, hoverTargetEl) {
          var rootEl = document.getElementById('asset-root');
          if (!rootEl || !offsetY) return;

          var maxScroll = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
          var proposed = window._acFakeScrollY + offsetY;
          var proposedAbs = window._acScrollStartY + proposed;

          if (proposedAbs < 0) proposed = -window._acScrollStartY;
          if (proposedAbs > maxScroll) proposed = maxScroll - window._acScrollStartY;

          window._acFakeScrollY = proposed;

          // 위 방향 오토스크롤 잠금 해제용 1px 실스크롤 동기화
          if (window._acFakeScrollY > 0 && !window._acRealScrollNudge) {
            window.scrollTo(0, window._acScrollStartY + 1);
            window._acRealScrollNudge = 1;
          } else if (window._acFakeScrollY <= 0 && window._acRealScrollNudge) {
            window.scrollTo(0, window._acScrollStartY);
            window._acRealScrollNudge = 0;
          }

          var visualShift = window._acFakeScrollY - window._acRealScrollNudge;
          rootEl.style.transform = 'translateY(' + (-visualShift) + 'px)';
        },

        onStart: function() {
          window._acFakeScrollY = 0;
          window._acRealScrollNudge = 0;
          window._acScrollStartY = window.scrollY;
          _acSettleToken++; // 이전 드래그의 settle 루프(있다면, 현재 비활성화)를 무효화
          var rootEl = document.getElementById('asset-root');
          if (rootEl) rootEl.style.transition = 'none';
        },

        // ============================================================================
        // [포트폴리오에서 이식 — 방향 A 채택, 세션9 검증 완료분]
        // scrollTo에 behavior:'instant'를 명시하면 real scrollY가 동기적으로 즉시
        // 반영되어(포트폴리오 실기기 4회 재현으로 검증 완료) "따라잡을 gap" 자체가
        // 없어짐. 따라서 scrollTo 호출 직후 바로 transform을 지워도 됨 — 매 프레임
        // gap을 관찰하며 보정하던 settle rAF 루프는 불필요해짐.
        //
        // settle 루프를 완전히 삭제하지 않고 주석으로 남겨두는 이유(포트폴리오와
        // 동일): 아래 케이스들은 검증되지 않았음(미검증 — 팩트 아님, 우려 사항):
        //   - 매우 빠르게 연속으로 드래그를 반복하는 경우
        //   - 손을 뗀 시점에 관성(모멘텀) 스크롤이 이미 진행 중이던 경우와 겹침
        //   - 스크롤 최상단/최하단에서 iOS 바운스(rubber-band)와 겹치는 경우
        //   - 저사양 기기·부하 상황
        // 위 케이스 중 하나에서 튐 증상이 재현되면, 아래 주석을 해제해 안전망으로
        // 복원할 것.
        // ============================================================================
        onEnd: function() {
          var rootEl = document.getElementById('asset-root');
          var targetScroll = window._acScrollStartY + window._acFakeScrollY;
          var myToken = ++_acSettleToken;

          if (window._acFakeScrollY === 0) {
            if (rootEl) {
              rootEl.style.transform = '';
              rootEl.style.transition = '';
            }
            window._acRealScrollNudge = 0;
          } else {
            window.scrollTo({ top: targetScroll, left: 0, behavior: 'instant' });

            if (rootEl) {
              rootEl.style.transform = '';
              rootEl.style.transition = '';
            }
            window._acFakeScrollY = 0;
            window._acRealScrollNudge = 0;

            /* ── [비활성화] settle rAF 보정 루프 (안전망으로 보존) ────────────────
            var TOLERANCE = 1;
            var MAX_FRAMES = 90;

            (function _acSettle(frame) {
              if (myToken !== _acSettleToken) return;

              var currentReal = window.scrollY;
              var remaining = targetScroll - currentReal;

              if (Math.abs(remaining) <= TOLERANCE || frame >= MAX_FRAMES) {
                if (rootEl) {
                  rootEl.style.transform = '';
                  rootEl.style.transition = '';
                }
                window._acFakeScrollY = 0;
                window._acRealScrollNudge = 0;
                return;
              }

              if (rootEl) rootEl.style.transform = 'translateY(' + (-remaining) + 'px)';

              requestAnimationFrame(function() { _acSettle(frame + 1); });
            })(0);
            */
          }

          var order = _acSortable.toArray();
          Shiny.setInputValue(window._acNs + '-account_reorder', order, { priority: 'event' });
        },
      });
    });
  }

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
    if (c.cash_pct != null) {
      var barEl = document.getElementById('ac-card-cashbar-' + c.id);
      // 이전에 적용한 % 값과 다를 때만 width를 건드림 (가격 tick마다 오는
      // pnl/total 갱신 신호에 얹혀 매번 DOM write가 발생하지 않도록 방지)
      if (barEl && barEl.dataset.pct !== String(c.cash_pct)) {
        barEl.style.width = c.cash_pct + '%';
        barEl.dataset.pct = String(c.cash_pct);
      }
    }
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

    // updated_at: portfolio_js.py의 _applyOneTicker와 동일한 이유로 !== undefined 사용.
    if (p.updated_at !== undefined) {
      _acSetUpdated('ac-updated-' + p.id, p.updated_at);
      _acFlashPriceBox(p.id);
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

  // ── 티커 조회 결과 반영 ───────────────────────────────────────────────
  // 레버리지는 서버(KIS 응답)가 명확히 판단 가능한 경우(1/2/3배)에만 m.leverage로 전달됨.
  // 그 외(비정수 배수, yf 폴백 등)에는 leverage 필드 자체가 없으므로 select 값을
  // 건드리지 않고 사용자가 직접 고르도록 둔다.
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
      if (m.leverage != null) {
        var levEl = document.getElementById('ac-new-pos-leverage');
        if (levEl) levEl.value = String(m.leverage);
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
      if (m.leverage != null) {
        var levEl = document.getElementById('ac-edit-pos-leverage');
        if (levEl) levEl.value = String(m.leverage);
      }
    } else {
      alert('종목명을 찾지 못했습니다: ' + m.ticker);
    }
  });

  // ── 종목명 자동완성 (현재 계좌에 없는 기존 보유 종목 검색) ─────────────
  // 티커/시장/레버리지는 tickers 테이블 기준 하나로 고정되어 있으므로,
  // 검색 결과를 클릭하면 재조회 없이 그 값을 그대로 채운다.
  var _nameSearchTimer = null;

  window.acHandleNameInput = function() {
    var nameEl      = document.getElementById('ac-new-pos-name');
    var query       = nameEl ? nameEl.value.trim() : '';
    var dropdownEl  = document.getElementById('ac-new-pos-name-dropdown');

    if (_nameSearchTimer) clearTimeout(_nameSearchTimer);

    if (!query) {
      if (dropdownEl) { dropdownEl.innerHTML = ''; dropdownEl.style.display = 'none'; }
      return;
    }

    _nameSearchTimer = setTimeout(function() {
      Shiny.setInputValue(window._acNs + '-search_ticker_name', query, {priority: 'event'});
    }, 300);
  };

  Shiny.addCustomMessageHandler('ac_ticker_name_search_result', function(m) {
    var nameEl     = document.getElementById('ac-new-pos-name');
    var dropdownEl = document.getElementById('ac-new-pos-name-dropdown');
    if (!dropdownEl) return;

    // 응답이 오는 사이 입력값이 바뀌었으면(레이스 컨디션) 무시
    var curQuery = nameEl ? nameEl.value.trim() : '';
    if (m.query !== curQuery) return;

    if (!m.results || m.results.length === 0) {
      dropdownEl.innerHTML = '';
      dropdownEl.style.display = 'none';
      return;
    }

    dropdownEl.innerHTML = m.results.map(function(r) {
      var badge = (r.leverage && r.leverage > 1)
        ? '<span class="lev-badge lev-x' + r.leverage + '">x' + r.leverage + '</span>'
        : '';
      var safeName = String(r.name).replace(/"/g, '&quot;');
      return (
        '<div class="ac-autocomplete-item" ' +
        'data-ticker="' + r.ticker + '" ' +
        'data-name="' + safeName + '" ' +
        'data-market="' + r.market + '" ' +
        'data-leverage="' + r.leverage + '" ' +
        'onclick="acSelectAutocompleteTicker(this);">' +
          '<span class="ac-autocomplete-name">' + r.name + '</span>' + badge +
        '</div>'
      );
    }).join('');
    dropdownEl.style.display = '';
  });

  window.acSelectAutocompleteTicker = function(el) {
    var tickerEl   = document.getElementById('ac-new-pos-ticker');
    var nameEl     = document.getElementById('ac-new-pos-name');
    var marketEl   = document.getElementById('ac-new-pos-market');
    var leverageEl = document.getElementById('ac-new-pos-leverage');
    var dropdownEl = document.getElementById('ac-new-pos-name-dropdown');

    if (tickerEl)   tickerEl.value   = el.getAttribute('data-ticker');
    if (nameEl)     nameEl.value     = el.getAttribute('data-name');
    if (marketEl)   marketEl.value   = el.getAttribute('data-market');
    if (leverageEl) leverageEl.value = el.getAttribute('data-leverage');

    if (dropdownEl) { dropdownEl.innerHTML = ''; dropdownEl.style.display = 'none'; }
    acUpdateAddPreview();
  };

  // 드롭다운 바깥을 클릭하면 닫기
  // (capture 단계에서 감지 — 모달박스의 event.stopPropagation()이
  //  버블링 단계에서 이벤트를 막기 전에 먼저 실행되도록 함)
  document.addEventListener('click', function(e) {
    var dropdownEl = document.getElementById('ac-new-pos-name-dropdown');
    var nameEl     = document.getElementById('ac-new-pos-name');
    if (!dropdownEl || dropdownEl.style.display === 'none') return;
    if (e.target === nameEl || dropdownEl.contains(e.target)) return;
    dropdownEl.innerHTML = '';
    dropdownEl.style.display = 'none';
  }, true);

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