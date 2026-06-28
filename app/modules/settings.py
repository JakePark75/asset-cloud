from shiny import ui, render, module, reactive
from app.db import get_db, get_config, save_config, get_market_currency, get_market_map
from app.modules.components import fmt_change
from app.price_signal import price_signal, ticker_signal
from scheduler.price_updater_common import get_market_status
from app.utils.display_diff import diff_display, diff_display_split
from common.redis_store import (
    get_all_prices,
    get_news_feed_cache,
    publish_ticker_changed,
    publish_news_source_changed,
    publish_news_keyword_changed,
)
import re
import subprocess
from deep_translator import GoogleTranslator
from deep_translator.exceptions import (
    TooManyRequests,
    RequestError,
    TranslationNotFound,
    NotValidLength,
    NotValidPayload,
)


def _notify_ticker_changed():
    """
    티커 추가/삭제 후 다른 화면들에게 갱신 신호를 보낸다.

    배경:
      티커가 추가/삭제되어도 price_updater 의 신호(Redis pub/sub)가 오기 전까지
      포트폴리오/대시보드 등 다른 화면은 변경을 인지하지 못한다.
      티커 변경은 시세 변경과 독립적인 이벤트이므로 직접 Redis pub/sub 신호를 발행한다.

    주의:
      - ticker_changed 채널을 사용 — price_updated와 분리됨.
      - 실패해도 설정 화면 자체의 갱신(refresh)에는 영향 없으므로 예외를 삼킨다.
    """
    try:
        publish_ticker_changed()
    except Exception as e:
        print(f"[settings] ticker_changed 신호 발행 실패 (무시): {e}")


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

_MARKET_ORDER = {
    "KR": 0,
    "NAS": 1, "NYS": 1, "AMS": 1, "ARC": 1,
    "CRYPTO": 2,
    "COM": 3,
    "FX": 4, "INDEX": 4,
}

def _ticker_to_id(ticker: str) -> str:
    return ticker.replace("-", "_").replace("^", "_").replace("=", "_")

def _sort_key(r):
    ticker, _, market, leverage, is_manual = r
    return (
        0 if is_manual else 1,
        _MARKET_ORDER.get(market, 99),
        -(leverage or 1),
        ticker,
    )

def _build_row_skeleton(ticker, name, market, leverage, is_manual, ns_str):
    """구조 변경 시 1회 전송하는 골격 HTML."""
    tid      = _ticker_to_id(ticker)
    leverage = int(leverage) if leverage else 1

    lev_html = (
        f'<span id="st-lev-{tid}" class="lev-badge lev-x{leverage}" '
        f'style="{" " if leverage > 1 else "display:none;"}">x{leverage}</span>'
    )

    delete_html = (
        f'<button class="btn-danger-sm" '
        f'onclick="if(confirm(\'{ticker} 티커를 삭제할까요?\')) '
        f'Shiny.setInputValue(\'{ns_str}confirm_delete_ticker\', \'{ticker}\', {{priority: \'event\'}});">'
        f'삭제</button>'
    ) if is_manual else '<div></div>'

    auto_attr = '' if is_manual else ' data-auto="1"'

    return (
        f'<div class="ticker-row" id="st-row-{tid}"{auto_attr}>'
        f'  <div>'
        f'    <div class="lev-name-wrap">'
        f'      {lev_html}'
        f'      <span id="st-name-{tid}" class="ticker-name">{name}</span>'
        f'      <span id="st-status-{tid}" class="ticker-status"></span>'
        f'    </div>'
        f'    <div class="ticker-qty">{ticker} / <span id="st-market-{tid}">{market}</span></div>'
        f'  </div>'
        f'  <div class="ticker-row-btn" style="display:flex; flex-direction:column; align-items:flex-end; gap:0;">'
        f'    {delete_html}'
        f'    <div class="ticker-change" id="st-change-{tid}"></div>'
        f'  </div>'
        f'</div>'
    )





def _build_tick_values(ticker, name, market, leverage, price, change_pct):
    """시세 갱신 시마다 전송하는 값 — static/dynamic 분리 구조."""
    tid      = _ticker_to_id(ticker)
    leverage = int(leverage) if leverage else 1

    currency = get_market_currency(market)
    price_str, chg_str, chg_css = fmt_change(price, change_pct, currency=currency)

    status = get_market_status(market)
    dot_map = {
        "open":    ("●", "Open",  "status-open"),
        "pre":     ("●", "Pre",   "status-pre"),
        "after":   ("●", "After", "status-after"),
    }
    status_dot, status_text, status_cls = dot_map.get(status, ("○", "Closed", "status-closed"))

    return {
        "static": {
            "id":         tid,
            "name":       name,
            "leverage":   leverage,
            "market":     market,
            "status_dot": status_dot,
            "status_txt": status_text,
            "status_cls": status_cls,
        },
        "dynamic": {
            "id":      tid,
            "price":   price_str,
            "chg":     chg_str,
            "chg_css": chg_css,
        },
    }


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def settings_ui():
    market_choices = list(get_market_map().keys())
    market_options = "".join(f'<option value="{m}">{m}</option>' for m in market_choices)

    return ui.div(
        ui.tags.style("""
/* ── 실시간 토글 스위치 ──────────────────── */
.toggle-track {
  display: inline-block;
  width: 42px; height: 24px;
  background: #333;
  border-radius: 12px;
  position: relative;
  transition: background 0.2s;
  flex-shrink: 0;
}
.toggle-track::after {
  content: '';
  position: absolute;
  top: 3px; left: 3px;
  width: 18px; height: 18px;
  background: #888;
  border-radius: 50%;
  transition: transform 0.2s, background 0.2s;
}
#st-realtime-toggle:checked ~ .toggle-track {
  background: #00c073;
}
#st-realtime-toggle:checked ~ .toggle-track::after {
  transform: translateX(18px);
  background: #fff;
}

/* ── 뉴스 소스 토글 (범용, class 기반) ────── */
.news-toggle-checkbox:checked ~ .toggle-track {
  background: #00c073;
}
.news-toggle-checkbox:checked ~ .toggle-track::after {
  transform: translateX(18px);
  background: #fff;
}
.news-source-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 0;
}
.news-source-name { font-size: 13px; color: #ccc; }

/* ── 언어 배지 ────────────────────────────── */
.news-lang-badge {
  display: inline-block;
  font-size: 10px; font-weight: bold;
  padding: 1px 5px; border-radius: 4px;
  flex-shrink: 0;
}
.news-lang-en { background: #1a3a5c; color: #5ab4ff; }
.news-lang-ko { background: #3a1a1a; color: #ff7070; }

/* ── 키워드 칩 ────────────────────────────── */
.news-keyword-chip {
  display: inline-flex; align-items: center; gap: 6px;
  background: #1e1e1e; border-radius: 14px;
  padding: 4px 10px; margin: 4px 4px 0 0;
  font-size: 12px; color: #ccc;
}
.news-keyword-chip:hover { background: #2a2a2a; }

/* ── 뉴스 피드 ────────────────────────────── */
.news-feed-item {
  padding: 10px 0; border-bottom: 1px solid #1e1e1e;
}
.news-feed-item.news-read .news-feed-title {
  color: #666;
}
.news-feed-title { font-size: 13px; color: #eee; text-decoration: none; }
.news-feed-title:hover { text-decoration: underline; }
.news-feed-meta {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 11px; color: #888; margin-top: 4px;
}
.news-kw-wrap { display: flex; gap: 4px; flex-wrap: wrap; }
.news-matched-kw {
  font-size: 10px; padding: 1px 6px; border-radius: 10px;
  background: #1e3a1e; color: #6ecf6e;
}

/* ── 뉴스 소스/키워드 편집 모달 ──────────── */
.news-edit-modal-overlay {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.7);
  display: flex; align-items: center; justify-content: center;
  z-index: 9999;
}
.news-edit-modal-box {
  background: #1a1a1a; border-radius: 12px;
  padding: 24px; width: 320px; max-width: 90vw;
}
.news-edit-modal-title {
  font-size: 14px; font-weight: bold; color: #eee; margin-bottom: 16px;
}
.news-edit-field { margin-bottom: 12px; }
.news-edit-field label { font-size: 11px; color: #888; display: block; margin-bottom: 4px; }
.news-edit-lang-row {
  display: flex; gap: 8px;
}
.news-edit-lang-btn {
  flex: 1; padding: 6px; border-radius: 6px; border: 1px solid #333;
  background: #111; color: #888; font-size: 12px; cursor: pointer;
}
.news-edit-lang-btn.active-en { background: #1a3a5c; color: #5ab4ff; border-color: #5ab4ff; }
.news-edit-lang-btn.active-ko { background: #3a1a1a; color: #ff7070; border-color: #ff7070; }
.news-edit-action-row {
  display: flex; gap: 8px; margin-top: 16px;
}
.news-edit-action-row button { flex: 1; padding: 8px; border-radius: 6px; border: none; font-size: 13px; cursor: pointer; }
.btn-modal-save   { background: #00c073; color: #000; }
.btn-modal-cancel { background: #333; color: #ccc; }
.btn-modal-delete { background: #3a1a1a; color: #ff7070; }
/* ── 뉴스 슬라이드업 패널 ─────────────────────────────── */
.st-news-panel {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.6);
  display: none; flex-direction: column;
  z-index: 9998;
  align-items: stretch;
}
.st-news-panel-inner {
  position: absolute; bottom: 0; left: 0; right: 0;
  height: 90vh;
  background: #111;
  border-radius: 16px 16px 0 0;
  display: flex; flex-direction: column;
  transform: translateY(100%);
  transition: transform 0.3s ease;
}
.st-news-panel-open .st-news-panel-inner {
  transform: translateY(0);
}
.st-news-panel-header {
  display: flex; justify-content: flex-end; align-items: center;
  padding: 12px 16px 8px;
  flex-shrink: 0;
}
.st-news-panel-close {
  background: none; border: none; color: #888;
  font-size: 20px; cursor: pointer; padding: 4px 8px;
}
.st-news-panel-close:hover { color: #eee; }
.st-news-panel-iframe {
  flex: 1; border: none; background: #fff;
  border-radius: 0 0 0 0;
}
.st-news-panel-error {
  padding: 24px; color: #aaa; font-size: 13px; text-align: center;
}
        """),
        ui.tags.script("""
(function() {

  // ── st_init: 종목 구성 변경 시 골격 통째 교체 ──────────────
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

  // ── st_news_sources: id 기준 DOM diff ───────────────────────
  Shiny.addCustomMessageHandler('st_news_sources', function(m) {
    var listEl = document.getElementById('st-news-sources-list');
    if (!listEl) return;

    // toggled: enabled 상태 변경 + 피드 hide/unhide
    if (m.toggled) {
      var cb = listEl.querySelector('.news-source-row[data-src-id="' + m.toggled.id + '"] .news-toggle-checkbox');
      if (cb) {
        cb.removeAttribute('onchange');
        cb.checked = m.toggled.enabled;
        var ns = listEl.querySelector('.news-source-row[data-src-id]').dataset.srcNs || 'settings-';
        cb.setAttribute('onchange',
          "Shiny.setInputValue('" + ns + "toggle_news_source'," +
          "{id:" + m.toggled.id + ",enabled:this.checked},{priority:'event'});");
      }
      var feedList = document.getElementById('st-news-feed-list');
      if (feedList && m.toggled.source_name) {
        if (!m.toggled.enabled) {
          // 비활성화: 해당 소스 기사 hide
          var hideCount = 0;
          feedList.querySelectorAll('.news-feed-item[data-source]').forEach(function(el) {
            if (el.dataset.source === m.toggled.source_name) {
              el.style.display = 'none';
              hideCount++;
            }
          });
          console.log('[news_feed] hide:', m.toggled.source_name, hideCount + '건');
          Shiny.setInputValue('settings-js_log', '[news_feed] hide: ' + m.toggled.source_name + ' ' + hideCount + '건', {priority: 'event'});
        } else {
          // 활성화: 재폴링 완료(st_news_feed) 후 unhide — 대기 상태 기록
          _pendingUnhideSource = m.toggled.source_name;
          console.log('[news_feed] unhide 대기:', _pendingUnhideSource);
          Shiny.setInputValue('settings-js_log', '[news_feed] unhide 대기: ' + _pendingUnhideSource, {priority: 'event'});
        }
      }
      return;
    }

    var sources = m.sources || [];
    var ns      = m.ns || 'settings-';

    if (sources.length === 0) {
      listEl.innerHTML = '<p style="color:#888; padding:8px 0;">등록된 소스가 없습니다.</p>';
      return;
    }

    var serverIds = new Set(sources.map(function(s) { return String(s.id); }));

    // 기존 DOM map: id → element
    var domMap = {};
    listEl.querySelectorAll('.news-source-row[data-src-id]').forEach(function(el) {
      domMap[el.dataset.srcId] = el;
    });

    // 없어진 소스 제거
    Object.keys(domMap).forEach(function(id) {
      if (!serverIds.has(id)) { domMap[id].remove(); delete domMap[id]; }
    });

    // 순서 유지하며 추가/갱신
    // 추가 버튼은 항상 맨 마지막 — 별도 관리
    var addBtn = listEl.querySelector('.news-source-add-wrap');

    var prevEl = null;
    sources.forEach(function(s) {
      var id  = String(s.id);
      var existing = domMap[id];

      if (existing) {
        // enabled 변경 시 체크박스만 업데이트 (change 이벤트 발화 방지)
        var cb = existing.querySelector('.news-toggle-checkbox');
        if (cb && cb.checked !== s.enabled) {
          cb.removeAttribute('onchange');
          cb.checked = s.enabled;
          cb.setAttribute('onchange',
            "Shiny.setInputValue('" + ns + "toggle_news_source'," +
            "{id:" + s.id + ",enabled:this.checked},{priority:'event'});");
        }
        // name/url/lang 변경 시 row 교체
        if (existing.dataset.srcName !== s.name ||
            existing.dataset.srcUrl  !== s.url  ||
            existing.dataset.srcLang !== s.lang) {
          var newEl = _buildSourceEl(s, ns);
          existing.replaceWith(newEl);
          existing = newEl;
          domMap[id] = newEl;
        }
        // 순서 맞추기
        if (prevEl) { if (existing.previousElementSibling !== prevEl) prevEl.after(existing); }
        else         { if (listEl.firstElementChild !== existing && listEl.firstElementChild !== addBtn) listEl.prepend(existing); }
      } else {
        var el = _buildSourceEl(s, ns);
        if (prevEl) prevEl.after(el);
        else if (addBtn) listEl.insertBefore(el, addBtn);
        else listEl.prepend(el);
        domMap[id] = el;
        existing = el;
      }
      prevEl = existing;
    });

    // 추가 버튼 없으면 생성
    if (!addBtn) {
      var wrap = document.createElement('div');
      wrap.className = 'news-source-add-wrap';
      wrap.style.paddingTop = '10px';
      var btn = document.createElement('button');
      btn.className = 'btn-danger-sm';
      btn.style.color = '#00c073';
      btn.dataset.srcNs = ns;
      btn.setAttribute('onclick', 'stShowNewsSourceModalFromEl(this, true);');
      btn.textContent = '+ 소스 추가';
      wrap.appendChild(btn);
      listEl.appendChild(wrap);
    }
  });

  function _buildSourceEl(s, ns) {
    var div = document.createElement('div');
    div.className = 'news-source-row';
    div.style.cursor = 'pointer';
    div.dataset.srcId      = String(s.id);
    div.dataset.srcName    = s.name;
    div.dataset.srcUrl     = s.url;
    div.dataset.srcLang    = s.lang;
    div.dataset.srcEnabled = s.enabled ? '1' : '0';
    div.dataset.srcNs      = ns;
    div.setAttribute('onclick', 'stShowNewsSourceModalFromEl(this);');

    var inner = document.createElement('div');
    inner.style.cssText = 'display:flex; align-items:center; gap:8px; flex:1; min-width:0;';

    var badge = document.createElement('span');
    badge.className = 'news-lang-badge news-lang-' + s.lang;
    badge.textContent = s.lang.toUpperCase();

    var nameSpan = document.createElement('span');
    nameSpan.className = 'news-source-name';
    nameSpan.textContent = s.name;

    inner.appendChild(badge);
    inner.appendChild(nameSpan);

    var label = document.createElement('label');
    label.style.cssText = 'display:inline-flex; align-items:center; cursor:pointer;';
    label.setAttribute('onclick', 'event.stopPropagation();');

    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'news-toggle-checkbox';
    cb.checked = !!s.enabled;
    cb.style.display = 'none';
    cb.setAttribute('onchange',
      "Shiny.setInputValue('" + ns + "toggle_news_source'," +
      "{id:" + s.id + ",enabled:this.checked},{priority:'event'});");

    var track = document.createElement('span');
    track.className = 'toggle-track';

    label.appendChild(cb);
    label.appendChild(track);
    div.appendChild(inner);
    div.appendChild(label);
    return div;
  }

  // ── st_news_keywords: id 기준 DOM diff ──────────────────────
  Shiny.addCustomMessageHandler('st_news_keywords', function(m) {
    var listEl = document.getElementById('st-news-keywords-list');
    if (!listEl) return;

    // removed: 키워드 칩 제거
    if (m.removed != null) {
      var el = listEl.querySelector('.news-keyword-chip[data-kw-id="' + m.removed + '"]');
      if (el) el.remove();
      if (listEl.querySelectorAll('.news-keyword-chip').length === 0) {
        listEl.innerHTML = '<p style="color:#888; padding:8px 0; font-size:12px;">등록된 키워드가 없습니다.</p>';
      }
      return;
    }

    // added: 새 키워드 칩 추가
    if (m.added) {
      var ns = m.ns || 'settings-';
      var el = _buildKeywordEl(m.added, ns);
      // 빈 메시지 제거
      var empty = listEl.querySelector('p');
      if (empty) empty.remove();
      listEl.appendChild(el);
      return;
    }

    var keywords = m.keywords || [];
    var ns       = m.ns || 'settings-';

    if (keywords.length === 0) {
      listEl.innerHTML = '<p style="color:#888; padding:8px 0; font-size:12px;">등록된 키워드가 없습니다.</p>';
      return;
    }

    var serverIds = new Set(keywords.map(function(k) { return String(k.id); }));

    var domMap = {};
    listEl.querySelectorAll('.news-keyword-chip[data-kw-id]').forEach(function(el) {
      domMap[el.dataset.kwId] = el;
    });

    // 없어진 키워드 제거
    Object.keys(domMap).forEach(function(id) {
      if (!serverIds.has(id)) { domMap[id].remove(); delete domMap[id]; }
    });

    var prevEl = null;
    keywords.forEach(function(k) {
      var id = String(k.id);
      var existing = domMap[id];
      if (existing) {
        if (prevEl) { if (existing.previousElementSibling !== prevEl) prevEl.after(existing); }
        else         { if (listEl.firstElementChild !== existing) listEl.prepend(existing); }
      } else {
        var el = _buildKeywordEl(k, ns);
        if (prevEl) prevEl.after(el);
        else listEl.prepend(el);
        domMap[id] = el;
        existing = el;
      }
      prevEl = existing;
    });
  });

  function _buildKeywordEl(k, ns) {
    var span = document.createElement('span');
    span.className = 'news-keyword-chip';
    span.style.cursor = 'pointer';
    span.dataset.kwId      = String(k.id);
    span.dataset.kwKeyword = k.keyword;
    span.dataset.kwLang    = k.lang;
    span.dataset.kwNs      = ns;
    span.setAttribute('onclick', 'stShowNewsKeywordModalFromEl(this);');

    var badge = document.createElement('span');
    badge.className = 'news-lang-badge news-lang-' + k.lang;
    badge.textContent = k.lang.toUpperCase();

    span.appendChild(badge);
    span.appendChild(document.createTextNode(' ' + k.keyword));
    return span;
  }


  // ── st_news_feed: 아이템 단위 diff (추가/제거/유지) ──────────
  Shiny.addCustomMessageHandler('st_news_feed', function(m) {
    var listEl = document.getElementById('st-news-feed-list');
    if (!listEl) return;

    var readSet = _getReadSet();

    // ── full: 최초 전체 렌더 + 캐시 초기화 ──────────────────
    if (m.full) {
      _feedCache = {};
      var items = m.full;
      console.log('[news_feed] full:', items.length + '건');
      if (items.length === 0) {
        listEl.innerHTML = '<p style="color:#888; padding:8px 0;">표시할 기사가 없습니다.</p>';
        return;
      }
      listEl.innerHTML = '';
      items.forEach(function(it) {
        var el = _buildFeedItemEl(it, readSet);
        _feedCache[it.link] = el;
        listEl.appendChild(el);
      });
      return;
    }

    // ── diff: 추가/삭제/변경만 처리 ──────────────────────────

    // 삭제 (RSS에서 실제로 사라진 기사 — 캐시에서도 제거)
    (m.removed || []).forEach(function(link) {
      if (_feedCache[link]) {
        _feedCache[link].remove();
        delete _feedCache[link];
      }
    });

    // source 변경 (캐시 element 업데이트)
    (m.changed || []).forEach(function(it) {
      var el = _feedCache[it.link];
      if (!el) return;
      var metaSpan = el.querySelector('.news-feed-meta > span');
      if (metaSpan) {
        var parts = metaSpan.textContent.split(' · ');
        metaSpan.textContent = it.source + ' · ' + (parts[1] || '');
      }
      el.dataset.source = it.source;
    });

    // 추가 (진짜 새 기사 — 캐시에 저장 + prepend)
    var added = (m.added || []).slice().reverse();
    if (added.length > 0) {
      var addedSources = {};
      added.forEach(function(it) {
        var el = _buildFeedItemEl(it, readSet);
        _feedCache[it.link] = el;
        listEl.prepend(el);
        addedSources[it.source] = (addedSources[it.source] || 0) + 1;
      });
      console.log('[news_feed] added:', added.length + '건', addedSources);
    }
    if ((m.removed || []).length > 0) console.log('[news_feed] removed:', m.removed.length + '건');
    if ((m.changed || []).length > 0) console.log('[news_feed] changed(source):', m.changed.length + '건');

    // 소스 활성화 대기 중이면 unhide
    if (_pendingUnhideSource) {
      var sourceName = _pendingUnhideSource;
      _pendingUnhideSource = null;
      var unhideCount = 0;
      Object.keys(_feedCache).forEach(function(link) {
        var el = _feedCache[link];
        if (el.dataset.source === sourceName) {
          el.style.display = '';
          unhideCount++;
        }
      });
      console.log('[news_feed] unhide:', sourceName, unhideCount + '건');
      Shiny.setInputValue('settings-js_log', '[news_feed] unhide: ' + sourceName + ' ' + unhideCount + '건', {priority: 'event'});
    }

    // 빈 결과
    var visible = listEl.querySelectorAll('.news-feed-item:not([style*="display: none"]):not([style*="display:none"])');
    if (visible.length === 0 && Object.keys(_feedCache).length === 0) {
      listEl.innerHTML = '<p style="color:#888; padding:8px 0;">표시할 기사가 없습니다.</p>';
    }

  });

  function _buildFeedItemEl(it, readSet) {
    var title     = it.translated_title || '';
    var link      = it.link || '#';
    var source    = it.source || '';
    var sourceLang = it.source_lang || 'en';
    var keywords  = it.matched_keywords || [];

    // UTC ISO → KST 표시
    var displayTime = '';
    try {
      var dt = new Date(it.published_at);
      displayTime = dt.toLocaleString('ko-KR', {
        timeZone: 'Asia/Seoul',
        month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit',
        hour12: false,
      }).replace(/[.] /g, '-').replace('.', '').replace(',', '');
    } catch(e) {}

    var div = document.createElement('div');
    div.className = 'news-feed-item';
    div.dataset.link = link;
    div.dataset.sourceLang = sourceLang;
    div.dataset.source = source;

    if (readSet && readSet.has(link)) {
      div.classList.add('news-read');
    }

    var a = document.createElement('a');
    a.className = 'news-feed-title';
    a.href = '#';
    a.dataset.url = link;
    a.dataset.sourceLang = sourceLang;
    a.setAttribute('onclick', 'stOpenNewsLink(this); return false;');
    a.textContent = title;  // textContent → XSS 안전

    var metaDiv = document.createElement('div');
    metaDiv.className = 'news-feed-meta';

    var sourceSpan = document.createElement('span');
    sourceSpan.textContent = source + ' · ' + displayTime;

    var kwWrap = document.createElement('span');
    kwWrap.className = 'news-kw-wrap';
    keywords.forEach(function(kw) {
      var kwSpan = document.createElement('span');
      kwSpan.className = 'news-matched-kw';
      kwSpan.textContent = kw;
      kwWrap.appendChild(kwSpan);
    });

    metaDiv.appendChild(sourceSpan);
    metaDiv.appendChild(kwWrap);
    div.appendChild(a);
    div.appendChild(metaDiv);
    return div;
  }

  // ── 읽음 처리 (localStorage) ──────────────────────────────

  // ── st_news_translated: 키워드 입력창 내용을 번역 결과로 교체 ─
  Shiny.addCustomMessageHandler('st_news_translated', function(m) {
    var input = document.getElementById('st-news-keyword-input');
    if (input && m.translated != null) {
      input.value = m.translated;
      // 번역 결과는 항상 en → lang 버튼 상태도 en으로 맞춤
      _setKeywordLang('en');
    }
  });

  // ── 읽음 처리 (localStorage) ──────────────────────────────
  var READ_KEY = 'news_read_links';

  function _getReadSet() {
    try {
      return new Set(JSON.parse(localStorage.getItem(READ_KEY) || '[]'));
    } catch(e) { return new Set(); }
  }

  function _markRead(url) {
    var s = _getReadSet();
    s.add(url);
    // 최대 500개 유지
    var arr = Array.from(s);
    if (arr.length > 500) arr = arr.slice(arr.length - 500);
    try { localStorage.setItem(READ_KEY, JSON.stringify(arr)); } catch(e) {}
    return s;  // 호출자가 재사용 가능하도록 반환
  }


  // ── 뉴스 링크 클릭 ────────────────────────────────────────
  window.stOpenNewsLink = function(el) {
    var url = el.dataset.url;
    if (!url) return;
    _markRead(url);
    var item = el.closest('.news-feed-item');
    if (item) item.classList.add('news-read');

    var sourceLang = el.dataset.sourceLang || 'en';
    var isIOS = /iPhone|iPad|iPod/i.test(navigator.userAgent);

    // ko 소스 → 슬라이드
    // en 소스 + iOS → 크롬으로 열기 (번역 기능)
    // en 소스 + Android/PC → 새 탭
    if (sourceLang === 'ko') {
      _openNewsPanel(url);
    } else if (isIOS && url.startsWith('https://')) {
      window.location.href = url.replace('https://', 'googlechromes://');
    } else {
      window.open(url, '_blank');
    }
  };

  // ── 뉴스 슬라이드업 패널 (ko 소스 전용) ──────────────────
  var _newsPanel = null;
  var _newsPanelIframe = null;

  // 뉴스 피드 로컬 캐시: link → element
  var _feedCache = {};
  // 활성화 대기 중인 소스명
  var _pendingUnhideSource = null;

  function _getNewsPanel() {
    if (!_newsPanel)       _newsPanel       = document.getElementById('st-news-panel');
    if (!_newsPanelIframe) _newsPanelIframe = document.getElementById('st-news-panel-iframe');
  }

  function _openNewsPanel(url) {
    _getNewsPanel();
    _newsPanelIframe.src = '';
    _newsPanel.style.display = 'flex';
    requestAnimationFrame(function() {
      _newsPanel.classList.add('st-news-panel-open');
      _newsPanelIframe.src = url;
    });
  }

  window.stCloseNewsPanel = function() {
    _getNewsPanel();
    _newsPanel.classList.remove('st-news-panel-open');
    setTimeout(function() {
      _newsPanel.style.display = 'none';
      _newsPanelIframe.src = '';
    }, 300);
  };

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

  // ── 뉴스 소스 편집 모달 ───────────────────────────────────
  var _srcNsStr = '';

  // data-attribute 경유 래퍼 (JS 문자열 이스케이프 우회)
  window.stShowNewsSourceModalFromEl = function(el, isNew) {
    if (isNew) {
      stShowNewsSourceModal(null, '', '', 'en', true, el.dataset.srcNs);
    } else {
      stShowNewsSourceModal(
        parseInt(el.dataset.srcId),
        el.dataset.srcName,
        el.dataset.srcUrl,
        el.dataset.srcLang,
        el.dataset.srcEnabled === '1',
        el.dataset.srcNs
      );
    }
  };

  window.stShowNewsKeywordModalFromEl = function(el) {
    stShowNewsKeywordModal(
      parseInt(el.dataset.kwId),
      el.dataset.kwKeyword,
      el.dataset.kwLang,
      el.dataset.kwNs
    );
  };

  window.stShowNewsSourceModal = function(id, name, url, lang, enabled, nsStr) {
    _srcNsStr = nsStr;
    var isNew = (id === null);

    document.getElementById('st-src-modal-title').textContent = isNew ? '소스 추가' : '소스 편집';
    document.getElementById('st-src-modal-id').value    = isNew ? '' : id;
    document.getElementById('st-src-modal-name').value  = name;
    document.getElementById('st-src-modal-url').value   = url;
    document.getElementById('st-src-modal-enabled').checked = enabled;

    _setSrcLang(lang);

    var deleteBtn = document.getElementById('st-src-modal-delete');
    deleteBtn.style.display = isNew ? 'none' : '';

    document.getElementById('st-src-modal-overlay').style.display = 'flex';
  };

  window.stHideNewsSourceModal = function() {
    document.getElementById('st-src-modal-overlay').style.display = 'none';
  };

  function _setSrcLang(lang) {
    document.getElementById('st-src-lang-en').className =
      'news-edit-lang-btn' + (lang === 'en' ? ' active-en' : '');
    document.getElementById('st-src-lang-ko').className =
      'news-edit-lang-btn' + (lang === 'ko' ? ' active-ko' : '');
    document.getElementById('st-src-modal-lang').value = lang;
  }

  window.stSrcLangSelect = function(lang) { _setSrcLang(lang); };

  window.stSaveNewsSource = function() {
    var id      = document.getElementById('st-src-modal-id').value;
    var name    = document.getElementById('st-src-modal-name').value.trim();
    var url     = document.getElementById('st-src-modal-url').value.trim();
    var lang    = document.getElementById('st-src-modal-lang').value;
    var enabled = document.getElementById('st-src-modal-enabled').checked;
    if (!name || !url) { alert('소스명과 URL을 입력하세요.'); return; }
    Shiny.setInputValue(_srcNsStr + 'save_news_source',
      { id: id ? parseInt(id) : null, name: name, url: url, lang: lang, enabled: enabled },
      { priority: 'event' });
    stHideNewsSourceModal();
  };

  window.stDeleteNewsSource = function() {
    var id = document.getElementById('st-src-modal-id').value;
    var name = document.getElementById('st-src-modal-name').value;
    if (!id) return;
    if (!confirm(name + ' 소스를 삭제할까요?')) return;
    Shiny.setInputValue(_srcNsStr + 'delete_news_source', parseInt(id), { priority: 'event' });
    stHideNewsSourceModal();
  };

  // ── 뉴스 키워드 편집 모달 ─────────────────────────────────
  var _kwNsStr = '';

  window.stShowNewsKeywordModal = function(id, keyword, lang, nsStr) {
    _kwNsStr = nsStr;
    var isNew = (id === null);

    document.getElementById('st-kw-modal-title').textContent = isNew ? '키워드 추가' : '키워드 편집';
    document.getElementById('st-kw-modal-id').value      = isNew ? '' : id;
    document.getElementById('st-kw-modal-keyword').value = keyword;

    _setKeywordLang(lang);

    var deleteBtn = document.getElementById('st-kw-modal-delete');
    deleteBtn.style.display = isNew ? 'none' : '';

    document.getElementById('st-kw-modal-overlay').style.display = 'flex';
  };

  window.stHideNewsKeywordModal = function() {
    document.getElementById('st-kw-modal-overlay').style.display = 'none';
  };

  function _setKeywordLang(lang) {
    document.getElementById('st-kw-lang-en').className =
      'news-edit-lang-btn' + (lang === 'en' ? ' active-en' : '');
    document.getElementById('st-kw-lang-ko').className =
      'news-edit-lang-btn' + (lang === 'ko' ? ' active-ko' : '');
    document.getElementById('st-kw-modal-lang').value = lang;
  }

  window.stKwLangSelect = function(lang) { _setKeywordLang(lang); };

  window.stTranslateKeyword = function() {
    var val = document.getElementById('st-kw-modal-keyword').value;
    if (!val.trim()) return;
    Shiny.setInputValue(_kwNsStr + 'btn_translate_keyword', val, { priority: 'event' });
  };

  window.stSaveNewsKeyword = function() {
    var id      = document.getElementById('st-kw-modal-id').value;
    var keyword = document.getElementById('st-kw-modal-keyword').value.trim();
    var lang    = document.getElementById('st-kw-modal-lang').value;
    if (!keyword) { alert('키워드를 입력하세요.'); return; }
    Shiny.setInputValue(_kwNsStr + 'save_news_keyword',
      { id: id ? parseInt(id) : null, keyword: keyword, lang: lang },
      { priority: 'event' });
    stHideNewsKeywordModal();
  };

  window.stDeleteNewsKeyword = function() {
    var id = document.getElementById('st-kw-modal-id').value;
    var kw = document.getElementById('st-kw-modal-keyword').value;
    if (!id) return;
    if (!confirm(kw + ' 키워드를 삭제할까요?')) return;
    Shiny.setInputValue(_kwNsStr + 'delete_news_keyword', parseInt(id), { priority: 'event' });
    stHideNewsKeywordModal();
  };

})();
        """),

        ui.div(
            # 시세조회 간격
            ui.div(
                ui.div(
                    ui.p("시세조회 간격", style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.08em; margin:0;"),
                    ui.div(
                        ui.tags.label(
                            ui.tags.input(
                                id="st-realtime-toggle",
                                type="checkbox",
                                style="display:none;",
                                onchange=(
                                    "Shiny.setInputValue('settings-btn_save_interval',"
                                    " this.checked ? 0 : -1, {priority: 'event'});"
                                ),
                            ),
                            ui.span(class_="toggle-track"),
                            style="display:inline-flex; align-items:center; cursor:pointer;",
                        ),
                        ui.span("실시간", style="font-size:13px; color:#ccc; margin-left:8px;"),
                        style="display:flex; align-items:center;",
                    ),
                    style="display:flex; justify-content:space-between; align-items:center;",
                ),
                style="padding: 20px 0; border-bottom: 1px solid #1e1e1e;",
            ),

            # 티커 관리
            ui.div(
                ui.p("티커 관리", style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.08em; margin:0;"),
                ui.div(
                    ui.tags.button(
                        "자동 표시",
                        id="st-auto-ticker-toggle",
                        class_="btn-danger-sm",
                        style="color:#00c073;",
                        data_hidden="1",
                        onclick="stToggleAutoTickers();",
                    ),
                    ui.tags.button(
                        "+ 추가",
                        class_="btn-danger-sm",
                        style="color:#00c073;",
                        onclick="stShowModal();",
                    ),
                    style="display:flex; gap:6px;",
                ),
                style="display:flex; justify-content:space-between; align-items:center; padding: 20px 0 12px 0;",
            ),

            ui.div({"id": "st-ticker-list"}),

            # ── RSS 소스 ────────────────────────────────────────────────
            ui.div(
                ui.p("뉴스 소스", style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.08em; margin:0;"),
                ui.div({"id": "st-news-sources-list"}, style="padding-top: 8px;"),
                style="padding: 20px 0; border-bottom: 1px solid #1e1e1e;",
            ),

            # ── 키워드 ──────────────────────────────────────────────────
            ui.div(
                ui.div(
                    ui.p("키워드", style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.08em; margin:0;"),
                    ui.tags.button(
                        "+ 추가",
                        class_="btn-danger-sm",
                        style="color:#00c073;",
                        onclick="stShowNewsKeywordModal(null, '', 'en', 'settings-');",
                    ),
                    style="display:flex; justify-content:space-between; align-items:center;",
                ),
                ui.div({"id": "st-news-keywords-list"}, style="padding-top: 8px;"),
                style="padding: 20px 0; border-bottom: 1px solid #1e1e1e;",
            ),

            # ── 뉴스 피드 ────────────────────────────────────────────────
            ui.div(
                ui.p("뉴스 피드", style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.08em; margin:0;"),
                ui.div({"id": "st-news-feed-list"}, style="padding-top: 8px;"),
                style="padding: 20px 0; border-bottom: 1px solid #1e1e1e;",
            ),

            # 내보내기
            ui.div(
                ui.tags.button(
                    "📥 내보내기",
                    style="background:none; border:none; color:#888; font-size:14px; padding: 20px 0; cursor:pointer; width:100%; text-align:center;",
                    onclick="window.location.href='/api/export';",
                ),
            ),

            # 로그아웃
            ui.div(
                ui.tags.button(
                    "로그아웃",
                    style="background:none; border:none; color:#888; font-size:14px; padding: 20px 0; cursor:pointer; width:100%; text-align:center;",
                    onclick="deleteCookie('auth_token'); location.reload();",
                ),
            ),

            class_="page-inner",
        ),

        # ── 티커 추가 모달 ──────────────────────────────────────────────
        ui.div(
            ui.div(
                ui.div(
                    ui.h4("티커 추가", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon", onclick="stHideModal();"),
                    class_="modal-header-row",
                ),
                ui.div(
                    ui.tags.label("티커"),
                    ui.tags.input(id="st-new-ticker", type="text", placeholder="예) USDKRW=X", class_="form-control"),
                ),
                ui.div(
                    ui.tags.label("종목명"),
                    ui.tags.input(id="st-new-ticker-name", type="text", placeholder="예) 달러/원 환율", class_="form-control"),
                ),
                ui.div(
                    ui.tags.label("시장"),
                    ui.tags.select(
                        ui.HTML(market_options),
                        id="st-new-ticker-market",
                        class_="form-control",
                    ),
                ),
                ui.div(
                    ui.tags.label("레버리지"),
                    ui.tags.input(id="st-new-ticker-leverage", type="number", value="1", min="1", max="3", class_="form-control"),
                ),
                ui.tags.button(
                    "추가",
                    class_="btn-add",
                    onclick=(
                        "Shiny.setInputValue('settings-btn_confirm_add_ticker', {"
                        "  ticker: document.getElementById('st-new-ticker').value,"
                        "  name:   document.getElementById('st-new-ticker-name').value,"
                        "  market: document.getElementById('st-new-ticker-market').value,"
                        "  leverage: parseInt(document.getElementById('st-new-ticker-leverage').value) || 1"
                        "}, {priority: 'event'});"
                    ),
                ),
                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            id="st-modal-overlay",
            class_="modal-overlay",
            style="display:none;",
            onclick="stHideModal();",
        ),

        # ── 뉴스 소스 편집 모달 ─────────────────────────────────────────
        ui.div(
            ui.div(
                ui.div(
                    ui.tags.span(id="st-src-modal-title", style="font-size:14px; font-weight:bold; color:#eee;"),
                    ui.span("✕", style="color:#888; cursor:pointer; font-size:16px;", onclick="stHideNewsSourceModal();"),
                    style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;",
                ),
                ui.tags.input(id="st-src-modal-id", type="hidden"),
                ui.tags.input(id="st-src-modal-lang", type="hidden", value="en"),
                ui.div(
                    ui.tags.label("소스명", style="font-size:11px; color:#888; display:block; margin-bottom:4px;"),
                    ui.tags.input(id="st-src-modal-name", type="text", class_="form-control", placeholder="예) CNBC World"),
                    class_="news-edit-field",
                ),
                ui.div(
                    ui.tags.label("URL", style="font-size:11px; color:#888; display:block; margin-bottom:4px;"),
                    ui.tags.input(id="st-src-modal-url", type="text", class_="form-control", placeholder="https://..."),
                    class_="news-edit-field",
                ),
                ui.div(
                    ui.tags.label("언어", style="font-size:11px; color:#888; display:block; margin-bottom:4px;"),
                    ui.div(
                        ui.tags.button("EN", id="st-src-lang-en", class_="news-edit-lang-btn active-en", onclick="stSrcLangSelect('en');"),
                        ui.tags.button("KO", id="st-src-lang-ko", class_="news-edit-lang-btn", onclick="stSrcLangSelect('ko');"),
                        class_="news-edit-lang-row",
                    ),
                    class_="news-edit-field",
                ),
                ui.div(
                    ui.tags.label(
                        ui.tags.input(id="st-src-modal-enabled", type="checkbox", checked=True, style="margin-right:6px;"),
                        "활성화",
                        style="font-size:13px; color:#ccc; cursor:pointer;",
                    ),
                    class_="news-edit-field",
                ),
                ui.div(
                    ui.tags.button("저장", class_="btn-modal-save", onclick="stSaveNewsSource();"),
                    ui.tags.button("삭제", id="st-src-modal-delete", class_="btn-modal-delete", onclick="stDeleteNewsSource();"),
                    class_="news-edit-action-row",
                ),
                class_="news-edit-modal-box",
                onclick="event.stopPropagation();",
            ),
            id="st-src-modal-overlay",
            class_="news-edit-modal-overlay",
            style="display:none;",
            onclick="stHideNewsSourceModal();",
        ),

        # ── 키워드 편집 모달 ─────────────────────────────────────────────
        ui.div(
            ui.div(
                ui.div(
                    ui.tags.span(id="st-kw-modal-title", style="font-size:14px; font-weight:bold; color:#eee;"),
                    ui.span("✕", style="color:#888; cursor:pointer; font-size:16px;", onclick="stHideNewsKeywordModal();"),
                    style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;",
                ),
                ui.tags.input(id="st-kw-modal-id", type="hidden"),
                ui.tags.input(id="st-kw-modal-lang", type="hidden", value="en"),
                ui.div(
                    ui.tags.label("키워드", style="font-size:11px; color:#888; display:block; margin-bottom:4px;"),
                    ui.div(
                        ui.tags.input(id="st-kw-modal-keyword", type="text", class_="form-control", style="flex:1;", placeholder="예) semiconductor"),
                        ui.tags.button("번역→EN", class_="btn-danger-sm", onclick="stTranslateKeyword();"),
                        style="display:flex; gap:6px; align-items:center;",
                    ),
                    class_="news-edit-field",
                ),
                ui.div(
                    ui.tags.label("언어", style="font-size:11px; color:#888; display:block; margin-bottom:4px;"),
                    ui.div(
                        ui.tags.button("EN", id="st-kw-lang-en", class_="news-edit-lang-btn active-en", onclick="stKwLangSelect('en');"),
                        ui.tags.button("KO", id="st-kw-lang-ko", class_="news-edit-lang-btn", onclick="stKwLangSelect('ko');"),
                        class_="news-edit-lang-row",
                    ),
                    class_="news-edit-field",
                ),
                ui.div(
                    ui.tags.button("저장", class_="btn-modal-save", onclick="stSaveNewsKeyword();"),
                    ui.tags.button("삭제", id="st-kw-modal-delete", class_="btn-modal-delete", onclick="stDeleteNewsKeyword();"),
                    class_="news-edit-action-row",
                ),
                class_="news-edit-modal-box",
                onclick="event.stopPropagation();",
            ),
            id="st-kw-modal-overlay",
            class_="news-edit-modal-overlay",
            style="display:none;",
            onclick="stHideNewsKeywordModal();",
        ),

        # ── 뉴스 슬라이드업 패널 (ko 소스 전용) ────────────────────────────
        ui.div(
            ui.div(
                ui.div(
                    ui.tags.button("✕", class_="st-news-panel-close", onclick="stCloseNewsPanel();"),
                    class_="st-news-panel-header",
                ),
                ui.tags.iframe(id="st-news-panel-iframe", class_="st-news-panel-iframe"),
                class_="st-news-panel-inner",
            ),
            id="st-news-panel",
            class_="st-news-panel",
            onclick="stCloseNewsPanel();",
        ),

        class_="page-container",
    )


# ── Server ────────────────────────────────────────────────────────────────────

@module.server
def settings_server(input, output, session, active_tab: reactive.value = None):
    _initialized = False
    refresh = reactive.value(0)

    _last_tickers: list = []
    _last_display: dict = {}

    @reactive.calc
    def _ticker_rows():
        ticker_signal.get()
        refresh()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT ticker, name, market, leverage, is_manual FROM tickers")
            rows = cur.fetchall()
            cur.close()
        return rows

    # ── 시세조회 간격 저장 ────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.btn_save_interval)
    def _():
        val = input.btn_save_interval()
        if val is None:
            return
        config = get_config()
        if val == 0:
            config["interval"] = 0
        else:
            config["interval"] = config.get("default_interval", 1)
        save_config(config)
        subprocess.Popen(["sudo", "systemctl", "restart", "price_updater"])

    # ── 티커 목록 갱신 ───────────────────────────────────────────────────────
    @reactive.effect
    async def _send_update():
        nonlocal _last_tickers, _last_display
        nonlocal _initialized
        price_signal.get()

        if _initialized and active_tab and active_tab.get() != "settings":
            return

        reactive.invalidate_later(60)

        prices = get_all_prices()

        rows = sorted(_ticker_rows(), key=_sort_key)

        current_tickers = [r[0] for r in rows]
        structure_changed = (current_tickers != _last_tickers)

        # structure_changed: 전체 필요 / tick: 자동 숨김 시 is_manual만
        def _build_ticker_values(include_auto: bool):
            result = {}
            for ticker, name, market, leverage, is_manual in rows:
                if not include_auto and not is_manual:
                    continue
                p_data     = prices.get(ticker)
                price      = float(p_data["price"])      if p_data else 0.0
                change_pct = float(p_data["change_pct"]) if p_data else 0.0
                result[ticker] = _build_tick_values(ticker, name, market, leverage, price, change_pct)
            return result

        if structure_changed:
            _last_tickers = current_tickers
            _last_display.clear()
            cfg      = get_config()
            ns_str   = session.ns("_")[:-1]
            ticker_list_html = "".join(
                _build_row_skeleton(ticker, name, market, leverage, is_manual, ns_str)
                for ticker, name, market, leverage, is_manual in rows
            )
            ticker_values = _build_ticker_values(include_auto=True)
            await session.send_custom_message("st_init", {
                "interval":         cfg.get("interval", 1),
                "ticker_list_html": ticker_list_html,
                # st_init: static+dynamic 병합해서 전송 (_applyOneTickerFull과 동일)
                "tickers": {
                    t: {**v["static"], **v["dynamic"]}
                    for t, v in ticker_values.items()
                },
            })
        else:
            auto_hidden = (input.auto_hidden() or '1') == '1'
            ticker_values = _build_ticker_values(include_auto=not auto_hidden)
            dyn_diff, sta_diff = diff_display_split(ticker_values, _last_display)
            if dyn_diff:
                await session.send_custom_message("st_tick", dyn_diff)
            if sta_diff:
                await session.send_custom_message("st_static_tick", sta_diff)
        _initialized = True

    # ── 뉴스: 소스/키워드 DB 캐시 ─────────────────────────────────────────────
    _news_refresh = reactive.value(0)

    @reactive.calc
    def _news_source_rows():
        _news_refresh()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, name, url, enabled, lang FROM news_sources ORDER BY id")
            rows = cur.fetchall()
            cur.close()
        return rows

    @reactive.calc
    def _news_keyword_rows():
        _news_refresh()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, keyword, lang FROM news_keywords ORDER BY id")
            rows = cur.fetchall()
            cur.close()
        return rows

    @reactive.effect
    @reactive.event(_news_refresh)
    async def _send_news_sources():
        rows = _news_source_rows()
        ns_str = session.ns("_")[:-1]
        sources = [
            {"id": r[0], "name": r[1], "url": r[2], "enabled": r[3], "lang": r[4]}
            for r in rows
        ]
        await session.send_custom_message("st_news_sources", {"sources": sources, "ns": ns_str})

    @reactive.effect
    @reactive.event(_news_refresh)
    async def _send_news_keywords():
        rows = _news_keyword_rows()
        ns_str = session.ns("_")[:-1]
        keywords = [
            {"id": r[0], "keyword": r[1], "lang": r[2]}
            for r in rows
        ]
        await session.send_custom_message("st_news_keywords", {"keywords": keywords, "ns": ns_str})

    # ── 뉴스: RSS 소스 on/off ────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.toggle_news_source)
    async def _():
        payload = input.toggle_news_source()
        if not payload:
            return
        src_id = payload.get("id")
        enabled = bool(payload.get("enabled"))
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE news_sources SET enabled = %s WHERE id = %s RETURNING name",
                (enabled, src_id)
            )
            row = cur.fetchone()
            conn.commit()
            cur.close()
        source_name = row[0] if row else ""
        await session.send_custom_message("st_news_sources", {
            "toggled": {"id": src_id, "enabled": enabled, "source_name": source_name}
        })
        if enabled:
            # 활성화: 재폴링 — 비활성화 시 제거했던 기사들이 added로 잡히도록
            publish_news_source_changed()
        else:
            # 비활성화: _last_feed_map에서 해당 소스 기사 제거
            # → 활성화 시 재폴링 결과와 비교 시 added로 인식
            to_remove = [link for link, it in _last_feed_map.items() if it.get("source") == source_name]
            for link in to_remove:
                del _last_feed_map[link]

    # ── 뉴스: 소스 저장 (추가 or 수정) ─────────────────────────────────────
    @reactive.effect
    @reactive.event(input.save_news_source)
    def _():
        payload = input.save_news_source()
        if not payload:
            return
        src_id  = payload.get("id")
        name    = str(payload.get("name", "")).strip()
        url     = str(payload.get("url", "")).strip()
        lang    = str(payload.get("lang", "en"))
        enabled = bool(payload.get("enabled", True))
        if not name or not url:
            return
        with get_db() as conn:
            cur = conn.cursor()
            if src_id:
                cur.execute(
                    "UPDATE news_sources SET name=%s, url=%s, lang=%s, enabled=%s WHERE id=%s",
                    (name, url, lang, enabled, src_id),
                )
            else:
                cur.execute(
                    "INSERT INTO news_sources (name, url, lang, enabled) VALUES (%s, %s, %s, %s)",
                    (name, url, lang, enabled),
                )
            conn.commit()
            cur.close()
        _news_refresh.set(_news_refresh() + 1)
        publish_news_source_changed()

    # ── 뉴스: 소스 삭제 ─────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.delete_news_source)
    def _():
        src_id = input.delete_news_source()
        if src_id is None:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM news_sources WHERE id = %s", (src_id,))
            conn.commit()
            cur.close()
        _news_refresh.set(_news_refresh() + 1)
        publish_news_source_changed()

    # ── 뉴스: 키워드 번역 ────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.btn_translate_keyword)
    async def _():
        text = input.btn_translate_keyword()
        if not text or not text.strip():
            return
        try:
            translated = GoogleTranslator(source="auto", target="en").translate(text.strip())
            if not translated:
                raise TranslationNotFound(text)
        except (TooManyRequests, RequestError, TranslationNotFound,
                NotValidLength, NotValidPayload) as e:
            print(f"[settings] 키워드 번역 실패 ({e.__class__.__name__}): {text} | {e}")
            return
        except Exception as e:
            print(f"[settings] 키워드 번역 실패 (예상치 못한 오류): {text} | {e}")
            return
        await session.send_custom_message("st_news_translated", {"translated": translated})

    # ── 뉴스: 키워드 저장 (추가 or 수정) ────────────────────────────────────
    @reactive.effect
    @reactive.event(input.save_news_keyword)
    async def _():
        payload = input.save_news_keyword()
        if not payload:
            return
        kw_id   = payload.get("id")
        keyword = str(payload.get("keyword", "")).strip()
        lang    = str(payload.get("lang", "en"))
        if not keyword:
            return
        with get_db() as conn:
            cur = conn.cursor()
            if kw_id:
                cur.execute(
                    "UPDATE news_keywords SET keyword=%s, lang=%s WHERE id=%s",
                    (keyword, lang, kw_id),
                )
                conn.commit()
                cur.close()
                # 수정: 전체 목록 재전송 (keyword/lang 변경)
                _news_refresh.set(_news_refresh() + 1)
            else:
                cur.execute(
                    "INSERT INTO news_keywords (keyword, lang) VALUES (%s, %s) RETURNING id",
                    (keyword, lang),
                )
                new_id = cur.fetchone()[0]
                conn.commit()
                cur.close()
                # 추가: 새 키워드만 전송
                ns_str = session.ns("_")[:-1]
                await session.send_custom_message("st_news_keywords", {
                    "added": {"id": new_id, "keyword": keyword, "lang": lang},
                    "ns": ns_str,
                })
        publish_news_keyword_changed()

    # ── 뉴스: 키워드 삭제 ────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.delete_news_keyword)
    async def _():
        kw_id = input.delete_news_keyword()
        if kw_id is None:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM news_keywords WHERE id = %s", (kw_id,))
            conn.commit()
            cur.close()
        # removed만 전송 — 전체 키워드 목록 재전송 불필요
        await session.send_custom_message("st_news_keywords", {"removed": kw_id})
        publish_news_keyword_changed()

    # ── 뉴스: 피드 표시 ──────────────────────────────────────────────────────
    _news_feed_initialized = False
    _news_feed_pending     = False  # 탭 밖에서 갱신 발생 시 마킹
    _last_feed_map: dict   = {}     # link → item (이전 전송 기준)

    def _build_items() -> list:
        raw_items = get_news_feed_cache() or []
        return [
            {
                "translated_title": it.get("translated_title") or it.get("title", ""),
                "link":             it.get("link", ""),
                "source":           it.get("source", ""),
                "source_lang":      it.get("source_lang", "en"),
                "published_at":     it.get("published_at", ""),
                "matched_keywords": it.get("matched_keywords") or [],
            }
            for it in raw_items
        ]

    def _build_feed_diff(items: list) -> dict:
        """이전 전송 기준으로 추가/삭제/변경된 것만 반환.
        최초 호출 시(_last_feed_map 비어있음) full=True로 전체 전송."""
        nonlocal _last_feed_map

        if not _last_feed_map:
            _last_feed_map = {it["link"]: it for it in items}
            return {"full": items}

        cur_map = {it["link"]: it for it in items}

        added   = [it for link, it in cur_map.items() if link not in _last_feed_map]
        removed = [link for link in _last_feed_map if link not in cur_map]
        # source 변경 감지 — link + source만 전송
        changed = [
            {"link": link, "source": it["source"]}
            for link, it in cur_map.items()
            if link in _last_feed_map and _last_feed_map[link]["source"] != it["source"]
        ]

        _last_feed_map = cur_map
        return {"added": added, "removed": removed, "changed": changed}

    async def _send_feed(full=False):
        items = _build_items()
        if full:
            _last_feed_map.clear()
        payload = _build_feed_diff(items)
        # 변경 없으면 전송 스킵
        if not payload.get("full") and not payload.get("added") and not payload.get("removed") and not payload.get("changed"):
            print("[news_feed] 변경 없음 — 전송 스킵", flush=True)
            return
        if payload.get("full"):
            print(f"[news_feed] full 전송: {len(payload['full'])}건", flush=True)
        else:
            added   = payload.get("added", [])
            removed = payload.get("removed", [])
            changed = payload.get("changed", [])
            added_sources = {}
            for it in added:
                s = it.get("source", "?")
                added_sources[s] = added_sources.get(s, 0) + 1
            print(
                f"[news_feed] diff — "
                f"added:{len(added)}{added_sources if added_sources else ''} "
                f"removed:{len(removed)} "
                f"changed:{len(changed)}",
                flush=True
            )
        await session.send_custom_message("st_news_feed", payload)

    # ── JS 로그 수신 ─────────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.js_log)
    def _():
        msg = input.js_log()
        if msg:
            print(msg, flush=True)

    @reactive.effect
    async def _send_news_feed():
        nonlocal _news_feed_initialized, _news_feed_pending
        from app.price_signal import news_feed_signal
        news_feed_signal.get()

        if _news_feed_initialized and active_tab and active_tab.get() != "settings":
            _news_feed_pending = True
            return

        await _send_feed(full=not _news_feed_initialized)
        _news_feed_initialized = True
        _news_feed_pending = False

    @reactive.effect
    @reactive.event(active_tab)
    async def _send_news_feed_on_tab_enter():
        """탭 진입 시 pending 갱신이 있으면 즉시 전송."""
        nonlocal _news_feed_pending
        if not active_tab or active_tab.get() != "settings":
            return
        if not _news_feed_pending:
            return
        await _send_feed()
        _news_feed_pending = False

    # ── 티커 삭제 ─────────────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.confirm_delete_ticker)
    def _():
        ticker = input.confirm_delete_ticker()
        if not ticker:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM tickers WHERE ticker = %s AND is_manual = true", (ticker,))
            conn.commit()
            cur.close()
        refresh.set(refresh() + 1)
        _notify_ticker_changed()

    # ── 티커 추가 ─────────────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.btn_confirm_add_ticker)
    def _():
        payload = input.btn_confirm_add_ticker()
        if not payload:
            return

        ticker   = str(payload.get("ticker", "")).strip().upper()
        name     = str(payload.get("name", "")).strip()
        market   = str(payload.get("market", ""))
        leverage = int(payload.get("leverage", 1))

        if not ticker or not name:
            return

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO tickers (ticker, name, market, leverage, is_manual, sort_order)
                VALUES (%s, %s, %s, %s, true, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM tickers WHERE is_manual = true))
                ON CONFLICT (ticker) DO UPDATE SET
                    name = EXCLUDED.name,
                    market = EXCLUDED.market,
                    leverage = EXCLUDED.leverage,
                    is_manual = true
            """, (ticker, name, market, leverage))
            conn.commit()
            cur.close()

        refresh.set(refresh() + 1)
        _notify_ticker_changed()