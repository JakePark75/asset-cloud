from pathlib import Path
from starlette.responses import PlainTextResponse
from starlette.routing import Route

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

routes = [
    Route("/api/context/{filename}", serve_md),
]
