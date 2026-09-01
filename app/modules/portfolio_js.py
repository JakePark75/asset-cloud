"""
portfolio_ui() 내에서 사용하는 클라이언트 JS.
accounts_js.py 와 동일한 패턴으로 분리됨.
"""


def portfolio_js() -> str:
    return """
(function() {

  // ── 갱신시각 카운트업 (서버 재통신 없이 클라이언트에서 매초 재계산) ─────
  // 서버는 값이 실제로 바뀔 때만 raw epoch(t.updated_at)을 보낸다. 그 값을
  // 여기 레지스트리에 저장해두고, 1초 인터벌이 모든 등록된 DOM에 대해
  // "지금 - updated_at"을 다시 계산해서 텍스트만 갱신한다. 서버 push는 값이
  // 바뀔 때만 오므로, 매초 증가하는 표시 자체는 서버 통신 없이 처리된다.
  var _pfUpdatedAt = {}; // { domId: epochSeconds|null }

  function _pfFmtElapsed(updatedAt) {
    if (updatedAt == null) return '-';
    var elapsed = Math.floor(Date.now() / 1000 - updatedAt);
    if (elapsed < 0) elapsed = 0;
    if (elapsed < 60)    return elapsed + 's';
    if (elapsed < 3600)  return Math.floor(elapsed / 60) + 'm';
    if (elapsed < 86400) return Math.floor(elapsed / 3600) + 'h';
    return Math.floor(elapsed / 86400) + 'd';
  }

  function _pfSetUpdated(domId, updatedAt) {
    _pfUpdatedAt[domId] = updatedAt;
    var el = document.getElementById(domId);
    if (el) el.textContent = _pfFmtElapsed(updatedAt);
  }

  setInterval(function() {
    Object.keys(_pfUpdatedAt).forEach(function(domId) {
      var el = document.getElementById(domId);
      if (el) el.textContent = _pfFmtElapsed(_pfUpdatedAt[domId]);
    });
  }, 1000);

  // ── 시세 갱신 시 가격박스 깜빡임(테두리) ────────────────────
  // 티커 id별로 setTimeout 핸들을 들고 있다가, 갱신이 다시 오면 clearTimeout 후
  // 재시작 — "표시 중 재갱신되면 0.5초 다시 카운트" 요구사항을 그대로 구현.
  // 테두리 색은 하드코딩하지 않고, 이미 chg_css(positive/negative) 클래스가 적용된
  // pf-chg-{id}의 실제 계산된 색(getComputedStyle)을 그대로 읽어와 사용한다.
  var _pfFlashTimers = {};

  function _pfFlashPriceBox(id) {
    var box = document.getElementById('pf-pricebox-' + id);
    if (!box) return;
    var chgEl = document.getElementById('pf-chg-' + id);
    var color = chgEl ? getComputedStyle(chgEl).color : '';

    if (_pfFlashTimers[id]) clearTimeout(_pfFlashTimers[id]);

    box.style.outline = '1px solid ' + (color || 'currentColor');
    box.style.outlineOffset = '1px';

    _pfFlashTimers[id] = setTimeout(function() {
      box.style.outline = '';
      box.style.outlineOffset = '';
      delete _pfFlashTimers[id];
    }, 500);
  }

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
    _pfInitSortable();
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

  // 세션5~9에서 오토스크롤 원인 규명용으로 쓰던 진단 로깅 인프라
  // (scrollTop 인터셉터, touchmove/scroll 로깅, 1초 flush 타이머,
  // window._pfDebugLog 등)는 세션9에서 방향 A 검증 완료 후 제거함.
  // 로직이 아니라 계측 코드였으므로, 추후 다른 스크롤 이슈가 재발하면
  // 이 시점 이전 버전을 참고해 필요한 부분만 다시 추가하면 된다.

  // ── 종목 드래그 정렬 (SortableJS) ────────────────────────────────────────
  // pf_init은 #pf-ticker-list의 innerHTML을 통째로 교체하므로, 이전 SortableJS
  // 인스턴스가 붙어있던 DOM 노드가 매번 파괴된다. 따라서 pf_init이 올 때마다
  // 재초기화가 필요하다.
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

  var _pfSortable = null;
  var _pfSettleToken = 0; // dragEnd 이후 실제 스크롤 수렴을 기다리는 루프의 유효성 토큰.
                          // 새 드래그가 시작되면 증가시켜, 이전 드래그의 settle 루프가
                          // 뒤늦게 transform을 건드리지 못하게 막는다.

  function _pfInitSortable() {
    var el = document.getElementById('pf-ticker-list-normal');
    if (!el) return;
    if (_pfSortable) { _pfSortable.destroy(); _pfSortable = null; }
    _loadSortable(function() {
      var el2 = document.getElementById('pf-ticker-list-normal');
      if (!el2) return;
      _pfSortable = Sortable.create(el2, {
        delay: 150,
        delayOnTouchOnly: true,
        animation: 150,
        dataIdAttr: 'data-ticker',
        fallbackOnBody: true,   // ghost를 document.body에 직접 부착 — transform 대상의
                                 // 후손이 되지 않도록 containing block 재해석 문제를 회피한다.
        scroll: true,
        scrollSensitivity: 150,  // px, 화면 가장자리로부터 이 거리 안이면 스크롤 시작
        scrollSpeed: 15,        // px/frame(24ms tick), scrollFn에 전달되는 offset의 스케일

        // ── transform 기반 자체 오토스크롤 ────────────────────────────────
        // iOS Safari가 터치 활성 중 실제 scrollTop 반영을 지연시키는 것이
        // "반전 시 속도 급증" 증상의 근본 원인으로 확인됨(세션5 인수인계 문서).
        // 네이티브 스크롤 API를 아예 안 쓰기 위해, SortableJS가 계산한
        // offsetY(이미 scrollSpeed가 곱해진 24ms당 델타)를 실제 scrollTop에
        // 쓰는 대신 #asset-root의 transform: translateY()에 누적 반영한다.
        //
        // 대상이 #pf-ticker-list가 아니라 #asset-root인 이유: 히어로(.db-hero)와
        // 서브탭 네비바(.asset-sub-tabbar)가 asset.py에서 #pf-ticker-list와 형제
        // 관계가 아니라 훨씬 상위(#asset-root)의 형제 요소로 존재함이 확인됨.
        // 실제 페이지 스크롤이라면 이들도 리스트와 함께 밀려 올라가야 하는데,
        // 리스트에만 transform을 걸면 그 위에 겹쳐 그려지는 문제가 있었음.
        //
        // 드롭 타겟 판별(_emulateDragOver)은 elementFromPoint 기반(뷰포트 좌표)
        // 이라 이 방식과 충돌하지 않음을 소스 확인 완료.
        //
        // AutoScroll.js의 vy 계산: 위 방향 스크롤 조건에 `!!scrollPosY`(실제
        // scrollTop이 0보다 큰지)가 들어있음(소스 확인 완료). 드래그 중 실제
        // scrollTop을 0으로 유지하면 위 방향 오토스크롤이 영원히 막히므로,
        // fakeScrollY가 0→양수로 바뀌는 최초 시점에 실제 scrollTop을 1px만
        // 밀어 이 조건을 만족시킨다. 이 1px은 transform 계산에서 상쇄해
        // 시각적으로는 이중 이동이 안 보이게 한다. 실기기 검증 필요.
        scrollFn: function(offsetX, offsetY, originalEvent, touchEvt, hoverTargetEl) {
          var rootEl = document.getElementById('asset-root');
          if (!rootEl || !offsetY) return;

          var maxScroll = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
          var proposed = window._pfFakeScrollY + offsetY;
          var proposedAbs = window._pfScrollStartY + proposed;

          if (proposedAbs < 0) proposed = -window._pfScrollStartY;
          if (proposedAbs > maxScroll) proposed = maxScroll - window._pfScrollStartY;

          window._pfFakeScrollY = proposed;

          // 위 방향 오토스크롤 잠금 해제용 1px 실스크롤 동기화
          if (window._pfFakeScrollY > 0 && !window._pfRealScrollNudge) {
            window.scrollTo(0, window._pfScrollStartY + 1);
            window._pfRealScrollNudge = 1;
          } else if (window._pfFakeScrollY <= 0 && window._pfRealScrollNudge) {
            window.scrollTo(0, window._pfScrollStartY);
            window._pfRealScrollNudge = 0;
          }

          var visualShift = window._pfFakeScrollY - window._pfRealScrollNudge;
          rootEl.style.transform = 'translateY(' + (-visualShift) + 'px)';
        },

        onStart: function() {
          window._pfFakeScrollY = 0;
          window._pfRealScrollNudge = 0;
          window._pfScrollStartY = window.scrollY;
          _pfSettleToken++; // 이전 드래그의 settle 루프(있다면)를 무효화 — settle 루프 자체는
                             // 현재 비활성화(주석 처리)돼 있지만, 재활성화 시 바로 쓸 수 있도록 유지.
          var rootEl = document.getElementById('asset-root');
          if (rootEl) rootEl.style.transition = 'none';
        },

        // ============================================================================
        // [방향 A 채택 — 세션9] 검증 완료, settle 루프 비활성화
        //
        // 세션8 문서 4-2절 방향 A 가설("scrollTo에 behavior:'instant'를 명시하면
        // real scrollY가 즉시 반영되어 따라잡을 gap 자체가 없어진다")을 세션9에서
        // 검증함. 실기기 4회 재현(위/아래 방향 혼합, target 120~510px) 전부에서:
        //   - scrollTo_called 직후 winScrollImmediatelyAfter가 target과 정확히 일치
        //   - settle 루프가 매번 frame 0에서 즉시 종료 (remaining=0)
        //   - 육안으로도 튐/어긋남 없음 (4회 전부 확인)
        // 결과가 일관되어, settle 루프가 실질적으로 할 일이 없다고 판단해 비활성화함.
        //
        // 완전히 삭제하지 않고 주석으로 남겨두는 이유: 이번 검증은 아래 케이스들을
        // 다루지 않았음(모두 미검증 — 팩트 아님, 우려 사항으로만 기록):
        //   - 매우 빠르게 연속으로 드래그를 반복하는 경우
        //   - 손을 뗀 시점에 관성(모멘텀) 스크롤이 이미 진행 중이던 경우와 겹침
        //   - 스크롤 최상단/최하단에서 iOS 바운스(rubber-band)와 겹치는 경우
        //   - 저사양 기기·부하 상황
        // 위 케이스 중 하나에서 다시 튐 증상이 재현되면, 아래 주석을 해제해
        // 안전망으로 복원할 것. 그 전까지는 정상 케이스에서 불필요한 로직이므로
        // 비활성화 상태를 유지한다.
        // ============================================================================

        onEnd: function() {
        var rootEl = document.getElementById('asset-root');
        var targetScroll = window._pfScrollStartY + window._pfFakeScrollY;
        var myToken = ++_pfSettleToken;

        if (window._pfFakeScrollY === 0) {
            if (rootEl) {
            rootEl.style.transform = '';
            rootEl.style.transition = '';
            }
            window._pfRealScrollNudge = 0;
        } else {
            // 방향 A: behavior:'instant' — real scrollY가 동기적으로 즉시 target에
            // 반영됨(세션9 실기기 검증 완료). 따라서 scrollTo 호출 직후 바로
            // transform을 지워도 됨 — settle로 따라잡을 gap이 없다.
            window.scrollTo({ top: targetScroll, left: 0, behavior: 'instant' });

            if (rootEl) {
            rootEl.style.transform = '';
            rootEl.style.transition = '';
            }
            window._pfFakeScrollY = 0;
            window._pfRealScrollNudge = 0;

            /* ── [비활성화 — 세션9] settle rAF 보정 루프 ──────────────────────────
               세션7~8에서 쓰던 fallback: scrollTo가 즉시 반영되지 않는다는 전제
               하에, real scrollY를 매 프레임 관찰하며 그 gap만큼 transform으로
               계속 보정하던 루프. behavior:'instant' 적용 후에는 gap이 발생하지
               않아(세션9 검증 완료) 이 루프가 실행될 일이 없으므로 비활성화함.
               위 주석에 적은 미검증 엣지케이스에서 튐이 재현되면 복원할 것.

            var TOLERANCE = 1;
            var MAX_FRAMES = 90;

            (function _pfSettle(frame) {
              if (myToken !== _pfSettleToken) {
                _pfDebugLog('settle_aborted_new_drag', { frame: frame });
                return;
              }

              var currentReal = window.scrollY;
              var remaining = targetScroll - currentReal;

              if (Math.abs(remaining) <= TOLERANCE || frame >= MAX_FRAMES) {
                if (rootEl) {
                  rootEl.style.transform = '';
                  rootEl.style.transition = '';
                }
                window._pfFakeScrollY = 0;
                window._pfRealScrollNudge = 0;
                _pfDebugLog('settle_complete', {
                  frame: frame,
                  finalReal: currentReal,
                  remaining: remaining,
                  timedOut: frame >= MAX_FRAMES,
                });
                return;
              }

              if (rootEl) rootEl.style.transform = 'translateY(' + (-remaining) + 'px)';
              _pfDebugLog('settle_tick', { frame: frame, real: currentReal, remaining: remaining });

              requestAnimationFrame(function() { _pfSettle(frame + 1); });
            })(0);
            */
        }

        var order = _pfSortable.toArray();
        Shiny.setInputValue(window._pfNs + '-ticker_reorder', order, { priority: 'event' });
        },
      });
    });
  }

  // pf_init용: static + dynamic 전체 적용
  function _applyTickers(tickers) {
    Object.values(tickers).forEach(function(t) { _applyOneTickerFull(t); });
  }

  // 시장상태 배지: dot/텍스트/색상클래스를 각각 독립적으로 갱신한다.
  // (원인: day→pre 전환 시 dot("●")은 안 바뀌어 diff에서 빠지는데, 예전엔
  //  dot+txt를 하나의 textContent로 합쳐서 dot 유무로 전체를 지웠다 —
  //  2026-08 실사용 중 배지가 통째로 사라지는 버그로 발견됨. price/chg 처리
  //  (_applyOneTicker)와 동일하게 "필드 하나 = 자기 요소만 갱신" 원칙으로 수정.)
  function _applyStatus(t) {
    if (t.status_dot != null) {
      var dotEl = document.getElementById('pf-status-dot-' + t.id);
      if (dotEl) dotEl.textContent = t.status_dot;
    }
    if (t.status_txt != null) {
      var txtEl = document.getElementById('pf-status-txt-' + t.id);
      if (txtEl) txtEl.textContent = t.status_txt;
    }
    if (t.status_cls != null) {
      var stEl = document.getElementById('pf-status-' + t.id);
      if (stEl) stEl.className = 'ticker-status ' + t.status_cls;
    }
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

    _applyStatus(t);

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

    _applyStatus(t);
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

    // updated_at: 값이 바뀐 티커만 diff에 실려 오므로 필드 유무(!== undefined)로
    // "이번에 갱신됐는지"를 판단한다. null도 유효한 값(=아직 갱신 이력 없음, '-' 표시)
    // 이라 다른 필드들과 달리 != null 체크를 쓰면 안 된다.
    if (t.updated_at !== undefined) {
      _pfSetUpdated('pf-updated-' + t.id, t.updated_at);
      _pfFlashPriceBox(t.id);
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
"""