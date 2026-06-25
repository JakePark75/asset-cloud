from shiny import ui, render, module, reactive
from app.db import get_db, get_config, save_config, get_market_currency, get_market_map
from app.modules.components import fmt_change
from app.price_signal import price_signal, ticker_signal
from scheduler.price_updater_common import get_market_status
from app.utils.display_diff import diff_display
import datetime
from zoneinfo import ZoneInfo
KST = ZoneInfo("Asia/Seoul")
from deep_translator import GoogleTranslator
from deep_translator.exceptions import (
    TooManyRequests,
    RequestError,
    TranslationNotFound,
    NotValidLength,
    NotValidPayload,
)
import subprocess


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
        from common.redis_store import publish_ticker_changed
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


def _detect_lang(text: str) -> str:
    """한글 포함 여부로 lang 자동 판별."""
    return "ko" if re.search(r"[가-힣]", text) else "en"


import re as _re
def _detect_lang(text: str) -> str:
    return "ko" if _re.search(r"[가-힣]", text) else "en"


def _build_news_sources_html(rows, ns_str) -> str:
    """rows: [(id, name, url, enabled, lang), ...]"""
    if not rows:
        return '<p style="color:#888; padding:8px 0;">등록된 소스가 없습니다.</p>'
    parts = []
    for src_id, name, url, enabled, lang in rows:
        checked = "checked" if enabled else ""
        lang_badge = (
            f'<span class="news-lang-badge news-lang-{lang}">{lang.upper()}</span>'
        )
        parts.append(
            f'<div class="news-source-row" style="cursor:pointer;"'
            f'  onclick="stShowNewsSourceModal({src_id}, \'{_js_escape(name)}\','
            f' \'{_js_escape(url)}\', \'{lang}\', {str(enabled).lower()}, \'{ns_str}\');">'
            f'  <div style="display:flex; align-items:center; gap:8px; flex:1; min-width:0;">'
            f'    {lang_badge}'
            f'    <span class="news-source-name">{name}</span>'
            f'  </div>'
            f'  <label style="display:inline-flex; align-items:center; cursor:pointer;" onclick="event.stopPropagation();">'
            f'    <input type="checkbox" class="news-toggle-checkbox" {checked} style="display:none;"'
            f'      onchange="Shiny.setInputValue(\'{ns_str}toggle_news_source\','
            f'        {{id: {src_id}, enabled: this.checked}}, {{priority: \'event\'}});">'
            f'    <span class="toggle-track"></span>'
            f'  </label>'
            f'</div>'
        )
    # 추가 버튼
    parts.append(
        f'<div style="padding-top:10px;">'
        f'  <button class="btn-danger-sm" style="color:#00c073;"'
        f'    onclick="stShowNewsSourceModal(null, \'\', \'\', \'en\', true, \'{ns_str}\');">+ 소스 추가</button>'
        f'</div>'
    )
    return "".join(parts)


def _build_news_keywords_html(rows, ns_str) -> str:
    """rows: [(id, keyword, lang), ...]"""
    if not rows:
        return '<p style="color:#888; padding:8px 0; font-size:12px;">등록된 키워드가 없습니다.</p>'
    parts = []
    for kw_id, keyword, lang in rows:
        lang_badge = f'<span class="news-lang-badge news-lang-{lang}">{lang.upper()}</span>'
        parts.append(
            f'<span class="news-keyword-chip" style="cursor:pointer;"'
            f'  onclick="stShowNewsKeywordModal({kw_id}, \'{_js_escape(keyword)}\', \'{lang}\', \'{ns_str}\');">'
            f'  {lang_badge} {keyword}'
            f'</span>'
        )
    return "".join(parts)


def _build_news_feed_html(items: list) -> str:
    """items: [{title, translated_title, link, source, published_at, matched_keywords, source_lang}, ...]"""
    if not items:
        return '<p style="color:#888; padding:8px 0;">표시할 기사가 없습니다.</p>'
    parts = []
    for it in items:
        title = it.get("translated_title") or it.get("title") or ""
        link = it.get("link", "#")
        source = it.get("source", "")
        published_at = it.get("published_at", "")
        matched_keywords = it.get("matched_keywords") or []

        display_time = published_at
        try:
            dt_utc = datetime.datetime.fromisoformat(published_at)
            display_time = dt_utc.astimezone(KST).strftime("%m-%d %H:%M")
        except Exception:
            pass

        kw_html = "".join(
            f'<span class="news-matched-kw">{kw}</span>'
            for kw in matched_keywords
        )

        # data-link 속성에 URL 저장 → JS에서 읽음 처리에 활용
        safe_link = link.replace('"', "&quot;")
        source_lang = it.get("source_lang", "en")
        parts.append(
            f'<div class="news-feed-item" data-link="{safe_link}" data-source-lang="{source_lang}">'
            f'  <a class="news-feed-title" href="#" data-url="{safe_link}" data-source-lang="{source_lang}"'
            f'    onclick="stOpenNewsLink(this); return false;">{title}</a>'
            f'  <div class="news-feed-meta">'
            f'    <span>{source} · {display_time}</span>'
            f'    <span class="news-kw-wrap">{kw_html}</span>'
            f'  </div>'
            f'</div>'
        )
    return "".join(parts)


def _js_escape(s: str) -> str:
    """JS 문자열 인라인 삽입용 이스케이프 (작은따옴표, 역슬래시)."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _build_tick_values(ticker, name, market, leverage, price, change_pct):
    """시세 갱신 시마다 전송하는 값 dict."""
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
        "id":         tid,
        "name":       name,
        "leverage":   leverage,
        "market":     market,
        "price":      price_str,
        "chg":        chg_str,
        "chg_css":    chg_css,
        "status_dot": status_dot,
        "status_txt": status_text,
        "status_cls": status_cls,
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

  // ── st_tick: 변경된 key만 patch ───────────────────────────
  Shiny.addCustomMessageHandler('st_tick', function(m) {
    Object.keys(m).forEach(function(key) {
      _applyOneTicker(m[key]);
    });
  });

  // ── st_news_sources: RSS 소스 목록 통째 교체 ──────────────
  Shiny.addCustomMessageHandler('st_news_sources', function(m) {
    var el = document.getElementById('st-news-sources-list');
    if (el) el.innerHTML = m.html || '';
  });

  // ── st_news_keywords: 키워드 칩 목록 통째 교체 ─────────────
  Shiny.addCustomMessageHandler('st_news_keywords', function(m) {
    var el = document.getElementById('st-news-keywords-list');
    if (el) el.innerHTML = m.html || '';
  });

  // ── st_news_feed: 뉴스 피드 목록 통째 교체 + 읽음 복원 ──────
  Shiny.addCustomMessageHandler('st_news_feed', function(m) {
    var el = document.getElementById('st-news-feed-list');
    if (!el) return;
    el.innerHTML = m.html || '<p style="color:#888; padding:8px 0;">표시할 기사가 없습니다.</p>';
    _applyReadState();
  });

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
  }

  function _applyReadState() {
    var readSet = _getReadSet();
    var items = document.querySelectorAll('#st-news-feed-list .news-feed-item');
    items.forEach(function(item) {
      var link = item.dataset.link;
      if (link && readSet.has(link)) {
        item.classList.add('news-read');
      }
    });
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
  function _openNewsPanel(url) {
    var panel   = document.getElementById('st-news-panel');
    var iframe  = document.getElementById('st-news-panel-iframe');

    iframe.src = '';
    panel.style.display = 'flex';
    requestAnimationFrame(function() {
      panel.classList.add('st-news-panel-open');
    });
    iframe.src = url;
  }

  window.stCloseNewsPanel = function() {
    var panel = document.getElementById('st-news-panel');
    panel.classList.remove('st-news-panel-open');
    setTimeout(function() {
      panel.style.display = 'none';
      document.getElementById('st-news-panel-iframe').src = '';
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
    btn.dataset.hidden = hidden ? '0' : '1';
    btn.style.color = hidden ? '#888' : '#00c073';
    btn.textContent = hidden ? '자동 숨김' : '자동 표시';
    _applyAutoTickerVisibility();
  };

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

  function _applyTickers(tickers) {
    Object.values(tickers).forEach(function(t) { _applyOneTicker(t); });
  }

  function _applyOneTicker(t) {
    var row = document.getElementById('st-row-' + t.id);
    if (row && row.dataset.auto && row.style.display === 'none') return;

    var nameEl = document.getElementById('st-name-' + t.id);
    if (nameEl && t.name != null) nameEl.textContent = t.name;

    var levEl = document.getElementById('st-lev-' + t.id);
    if (levEl && t.leverage != null) {
      levEl.textContent = 'x' + t.leverage;
      levEl.className   = 'lev-badge lev-x' + t.leverage;
      levEl.style.display = t.leverage > 1 ? '' : 'none';
    }

    var marketEl = document.getElementById('st-market-' + t.id);
    if (marketEl && t.market != null) marketEl.textContent = t.market;

    var stEl = document.getElementById('st-status-' + t.id);
    if (stEl) {
      stEl.textContent = t.status_dot ? t.status_dot + ' ' + t.status_txt : '';
      stEl.className   = 'ticker-status ' + t.status_cls;
    }

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

  // ── 뉴스 소스 편집 모달 ───────────────────────────────────
  var _srcNsStr = '';

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

import re

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

        from common.redis_store import get_all_prices
        prices = get_all_prices()

        rows = sorted(_ticker_rows(), key=_sort_key)

        ticker_values = {}
        for ticker, name, market, leverage, is_manual in rows:
            p_data     = prices.get(ticker)
            price      = float(p_data["price"])      if p_data else 0.0
            change_pct = float(p_data["change_pct"]) if p_data else 0.0
            ticker_values[ticker] = _build_tick_values(ticker, name, market, leverage, price, change_pct)

        current_tickers = [r[0] for r in rows]
        structure_changed = (current_tickers != _last_tickers)

        if structure_changed:
            _last_tickers = current_tickers
            _last_display.clear()
            cfg      = get_config()
            ns_str   = session.ns("_")[:-1]
            ticker_list_html = "".join(
                _build_row_skeleton(ticker, name, market, leverage, is_manual, ns_str)
                for ticker, name, market, leverage, is_manual in rows
            )
            await session.send_custom_message("st_init", {
                "interval":         cfg.get("interval", 1),
                "ticker_list_html": ticker_list_html,
                "tickers":          ticker_values,
            })
        else:
            diff = diff_display(ticker_values, _last_display)
            if diff:
                await session.send_custom_message("st_tick", diff)
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
    async def _send_news_sources():
        rows = _news_source_rows()
        ns_str = session.ns("_")[:-1]
        html = _build_news_sources_html(rows, ns_str)
        await session.send_custom_message("st_news_sources", {"html": html})

    @reactive.effect
    async def _send_news_keywords():
        rows = _news_keyword_rows()
        ns_str = session.ns("_")[:-1]
        html = _build_news_keywords_html(rows, ns_str)
        await session.send_custom_message("st_news_keywords", {"html": html})

    # ── 뉴스: RSS 소스 on/off ────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.toggle_news_source)
    def _():
        payload = input.toggle_news_source()
        if not payload:
            return
        src_id = payload.get("id")
        enabled = bool(payload.get("enabled"))
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE news_sources SET enabled = %s WHERE id = %s", (enabled, src_id))
            conn.commit()
            cur.close()
        _news_refresh.set(_news_refresh() + 1)
        from common.redis_store import publish_news_source_changed
        publish_news_source_changed()

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
        from common.redis_store import publish_news_source_changed
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
        from common.redis_store import publish_news_source_changed
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
    def _():
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
            else:
                cur.execute(
                    "INSERT INTO news_keywords (keyword, lang) VALUES (%s, %s)",
                    (keyword, lang),
                )
            conn.commit()
            cur.close()
        _news_refresh.set(_news_refresh() + 1)
        from common.redis_store import publish_news_keyword_changed
        publish_news_keyword_changed()

    # ── 뉴스: 키워드 삭제 ────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.delete_news_keyword)
    def _():
        kw_id = input.delete_news_keyword()
        if kw_id is None:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM news_keywords WHERE id = %s", (kw_id,))
            conn.commit()
            cur.close()
        _news_refresh.set(_news_refresh() + 1)
        from common.redis_store import publish_news_keyword_changed
        publish_news_keyword_changed()

    # ── 뉴스: 피드 표시 ──────────────────────────────────────────────────────
    _news_feed_initialized = False

    @reactive.effect
    async def _send_news_feed():
        nonlocal _news_feed_initialized
        from app.price_signal import news_feed_signal
        news_feed_signal.get()

        if _news_feed_initialized and active_tab and active_tab.get() != "settings":
            return

        from common.redis_store import get_news_feed_cache
        items = get_news_feed_cache() or []
        html = _build_news_feed_html(items)
        await session.send_custom_message("st_news_feed", {"html": html})
        _news_feed_initialized = True

    @reactive.effect
    async def _send_news_feed_init():
        """앱 시작 시 즉시 1회 피드 표시."""
        nonlocal _news_feed_initialized
        if _news_feed_initialized:
            return
        from common.redis_store import get_news_feed_cache
        items = get_news_feed_cache() or []
        html = _build_news_feed_html(items)
        await session.send_custom_message("st_news_feed", {"html": html})
        _news_feed_initialized = True

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