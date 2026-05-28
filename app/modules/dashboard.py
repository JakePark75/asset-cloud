from shiny import ui, render, module

@module.ui
def dashboard_ui():
    return ui.page_fluid(
        ui.h2("대시보드")
    )

@module.server
def dashboard_server(input, output, session):
    pass
