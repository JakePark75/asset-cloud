from shiny import ui, render, module, reactive

# 하위 모듈은 app.modules 로 시작
from app.modules.accounts_DAL import fetch_accounts_summary, fetch_account_details
from app.modules.accounts_components import render_asset_card, render_ticker_row
from app.modules.accounts_modals import modal_add_account_ui, modal_add_position_ui, modal_add_cash_ui, modal_edit_position_ui, modal_edit_cash_ui

# app 폴더에 직접 위치한 파일들은 app. 으로 시작
from app.db import get_connection, get_usd_krw, get_config, get_market_currency
from app.modules.components import render_summary_header
from app.price_signal import price_signal, start_signal_listener


def _notify_price_updated():
    """
    포트폴리오 DB 변경(계좌/포지션/현금 CRUD) 후 다른 화면들에게 갱신 신호를 보낸다.

    배경:
      price_signal 은 price_updater 가 주기적으로 NOTIFY price_updated 를 발송할 때만
      갱신 신호를 받는다. 따라서 계좌관리에서 DB 를 변경해도 다음 price_updater 주기
      (REST 모드 기준 최대 수십 분)까지 포트폴리오/대시보드/실적 화면에 반영되지 않는다.

      이 함수는 DB 변경이 완료된 직후 직접 NOTIFY 를 발송해 price_signal 을 즉시 트리거,
      모든 화면이 변경된 포트폴리오를 바로 반영하도록 한다.

    주의:
      - price_updater 와 동일한 채널(price_updated)을 사용하므로 추가 리스너 불필요.
      - 실패해도 계좌 화면 자체의 갱신(refresh)에는 영향 없으므로 예외를 삼킨다.
    """
    try:
        conn = get_connection()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("NOTIFY price_updated")
        cur.close()
        conn.close()
    except Exception as e:
        # NOTIFY 실패는 비치명적 — 계좌 화면은 refresh 로 이미 갱신됨
        print(f"[accounts] NOTIFY price_updated 실패 (무시): {e}")

    # Redis: today_row 재계산 (휘발성 실적 데이터 — Step 6 완료 후 각 화면이 Redis에서만 읽음)
    try:
        from common.redis_store import recalc_today_row
        recalc_today_row()
    except Exception as e:
        print(f"[accounts] recalc_today_row 실패 (무시): {e}")


@module.ui
def accounts_ui():
    return ui.div(
        ui.output_ui("main_view"),
        ui.output_ui("modal_add_account"),
        ui.output_ui("modal_add_position"),
        ui.output_ui("modal_add_cash"),
        ui.output_ui("modal_edit_position"),
        ui.output_ui("modal_edit_cash"),
    )

@module.server
def accounts_server(input, output, session):
    ns = session.ns  # 네임스페이스 함수 정의
    start_signal_listener(get_config()["db_password"])
    selected_account = reactive.value(None)
    show_modal = reactive.value(False)
    refresh = reactive.value(0)
    show_modal_position = reactive.value(False)
    show_modal_cash = reactive.value(False)
    show_modal_edit_position = reactive.value(False)
    edit_position_id = reactive.value(None)
    show_modal_edit_cash = reactive.value(False)
    edit_cash_id = reactive.value(None)

    @render.ui
    def main_view():
        price_signal.get()
        refresh()
        acc_id = selected_account()
        ns = session.ns
        usd_rate_val, usd_chg = get_usd_krw()

        # ---------------------------------------------------------
        # 1. 메인 계좌 목록 화면 (acc_id가 없을 때)
        # ---------------------------------------------------------
        if acc_id is None:
            accounts = fetch_accounts_summary()
            
            # 합계 계산 (감시계좌 제외)
            total_sum = sum(acc[3] for acc in accounts if not acc[5])
            cash_sum = sum(acc[4] for acc in accounts if not acc[5])
            yesterday_total = sum(acc[6] for acc in accounts if not acc[5])
            pnl_sum = total_sum - yesterday_total
            
            pnl_pct_sum = (pnl_sum / yesterday_total * 100) if yesterday_total > 0 else 0
            
            return ui.div(
                ui.div(
                    render_summary_header(
                        label="총자산",
                        total_asset=total_sum,
                        pnl=pnl_sum,
                        pnl_pct=pnl_pct_sum,
                        usd_rate=usd_rate_val,
                        usd_chg=usd_chg,
                    ),
                    ui.h4("계좌 목록", class_="section-heading"),
                    ui.div(*[render_asset_card(acc, ns) for acc in accounts if not acc[5]]) if any(not acc[5] for acc in accounts) else ui.p("등록된 계좌가 없습니다.", style="color:#888; padding:16px 0;"),
                    ui.h4("감시 계좌", class_="section-heading") if any(acc[5] for acc in accounts) else ui.span(),
                    ui.div(*[render_asset_card(acc, ns) for acc in accounts if acc[5]]) if any(acc[5] for acc in accounts) else ui.span(),
                    ui.input_action_button("btn_add_account", "+ 계좌 추가", class_="btn-add"),
                    class_="page-inner",
                )
            )

        # ---------------------------------------------------------
        # 2. 계좌 상세 종목 화면 (acc_id가 있을 때)
        # ---------------------------------------------------------
        else:
            acc, positions, usd_rate = fetch_account_details(acc_id)
            prev_total = float(acc[3])  # accounts.prev_total_asset

            # 상세 계산 로직
            total_sum = 0
            for pos in positions:
                ticker, qty, price, t_market = pos[1], float(pos[2] or 0), float(pos[4] or 0), pos[6]
                rate = usd_rate if (get_market_currency(t_market) == "USD" or ticker == "USD") else 1
                amt = qty * (price if ticker not in ('KRW', 'USD') else 1) * rate
                total_sum += amt

            pnl_sum = total_sum - prev_total
            pnl_pct_sum = (pnl_sum / prev_total * 100) if prev_total > 0 else 0
            acc_usd_rate, acc_usd_chg = get_usd_krw()

            return ui.div(
                ui.div(
                    ui.input_action_button("btn_back", "‹", class_="detail-titlebar-back"),
                    ui.span(f"{acc[0]}" + (f" ({acc[1]})" if acc[1] else ""), class_="detail-titlebar-title"),
                    class_="detail-titlebar",
                ),
                ui.div(
                    render_summary_header(
                        label="계좌자산",
                        total_asset=total_sum,
                        pnl=pnl_sum,
                        pnl_pct=pnl_pct_sum,
                        usd_rate=acc_usd_rate,
                        usd_chg=acc_usd_chg,
                    ),
                    ui.div(*[
                        ui.div(
                            render_ticker_row(pos, usd_rate),
                            onclick=f"Shiny.setInputValue('{ns('edit_pos_id')}', {pos[0]}, {{priority: 'event'}});",
                            style="cursor:pointer;",
                        )
                        for pos in positions
                    ]) if positions else ui.p("종목이 없습니다.", style="color:#888; padding:16px;"),
                    ui.div(
                        ui.input_action_button("btn_add_position", "+ 종목 추가", class_="btn-add"),
                        ui.input_action_button("btn_add_cash", "+ 현금 추가", class_="btn-add"),
                        ui.input_action_button("btn_delete_account", "계좌 삭제", class_="btn-account-delete-bottom",
                            onclick=f"if(confirm('계좌를 삭제하시겠습니까?')) Shiny.setInputValue('{ns('confirm_delete_account')}', Math.random(), {{priority: 'event'}});"),
                        style="margin-top: 20px;"
                    ),
                    class_="page-inner",
                ),
            )
        
    @render.ui
    def modal_add_account():
        if not show_modal(): return ui.div()
        return modal_add_account_ui(ns)  # 분리된 함수 호출        

    @render.ui
    def modal_add_position():
        if not show_modal_position(): return ui.div()
        return modal_add_position_ui(ns)  # 분리된 함수 호출

    @render.ui
    def modal_add_cash():
        if not show_modal_cash(): return ui.div()
        return modal_add_cash_ui(ns)

    @render.ui
    def modal_edit_position():
        if not show_modal_edit_position(): return ui.div()
        pos_id = edit_position_id()
        acc_id = selected_account()
        _, positions, _ = fetch_account_details(acc_id)
        pos = next((p for p in positions if p[0] == pos_id), None)
        if not pos: return ui.div()
        _, ticker, qty, name, _, _, market, leverage = pos
        return modal_edit_position_ui(ns, ticker, name, market, leverage, qty)

    @render.ui
    def modal_edit_cash():
        if not show_modal_edit_cash(): return ui.div()
        pos_id = edit_cash_id()
        acc_id = selected_account()
        _, positions, _ = fetch_account_details(acc_id)
        pos = next((p for p in positions if p[0] == pos_id), None)
        if not pos: return ui.div()
        _, ticker, qty, _, _, _, _, _ = pos
        return modal_edit_cash_ui(ns, ticker, qty)

    @reactive.effect
    def handle_card_click():
        try:
            acc_id = input.selected_id()
            if acc_id is not None:
                selected_account.set(acc_id)
        except:
            pass

    @reactive.effect
    @reactive.event(input.btn_add_account)
    def open_modal():
        show_modal.set(True)

    @reactive.effect
    @reactive.event(input.modal_close)
    def close_modal():
        show_modal.set(False)

    @reactive.effect
    @reactive.event(input.btn_confirm_add)
    def add_account():
        name = input.new_account_name().strip()
        if not name:
            return
        alias = input.new_account_alias().strip() or None
        is_watch = input.new_account_is_watch() if hasattr(input, 'new_account_is_watch') else False
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO accounts (name, alias, is_watch) VALUES (%s, %s, %s)", (name, alias, is_watch))
        conn.commit()
        cur.close()
        conn.close()
        show_modal.set(False)
        refresh.set(refresh() + 1)
        # 계좌 추가 → 포트폴리오/대시보드/실적 화면에 갱신 신호 전송
        _notify_price_updated()

    @reactive.effect
    @reactive.event(input.btn_back)
    def go_back():
        # 단순 화면 이동 — DB 변경 없으므로 NOTIFY 불필요
        selected_account.set(None)
        refresh.set(refresh() + 1)

    @reactive.effect
    @reactive.event(input.btn_add_position)
    def open_modal_position():
        show_modal_position.set(True)

    @reactive.effect
    @reactive.event(input.modal_position_close)
    def close_modal_position():
        show_modal_position.set(False)

    @reactive.effect
    @reactive.event(input.btn_add_cash)
    def open_modal_cash():
        show_modal_cash.set(True)

    @reactive.effect
    @reactive.event(input.modal_cash_close)
    def close_modal_cash():
        show_modal_cash.set(False)

    @reactive.effect
    @reactive.event(input.btn_confirm_add_position)
    def add_position():
        name = input.new_position_name().strip()
        ticker = input.new_position_ticker().strip().upper()
        qty = input.new_position_qty()
        acc_id = selected_account()
        if not ticker or not acc_id:
            return
        market = input.new_position_market()
        leverage = int(input.new_position_leverage())
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM tickers WHERE ticker = %s", (ticker,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO tickers (ticker, name, market, leverage, is_manual) VALUES (%s, %s, %s, %s, false)",
                (ticker, name or ticker, market, leverage)
            )
        cur.execute("INSERT INTO positions (account_id, ticker, quantity) VALUES (%s, %s, %s)", (acc_id, ticker, qty))
        conn.commit()
        cur.close()
        conn.close()
        show_modal_position.set(False)
        refresh.set(refresh() + 1)
        # 종목 추가 → 포트폴리오/대시보드/실적 화면에 갱신 신호 전송
        _notify_price_updated()

    @reactive.effect
    @reactive.event(input.btn_confirm_add_cash)
    def add_cash():
        cash_type = input.new_cash_type()
        amount = input.new_cash_amount()
        acc_id = selected_account()
        if not acc_id:
            return
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO positions (account_id, ticker, quantity) VALUES (%s, %s, %s)", (acc_id, cash_type, amount))
        conn.commit()
        cur.close()
        conn.close()
        show_modal_cash.set(False)
        refresh.set(refresh() + 1)
        # 현금 추가 → 포트폴리오/대시보드/실적 화면에 갱신 신호 전송
        _notify_price_updated()

    @reactive.effect
    @reactive.event(input.confirm_delete_account)
    def delete_account():
        acc_id = selected_account()
        if acc_id is None:
            return
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM accounts WHERE id = %s", (acc_id,))
        conn.commit()
        cur.close()
        conn.close()
        selected_account.set(None)
        refresh.set(refresh() + 1)
        # 계좌 삭제 → 포트폴리오/대시보드/실적 화면에 갱신 신호 전송
        _notify_price_updated()

    @reactive.effect
    def handle_edit_pos_click():
        try:
            pos_id = input.edit_pos_id()
            if pos_id is not None:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute("SELECT ticker FROM positions WHERE id = %s", (pos_id,))
                row = cur.fetchone()
                cur.close()
                conn.close()
                if row and row[0] in ('KRW', 'USD'):
                    edit_cash_id.set(pos_id)
                    show_modal_edit_cash.set(True)
                else:
                    edit_position_id.set(pos_id)
                    show_modal_edit_position.set(True)
        except:
            pass

    @reactive.effect
    @reactive.event(input.modal_edit_position_close)
    def close_modal_edit_position():
        show_modal_edit_position.set(False)

    @reactive.effect
    @reactive.event(input.btn_confirm_edit_position)
    def edit_position():
        pos_id = edit_position_id()
        if not pos_id:
            return
        qty = input.edit_position_qty()
        name = input.edit_position_name().strip()
        market = input.edit_position_market()
        leverage = int(input.edit_position_leverage())
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE positions SET quantity = %s WHERE id = %s", (qty, pos_id))
        cur.execute("""
            UPDATE tickers SET name = %s, market = %s, leverage = %s
            WHERE ticker = (SELECT ticker FROM positions WHERE id = %s)
        """, (name, market, leverage, pos_id))
        conn.commit()
        cur.close()
        conn.close()
        show_modal_edit_position.set(False)
        refresh.set(refresh() + 1)
        # 종목 수정(수량/시장/레버리지) → 포트폴리오/대시보드/실적 화면에 갱신 신호 전송
        _notify_price_updated()

    @reactive.effect
    @reactive.event(input.confirm_delete_position)
    def delete_position():
        pos_id = edit_position_id()
        if not pos_id:
            return
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM positions WHERE id = %s", (pos_id,))
        conn.commit()
        cur.close()
        conn.close()
        show_modal_edit_position.set(False)
        refresh.set(refresh() + 1)
        # 종목 삭제 → 포트폴리오/대시보드/실적 화면에 갱신 신호 전송
        _notify_price_updated()

    @reactive.effect
    @reactive.event(input.modal_edit_cash_close)
    def close_modal_edit_cash():
        show_modal_edit_cash.set(False)

    @reactive.effect
    @reactive.event(input.btn_confirm_edit_cash)
    def edit_cash():
        pos_id = edit_cash_id()
        if not pos_id:
            return
        cash_type = input.edit_cash_type()
        amount = input.edit_cash_amount()
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE positions SET ticker = %s, quantity = %s WHERE id = %s", (cash_type, amount, pos_id))
        conn.commit()
        cur.close()
        conn.close()
        show_modal_edit_cash.set(False)
        refresh.set(refresh() + 1)
        # 현금 수정(금액/종류) → 포트폴리오/대시보드/실적 화면에 갱신 신호 전송
        _notify_price_updated()

    @reactive.effect
    @reactive.event(input.confirm_delete_cash)
    def delete_cash():
        pos_id = edit_cash_id()
        if not pos_id:
            return
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM positions WHERE id = %s", (pos_id,))
        conn.commit()
        cur.close()
        conn.close()
        show_modal_edit_cash.set(False)
        refresh.set(refresh() + 1)
        # 현금 삭제 → 포트폴리오/대시보드/실적 화면에 갱신 신호 전송
        _notify_price_updated()