"""
news.py — 뉴스 소스/키워드/피드 UI 및 서버 로직.

settings.py에서 분리됨 (2026-06-28). 현재는 독립 Shiny 모듈이 아니라
일반 함수 모음이며, settings_ui()/settings_server() 안에서 호출되어
기존과 동일하게 'settings-' 네임스페이스로 동작한다.
추후 뉴스 화면을 완전히 독립시킬 때 @module.ui / @module.server 로 전환할 것.
"""

from shiny import ui, reactive
from app.modules.news_js import news_js
from app.db import get_db
from common.redis_store import (
    get_news_feed_cache,
    publish_news_source_changed,
    publish_news_keyword_changed,
)
from deep_translator import GoogleTranslator
from deep_translator.exceptions import (
    TooManyRequests,
    RequestError,
    TranslationNotFound,
    NotValidLength,
    NotValidPayload,
)


# ── UI ────────────────────────────────────────────────────────────────────────

def news_script_ui():
    """뉴스 소스/키워드/피드 관련 클라이언트 JS. settings_ui()에서 1회 호출."""
    return ui.tags.script(news_js())


def news_ui_section():
    """설정 화면의 page-inner 안에 삽입되는 뉴스 소스/키워드/피드 섹션."""
    return ui.TagList(
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
    )


def news_modals_ui():
    """뉴스 소스 편집 모달 + 키워드 편집 모달 + 뉴스 슬라이드업 패널(ko 소스 전용)."""
    return ui.TagList(
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
                onclick="event.stopPropagation();",
            ),
            id="st-news-panel",
            class_="st-news-panel",
            onclick="stCloseNewsPanel();",
        ),
    )


# ── Server ────────────────────────────────────────────────────────────────────

def news_server_logic(input, output, session, active_tab: reactive.value = None):
    """뉴스 소스/키워드/피드 서버 로직. settings_server() 안에서 호출."""
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