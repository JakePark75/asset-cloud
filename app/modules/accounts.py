from shiny import ui, render, module, reactive
from db import get_connection, get_usd_krw
from price_signal import price_signal, start_signal_listener
from db import get_config

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

    def load_accounts():
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                a.id, a.name, a.alias,
                COALESCE(SUM(
                    CASE
                        WHEN p.ticker = 'KRW' THEN p.quantity
                        WHEN p.ticker = 'USD' THEN p.quantity * COALESCE((SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X'), 1)
                        WHEN t.market IN ('NAS', 'AMS', 'ARC') THEN p.quantity * COALESCE(t.current_price, 0) * COALESCE((SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X'), 1)
                        ELSE p.quantity * COALESCE(t.current_price, 0)
                    END
                ), 0) AS total_asset,
                COALESCE(SUM(
                    CASE
                        WHEN p.ticker = 'KRW' THEN p.quantity
                        WHEN p.ticker = 'USD' THEN p.quantity * COALESCE((SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X'), 1)
                        ELSE 0
                    END
                ), 0) AS cash,
                COALESCE(SUM(
                    CASE WHEN p.ticker NOT IN ('KRW','USD')
                    THEN p.quantity * COALESCE(t.current_price,0) * COALESCE(t.change_pct,0) / 100
                        * CASE WHEN t.market IN ('NAS', 'AMS', 'ARC') THEN COALESCE((SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X'), 1) ELSE 1 END
                    ELSE 0 END
                ), 0) AS daily_pnl
            FROM accounts a
            LEFT JOIN positions p ON p.account_id = a.id
            LEFT JOIN tickers t ON t.ticker = p.ticker
            GROUP BY a.id, a.name, a.alias
            ORDER BY a.id
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    def load_positions(acc_id):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT name, alias FROM accounts WHERE id = %s", (acc_id,))
        acc = cur.fetchone()
        cur.execute("""
            SELECT p.id, p.ticker, p.quantity, t.name, t.current_price, t.change_pct, t.market, t.leverage
            FROM positions p
            LEFT JOIN tickers t ON t.ticker = p.ticker
            WHERE p.account_id = %s
            ORDER BY
                CASE WHEN p.ticker IN ('KRW','USD') THEN 1 ELSE 0 END,
                CASE WHEN t.market = 'KR' THEN 0 WHEN t.market IN ('NAS', 'AMS', 'ARC') THEN 1 WHEN t.market = 'CRYPTO' THEN 2 ELSE 3 END,
                t.leverage DESC NULLS LAST,
                p.ticker
        """, (acc_id,))
        positions = cur.fetchall()
        cur.execute("SELECT current_price FROM tickers WHERE ticker = 'USDKRW=X'")
        usd_row = cur.fetchone()
        usd_rate = float(usd_row[0]) if usd_row else 1
        cur.close()
        conn.close()
        return acc, positions, usd_rate

    @render.ui
    def main_view():
        price_signal.get()
        refresh()
        acc_id = selected_account()

        if acc_id is None:
            accounts = load_accounts()
            usd_rate, usd_chg = get_usd_krw()
            total_sum = sum(acc[3] for acc in accounts)
            cash_sum = sum(acc[4] for acc in accounts)
            pnl_sum = sum(acc[5] for acc in accounts)
            invest_sum = total_sum - cash_sum
            pnl_pct_sum = (pnl_sum / invest_sum * 100) if invest_sum > 0 else 0
            pnl_class_sum = "positive" if pnl_sum >= 0 else "negative"
            triangle_sum = "▲" if pnl_sum >= 0 else "▼"
            pnl_text_sum = f"{triangle_sum}{int(pnl_sum):,}원 ({pnl_pct_sum:.2f}%)"            
            if not accounts:
                cards = ui.p("등록된 계좌가 없습니다.", style="color:#888; padding: 16px 0;")
            else:
                card_list = []
                for acc in accounts:
                    a_id, name, alias, total, cash, pnl = acc
                    invest = total - cash
                    pnl_pct = (pnl / invest * 100) if invest > 0 else 0
                    pnl_class = "positive" if pnl >= 0 else "negative"
                    triangle = "▲" if pnl >= 0 else "▼"
                    pnl_text = f"{triangle}{int(pnl):,}원 ({pnl_pct:.2f}%)"
                    card_list.append(
                        ui.div(
                            ui.div(
                                ui.strong(name),
                                ui.span(f" ({alias})" if alias else "", class_="account-alias"),
                            ),
                            ui.div(
                                ui.div(f"{int(total):,}원", class_="amount-large"),
                                ui.div(
                                    ui.span("일간손익 ", class_="card-pnl-label"),
                                    ui.span(pnl_text, class_=pnl_class),
                                    ui.span(f"현금 {int(cash):,}원", class_="card-cash-label"),
                                    class_="card-pnl-row",
                                ),
                            ),
                            class_="asset-card",
                            onclick=f"Shiny.setInputValue('{session.ns('selected_id')}', {a_id}, {{priority: 'event'}});",
                        )
                    )
                cards = ui.div(*card_list)

            if usd_rate is not None and usd_chg is not None:
                usd_sign = "+" if usd_chg >= 0 else "-"
                usd_class = "positive" if usd_chg >= 0 else "negative"
                usd_text_label = "USD/KRW "
                usd_text_num = f"{usd_rate:,.2f} ({usd_sign}{abs(usd_chg):.2f}%)"
            else:
                usd_text_label = ""
                usd_text_num = ""
                usd_class = ""
            return ui.div(
                # 환율 표시 텍스트 생성
                ui.div(
                    ui.div("총자산", class_="account-alias"),
                    ui.div(f"{int(total_sum):,}원", class_="total-summary-amount"),
                    ui.div(
                        ui.span(pnl_text_sum, class_=f"total-summary-pnl-text {pnl_class_sum}"),
                        ui.span(
                            ui.span(usd_text_label, style="color:#888888;"),
                            ui.span(usd_text_num, class_=usd_class),
                            style="margin-left:auto; font-size:11px;"
                        ) if usd_text_label else None,
                        class_="total-summary-pnl",
                        style="justify-content:space-between;"
                    ),
                    class_="total-summary",
                ),
                ui.div(
                    ui.h4("계좌 목록", class_="section-heading"),
                    cards,
                    ui.input_action_button("btn_add_account", "+ 계좌 추가", class_="btn-add"),
                    class_="page-inner",
                )
            )

        else:
            acc, positions, usd_rate = load_positions(acc_id)
            total_sum = 0
            pnl_sum = 0
            rows = []
            for pos in positions:
                pos_id, ticker, qty, tname, price, chg_pct, t_market, leverage = pos
                is_cash = ticker in ('KRW', 'USD')
                if is_cash:
                    display_name = "현금(KRW)" if ticker == "KRW" else "현금(USD)"
                    if ticker == "USD":
                        amount_str = f"{int(float(qty) * usd_rate):,}원"
                        total_sum += float(qty) * usd_rate
                    else:
                        amount_str = f"{int(qty):,}원"
                        total_sum += float(qty)
                    qty_str = ""
                    chg_str = ""
                    chg_class = ""
                else:
                    rate = usd_rate if t_market in ('NAS', 'AMS', 'ARC') else 1
                    amount = float(qty) * float(price or 0) * rate
                    chg = float(chg_pct or 0)
                    chg_sign = "+" if chg >= 0 else ""
                    chg_class = "positive" if chg >= 0 else "negative"
                    display_name = tname or ticker
                    amount_str = f"{int(amount):,}원"
                    qty_str = f"{qty:g}주"
                    chg_str = f"{chg_sign}{chg:.2f}%"
                    total_sum += amount
                    pnl_sum += amount * chg / 100

                rows.append(
                    ui.div(
                        ui.div(
                            ui.div(
                                ui.span(f"x{leverage}", class_=f"lev-badge lev-x{leverage}") if leverage and leverage > 1 else None,
                                ui.span(display_name, class_="ticker-name"),
                                class_="lev-name-wrap",
                            ),
                            ui.div(qty_str, class_="ticker-qty"),
                        ),
                        ui.div(
                            ui.div(amount_str, class_="ticker-amount"),
                            ui.div(chg_str, class_=f"ticker-change {chg_class}"),
                        ),
                        class_="ticker-row",
                        onclick=f"Shiny.setInputValue('{session.ns('edit_pos_id')}', {pos_id}, {{priority: 'event'}});",
                    )
                )

                pnl_class_sum = "positive" if pnl_sum >= 0 else "negative"
                triangle_sum = "▲" if pnl_sum >= 0 else "▼"
                invest_sum = total_sum  # 계좌 상세는 현금 포함 전체 대비
                pnl_pct_sum = (pnl_sum / invest_sum * 100) if invest_sum > 0 else 0
                pnl_text_sum = f"{triangle_sum}{int(pnl_sum):,}원 ({pnl_pct_sum:.2f}%)"

            return ui.div(
                ui.div(
                    ui.input_action_button("btn_back", "‹", class_="detail-titlebar-back"),
                    ui.span(f"{acc[0]}" + (f" ({acc[1]})" if acc[1] else ""), class_="detail-titlebar-title"),
                    class_="detail-titlebar",
                ),
                ui.div(
                    ui.div("총자산", class_="account-alias"),
                    ui.div(f"{int(total_sum):,}원", class_="total-summary-amount"),
                    ui.div(ui.span(pnl_text_sum, class_=f"total-summary-pnl-text {pnl_class_sum}"), class_="total-summary-pnl"),
                    class_="total-summary",
                ),                
                ui.div(*rows, class_="page-inner") if rows else ui.p("종목이 없습니다.", style="color:#888; padding: 16px;"),
                ui.div(
                    ui.input_action_button("btn_add_position", "+ 종목 추가", class_="btn-add"),
                    ui.input_action_button("btn_add_cash", "+ 현금 추가", class_="btn-add"),
                    ui.input_action_button("btn_delete_account", "계좌 삭제", class_="btn-account-delete-bottom",
                        onclick=f"if(confirm('계좌를 삭제하시겠습니까?')) Shiny.setInputValue('{session.ns('confirm_delete_account')}', Math.random(), {{priority: 'event'}});"),
                    class_="page-inner",
                ),
            )

    @render.ui
    def modal_add_account():
        if not show_modal():
            return ui.div()
        return ui.div(
            ui.div(
                ui.div(
                    ui.h4("계좌 추가", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon",
                            onclick=f"Shiny.setInputValue('{session.ns('modal_close')}', Math.random(), {{priority: 'event'}});"),
                    class_="modal-header-row",
                ),
                ui.input_text("new_account_name", "계좌명", placeholder="예) 키움증권"),
                ui.input_text("new_account_alias", "별명 (선택)", placeholder="예) 키움"),
                ui.input_action_button("btn_confirm_add", "추가", class_="btn-add"),
                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            class_="modal-overlay",
            onclick=f"Shiny.setInputValue('{session.ns('modal_close')}', Math.random(), {{priority: 'event'}});",
        )

    @render.ui
    def modal_add_position():
        if not show_modal_position():
            return ui.div()
        return ui.div(
            ui.div(
                ui.div(
                    ui.h4("종목 추가", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon",
                            onclick=f"Shiny.setInputValue('{session.ns('modal_position_close')}', Math.random(), {{priority: 'event'}});"),
                    class_="modal-header-row",
                ),
                ui.input_text("new_position_name", "종목명", placeholder="예) 애플"),
                ui.input_text("new_position_ticker", "티커", placeholder="예) AAPL"),
                ui.input_select("new_position_market", "시장", {"KR": "KR (한국)", "NAS": "NAS (나스닥)", "AMS": "AMS (아멕스)", "ARC": "ARC (NYSE Arca)", "FX": "FX (환율)", "INDEX": "INDEX (지수)", "CRYPTO": "CRYPTO (암호화폐)"}),
                ui.input_select("new_position_leverage", "레버리지", {"1": "x1", "2": "x2", "3": "x3"}),
                ui.input_numeric("new_position_qty", "수량", value=None, min=0),
                ui.input_action_button("btn_confirm_add_position", "추가", class_="btn-add"),
                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            class_="modal-overlay",
            onclick=f"Shiny.setInputValue('{session.ns('modal_position_close')}', Math.random(), {{priority: 'event'}});",
        )

    @render.ui
    def modal_add_cash():
        if not show_modal_cash():
            return ui.div()
        return ui.div(
            ui.div(
                ui.div(
                    ui.h4("현금 추가", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon",
                            onclick=f"Shiny.setInputValue('{session.ns('modal_cash_close')}', Math.random(), {{priority: 'event'}});"),
                    class_="modal-header-row",
                ),
                ui.input_select("new_cash_type", "통화", {"KRW": "원화(KRW)", "USD": "달러(USD)"}),
                ui.input_numeric("new_cash_amount", "금액", value=None, min=0),
                ui.input_action_button("btn_confirm_add_cash", "추가", class_="btn-add"),
                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            class_="modal-overlay",
            onclick=f"Shiny.setInputValue('{session.ns('modal_cash_close')}', Math.random(), {{priority: 'event'}});",
        )

    @render.ui
    def modal_edit_cash():
        if not show_modal_edit_cash():
            return ui.div()
        pos_id = edit_cash_id()
        if pos_id is None:
            return ui.div()
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT ticker, quantity FROM positions WHERE id = %s", (pos_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return ui.div()
        ticker, qty = row
        return ui.div(
            ui.div(
                ui.div(
                    ui.h4("현금 수정", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon",
                            onclick=f"Shiny.setInputValue('{session.ns('modal_edit_cash_close')}', Math.random(), {{priority: 'event'}});"),
                    class_="modal-header-row",
                ),
                ui.input_select("edit_cash_type", "통화", {"KRW": "원화(KRW)", "USD": "달러(USD)"}, selected=ticker),
                ui.input_numeric("edit_cash_amount", "금액", value=int(qty) if qty else 0, min=0),
                ui.input_action_button("btn_confirm_edit_cash", "저장", class_="btn-add"),
                ui.input_action_button("btn_delete_cash", "현금 삭제", class_="btn-modal-delete-bottom",
                    onclick=f"if(confirm('삭제하시겠습니까?')) Shiny.setInputValue('{session.ns('confirm_delete_cash')}', Math.random(), {{priority: 'event'}});"),                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            class_="modal-overlay",
            onclick=f"Shiny.setInputValue('{session.ns('modal_edit_cash_close')}', Math.random(), {{priority: 'event'}});",
        )

    @render.ui
    def modal_edit_position():
        if not show_modal_edit_position():
            return ui.div()
        pos_id = edit_position_id()
        if pos_id is None:
            return ui.div()
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT p.ticker, p.quantity, t.name, t.market, t.leverage
            FROM positions p LEFT JOIN tickers t ON t.ticker = p.ticker
            WHERE p.id = %s
        """, (pos_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return ui.div()
        ticker, qty, tname, market, leverage = row
        return ui.div(
            ui.div(
                ui.div(
                    ui.h4("종목 수정", class_="modal-title"),
                    ui.span("✕", class_="modal-close-icon",
                            onclick=f"Shiny.setInputValue('{session.ns('modal_edit_position_close')}', Math.random(), {{priority: 'event'}});"),
                    class_="modal-header-row",
                ),
                ui.p(ticker, class_="ticker-readonly-label"),
                ui.input_text("edit_position_name", "종목명", value=tname or ""),
                ui.input_select("edit_position_market", "시장", {"KR": "KR (한국)", "NAS": "NAS (나스닥)", "AMS": "AMS (아멕스)", "ARC": "ARC (NYSE Arca)", "FX": "FX (환율)", "INDEX": "INDEX (지수)", "CRYPTO": "CRYPTO (암호화폐)"}, selected=market or "NAS"),
                ui.input_select("edit_position_leverage", "레버리지", {"1": "x1", "2": "x2", "3": "x3"}, selected=str(leverage or 1)),
                ui.input_numeric("edit_position_qty", "수량", value=int(qty) if qty else 0, min=0),
                ui.input_action_button("btn_confirm_edit_position", "저장", class_="btn-add"),
                ui.input_action_button("btn_delete_position", "종목 삭제", class_="btn-modal-delete-bottom",
                    onclick=f"if(confirm('삭제하시겠습니까?')) Shiny.setInputValue('{session.ns('confirm_delete_position')}', Math.random(), {{priority: 'event'}});"),                class_="modal-box",
                onclick="event.stopPropagation();",
            ),
            class_="modal-overlay",
            onclick=f"Shiny.setInputValue('{session.ns('modal_edit_position_close')}', Math.random(), {{priority: 'event'}});",
        )

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
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO accounts (name, alias) VALUES (%s, %s)", (name, alias))
        conn.commit()
        cur.close()
        conn.close()
        show_modal.set(False)
        refresh.set(refresh() + 1)

    @reactive.effect
    @reactive.event(input.btn_back)
    def go_back():
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