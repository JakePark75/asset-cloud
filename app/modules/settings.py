from shiny import ui, render, module, reactive
from app.db import get_connection, get_config, save_config, get_market_currency, get_market_map
from app.modules.components import fmt_change
from app.price_signal import price_signal
from scheduler.price_updater_common import get_market_status
from datetime import datetime, time
import subprocess
import pytz

def _notify_price_updated():
    """
    티커 추가/삭제 후 다른 화면들에게 갱신 신호를 보낸다.

    배경:
      티커가 추가/삭제되어도 price_updater 의 NOTIFY 가 오기 전까지
      포트폴리오/대시보드 등 다른 화면은 변경을 인지하지 못한다.
      티커 변경은 시세 변경과 독립적인 이벤트이므로 직접 NOTIFY 를 발송한다.

    주의:
      - price_updater 와 동일한 채널(price_updated)을 사용하므로 추가 리스너 불필요.
      - 실패해도 설정 화면 자체의 갱신(refresh)에는 영향 없으므로 예외를 삼킨다.
    """
    try:
        conn = get_connection()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("NOTIFY price_updated")
        cur.close()
        conn.close()
    except Exception as e:
        # NOTIFY 실패는 비치명적 — 설정 화면은 refresh 로 이미 갱신됨
        print(f"[settings] NOTIFY price_updated 실패 (무시): {e}")


@module.ui
def settings_ui():
    return ui.div(
        ui.div(
            # 시세조회 간격
            ui.div(
                ui.p("시세조회 간격", style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.08em; margin-bottom:12px;"),
                ui.output_ui("interval_buttons"),
                style="padding: 20px 0; border-bottom: 1px solid #1e1e1e;"
            ),
            # 티커 관리
            ui.div(
                ui.p("티커 관리", style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.08em; margin:0;"),
                ui.div(
                    ui.input_action_button("btn_add_ticker", "+ 추가", class_="btn-danger-sm", style="color:#00c073;"),
                    class_="ticker-row-btn"
                ),
                style="display:flex; justify-content:space-between; align-items:center; padding: 20px 0 12px 0;"
            ),
            ui.output_ui("ticker_list"),
            # 로그아웃
            ui.div(
                ui.tags.button(
                    "로그아웃",
                    style="background:none; border:none; color:#888; font-size:14px; padding: 20px 0; cursor:pointer; width:100%; text-align:center;",
                    onclick="deleteCookie('auth_token'); location.reload();"
                ),
            ),
            class_="page-inner",
        ),
        ui.output_ui("modal_add_ticker"),
    )

@module.server
def settings_server(input, output, session):
    refresh = reactive.value(0)
    show_modal_ticker = reactive.value(False)

    # 시세조회 간격 버튼 렌더링
    @render.ui
    def interval_buttons():
        current = get_config().get("interval", 1)
        buttons = []
        options = [(0, "실시간"), (1, "1분"), (3, "3분"), (5, "5분"), (10, "10분"), (30, "30분")]
        for v, label in options:
            active_class = "interval-btn active" if v == current else "interval-btn"
            buttons.append(
                ui.tags.button(
                    label,
                    class_=active_class,
                    onclick=f"Shiny.setInputValue('settings-btn_save_interval', {v}, {{priority: 'event'}}); document.querySelectorAll('.interval-btn').forEach(b => b.classList.remove('active')); this.classList.add('active');"
                )
            )
        return ui.div(*buttons, style="display:flex; gap:8px;")

    # 시세조회 간격 저장
    @reactive.effect
    @reactive.event(input.btn_save_interval)
    def _():
        val = input.btn_save_interval()
        if val is None:
            return
        config = get_config()
        config["interval"] = val
        save_config(config)
        subprocess.Popen(["sudo", "systemctl", "restart", "price_updater"])

    # 수동 티커 목록
    @render.ui
    def ticker_list():
        refresh()
        # price_signal 구독 — 계좌에서 종목 추가 시 tickers 테이블이 변경되므로
        # NOTIFY 를 받아 티커 목록을 즉시 갱신한다
        price_signal.get()
        # 60초마다 화면 자동 갱신 (시장 open/closed 상태 표시 반영용)
        # price_signal 과 별개로 시장 상태는 시세와 무관하게 시간에 따라 바뀌므로 유지
        reactive.invalidate_later(60)

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, name, market, leverage, is_manual, current_price, change_pct FROM tickers
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        _MARKET_ORDER = {
            "KR": 0,
            "NAS": 1, "NYS": 1, "AMS": 1, "ARC": 1,
            "CRYPTO": 2,
            "COM": 3,
            "FX": 4, "INDEX": 4,
        }
        def _sort_key(r):
            ticker, _, market, leverage, is_manual, _, _ = r
            return (
                0 if is_manual else 1,
                _MARKET_ORDER.get(market, 99),
                -(leverage or 1),
                ticker,
            )
        rows = sorted(rows, key=_sort_key)

        if not rows:
            return ui.p("등록된 티커가 없습니다.", style="color:#888; padding: 8px 0;")

        items = []
        for ticker, name, market, leverage, is_manual, current_price, change_pct in rows:
            currency = get_market_currency(market)
            price_str, chg_str, chg_css = fmt_change(float(current_price or 0), float(change_pct or 0), currency=currency)
            status = get_market_status(market)
            if status == "open":
                status_dot, status_text, status_class = "●", "Open", "status-open"
            elif status == "pre":
                status_dot, status_text, status_class = "●", "Pre", "status-pre"
            elif status == "after":
                status_dot, status_text, status_class = "●", "After", "status-after"
            elif status == "closing":
                status_dot, status_text, status_class = "●", "Closing...", "status-closing"
            else:
                status_dot, status_text, status_class = "○", "Closed", "status-closed"

            items.append(
                ui.div(
                    ui.div(
                        ui.div(
                            ui.span(f"x{leverage}", class_=f"lev-badge lev-x{leverage}") if leverage > 1 else None,
                            ui.span(f"{name}", class_="ticker-name"),
                            ui.span(f"{status_dot} {status_text}", class_=f"ticker-status {status_class}"),
                            class_="lev-name-wrap",
                        ),
                        ui.div(f"{ticker} / {market}", class_="ticker-qty"),
                    ),
                    ui.div(
                        ui.tags.button(
                            "삭제",
                            class_="btn-danger-sm",
                            onclick=f"if(confirm('{ticker} 티커를 삭제할까요?')) Shiny.setInputValue('{session.ns('confirm_delete_ticker')}', '{ticker}', {{priority: 'event'}});"
                        ) if is_manual else ui.div(),
                        ui.div(
                            ui.span(price_str, class_=chg_css, style="margin-right:4px;") if price_str else None,
                            ui.span(chg_str, class_=chg_css),
                            class_="ticker-change",
                        ) if chg_str else ui.div(),
                        class_="ticker-row-btn",
                        style="display:flex; flex-direction:column; align-items:flex-end; gap:0;",
                    ),
                    class_="ticker-row",
                )
            )
        return ui.div(*items)

    # 티커 삭제
    @reactive.effect
    @reactive.event(input.confirm_delete_ticker)
    def _():
        ticker = input.confirm_delete_ticker()
        if not ticker:
            return
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM tickers WHERE ticker = %s AND is_manual = true", (ticker,))
        conn.commit()
        cur.close()
        conn.close()
        refresh.set(refresh() + 1)
        # 수동 티커 삭제 → 다른 화면에 갱신 신호 전송
        _notify_price_updated()

    # 티커 추가 모달 열기
    @reactive.effect
    @reactive.event(input.btn_add_ticker)
    def _():
        show_modal_ticker.set(True)

    # 티커 추가 모달 닫기
    @reactive.effect
    @reactive.event(input.modal_ticker_close)
    def _():
        show_modal_ticker.set(False)

    @render.ui
    def modal_add_ticker():
        if not show_modal_ticker():
            return ui.div()
        return ui.div(
            ui.div(
                ui.div(
                    ui.h4("티커 추가", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon",
                            onclick=f"Shiny.setInputValue('{session.ns('modal_ticker_close')}', Math.random(), {{priority: 'event'}});"),
                    class_="modal-header-row",
                ),
                ui.input_text("new_ticker", "티커", placeholder="예) USDKRW=X"),
                ui.input_text("new_ticker_name", "종목명", placeholder="예) 달러/원 환율"),
                ui.input_select("new_ticker_market", "시장", choices=list(get_market_map().keys())),
                ui.input_numeric("new_ticker_leverage", "레버리지", value=1, min=1, max=3),
                ui.input_action_button("btn_confirm_add_ticker", "추가", class_="btn-add"),
                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            class_="modal-overlay",
            onclick=f"Shiny.setInputValue('{session.ns('modal_ticker_close')}', Math.random(), {{priority: 'event'}});",
        )

    # 티커 추가 확인
    @reactive.effect
    @reactive.event(input.btn_confirm_add_ticker)
    def _():
        ticker = input.new_ticker().strip().upper()
        name = input.new_ticker_name().strip()
        market = input.new_ticker_market()
        leverage = input.new_ticker_leverage()

        if not ticker or not name:
            return

        conn = get_connection()
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
        conn.close()
        show_modal_ticker.set(False)
        refresh.set(refresh() + 1)
        # 수동 티커 추가 → 다른 화면에 갱신 신호 전송
        _notify_price_updated()