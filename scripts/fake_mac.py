"""A pretend BlueBubbles Server (REST + Socket.IO) with fictional data, for trying Mac mode.

Usage: .venv\\Scripts\\python.exe scripts\\fake_mac.py [port]      (password: pw)
Push live events:  POST http://127.0.0.1:<port>/mock/push  {"event": "...", "data": {...}}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import socketio
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.fake_bluebubbles import PASSWORD, FakeBlueBubbles

fake = FakeBlueBubbles()
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


@sio.event
async def connect(sid: str, environ: dict[str, object], auth: object = None) -> bool:
    query = parse_qs(str(environ.get("QUERY_STRING", "")))
    return query.get("password", [""])[0] == PASSWORD


async def api(request: Request) -> Response:
    body = await request.body()
    proxied = httpx.Request(
        request.method,
        str(request.url),
        headers={"content-type": request.headers.get("content-type", "")},
        content=body,
    )
    answer = fake.handle(proxied)
    return Response(answer.content, status_code=answer.status_code, headers=dict(answer.headers))


async def push(request: Request) -> Response:
    payload = json.loads(await request.body())
    await sio.emit(payload["event"], payload.get("data"))
    return JSONResponse({"ok": True})


app = Starlette(
    routes=[
        Route("/api/v1/{path:path}", api, methods=["GET", "POST", "PUT", "DELETE"]),
        Route("/mock/push", push, methods=["POST"]),
    ]
)
asgi = socketio.ASGIApp(sio, other_asgi_app=app)

if __name__ == "__main__":
    uvicorn.run(asgi, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 18234, log_level="warning")
