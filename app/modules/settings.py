from shiny import ui, render, module, reactive
from app.db import get_db, get_config, save_config, get_market_currency, get_market_map
from app.modules.components import fmt_change
from app.price_signal import price_signal, ticker_signal
from scheduler.price_updater_common import get_market_status
from app.utils.display_diff import diff_display
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

    lev_html = f'<span class="lev-badge lev-x{leverage}">x{leverage}</span>' if leverage > 1 else ""

    delete_html = (
        f'<button class="btn-danger-sm" '
        f'onclick="if(confirm(\'{ticker} 티커를 삭제할까요?\')) '
        f'Shiny.setInputValue(\'{ns_str}confirm_delete_ticker\', \'{ticker}\', {{priority: \'event\'}});">'
        f'삭제</button>'
    ) if is_manual else '<div></div>'

    return (
        f'<div class="ticker-row" id="st-row-{tid}">'
        f'  <div>'
        f'    <div class="lev-name-wrap">'
        f'      {lev_html}'
        f'      <span class="ticker-name">{name}</span>'
        f'      <span id="st-status-{tid}" class="ticker-status"></span>'
        f'    </div>'
        f'    <div class="ticker-qty">{ticker} / {market}</div>'
        f'  </div>'
        f'  <div class="ticker-row-btn" style="display:flex; flex-direction:column; align-items:flex-end; gap:0;">'
        f'    {delete_html}'
        f'    <div class="ticker-change" id="st-change-{tid}"></div>'
        f'  </div>'
        f'</div>'
    )

def _build_tick_values(ticker, market, price, change_pct):
    """시세 갱신 시마다 전송하는 값 dict."""
    tid = _ticker_to_id(ticker)

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
        """),
        ui.tags.script("""
(function() {

  // ── st_init: 종목 구성 변경 시 골격 통째 교체 ──────────────
  Shiny.addCustomMessageHandler('st_init', function(m) {
    // 실시간 토글 상태 반영
    var toggle = document.getElementById('st-realtime-toggle');
    if (toggle) toggle.checked = (m.interval === 0);

    var listEl = document.getElementById('st-ticker-list');
    if (listEl) listEl.innerHTML = m.ticker_list_html || '<p style="color:#888; padding:8px 0;">등록된 티커가 없습니다.</p>';

    _applyTickers(m.tickers);
  });

  // ── st_tick: 변경된 key만 patch ───────────────────────────
  Shiny.addCustomMessageHandler('st_tick', function(m) {
    Object.keys(m).forEach(function(key) {
      _applyOneTicker(m[key]);
    });
  });

  function _applyTickers(tickers) {
    Object.values(tickers).forEach(function(t) { _applyOneTicker(t); });
  }

  function _applyOneTicker(t) {
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

  // ── 모달 show/hide ─────────────────────────────────────────
  window.stShowModal = function() {
    document.getElementById('st-modal-overlay').style.display = '';
  };
  window.stHideModal = function() {
    document.getElementById('st-modal-overlay').style.display = 'none';
    // 입력값 초기화
    ['st-new-ticker', 'st-new-ticker-name'].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.value = '';
    });
    var lev = document.getElementById('st-new-ticker-leverage');
    if (lev) lev.value = '1';
    var mkt = document.getElementById('st-new-ticker-market');
    if (mkt) mkt.selectedIndex = 0;
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
                        "+ 추가",
                        class_="btn-danger-sm",
                        style="color:#00c073;",
                        onclick="stShowModal();",
                    ),
                    class_="ticker-row-btn",
                ),
                style="display:flex; justify-content:space-between; align-items:center; padding: 20px 0 12px 0;",
            ),

            ui.div({"id": "st-ticker-list"}),

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

        # ── 티커 추가 모달 (정적 HTML, JS show/hide) ──────────────────────────
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

        class_="page-container",
    )


# ── Server ────────────────────────────────────────────────────────────────────

@module.server
def settings_server(input, output, session, active_tab: reactive.value = None):
    initialized = reactive.value(False)
    refresh = reactive.value(0)

    # 종목 구성 캐시 — ticker 목록이 바뀌면 st_init 전송
    _last_tickers: list = []
    _last_display: dict = {}

    # DB 캐시 — tickers 메타데이터 (ticker_changed / refresh 시에만 재조회)
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
    # val=0  → 실시간 ON  : config["interval"] = 0
    # val=-1 → 실시간 OFF : config["interval"] 를 config["default_interval"] 로 복원

    @reactive.effect
    @reactive.event(input.btn_save_interval)
    def _():
        val = input.btn_save_interval()
        if val is None:
            return
        config = get_config()
        if val == 0:
            # 실시간 ON
            config["interval"] = 0
        else:
            # 실시간 OFF → config 에 저장된 default_interval 로 복원
            config["interval"] = config.get("default_interval", 1)
        save_config(config)
        subprocess.Popen(["sudo", "systemctl", "restart", "price_updater"])

    # ── 티커 목록 갱신 ───────────────────────────────────────────────────────
    # ── 시세/daily insert 수신 시 대시보드 전체 갱신 ─────────────────────
    # price_signal 에 연결됨.
    # diff_display 로 이전 화면과 비교해 변경된 필드만 JS로 전송 (DOM 전체 교체 아님).
    # 탭 비활성 시 스킵: 보이지 않는 DOM을 패치하는 건 낭비이고,
    # 탭 활성화 순간 active_tab 이 "settings"로 바뀌면서 자동으로 재실행된다.

    @reactive.effect
    async def _send_update():
        nonlocal _last_tickers, _last_display
        price_signal.get()

        if initialized.get() and active_tab and active_tab.get() != "settings":
            return

        reactive.invalidate_later(60)

        from common.redis_store import get_all_prices
        prices = get_all_prices()

        rows = sorted(_ticker_rows(), key=_sort_key)

        # ticker → top-level key
        ticker_values = {}
        for ticker, name, market, leverage, is_manual in rows:
            p_data     = prices.get(ticker)
            price      = float(p_data["price"])      if p_data else 0.0
            change_pct = float(p_data["change_pct"]) if p_data else 0.0
            ticker_values[ticker] = _build_tick_values(ticker, market, price, change_pct)

        # 구조 변경 감지
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
        initialized.set(True)

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