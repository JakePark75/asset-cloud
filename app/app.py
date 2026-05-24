from shiny import App, ui, render
from pathlib import Path
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, Mount

BASE_DIR = Path(__file__).parent.parent

async def serve_md(request):
    filename = request.path_params["filename"]
    filepath = BASE_DIR / "AIContext" / filename
    if not filepath.exists():
        return PlainTextResponse("Not found", status_code=404)
    content = filepath.read_text(encoding="utf-8")
    return PlainTextResponse(content, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache"
    })

shiny_app_ui = ui.page_fluid(
    ui.h2("Asset Management"),
)

def server(input, output, session):
    pass

shiny_app = App(shiny_app_ui, server)

routes = [
    Route("/api/context/{filename}", serve_md),
    Mount("/", app=shiny_app),
]

app = Starlette(routes=routes)