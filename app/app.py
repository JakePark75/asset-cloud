from shiny import App, ui, render
from pathlib import Path

app_ui = ui.page_fluid(
    ui.h2("Asset Management"),
)

def server(input, output, session):
    pass

app = App(app_ui, server)