def news_js() -> str:
    """
    뉴스 소스/키워드/피드 페이지 JS — news_script_ui()에서 ui.tags.script()로 주입.
    """
    return """
(function() {

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
"""
