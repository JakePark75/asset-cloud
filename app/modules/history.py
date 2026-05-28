from shiny import ui, render, module

@module.ui
def history_ui():
    return ui.page_fluid(
        ui.h2("실적 히스토리")
    )

@module.server
def history_server(input, output, session):
    pass
