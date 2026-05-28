from shiny import ui, render, module

@module.ui
def settings_ui():
    return ui.page_fluid(
        ui.h2("설정")
    )

@module.server
def settings_server(input, output, session):
    pass
