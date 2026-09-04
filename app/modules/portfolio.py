from shiny import ui, reactive, module
import subprocess
import sys
from app.db import CASH_KRW, CASH_USD, get_db, get_usd_krw, get_config, get_market_currency
from app.price_signal import price_signal, position_signal, ticker_signal
from app.modules.components import (
    build_ticker_row_skeleton, build_ticker_row_values,
    build_account_row_skeleton, build_account_row_values,
)
from app.modules.portfolio_js import portfolio_js
from scheduler.price_updater_common import get_market_status
from app.utils.display_diff import diff_display, diff_display_split


# ── DAL ───────────────────────────────────────────────────────────────────────

def load_portfolio(db_rows):
    from common.redis_store import get_all_prices

    prices = get_all_prices()
    rows   = []
    for ticker, qty, name, market, leverage, avg_price, sort_order in db_rows:
        p_data     = prices.get(ticker)
        price      = float(p_data["price"])      if p_data else 0.0
        change_pct = float(p_data["change_pct"]) if p_data else 0.0
        rows.append((ticker, qty, name, price, change_pct, market, leverage, avg_price, sort_order))

    return rows


def load_watch_only(db_rows):
    """
    감시계좌에만 존재하고 비감시계좌 보유가 없는 ticker(감시종목).
    가격은 일반 종목과 동일하게 Redis에서 주입. qty/avg_price는 보유가 없으므로 항상 0/None.
    """
    from common.redis_store import get_all_prices

    prices = get_all_prices()
    rows   = []
    for ticker, name, market, leverage in db_rows:
        p_data     = prices.get(ticker)
        price      = float(p_data["price"])      if p_data else 0.0
        change_pct = float(p_data["change_pct"]) if p_data else 0.0
        rows.append((ticker, 0, name, price, change_pct, market, leverage, None))

    return rows


def load_ticker_accounts(ticker: str, db_rows, usd_rate: float):
    """
    특정 ticker 보유 계좌 목록 (아코디언 내용).
    가격은 Redis에서 읽어 주입 (position_signal 구독 캐시 기반, price_signal 무관).
    반환: (acc_rows, price, chg_pct)
      acc_rows: [(acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, price, chg_pct, ticker_name), ...]
    """
    from common.redis_store import get_all_prices
    prices  = get_all_prices()
    p_data  = prices.get(ticker)
    price   = float(p_data["price"])      if p_data else 0.0
    chg_pct = float(p_data["change_pct"]) if p_data else 0.0

    result = [
        (acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, price, chg_pct, ticker_name)
        for acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, ticker_name in db_rows
    ]
    return result


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _ticker_to_id(ticker: str) -> str:
    return ticker.replace("-", "_").replace("^", "_")


def _calc_amount(ticker, qty_f, price_f, market, usd_rate):
    if ticker == CASH_KRW:                     return qty_f
    elif ticker == CASH_USD:                    return qty_f * usd_rate
    elif get_market_currency(market) == "USD": return qty_f * price_f * usd_rate
    else:                                      return qty_f * price_f


_UNSET_SORT_ORDER = 10**9  # sort_order 미지정 종목을 뒤로 보내기 위한 큰 값 (드래그로 지정된 종목이 항상 우선)


def _sort_rows(rows, usd_rate):
    """
    정렬 우선순위: (1) 현금(KRW/USD)은 항상 맨 뒤 (2) sort_order가 지정된 종목은 그 값 순
    (3) sort_order 미지정 종목은 평가금액 내림차순 (기존 동작과 동일하게 폴백)
    rows[i]: (ticker, qty, name, price, chg_pct, market, leverage, avg_price, sort_order)
    """
    return sorted(
        rows,
        key=lambda r: (
            1 if r[0] in (CASH_KRW, CASH_USD) else 0,
            r[8] if r[8] is not None else _UNSET_SORT_ORDER,
            -_calc_amount(r[0], float(r[1] or 0), float(r[3] or 0), r[5], usd_rate)
        )
    )


def _pinned_ticker_signature(rows_sorted):
    """
    sort_order가 명시적으로 지정된(드래그로 순서를 정한) 종목만, 그 순서 그대로 추출.
    sort_order 미지정 종목(amount 기준 tie-break으로 정렬)의 순위 흔들림은
    여기 포함되지 않는다 — structure_changed 판정에서 가격발 재정렬과
    드래그발 순서변경을 구분하기 위한 용도.
    rows[i]: (ticker, qty, name, price, chg_pct, market, leverage, avg_price, sort_order)
    """
    return tuple(r[0] for r in rows_sorted if r[8] is not None)


def _sort_watch_rows(rows):
    """감시종목 정렬 — 보유 자산이 없어 금액 기준 정렬이 의미 없으므로 이름/ticker 기준 고정 정렬.
    (정렬 기준이 매 갱신마다 흔들리면 structure_changed가 불필요하게 자주 True가 됨)"""
    return sorted(rows, key=lambda r: (r[2] or r[0]))


def _build_pf_row_skeleton(ticker, qty, name, market, leverage, avg_price=None):
    """포트폴리오 종목 행 골격 + 아코디언 컨테이너(빈 채로, 기본 display:none)"""
    tid      = _ticker_to_id(ticker)
    qty_f    = float(qty or 0)
    leverage = int(leverage) if leverage else 1
    is_cash  = ticker in (CASH_KRW, CASH_USD)

    if ticker == CASH_KRW:
        display_name = "현금(KRW)"
        qty_fixed    = ""
    elif ticker == CASH_USD:
        display_name = "현금(USD)"
        qty_fixed    = None  # 1행 구조 — 달러 잔액은 amount_str에 통합 표시
    else:
        display_name = name or ticker
        qty_fixed    = None  # span으로 비워둠 (tick에서 채움)

    onclick_attr = (
        "" if is_cash
        else f"pfToggleTicker('{ticker}', '{tid}');"
    )

    row_html = build_ticker_row_skeleton(
        ticker       = ticker,
        display_name = display_name,
        market       = market,
        leverage     = leverage,
        id_prefix    = "pf",
        row_id       = tid,
        qty_fixed    = qty_fixed,
        onclick_attr = onclick_attr,
        data_attrs   = "",
    )

    if is_cash:
        return row_html

    accordion_html = f'<div class="subtab-accordion" id="pf-acc-{tid}" style="display:none;"></div>'
    return f'<div class="pf-item" data-ticker="{ticker}">{row_html}{accordion_html}</div>'


def _build_pf_tick_values(ticker, qty, name, price, chg_pct, market, leverage, usd_rate, avg_price=None, is_watch_only=False):
    """포트폴리오 종목 tick 값"""
    tid     = _ticker_to_id(ticker)
    qty_f   = float(qty   or 0)
    price_f = float(price or 0)

    amount = _calc_amount(ticker, qty_f, price_f, market, usd_rate)

    if ticker == CASH_KRW:
        display_name = "현금(KRW)"
    elif ticker == CASH_USD:
        display_name = "현금(USD)"
    else:
        display_name = name or ticker

    return build_ticker_row_values(
        ticker                 = ticker,
        amount                 = amount,
        qty                    = qty,
        price                  = price,
        chg_pct                = chg_pct,
        market                 = market,
        avg_price              = avg_price,
        id_prefix              = "pf",
        row_id                 = tid,
        get_market_currency_fn = get_market_currency,
        get_market_status_fn   = get_market_status,
        name                   = display_name,
        leverage               = leverage,
        usd_rate               = usd_rate,
        qty_in_values          = True,
        is_watch_only          = is_watch_only,
    )


def _build_drilldown_row_skeleton(acc_id, acc_name, alias, qty):
    """아코디언 내부 계좌 행 골격 — 계좌명·수량만."""
    qty_f         = float(qty or 0)
    display_name  = acc_name + (f" ({alias})" if alias else "")
    qty_text      = f"≈{qty_f:.2f}주" if qty_f != int(qty_f) else f"{qty_f:g}주"

    return build_account_row_skeleton(
        display_name = display_name,
        qty_text     = qty_text,
        row_id       = str(acc_id),
        id_prefix    = "pfd",
    )


def _build_drilldown_row_values(acc_id, ticker, qty, avg_price, price, market, usd_rate):
    """아코디언 내부 계좌 행 tick 값 — 평가금액 + 이 계좌 포지션의 손익액/수익률"""
    qty_f   = float(qty       or 0)
    avg_f   = float(avg_price or 0)
    price_f = float(price     or 0)

    amount     = _calc_amount(ticker, qty_f, price_f, market, usd_rate)
    cost_basis = _calc_amount(ticker, qty_f, avg_f,   market, usd_rate)
    pnl_amount = amount - cost_basis
    pnl_pct    = ((price_f - avg_f) / avg_f * 100) if avg_f else 0.0

    return build_account_row_values(
        avg_price  = avg_f,
        amount     = amount,
        pnl_amount = pnl_amount,
        pnl_pct    = pnl_pct,
        currency   = get_market_currency(market),
        row_id     = str(acc_id),
    )


def _build_accordion_html(acc_rows):
    """아코디언 내부 계좌 목록 HTML (헤더 없음 — 종목 행 자체에 가격/손익 이미 표시됨)"""
    normal = [r for r in acc_rows if not r[3]]
    watch  = [r for r in acc_rows if r[3]]

    def _section(rows_subset):
        return "".join(
            _build_drilldown_row_skeleton(acc_id, acc_name, alias, qty)
            for acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, p, c, _
            in rows_subset
        )

    html = _section(normal)
    if watch:
        html += '<h4 class="section-heading">감시 계좌</h4>'
        html += _section(watch)
    return html


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def portfolio_ui():
    return ui.div(
        ui.tags.script(portfolio_js()),

        ui.div(
            {"class": "page-inner", "style": "position:relative;"},

            # ── 강제 조회 버튼 ────────────────────────────────────────────────
            ui.div(
                {"id": "pf-force-btn-wrap", "style": "display:none;"},
                ui.input_action_button("force_update", "↺", class_="force-update-btn"),
            ),

            # ── 포트폴리오 목록 (아코디언 포함) ───────────────────────────────
            ui.div(
                {"id": "pf-ticker-list", "class": "ticker-list"},
            ),
        ),

        class_="page-container",
    )


# ── Server ────────────────────────────────────────────────────────────────────

@module.server
def portfolio_server(input, output, session, active_tab: reactive.value = None,
                     active_sub_tab: reactive.value = None):

    ns_str = session.ns("_")[:-1]

    _initialized = False  # 일반 변수: effect 자기-재트리거 방지
    open_ticker  = reactive.value(None)  # None: 아코디언 닫힘, str: 해당 ticker 아코디언 열림 (한번에 하나만)

    _last_tickers:      list      = []
    _last_pinned_order: tuple     = ()
    _last_display:      dict      = {}
    _last_open_ticker:  str | None = None
    _last_dd_accounts:  list      = []
    _last_dd_display:   dict      = {}

    # ── DB 캐시 ──────────────────────────────────────────────────────────────

    @reactive.calc
    def _db_portfolio_rows():
        """positions + tickers JOIN — position_signal / ticker_signal 시에만 재조회"""
        position_signal.get()
        ticker_signal.get()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.ticker, SUM(p.quantity) AS quantity,
                       t.name, t.market, t.leverage,
                       SUM(p.quantity * p.avg_price) / NULLIF(SUM(p.quantity), 0) AS avg_price,
                       t.sort_order
                FROM positions p
                LEFT JOIN tickers t ON p.ticker = t.ticker
                LEFT JOIN accounts a ON p.account_id = a.id
                WHERE a.is_watch = false
                GROUP BY p.ticker, t.name, t.market, t.leverage, t.sort_order
            """)
            rows = cur.fetchall()
            cur.close()
        return rows

    @reactive.calc
    def _db_watch_only_tickers():
        """
        감시계좌(is_watch=true)에는 존재하지만 비감시계좌(is_watch=false) 보유가 전혀 없는 ticker.
        position_signal / ticker_signal 시에만 재조회. 보유 0이므로 avg_price 없음.
        """
        position_signal.get()
        ticker_signal.get()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT p.ticker, t.name, t.market, t.leverage
                FROM positions p
                JOIN accounts a ON p.account_id = a.id
                LEFT JOIN tickers t ON p.ticker = t.ticker
                WHERE a.is_watch = true
                  AND p.ticker NOT IN (
                      SELECT p2.ticker
                      FROM positions p2
                      JOIN accounts a2 ON p2.account_id = a2.id
                      WHERE a2.is_watch = false
                  )
            """)
            rows = cur.fetchall()
            cur.close()
        return rows

    @reactive.calc
    def _db_ticker_accounts():
        """열려있는 아코디언의 ticker 보유 계좌 목록 — position_signal / ticker_signal 시에만 재조회.
        open_ticker가 None이면 빈 리스트 (불필요한 쿼리 방지)."""
        position_signal.get()
        ticker_signal.get()
        ticker = open_ticker.get()
        if not ticker:
            return []
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT a.id, a.name, a.alias, a.is_watch,
                       p.quantity, p.avg_price,
                       t.market, t.leverage, t.name AS ticker_name
                FROM positions p
                JOIN accounts a ON p.account_id = a.id
                LEFT JOIN tickers t ON p.ticker = t.ticker
                WHERE p.ticker = %s
                ORDER BY a.is_watch ASC, a.name ASC
            """, (ticker,))
            rows = cur.fetchall()
            cur.close()
        return rows

    # ── 강제 시세 조회 모달 ───────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.force_update)
    def _show_force_modal():
        m = ui.modal(
            ui.div(
                ui.p("전체 종목 시세를 강제 조회합니다. 장외시간 종목도 포함됩니다."),
                ui.div(
                    ui.input_action_button("force_confirm", "확인", class_="btn-primary"),
                    ui.input_action_button("force_cancel", "취소", class_="btn-secondary"),
                    class_="modal-btn-row-half",
                    style="display:flex; gap:8px; margin-top:12px;",
                ),
                class_="modal-body-inner",
            ),
            title="강제 시세 조회",
            easy_close=True,
            footer=None,
        )
        ui.modal_show(m)

    @reactive.effect
    @reactive.event(input.force_confirm)
    def _do_force_update():
        ui.modal_remove()
        subprocess.Popen(
            [sys.executable, "scheduler/price_updater.py", "--force"],
            cwd="/home/ubuntu/asset-cloud"
        )

    @reactive.effect
    @reactive.event(input.force_cancel)
    def _cancel_force_update():
        ui.modal_remove()

    # ── 종목 클릭 → 아코디언 토글 ───────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.ticker_clicked)
    def _handle_ticker_click():
        nonlocal _last_dd_accounts, _last_dd_display
        payload = input.ticker_clicked()
        ticker  = payload.get("ticker") if payload else None
        if not ticker:
            # 닫기 — 상태 초기화
            _last_dd_accounts.clear()
            _last_dd_display.clear()
        open_ticker.set(ticker)

    # ── 종목 순서 변경 (드래그 정렬) ─────────────────────────────────────────────
    # sort_order 변경은 ticker/quantity/leverage/market 중 어느 것도 바꾸지 않으므로,
    # refresh_position_cache()(positions×tickers×is_watch 캐시)는 갱신 대상에
    # sort_order가 없어 호출할 필요가 없다(redis_store.py 확인 완료).
    # publish_ticker_changed()만 발행하면 price_signal.py의 리스너가 받아
    # ticker_signal을 갱신하고, 그 결과 이 세션을 포함한 모든 세션의
    # _db_portfolio_rows()가 재조회되어 새 sort_order가 반영된다.

    @reactive.effect
    @reactive.event(input.ticker_reorder)
    def _reorder_tickers():
        payload = input.ticker_reorder()
        if not payload:
            return
        ordered_tickers = [str(t) for t in payload]
        from app.modules.portfolio_DAL import update_ticker_order
        from common.redis_store import publish_ticker_changed
        update_ticker_order(ordered_tickers)
        publish_ticker_changed()

    # 세션9: 오토스크롤 진단 로깅용 이펙트(_log_autoscroll_debug) 제거함.
    # 클라이언트 쪽 로깅 인프라(portfolio_ui의 _pfDebugLog 등)도 함께 제거되어
    # input.autoscroll_debug_log 자체가 더 이상 발생하지 않음. 필요 시 재추가.

    # ── 화면 갱신 ─────────────────────────────────────────────────────────────

    @reactive.effect
    async def _send_update():
        nonlocal _last_tickers, _last_pinned_order, _last_display
        nonlocal _last_open_ticker, _last_dd_accounts, _last_dd_display
        nonlocal _initialized

        price_signal.get()
        position_signal.get()
        ticker_signal.get()
        cur_open_ticker = open_ticker.get()  # 탭 가드 전에 의존성 등록

        tab = active_sub_tab if active_sub_tab is not None else active_tab
        if _initialized and tab and tab.get() != "portfolio":
            return

        usd_rate, usd_chg = get_usd_krw()
        usd_rate = usd_rate or 0

        # ── 포트폴리오 목록 (항상 갱신) ────────────────────────────────────
        rows = load_portfolio(_db_portfolio_rows())
        watch_rows = load_watch_only(_db_watch_only_tickers())

        rows_sorted  = _sort_rows(rows, usd_rate)
        watch_sorted = _sort_watch_rows(watch_rows)

        ticker_values = {
            t: _build_pf_tick_values(t, qty, name, price, chg_pct, market, leverage, usd_rate, avg_price)
            for t, qty, name, price, chg_pct, market, leverage, avg_price, _so in rows_sorted
        }
        ticker_values.update({
            t: _build_pf_tick_values(t, qty, name, price, chg_pct, market, leverage, usd_rate, avg_price, is_watch_only=True)
            for t, qty, name, price, chg_pct, market, leverage, avg_price in watch_sorted
        })

        current_tickers = [r[0] for r in rows_sorted] + [r[0] for r in watch_sorted]
        # structure_changed는 두 가지를 OR로 판단한다.
        # (1) 멤버십(set) 비교 — 종목 구성(추가/삭제) 감지.
        #     rows_sorted의 amount 기준 tie-break은 시세가 바뀔 때마다 재계산되므로,
        #     리스트(순서 포함) 비교를 쓰면 종목 구성은 그대로인데 순위만 뒤바뀌는
        #     경우에도 structure_changed=True가 되어, 그 시점에 열려있던 드릴다운
        #     아코디언 DOM이 빈 상태로 리셋되는 문제가 있었다(리스트→set 비교로 수정 완료).
        # (2) pinned order(sort_order 지정 종목만의 순서) 비교 — 드래그로 순서를
        #     지정/변경한 경우 감지. sort_order 미지정 종목의 amount발 순위 흔들림은
        #     _pinned_ticker_signature에 애초에 포함되지 않으므로 (1)의 취지를 해치지 않는다.
        current_pinned_order = _pinned_ticker_signature(rows_sorted)
        structure_changed = (
            set(current_tickers) != set(_last_tickers)
            or current_pinned_order != _last_pinned_order
        )

        if structure_changed:
            _last_tickers      = current_tickers
            _last_pinned_order = current_pinned_order
            _last_display.clear()
            cfg        = get_config()
            show_force = int(cfg.get("interval", 1)) != 0

            # 정상 섹션 중 현금(KRW/USD)은 드래그 대상이 아니므로 별도 렌더링하여
            # SortableJS 컨테이너(#pf-ticker-list-normal) 밖, 감시종목 위에 고정 배치한다.
            cash_rows   = [r for r in rows_sorted if r[0] in (CASH_KRW, CASH_USD)]
            normal_rows = [r for r in rows_sorted if r[0] not in (CASH_KRW, CASH_USD)]

            normal_html = "".join(
                _build_pf_row_skeleton(t, qty, name, market, leverage, avg_price)
                for t, qty, name, price, chg_pct, market, leverage, avg_price, _so in normal_rows
            )
            cash_html = "".join(
                _build_pf_row_skeleton(t, qty, name, market, leverage, avg_price)
                for t, qty, name, price, chg_pct, market, leverage, avg_price, _so in cash_rows
            )
            ticker_list_html = f'<div id="pf-ticker-list-normal">{normal_html}</div>{cash_html}'

            if watch_sorted:
                ticker_list_html += '<h4 class="section-heading">감시종목</h4>'
                ticker_list_html += "".join(
                    _build_pf_row_skeleton(t, qty, name, market, leverage, avg_price)
                    for t, qty, name, price, chg_pct, market, leverage, avg_price in watch_sorted
                )
            # pf_init: static(이름/레버리지/수량/평단/상태) + dynamic(가격/평가금액/손익) 모두 전송
            await session.send_custom_message("pf_init", {
                "ticker_list_html": ticker_list_html,
                "show_force_btn":   show_force,
                "tickers": {t: {**v["static"], **v["dynamic"]} for t, v in ticker_values.items()},
            })
            _last_display.update(ticker_values)
        else:
            # pf_tick: dynamic 필드 단위 diff / pf_static_tick: static 필드 단위 diff
            dyn_diff, sta_diff = diff_display_split(ticker_values, _last_display)
            if dyn_diff:
                await session.send_custom_message("pf_tick", dyn_diff)
            if sta_diff:
                await session.send_custom_message("pf_static_tick", sta_diff)

        # ── 아코디언 (열려있는 종목이 있을 때만 추가 계산) ───────────────────
        if cur_open_ticker:
            tid = _ticker_to_id(cur_open_ticker)
            db_rows = _db_ticker_accounts()
            acc_rows = load_ticker_accounts(cur_open_ticker, db_rows, usd_rate)

            current_accounts = [r[0] for r in acc_rows]
            ticker_switched   = (cur_open_ticker != _last_open_ticker)
            acc_structure_changed = ticker_switched or (current_accounts != _last_dd_accounts)

            row_values = {
                str(acc_id): _build_drilldown_row_values(
                    acc_id, cur_open_ticker, qty, avg_price, p, market, usd_rate
                )
                for acc_id, _, _, _, qty, avg_price, market, leverage, p, c, _ in acc_rows
            }

            if acc_structure_changed:
                _last_dd_accounts = current_accounts
                _last_dd_display.clear()
                await session.send_custom_message("pf_acc_init", {
                    "tid":               tid,
                    "account_list_html": _build_accordion_html(acc_rows),
                    "rows":              row_values,
                })
            else:
                diff = diff_display(row_values, _last_dd_display)
                if diff:
                    await session.send_custom_message("pf_acc_tick", {"rows": diff})

            _last_open_ticker = cur_open_ticker
        else:
            _last_open_ticker = None

        _initialized = True