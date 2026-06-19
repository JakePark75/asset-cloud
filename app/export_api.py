"""
app/export_api.py
GET /api/export → xlsx 다운로드 (Starlette 직접 응답, Shiny 렌더링 우회)
"""
import datetime
from zoneinfo import ZoneInfo

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from app.auth import verify_token

KST = ZoneInfo("Asia/Seoul")


async def export_handler(request: Request):
    token = request.cookies.get("auth_token", "")
    if not verify_token(token):
        return Response("Unauthorized", status_code=401)

    from app.modules.export_DAL import build_export_xlsx
    xlsx_bytes = build_export_xlsx()

    filename = "assets_" + datetime.datetime.now(KST).strftime("%Y%m%d_%H%M") + ".xlsx"

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


routes = [
    Route("/api/export", endpoint=export_handler, methods=["GET"]),
]