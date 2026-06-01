from shiny import ui

def modal_add_account_ui(ns):
    return ui.div(
        ui.div(
            ui.div(
                ui.h4("계좌 추가", class_="modal-title"),
                ui.span("✕", class_="modal-close-icon",
                        onclick=f"Shiny.setInputValue('{ns('modal_close')}', Math.random(), {{priority: 'event'}});"),
                class_="modal-header-row",
            ),
            ui.input_text(ns("new_account_name"), "계좌명", placeholder="예) 키움증권"),
            ui.input_text(ns("new_account_alias"), "별명 (선택)", placeholder="예) 키움"),
            ui.input_action_button(ns("btn_confirm_add"), "추가", class_="btn-add"),
            class_="modal-box",
            onclick="event.stopPropagation();",
        ),
        class_="modal-overlay",
        onclick=f"Shiny.setInputValue('{ns('modal_close')}', Math.random(), {{priority: 'event'}});",
    )

def modal_add_position_ui(ns):
    return ui.div(
        ui.div(
            ui.div(
                ui.h4("종목 추가", class_="modal-title"),
                ui.span("✕", class_="modal-close-icon",
                        onclick=f"Shiny.setInputValue('{ns('modal_position_close')}', Math.random(), {{priority: 'event'}});"),
                class_="modal-header-row",
            ),
            ui.input_text(ns("new_position_name"), "종목명", placeholder="예) 애플"),
            ui.input_text(ns("new_position_ticker"), "티커", placeholder="예) AAPL"),
            ui.input_select(ns("new_position_market"), "시장", {
                "KR": "KR (한국)", "NAS": "NAS (나스닥)", "AMS": "AMS (아멕스)",
                "ARC": "ARC (NYSE Arca)", "FX": "FX (환율)", "INDEX": "INDEX (지수)",
                "CRYPTO": "CRYPTO (암호화폐)"
            }),
            ui.input_select(ns("new_position_leverage"), "레버리지", {"1": "x1", "2": "x2", "3": "x3"}),
            ui.input_numeric(ns("new_position_qty"), "수량", value=None, min=0),
            ui.input_action_button(ns("btn_confirm_add_position"), "추가", class_="btn-add"),
            class_="modal-box",
            onclick="event.stopPropagation();",
        ),
        class_="modal-overlay",
        onclick=f"Shiny.setInputValue('{ns('modal_position_close')}', Math.random(), {{priority: 'event'}});",
    )

def modal_add_cash_ui(ns):
    return ui.div(
        ui.div(
            ui.div(
                ui.h4("현금 추가", class_="modal-title"),
                ui.span("✕", class_="modal-close-icon",
                        onclick=f"Shiny.setInputValue('{ns('modal_cash_close')}', Math.random(), {{priority: 'event'}});"),
                class_="modal-header-row",
            ),
            ui.input_select(ns("new_cash_type"), "통화", {"KRW": "KRW (원화)", "USD": "USD (달러)"}),
            ui.input_numeric(ns("new_cash_amount"), "금액", value=None, min=0),
            ui.input_action_button(ns("btn_confirm_add_cash"), "추가", class_="btn-add"),
            class_="modal-box",
            onclick="event.stopPropagation();",
        ),
        class_="modal-overlay",
        onclick=f"Shiny.setInputValue('{ns('modal_cash_close')}', Math.random(), {{priority: 'event'}});",
    )

def modal_edit_position_ui(ns, ticker, name, market, leverage, qty):
    return ui.div(
        ui.div(
            ui.div(
                ui.h4("종목 수정", class_="modal-title"),
                ui.span("✕", class_="modal-close-icon",
                        onclick=f"Shiny.setInputValue('{ns('modal_edit_position_close')}', Math.random(), {{priority: 'event'}});"),
                class_="modal-header-row",
            ),
            ui.p(ticker, class_="ticker-readonly"),
            ui.input_text(ns("edit_position_name"), "종목명", value=name or ""),
            ui.input_select(ns("edit_position_market"), "시장", {
                "KR": "KR (한국)", "NAS": "NAS (나스닥)", "AMS": "AMS (아멕스)",
                "ARC": "ARC (NYSE Arca)", "FX": "FX (환율)", "INDEX": "INDEX (지수)",
                "CRYPTO": "CRYPTO (암호화폐)"
            }, selected=market or "KR"),
            ui.input_select(ns("edit_position_leverage"), "레버리지", {"1": "x1", "2": "x2", "3": "x3"}, selected=str(leverage or 1)),
            ui.input_numeric(ns("edit_position_qty"), "수량", value=float(qty or 0), min=0),
            ui.input_action_button(ns("btn_confirm_edit_position"), "저장", class_="btn-add"),
            ui.input_action_button(ns("btn_delete_position"), "종목 삭제", class_="btn-modal-delete-bottom",
                onclick=f"event.stopPropagation(); if(confirm('종목을 삭제하시겠습니까?')) Shiny.setInputValue('{ns('confirm_delete_position')}', Math.random(), {{priority: 'event'}});"),
            class_="modal-box",
            onclick="event.stopPropagation();",
        ),
        class_="modal-overlay",
        onclick=f"Shiny.setInputValue('{ns('modal_edit_position_close')}', Math.random(), {{priority: 'event'}});",
    )

def modal_edit_cash_ui(ns, ticker, amount):
    return ui.div(
        ui.div(
            ui.div(
                ui.h4("현금 수정", class_="modal-title"),
                ui.span("✕", class_="modal-close-icon",
                        onclick=f"Shiny.setInputValue('{ns('modal_edit_cash_close')}', Math.random(), {{priority: 'event'}});"),
                class_="modal-header-row",
            ),
            ui.input_select(ns("edit_cash_type"), "통화", {"KRW": "KRW (원화)", "USD": "USD (달러)"}, selected=ticker),
            ui.input_numeric(ns("edit_cash_amount"), "금액", value=float(amount or 0), min=0),
            ui.input_action_button(ns("btn_confirm_edit_cash"), "저장", class_="btn-add"),
            ui.input_action_button(ns("btn_delete_cash"), "현금 삭제", class_="btn-modal-delete-bottom",
                onclick=f"event.stopPropagation(); if(confirm('현금을 삭제하시겠습니까?')) Shiny.setInputValue('{ns('confirm_delete_cash')}', Math.random(), {{priority: 'event'}});"),
            class_="modal-box",
            onclick="event.stopPropagation();",
        ),
        class_="modal-overlay",
        onclick=f"Shiny.setInputValue('{ns('modal_edit_cash_close')}', Math.random(), {{priority: 'event'}});",
    )