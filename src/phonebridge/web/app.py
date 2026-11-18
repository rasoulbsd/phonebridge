"""HTTP + WebSocket API for the UI (docs/ui-api.md)."""

from __future__ import annotations

import asyncio
import contextlib
import html
import ipaddress
import mimetypes
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from urllib.parse import urlsplit

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.background import BackgroundTask

from phonebridge.branding import DISPLAY_NAME
from phonebridge.domain.ui import (
    AppStatus,
    Call,
    Chat,
    ChatMessage,
    ChatSearchHit,
    ContactCard,
    ReactionType,
    StatusUpdate,
    ThreadPage,
)
from phonebridge.services.messaging import NotFoundError, RequestError, UnsupportedError

if TYPE_CHECKING:
    import os
    from collections.abc import AsyncIterator, Awaitable, Callable, Collection

    from starlette.requests import HTTPConnection
    from starlette.staticfiles import PathLike
    from starlette.types import Scope

    from phonebridge.runtime import DesktopHooks
    from phonebridge.services.manager import ServiceManager
    from phonebridge.web.hub import WebSocketHub

STATIC_DIR = Path(__file__).parent / "static"
ACCESS_COOKIE = "pb_access"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "phonebridge"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
_UPLOAD_CHUNK = 1024 * 1024
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_POLICY_VIOLATION = 1008

type LinkOpener = Callable[[str], Awaitable[bool]]


class _InterfaceFiles(StaticFiles):
    """The UI is several ES modules; no-cache keeps them from mixing versions after an update."""

    def file_response(
        self, full_path: PathLike, stat_result: os.stat_result, scope: Scope, status_code: int = 200
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache"
        return response


class SendText(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    reply_to: str | None = None
    temp_id: str | None = Field(default=None, max_length=100)


class NewChat(BaseModel):
    addresses: list[str] = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=20_000)


class Typing(BaseModel):
    typing: bool


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class Participant(BaseModel):
    address: str = Field(min_length=1, max_length=320)


class ChatRef(BaseModel):
    chat_id: str


class React(ChatRef):
    type: ReactionType
    remove: bool = False


class Edit(ChatRef):
    text: str = Field(min_length=1, max_length=20_000)


class CallLink(BaseModel):
    link: str
    opened: bool


class StartCall(ChatRef):
    video: bool = True


class MacSettings(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    password: str = Field(min_length=1, max_length=500)


class MacSettingsView(BaseModel):
    url: str


class SettingsView(BaseModel):
    mode: str
    mac: MacSettingsView | None


class FocusReport(BaseModel):
    """Sent by a UI window so notifications only appear when nobody is looking."""

    type: str
    focused: bool
    chat_id: str | None = Field(default=None, max_length=500)


class _Guard:
    """Host, token and CSRF checks shared by HTTP requests and the WebSocket."""

    def __init__(self, *, token: str | None, allowed_hosts: Collection[str]) -> None:
        self._token = token
        self._allowed_hosts = frozenset(allowed_hosts)

    def host_allowed(self, connection: HTTPConnection) -> bool:
        # A token makes DNS-rebinding moot, so remote access can use any host name.
        return self._token is not None or connection.headers.get("host", "") in self._allowed_hosts

    def supplied_token(self, connection: HTTPConnection) -> str | None:
        auth = connection.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:]
        return connection.query_params.get("token") or connection.cookies.get(ACCESS_COOKIE)

    def token_ok(self, connection: HTTPConnection) -> bool:
        if self._token is None:
            return True
        supplied = self.supplied_token(connection)
        return supplied is not None and secrets.compare_digest(supplied, self._token)

    def origin_ok(self, connection: HTTPConnection) -> bool:
        origin = connection.headers.get("origin")
        return origin is None or urlsplit(origin).netloc == connection.headers.get("host", "")


def _is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _read_upload(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(_UPLOAD_CHUNK):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise RequestError("Files over 100 MB can't be sent.")
        chunks.append(chunk)
    return b"".join(chunks)


def create_app(
    manager: ServiceManager,
    hub: WebSocketHub,
    *,
    token: str | None,
    allowed_hosts: Collection[str],
    desktop: DesktopHooks | None = None,
    open_link: LinkOpener | None = None,
) -> FastAPI:
    app = FastAPI(title="Phonebridge", docs_url=None, redoc_url=None, openapi_url=None)
    guard = _Guard(token=token, allowed_hosts=allowed_hosts)
    # Windows takes MIME types from the registry, where .js is sometimes text/plain;
    # browsers refuse to run ES modules served that way.
    mimetypes.add_type("text/javascript", ".js")

    @app.middleware("http")
    async def protect(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not guard.host_allowed(request):
            return PlainTextResponse("Unexpected host.", status_code=status.HTTP_400_BAD_REQUEST)
        if not guard.token_ok(request):
            return JSONResponse(
                {"detail": "Access token required."}, status_code=status.HTTP_401_UNAUTHORIZED
            )
        if request.method not in _SAFE_METHODS and (
            request.headers.get(CSRF_HEADER) != CSRF_VALUE or not guard.origin_ok(request)
        ):
            return JSONResponse(
                {"detail": "Cross-site request blocked."}, status_code=status.HTTP_403_FORBIDDEN
            )
        response = await call_next(request)
        query_token = request.query_params.get("token")
        if token is not None and query_token:
            response.set_cookie(ACCESS_COOKIE, query_token, httponly=True, samesite="strict")
        return response

    @app.exception_handler(NotFoundError)
    async def not_found(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Not found."}, status_code=status.HTTP_404_NOT_FOUND)

    @app.exception_handler(UnsupportedError)
    async def unsupported(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_409_CONFLICT)

    @app.exception_handler(RequestError)
    async def failed(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_400_BAD_REQUEST)

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        page = await asyncio.to_thread((STATIC_DIR / "index.html").read_text, encoding="utf-8")
        return HTMLResponse(
            page.replace("__APP_NAME__", html.escape(DISPLAY_NAME)), headers={"Cache-Control": "no-store"}
        )

    app.mount("/static", _InterfaceFiles(directory=STATIC_DIR), name="static")

    async def link_response(request: Request, link: str) -> CallLink:
        opened = bool(open_link and _is_local(request) and await open_link(link))
        return CallLink(link=link, opened=opened)

    # Status and settings

    @app.get("/api/status")
    async def get_status() -> AppStatus:
        return await manager.current.status()

    @app.get("/api/settings")
    async def get_settings() -> SettingsView:
        mac = manager.config.mac
        return SettingsView(
            mode=(await manager.current.status()).mode.value,
            mac=MacSettingsView(url=mac.url) if mac else None,
        )

    @app.put("/api/settings/mac")
    async def connect_mac(body: MacSettings) -> AppStatus:
        return await manager.connect_mac(body.url, body.password)

    @app.delete("/api/settings/mac")
    async def disconnect_mac() -> AppStatus:
        return await manager.disconnect_mac()

    # Chats

    @app.get("/api/chats")
    async def list_chats(q: str | None = None) -> list[Chat]:
        return await manager.current.chats(q)

    @app.post("/api/chats", status_code=status.HTTP_201_CREATED)
    async def new_chat(body: NewChat) -> ThreadPage:
        return await manager.current.new_chat(body.addresses, body.text)

    @app.get("/api/chats/{chat_id}")
    async def get_chat(chat_id: str) -> Chat:
        return await manager.current.chat(chat_id)

    @app.patch("/api/chats/{chat_id}")
    async def rename_chat(chat_id: str, body: Rename) -> Chat:
        return await manager.current.rename(chat_id, body.title)

    @app.get("/api/chats/{chat_id}/messages")
    async def list_messages(
        chat_id: str, limit: Annotated[int, Query(ge=1, le=200)] = 50, before: str | None = None
    ) -> ThreadPage:
        return await manager.current.messages(chat_id, before=before, limit=limit)

    @app.post("/api/chats/{chat_id}/messages", status_code=status.HTTP_202_ACCEPTED)
    async def send_text(chat_id: str, body: SendText) -> ChatMessage:
        return await manager.current.send_text(
            chat_id, body.text, reply_to=body.reply_to, temp_id=body.temp_id
        )

    @app.post("/api/chats/{chat_id}/attachments", status_code=status.HTTP_202_ACCEPTED)
    async def send_attachment(
        chat_id: str,
        file: Annotated[UploadFile, File()],
        temp_id: Annotated[str | None, Form(max_length=100)] = None,
    ) -> ChatMessage:
        data = await _read_upload(file)
        return await manager.current.send_attachment(
            chat_id,
            filename=Path(file.filename or "attachment").name,
            content_type=file.content_type or "application/octet-stream",
            data=data,
            temp_id=temp_id,
        )

    @app.post("/api/chats/{chat_id}/read", status_code=status.HTTP_204_NO_CONTENT)
    async def mark_read(chat_id: str) -> None:
        await manager.current.mark_read(chat_id)

    @app.post("/api/chats/{chat_id}/typing", status_code=status.HTTP_204_NO_CONTENT)
    async def typing(chat_id: str, body: Typing) -> None:
        await manager.current.set_typing(chat_id, typing=body.typing)

    @app.post("/api/chats/{chat_id}/participants")
    async def add_participant(chat_id: str, body: Participant) -> Chat:
        return await manager.current.add_participant(chat_id, body.address)

    @app.delete("/api/chats/{chat_id}/participants/{address}")
    async def remove_participant(chat_id: str, address: str) -> Chat:
        return await manager.current.remove_participant(chat_id, address)

    @app.post("/api/chats/{chat_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
    async def leave(chat_id: str) -> None:
        await manager.current.leave(chat_id)

    # Messages

    @app.post("/api/messages/{message_id}/reactions", status_code=status.HTTP_202_ACCEPTED)
    async def react(message_id: str, body: React) -> None:
        await manager.current.react(body.chat_id, message_id, body.type, remove=body.remove)

    @app.post("/api/messages/{message_id}/edit", status_code=status.HTTP_202_ACCEPTED)
    async def edit(message_id: str, body: Edit) -> None:
        await manager.current.edit(body.chat_id, message_id, body.text)

    @app.post("/api/messages/{message_id}/unsend", status_code=status.HTTP_202_ACCEPTED)
    async def unsend(message_id: str, body: ChatRef) -> None:
        await manager.current.unsend(body.chat_id, message_id)

    @app.post("/api/messages/{message_id}/retry", status_code=status.HTTP_202_ACCEPTED)
    async def retry(message_id: str, body: ChatRef) -> ChatMessage:
        return await manager.current.retry(body.chat_id, message_id)

    @app.get("/api/attachments/{attachment_id}")
    async def attachment(attachment_id: str, thumb: bool = False) -> StreamingResponse:
        stream = await manager.current.open_attachment(attachment_id, thumb=thumb)
        headers = {"Cache-Control": "private, max-age=86400"}
        if stream.size is not None:
            headers["Content-Length"] = str(stream.size)

        async def body() -> AsyncIterator[bytes]:
            async for chunk in stream.chunks:
                yield chunk

        return StreamingResponse(
            body(), media_type=stream.content_type, headers=headers, background=BackgroundTask(stream.close)
        )

    @app.get("/api/search")
    async def search(q: str, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[ChatSearchHit]:
        return await manager.current.search(q, limit)

    @app.get("/api/photos/{photo_id}")
    async def photo(photo_id: str) -> Response:
        found = await manager.current.photo(photo_id)
        # Photo ids are content hashes, so a photo never changes under the same URL.
        headers = {"Cache-Control": "private, max-age=604800, immutable"}
        return Response(content=found.data, media_type=found.content_type, headers=headers)

    @app.get("/api/contacts")
    async def contacts(q: str = "", limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[ContactCard]:
        return await manager.current.contacts(q, limit)

    # FaceTime

    @app.post("/api/calls")
    async def start_call(body: StartCall) -> Call:
        return await manager.current.start_call(body.chat_id, video=body.video)

    # Both the main window and the floating call window ask to join; FaceTime opens once.
    joined_here: set[str] = set()

    @app.post("/api/calls/{call_id}/join")
    async def join_call(request: Request, call_id: str) -> CallLink:
        link = await manager.current.join_call(call_id)
        if call_id in joined_here and _is_local(request):
            return CallLink(link=link, opened=True)
        result = await link_response(request, link)
        if result.opened:
            joined_here.add(call_id)
        return result

    @app.post("/api/calls/{call_id}/answer")
    async def answer_call(request: Request, call_id: str) -> CallLink:
        return await link_response(request, await manager.current.answer_call(call_id))

    @app.post("/api/calls/{call_id}/decline", status_code=status.HTTP_204_NO_CONTENT)
    async def decline_call(call_id: str) -> None:
        await manager.current.decline_call(call_id)

    # Phone Link and desktop window

    @app.post("/api/phone-link/close", status_code=status.HTTP_204_NO_CONTENT)
    async def close_phone_link() -> None:
        if not await manager.current.close_phone_link():
            raise HTTPException(status.HTTP_409_CONFLICT, "Couldn't close Phone Link.")

    @app.post("/api/desktop/show", status_code=status.HTTP_204_NO_CONTENT)
    async def show_window() -> None:
        if desktop is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not running as a desktop app.")
        desktop.show_window()

    @app.post("/api/desktop/quit", status_code=status.HTTP_204_NO_CONTENT)
    async def quit_app() -> None:
        if desktop is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not running as a desktop app.")
        desktop.quit_app()

    @app.websocket("/ws")
    async def events(websocket: WebSocket) -> None:
        if not (guard.host_allowed(websocket) and guard.token_ok(websocket) and guard.origin_ok(websocket)):
            await websocket.close(code=_POLICY_VIOLATION)
            return
        await websocket.accept()
        hub.attach(websocket)
        try:
            await websocket.send_json(
                StatusUpdate(status=await manager.current.status()).model_dump(mode="json")
            )
            with contextlib.suppress(WebSocketDisconnect):
                while True:
                    raw = await websocket.receive_text()
                    try:
                        report = FocusReport.model_validate_json(raw)
                    except ValidationError:
                        continue
                    if report.type == "focus":
                        hub.set_focus(websocket, focused=report.focused, chat_id=report.chat_id)
        finally:
            hub.detach(websocket)

    return app
