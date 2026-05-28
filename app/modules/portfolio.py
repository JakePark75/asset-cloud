from shiny import ui, render, module

@module.ui
def portfolio_ui():
    return ui.page_fluid(
        ui.h2("포트폴리오")
    )

@module.server
def portfolio_server(input, output, session):
    pass
