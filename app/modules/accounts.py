import re

import yfinance as yf
from shiny import ui, module, reactive

from app.modules.accounts_DAL import (
    fetch_accounts_summary, calc_accounts_summary,
    fetch_account_details, calc_account_details,
    execute_buy, execute_sell,
    add_account, delete_account,
    add_position, edit_position, delete_position,
    add_cash, edit_cash, delete_cash,
)
from app.modules.accounts_helpers import (
    _build_account_card_skeleton, _build_account_card_values,
    _build_position_row_skeleton, _build_position_row_values,
)
from app.modules.accounts_modals import (
    modal_edit_position_html,
    modal_add_account_html,
    modal_add_position_html,
    modal_add_cash_html,
    modal_edit_cash_html,
)
from app.modules.accounts_js import accounts_js
from app.db import get_usd_krw, get_market_map, get_market_label, get_market_currency
from app.price_signal import price_signal, daily_insert_signal
from app.utils.display_diff import diff_display, diff_display_split
import json


def _notify_position_changed():
    try:
        from common.redis_store import recalc_today_row, publish_position_changed
        recalc_today_row()
        publish_position_changed()
    except Exception as e:
        print(f"[accounts] position_changed 신호 발행 실패 (무시): {e}")


def _notify_ticker_changed():
    try:
        from common.redis_store import publish_ticker_changed
        publish_ticker_changed()
    except Exception as e:
        print(f"[accounts] ticker_changed 신호 발행 실패 (무시): {e}")


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def accounts_ui():
    market_map = get_market_map()
    market_options = "".join(
        f'<option value="{m}">{m} ({get_market_label(m)})</option>'
        for m in market_map
    )
    market_currency_map_js = json.dumps(
        {m: v.get("currency", "KRW") for m, v in market_map.items()}
    )

    return ui.div(
        ui.tags.script(accounts_js(market_currency_map_js)),

        # ── 계좌 목록 화면 ────────────────────────────────────────────────────
        ui.div(
            {"id": "ac-list-view"},
            ui.div(
                ui.div({"id": "ac-account-list", "class": "ticker-list"}),
                ui.tags.button(
                    "+ 계좌 추가",
                    class_="btn-add",
                    onclick="acShowModal('ac-modal-add-account');",
                ),
                class_="page-inner",
            ),
        ),

        # ── 모달 ──────────────────────────────────────────────────────────────
        modal_add_account_html(),
        modal_add_position_html(market_options),
        modal_add_cash_html(),
        modal_edit_position_html(market_options),
        modal_edit_cash_html(),

        class_="page-container",
    )


# ── Server ────────────────────────────────────────────────────────────────────

@module.server
def accounts_server(input, output, session, active_tab: reactive.value = None,
                    active_sub_tab: reactive.value = None):

    ns_str = session.ns("_")[:-1]

    _initialized = False
    open_account = reactive.value(None)
    refresh      = reactive.value(0)

    _last_accounts:  list = []
    _last_list_disp: dict = {}
    _last_positions: dict = {}
    _last_acc_disp:  dict = {}

    # ── DB 캐시 (price_signal 비의존, 구조만) ───────────────────────────────

    @reactive.calc
    def _db_accounts():
        refresh()
        return fetch_accounts_summary()

    @reactive.calc
    def _db_account_positions():
        refresh()
        acc_id = open_account()
        if acc_id is None:
            return None
        return fetch_account_details(acc_id)

    # ── 아코디언 하단 버튼 HTML ──────────────────────────────────────────────

    def _build_accordion_footer(acc_id: int) -> str:
        return (
            f'<div style="margin-top:12px; display:flex; gap:8px; flex-wrap:wrap;">'
            f'  <button class="btn-add" '
            f'    onclick="event.stopPropagation(); acShowModal(\'ac-modal-add-position\'); acUpdateAddPreview();">'
            f'    + 종목 추가</button>'
            f'  <button class="btn-add" '
            f'    onclick="event.stopPropagation(); acShowModal(\'ac-modal-add-cash\');">'
            f'    + 현금 추가</button>'
            f'  <button class="btn-account-delete-bottom" '
            f'    onclick="event.stopPropagation(); if(confirm(\'계좌를 삭제하시겠습니까?\')) '
            f'Shiny.setInputValue(window._acNs + \'-confirm_delete_account\', '
            f'Math.random(), {{priority: \'event\'}});">'
            f'    계좌 삭제</button>'
            f'</div>'
        )

    # ── 화면 갱신 ─────────────────────────────────────────────────────────────

    @reactive.effect
    async def _send_update():
        nonlocal _last_accounts, _last_list_disp, _last_positions, _last_acc_disp
        nonlocal _initialized
        price_signal.get()
        daily_insert_signal.get()
        acc_id = open_account()

        tab = active_sub_tab if active_sub_tab is not None else active_tab
        if _initialized and tab and tab.get() != "accounts":
            return

        usd_rate_val, usd_chg = get_usd_krw()

        from common.redis_store import get_all_prices
        prices   = get_all_prices()
        accounts = calc_accounts_summary(_db_accounts(), prices, usd_rate_val)
        normal   = [a for a in accounts if not a[5]]
        watch    = [a for a in accounts if a[5]]

        card_values = {str(a[0]): _build_account_card_values(a) for a in accounts}
        current_accounts = [a[0] for a in accounts]
        structure_changed = (current_accounts != _last_accounts)

        if structure_changed:
            _last_accounts = current_accounts
            _last_list_disp.clear()
            if normal:
                skeleton_html = "".join(
                    _build_account_card_skeleton(a, ns_str) for a in normal
                )
            else:
                skeleton_html = '<p style="color:#888; padding:16px 0;">등록된 계좌가 없습니다.</p>'
            if watch:
                skeleton_html += '<h4 class="section-heading">감시 계좌</h4>'
                skeleton_html += "".join(
                    _build_account_card_skeleton(a, ns_str) for a in watch
                )
            await session.send_custom_message("ac_list_init", {
                "account_list_html": skeleton_html,
                "cards":             card_values,
            })
        else:
            diff = diff_display(card_values, _last_list_disp)
            if diff:
                await session.send_custom_message("ac_list_tick", diff)

        _last_list_disp.update(card_values)

        # ── 아코디언 종목 갱신 (열려있을 때만) ─────────────────────────────
        if acc_id is not None:
            db_detail = _db_account_positions()
            if db_detail is None:
                return
            acc_row, db_rows = db_detail

            acc, positions, usd_rate = calc_account_details(acc_row, db_rows, prices, usd_rate_val)

            pos_values = {str(p[0]): _build_position_row_values(p, usd_rate) for p in positions}

            current_pos_ids = [p[0] for p in positions]
            pos_structure_changed = (_last_positions.get(acc_id) != current_pos_ids)

            if pos_structure_changed:
                _last_positions[acc_id] = current_pos_ids
                _last_acc_disp.clear()
                if positions:
                    skeleton_html = "".join(
                        _build_position_row_skeleton(p, ns_str) for p in positions
                    )
                else:
                    skeleton_html = '<p style="color:#888; padding:16px;">종목이 없습니다.</p>'
                skeleton_html += _build_accordion_footer(acc_id)

                await session.send_custom_message("ac_acc_init", {
                    "acc_id":             acc_id,
                    "position_list_html": skeleton_html,
                    "positions":          {k: {**v["static"], **v["dynamic"]} for k, v in pos_values.items()},
                })
            else:
                dyn_diff, sta_diff = diff_display_split(pos_values, _last_acc_disp)
                if dyn_diff:
                    await session.send_custom_message("ac_acc_tick", {
                        "positions": dyn_diff,
                    })
                if sta_diff:
                    await session.send_custom_message("ac_acc_static_tick", {
                        "positions": sta_diff,
                    })

            _last_acc_disp.update(pos_values)

        _initialized = True

    # ── 계좌 카드 클릭 (아코디언 토글) ──────────────────────────────────────

    @reactive.effect
    @reactive.event(input.card_clicked)
    def _handle_card_click():
        nonlocal _last_acc_disp, _last_positions
        acc_id = input.card_clicked()
        if not acc_id:
            _last_acc_disp.clear()
            _last_positions.clear()
            open_account.set(None)
        else:
            _last_acc_disp.clear()
            _last_positions.pop(acc_id, None)
            open_account.set(acc_id)

    # ── 티커 자동조회 ─────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.lookup_ticker)
    async def _lookup_ticker():
        payload = input.lookup_ticker()
        ticker  = str(payload.get("ticker", "")).strip().upper()
        source  = str(payload.get("source", "add"))
        if not ticker:
            return

        is_kr = bool(re.search(r'\d', ticker)) and '.' not in ticker

        def _fetch(t):
            try:
                info = yf.Ticker(t).info
                name = info.get("longName") or info.get("shortName") or ""
                return name, info.get("exchange", ""), info.get("quoteType", "")
            except Exception:
                return "", "", ""

        if is_kr:
            name, exchange, qtype = _fetch(ticker + ".KS")
            if not name:
                name, exchange, qtype = _fetch(ticker + ".KQ")
        else:
            name, exchange, qtype = _fetch(ticker)

        exchange_map = {
            "KSC": "KR", "KOE": "KR",
            "NMS": "NAS", "NGM": "NAS", "NCM": "NAS",
            "NYQ": "NYS",
            "PCX": "ARC",
            "ASE": "AMS",
            "NIM": "INDEX",
        }
        if qtype == "CRYPTOCURRENCY":
            market = "CRYPTO"
        elif qtype == "INDEX":
            market = "INDEX"
        else:
            market = exchange_map.get(exchange, "")

        channel = "ac_ticker_lookup_result" if source == "add" else "ac_ticker_lookup_result_edit"
        await session.send_custom_message(channel, {
            "ticker": ticker,
            "name":   name,
            "market": market,
        })

    # ── 계좌 추가 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_add)
    def _add_account():
        payload  = input.btn_confirm_add()
        if not payload:
            return
        name     = str(payload.get("name", "")).strip()
        alias    = str(payload.get("alias", "")).strip() or None
        is_watch = bool(payload.get("is_watch", False))
        if not name:
            return
        add_account(name, alias, is_watch)
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 계좌 삭제 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.confirm_delete_account)
    def _delete_account():
        acc_id = open_account()
        if acc_id is None:
            return
        delete_account(acc_id)
        open_account.set(None)
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 종목 추가 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_add_position)
    def _add_position():
        payload   = input.btn_confirm_add_position()
        if not payload:
            return
        ticker    = str(payload.get("ticker", "")).strip().upper()
        acc_id    = open_account()
        if not ticker or not acc_id:
            return
        avg_price = payload.get("avg_price")
        if avg_price is not None:
            avg_price = float(avg_price)
        add_position(
            account_id = acc_id,
            ticker     = ticker,
            name       = str(payload.get("name", "")).strip(),
            market     = str(payload.get("market", "")),
            leverage   = int(payload.get("leverage", 1)),
            qty        = float(payload.get("qty", 0)),
            avg_price  = avg_price,
        )
        refresh.set(refresh() + 1)
        _notify_position_changed()
        _notify_ticker_changed()

    # ── 현금 추가 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_add_cash)
    def _add_cash():
        payload = input.btn_confirm_add_cash()
        if not payload:
            return
        acc_id = open_account()
        if not acc_id:
            return
        add_cash(
            account_id = acc_id,
            cash_type  = str(payload.get("cash_type", "KRW")),
            amount     = float(payload.get("amount", 0)),
        )
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 종목 수정 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_edit_position)
    def _edit_position():
        payload   = input.btn_confirm_edit_position()
        pos_id    = payload.get("pos_id")
        if not pos_id:
            return
        avg_price = payload.get("avg_price")
        if avg_price is not None:
            avg_price = float(avg_price)
        edit_position(
            pos_id    = pos_id,
            name      = str(payload.get("name", "")).strip(),
            market    = str(payload.get("market", "")),
            leverage  = int(payload.get("leverage", 1)),
            qty       = float(payload.get("qty", 0)),
            avg_price = avg_price,
        )
        refresh.set(refresh() + 1)
        _notify_position_changed()
        _notify_ticker_changed()

    # ── 매수 ──────────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_buy)
    def _buy():
        payload = input.btn_confirm_buy()
        pos_id  = payload.get("pos_id")
        qty     = float(payload.get("qty", 0))
        price   = float(payload.get("price", 0))
        if not pos_id or qty <= 0 or price <= 0:
            return
        usd_markets = {m for m, v in get_market_map().items() if v.get("currency") == "USD"}
        try:
            execute_buy(pos_id, qty, price, usd_markets)
        except Exception as e:
            print(f"[accounts] 매수 처리 오류: {e}")
            return
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 매도 ──────────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_sell)
    def _sell():
        payload = input.btn_confirm_sell()
        pos_id  = payload.get("pos_id")
        qty     = float(payload.get("qty", 0))
        price   = float(payload.get("price", 0))
        if not pos_id or qty <= 0 or price <= 0:
            return
        usd_markets = {m for m, v in get_market_map().items() if v.get("currency") == "USD"}
        try:
            execute_sell(pos_id, qty, price, usd_markets)
        except ValueError as e:
            print(f"[accounts] 매도 처리 오류: {e}")
            return
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 종목 삭제 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.confirm_delete_position)
    def _delete_position():
        payload = input.confirm_delete_position()
        pos_id  = payload.get("pos_id") if isinstance(payload, dict) else None
        if not pos_id:
            return
        delete_position(pos_id)
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 현금 수정 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.btn_confirm_edit_cash)
    def _edit_cash():
        payload = input.btn_confirm_edit_cash()
        pos_id  = payload.get("pos_id")
        if not pos_id:
            return
        edit_cash(
            pos_id    = pos_id,
            cash_type = str(payload.get("cash_type", "KRW")),
            amount    = float(payload.get("amount", 0)),
        )
        refresh.set(refresh() + 1)
        _notify_position_changed()

    # ── 현금 삭제 ─────────────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.confirm_delete_cash)
    def _delete_cash():
        payload = input.confirm_delete_cash()
        pos_id  = payload.get("pos_id") if isinstance(payload, dict) else None
        if not pos_id:
            return
        delete_cash(pos_id)
        refresh.set(refresh() + 1)
        _notify_position_changed()