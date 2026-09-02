import asyncio
import subprocess

from shiny import ui, module, reactive
from app.db import get_db, get_market_currency, get_market_map
from app.modules.components import fmt_change, track_price_change
from app.price_signal import price_signal, ticker_signal
from scheduler.price_updater_common import get_market_status
from app.utils.display_diff import diff_display_split
from common.redis_store import get_all_prices, publish_ticker_changed, refresh_position_cache

from app.modules.news import news_script_ui, news_ui_section, news_modals_ui, news_server_logic
from app.modules.settings_js import settings_js

# 서버관리 화면에서 재시작/로그조회를 허용하는 서비스 목록.
# 화이트리스트 — 여기 없는 값은 재시작/로그조회 둘 다 무시한다.
_MANAGED_SERVICES = ["myassets", "price_updater", "daily_inserter", "news_fetcher", "nginx"]


def _fetch_service_log(svc: str, cursor: str | None = None) -> tuple[str, str | None]:
    """journalctl 조회 (블로킹 호출 — asyncio.to_thread로 감싸서 사용).

    cursor가 없으면 최근 100줄 전체(최초 오픈 시), 있으면 그 이후 신규분만 조회한다.
    반환: (log_text, new_cursor). 새 로그가 없으면 log_text=""이고 cursor는 그대로 유지된다.
    """
    cmd = ["journalctl", "-u", svc, "--no-pager", "--show-cursor"]
    if cursor:
        cmd += ["--after-cursor", cursor]
    else:
        cmd += ["-n", "100"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        raw = result.stdout or result.stderr or ""
    except Exception as e:
        return f"로그 조회 실패: {e}", cursor

    lines = raw.splitlines()
    new_cursor = cursor
    # --show-cursor는 항목들 뒤에 "-- cursor: ..." 줄을 추가로 붙여서 출력한다 — 실제 로그가 아니므로 분리해서 파싱
    if lines and lines[-1].startswith("-- cursor: "):
        new_cursor = lines[-1][len("-- cursor: "):]
        lines = lines[:-1]
    # 조회 결과가 0건이면 journalctl이 실제 로그 대신 이 상태 메시지를 출력한다 — 로그 내용이 아니므로 제외
    if lines == ["-- No entries --"]:
        lines = []
    return "\n".join(lines), new_cursor


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
        refresh_position_cache()
        publish_ticker_changed()
    except Exception as e:
        print(f"[settings] ticker_changed 신호 발행 실패 (무시): {e}")

# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

_MARKET_ORDER = {
    "KR": 0,
    "NAS": 1, "NYS": 1, "AMS": 1,
    "CRYPTO": 2,
    "COM": 3,
    "ETC": 4,
    "FX": 5, "INDEX": 5,
}

def _ticker_to_id(ticker: str) -> str:
    return ticker.replace("-", "_").replace("^", "_").replace("=", "_")

def _sort_key(r):
    ticker, _, market, leverage = r
    return (
        _MARKET_ORDER.get(market, 99),
        -(leverage or 1),
        ticker,
    )

_UNSET_SORT_ORDER = 10**9  # sort_order 미지정 종목을 뒤로 보내기 위한 큰 값 (드래그로 지정된 종목이 항상 우선)

def _sort_rows(rows):
    """
    정렬 우선순위: (1) sort_order가 지정된 티커는 그 값 순
    (2) sort_order 미지정 티커는 기존 방식(시장/레버리지/티커명)으로 폴백.
    rows[i]: (ticker, name, market, leverage, sort_order)

    tickers.sort_order는 portfolio_DAL.update_ticker_order를 통해 포트폴리오
    화면과 공유하는 컬럼이다. (수동추가 티커는 대부분 환율/지수처럼 포트폴리오에
    보유할 수 없는 종목이라 실질적으로 겹치는 케이스가 없다는 전제 하에 공유 결정함 — 2026-09)
    """
    return sorted(
        rows,
        key=lambda r: (
            r[4] if r[4] is not None else _UNSET_SORT_ORDER,
            _sort_key(r[:4]),
        )
    )

def _build_row_skeleton(ticker, name, market, leverage, ns_str):
    """구조 변경 시 1회 전송하는 골격 HTML.

    자동추가 티커는 더 이상 이 화면에 노출되지 않으므로(2026-09 정리),
    tickers 테이블은 항상 is_manual=true 행만 조회한다. 따라서 삭제 버튼은
    조건 없이 항상 노출한다.
    """
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
    )

    row_html = (
        f'<div class="ticker-row" id="st-row-{tid}">'
        f'  <div>'
        f'    <div class="lev-name-wrap">'
        f'      {lev_html}'
        f'      <span id="st-name-{tid}" class="ticker-name">{name}</span>'
        f'      <span id="st-status-{tid}" class="ticker-status"></span>'
        f'      <span id="st-updated-{tid}" class="ticker-updated" '
        f'style="font-size:11px; color:var(--text-dim,#888); margin-left:4px; white-space:nowrap;"></span>'
        f'    </div>'
        f'    <div class="ticker-qty">{ticker} / <span id="st-market-{tid}">{market}</span></div>'
        f'  </div>'
        f'  <div class="ticker-row-btn" style="display:flex; flex-direction:column; align-items:flex-end; gap:0;">'
        f'    {delete_html}'
        f'    <div class="ticker-change" id="st-change-{tid}"></div>'
        f'  </div>'
        f'</div>'
    )
    return f'<div class="st-item" data-ticker="{ticker}">{row_html}</div>'


def _build_tick_values(ticker, name, market, leverage, price, change_pct):
    """시세 갱신 시마다 전송하는 값 — static/dynamic 분리 구조."""
    tid      = _ticker_to_id(ticker)
    leverage = int(leverage) if leverage else 1

    currency = get_market_currency(market)
    price_str, chg_str, chg_css = fmt_change(price, change_pct, currency=currency)
    changed_at = track_price_change(ticker, price_str)  # 화면 문자열 기준 갱신시각 판단

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
            "id":         tid,
            "price":      price_str,
            "chg":        chg_str,
            "chg_css":    chg_css,
            "updated_at": changed_at,  # raw epoch — 화면 문자열이 바뀐 시점에만 새 값
        },
    }


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def settings_ui():
    market_choices = list(get_market_map().keys())
    market_options = "".join(f'<option value="{m}">{m}</option>' for m in market_choices)

    return ui.div(
        ui.tags.script(settings_js()),
        news_script_ui(),

        ui.div(
            # 티커 관리
            ui.div(
                ui.p("티커 관리", style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.08em; margin:0;"),
                ui.div(
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

            news_ui_section(),

            # 내보내기
            ui.div(
                ui.tags.button(
                    "📥 내보내기",
                    style="background:none; border:none; color:#888; font-size:14px; padding: 20px 0; cursor:pointer; width:100%; text-align:center;",
                    onclick="window.location.href='/api/export';",
                ),
            ),

            # 서버 관리
            ui.div(
                ui.tags.button(
                    "🖥️ 서버관리",
                    style="background:none; border:none; color:#888; font-size:14px; padding: 20px 0; cursor:pointer; width:100%; text-align:center;",
                    onclick="stShowServerModal();",
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
                    ui.div(
                        ui.tags.input(id="st-new-ticker", type="text", placeholder="예) USDKRW=X",
                                      class_="form-control", style="flex:1;",
                                      oninput="this.value=this.value.toUpperCase();"),
                        ui.tags.button("🔍", id="st-new-ticker-lookup-btn",
                                       style="margin-left:6px; padding:0; font-size:18px; background:none; border:none; outline:none; cursor:pointer; line-height:1; -webkit-appearance:none;",
                                       onclick="stLookupTicker();"),
                        style="display:flex; align-items:center;",
                    ),
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

        news_modals_ui(),

        class_="page-container",
    )


# 서버관리 모달은 여기서 만들지만, settings_ui()의 리턴 트리에는 포함하지 않는다.
# .tab-content(=settings_ui 전체)는 탭 전환 시 switchTab()이 display:none 처리하므로,
# 이 자리에 있으면 다른 탭으로 이동하는 순간 로그를 보다가도 팝업이 같이 사라진다.
# app.py에서 이 함수를 별도로 호출해 탭 바깥(screen-main 최상위)에 렌더링해야
# 탭을 옮겨도 모달이 유지된다. Shiny.setInputValue('settings-...')는 DOM 위치와
# 무관하게 동작하므로 이렇게 분리해도 settings_server의 입력 바인딩은 그대로 동작한다.
def settings_server_modal_ui():
    return ui.div(
        ui.div(
            # 서비스 목록 뷰
            ui.div(
                ui.div(
                    ui.h4("서버 관리", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon", onclick="stHideServerModal();"),
                    class_="modal-header-row",
                    style="cursor:move; touch-action:none;",
                    onpointerdown="stStartDragServerModal(event);",
                ),
                ui.div(
                    *[
                        ui.div(
                            ui.span(svc, style="font-size:14px; color:#ccc;"),
                            ui.div(
                                ui.tags.button(
                                    "재시작", class_="btn-danger-sm",
                                    onclick=f"stRestartService('{svc}');",
                                ),
                                ui.tags.button(
                                    "로그", class_="btn-danger-sm", style="color:#00c073;",
                                    onclick=f"stViewLog('{svc}');",
                                ),
                                style="display:flex; gap:6px;",
                            ),
                            style="display:flex; justify-content:space-between; align-items:center; "
                                  "padding:10px 0; border-bottom:1px solid #1e1e1e;",
                        )
                        for svc in _MANAGED_SERVICES
                    ],
                ),
                id="st-server-list-view",
            ),
            # 로그 뷰
            ui.div(
                ui.div(
                    ui.span("←", class_="modal-close-icon", onclick="stBackToServiceList();"),
                    ui.h4("", id="st-log-title", class_="modal-title", style="margin-left:8px;"),
                    ui.span("✕", class_="modal-close-icon", onclick="stHideServerModal();"),
                    class_="modal-header-row",
                    style="cursor:move; touch-action:none;",
                    onpointerdown="stStartDragServerModal(event);",
                ),
                ui.tags.pre(
                    id="st-log-content",
                    style="background:#111; color:#ccc; font-size:12px; padding:10px; "
                          "height:320px; overflow-y:auto; overflow-x:auto; white-space:pre; "
                          "border-radius:6px;",
                ),
                id="st-server-log-view",
                style="display:none;",
            ),
            id="st-server-modal-box",
            class_="modal-box",
            style="pointer-events:auto;",
            onclick="event.stopPropagation();",
        ),
        id="st-server-modal-overlay",
        class_="modal-overlay",
        style="display:none; pointer-events:none;",
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
            # 포트폴리오 화면이 보유종목 시세를 자체적으로 잘 보여주므로(2026-09),
            # 자동추가(is_manual=false) 티커는 이 설정 화면에서 더 이상 노출하지 않는다.
            cur.execute("SELECT ticker, name, market, leverage, sort_order FROM tickers WHERE is_manual = true")
            rows = cur.fetchall()
            cur.close()
        return rows

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

        rows = _sort_rows(_ticker_rows())

        current_tickers = [r[0] for r in rows]
        structure_changed = (current_tickers != _last_tickers)
        # print(f"[SETTINGS_DEBUG] === cycle start === "
        #       f"structure_changed={structure_changed} current_tickers={current_tickers} "
        #       f"_last_tickers={_last_tickers}")

        def _build_ticker_values():
            result = {}
            for ticker, name, market, leverage, _sort_order in rows:
                p_data     = prices.get(ticker)
                price      = float(p_data["price"])      if p_data else 0.0
                change_pct = float(p_data["change_pct"]) if p_data else 0.0
                result[ticker] = _build_tick_values(ticker, name, market, leverage, price, change_pct)
            return result

        if structure_changed:
            _last_tickers = current_tickers
            _last_display.clear()
            ns_str   = session.ns("_")[:-1]
            rows_html = "".join(
                _build_row_skeleton(ticker, name, market, leverage, ns_str)
                for ticker, name, market, leverage, _sort_order in rows
            )
            # #st-ticker-list-normal: settings_js.py의 SortableJS가 드래그 정렬 대상으로
            # 잡는 컨테이너 (portfolio_js.py의 #pf-ticker-list-normal과 동일한 패턴).
            ticker_list_html = f'<div id="st-ticker-list-normal">{rows_html}</div>'
            ticker_values = _build_ticker_values()
            # print(f"[SETTINGS_DEBUG] structure_changed branch: sending st_init, "
            #       f"ticker_values_keys={list(ticker_values.keys())}")
            await session.send_custom_message("st_init", {
                "ticker_list_html": ticker_list_html,
                # st_init: static+dynamic 병합해서 전송 (_applyOneTickerFull과 동일)
                "tickers": {
                    t: {**v["static"], **v["dynamic"]}
                    for t, v in ticker_values.items()
                },
            })
        else:
            ticker_values = _build_ticker_values()
            dyn_diff, sta_diff = diff_display_split(ticker_values, _last_display)
            # print(f"[SETTINGS_DEBUG] else branch: "
            #       f"ticker_values_keys={list(ticker_values.keys())} "
            #       f"dyn_diff={dyn_diff} sta_diff={sta_diff}")
            if dyn_diff:
                await session.send_custom_message("st_tick", dyn_diff)
            if sta_diff:
                await session.send_custom_message("st_static_tick", sta_diff)
        _initialized = True

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

    # ── 티커 순서 변경 (드래그 정렬) ─────────────────────────────────────────────
    # tickers.sort_order 컬럼을 포트폴리오 화면과 공유하므로 portfolio_DAL.update_ticker_order를
    # 그대로 재사용한다 (사용자 확인: 수동추가 티커는 대부분 매수 불가능한 환율/지수라
    # 실질적으로 겹치는 케이스가 없음 — 2026-09).
    # sort_order 변경은 ticker/quantity/leverage/market 중 어느 것도 바꾸지 않으므로
    # refresh_position_cache()(positions×tickers×is_watch 캐시)는 호출할 필요가 없다
    # (portfolio.py의 동일 로직·근거 참고). publish_ticker_changed()만 발행하면
    # price_signal.py 리스너가 받아 ticker_signal을 갱신하고, 이 세션을 포함한 모든
    # 세션의 _ticker_rows()가 재조회되어 새 sort_order가 반영된다.
    @reactive.effect
    @reactive.event(input.ticker_reorder)
    def _reorder_tickers():
        payload = input.ticker_reorder()
        if not payload:
            return
        ordered_tickers = [str(t) for t in payload]
        from app.modules.portfolio_DAL import update_ticker_order
        update_ticker_order(ordered_tickers)
        publish_ticker_changed()

    # ── 티커 자동조회 ─────────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.lookup_ticker)
    async def _lookup_ticker():
        payload = input.lookup_ticker()
        ticker  = str(payload.get("ticker", "")).strip().upper()
        if not ticker:
            return

        from common.kis_lookup import resolve_ticker_info
        result = resolve_ticker_info(ticker)

        out_payload = {
            "ticker": ticker,
            "name":   result["name"],
            "market": result["market"],
        }
        if result["leverage"] is not None:
            out_payload["leverage"] = result["leverage"]

        await session.send_custom_message("st_ticker_lookup_result", out_payload)

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

    # ── 서버 관리: 재시작 ───────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.btn_restart_service)
    def _():
        svc = input.btn_restart_service()
        if svc not in _MANAGED_SERVICES:
            return
        subprocess.Popen(["sudo", "systemctl", "restart", svc])

    # ── 서버 관리: 로그 조회 (2초 폴링, 커서 기반 증분) ─────────────────────
    log_service = reactive.value(None)
    _log_cursor: dict = {}

    @reactive.effect
    @reactive.event(input.btn_view_log)
    def _():
        svc = input.btn_view_log()
        if svc in _MANAGED_SERVICES:
            _log_cursor.pop(svc, None)  # 새로 열 때마다 최근 100줄부터 다시 시작
            log_service.set(svc)

    @reactive.effect
    @reactive.event(input.btn_close_log)
    def _():
        log_service.set(None)

    @reactive.effect
    async def _poll_service_log():
        svc = log_service.get()
        if not svc:
            return
        reactive.invalidate_later(2)
        cursor = _log_cursor.get(svc)
        log_text, new_cursor = await asyncio.to_thread(_fetch_service_log, svc, cursor)
        # 폴링 대기 중 다른 서비스로 전환되었거나 로그 화면을 닫았으면 늦게 온 응답은 버린다.
        if log_service.get() != svc:
            return
        _log_cursor[svc] = new_cursor
        if not log_text:
            return  # 신규 로그 없음 — 의미 없는 패킷을 보내지 않는다
        await session.send_custom_message("st_log_update", {
            "service": svc,
            "log": log_text,
            "append": cursor is not None,  # 최초 오픈(cursor 없음)은 교체, 이후는 append
        })

    news_server_logic(input, output, session, active_tab)