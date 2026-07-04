from shiny import ui, module, reactive
from app.db import get_db, get_config, save_config, get_market_currency, get_market_map
from app.modules.components import fmt_change
from app.price_signal import price_signal, ticker_signal
from scheduler.price_updater_common import get_market_status
from app.utils.display_diff import diff_display_split
from common.redis_store import get_all_prices, publish_ticker_changed, refresh_position_cache
import subprocess

from app.modules.news import news_script_ui, news_ui_section, news_modals_ui, news_server_logic
from app.modules.settings_js import settings_js


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
        ui.tags.script(settings_js()),
        news_script_ui(),

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

            news_ui_section(),

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

        news_modals_ui(),

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

    news_server_logic(input, output, session, active_tab)