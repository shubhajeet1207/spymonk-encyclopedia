"""FastAPI application factory and ASGI entry point."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.api.routes import documents, inquiries, memory, questions, research, system
from backend.api.security import require_api_access
from backend.core import config
from backend.observability.logging import (
    configure_logging,
    disable_file_logging,
    enable_file_logging,
    new_request_id,
    request_id_context,
    safe_exception_context,
)

configure_logging()
logger = logging.getLogger("spymonk.http")

ROOT_DIR = config.ROOT_DIR
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
MAX_SMALL_BODY_BYTES = 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
API_CSP = b"default-src 'none'; frame-ancestors 'none'"
FRONTEND_CSP = (
    "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; "
    f"script-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors {config.FRAME_ANCESTORS}"
).encode("ascii")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from backend.core import run_log, store
    from backend.core.instance_lock import InstanceLock
    from backend.core.reindex import rebuild_if_missing
    from backend.services.backup import create_manager
    from backend.services.jobs import shutdown_job_manager

    if config.HOSTED and not config.APP_API_TOKEN:
        # Behind a host's proxy every request looks the same; without a token
        # the whole API, including spending and deletion, would be public.
        raise RuntimeError("Set APP_API_TOKEN (as a secret) before running a hosted deployment.")
    lock = InstanceLock(config.DATA_DIR / ".spymonk-api.lock") if config.INSTANCE_LOCK else None
    if lock is not None:
        lock.acquire()
    if config.LOG_TO_FILE:
        try:
            log_path = enable_file_logging(
                config.LOG_DIR, max_bytes=config.LOG_FILE_MAX_BYTES, backups=config.LOG_FILE_BACKUPS
            )
            logger.info("Writing logs to %s", log_path)
            run_log.enable(config.LOG_DIR / "research")
        except OSError as exc:
            logger.warning("Could not open the log file; logging to the console only (%s)", safe_exception_context(exc))
    backup = create_manager(config.DATA_DIR)
    try:
        if backup is not None:
            # Before serving anything: a failed restore stops startup.
            backup.restore()
    except BaseException:
        if lock is not None:
            lock.release()
        run_log.disable()
        disable_file_logging()
        raise
    if config.REBUILD_INDEX_ON_START:
        try:
            rebuild_if_missing()
        except Exception as exc:
            # The files are safe; the archive is searchable again after a rebuild.
            logger.error("Could not rebuild the search index at startup; use Rebuild in the UI (%s)", safe_exception_context(exc))
    if backup is not None:
        backup.start()
    if config.EMBEDDINGS_WARMUP:
        from backend.core.embeddings import warm_up_embeddings

        threading.Thread(target=warm_up_embeddings, name="embeddings-warmup", daemon=True).start()
    try:
        yield
    finally:
        shutdown_job_manager()
        if backup is not None:
            backup.stop()
        store.close_clients()
        if lock is not None:
            lock.release()
        run_log.disable()
        disable_file_logging()


class RequestBodyLimitMiddleware:
    """Reject oversized bodies before FastAPI spools multipart data to disk."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = (
            documents.MAX_UPLOAD_FILES * config.MAX_PDF_BYTES + MULTIPART_OVERHEAD_BYTES
            if scope.get("path") == "/api/documents"
            else MAX_SMALL_BODY_BYTES
        )
        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
                if declared_size < 0:
                    raise ValueError("negative length")
            except ValueError:
                response = JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header."})
                await response(scope, receive, send)
                return
            if declared_size > limit:
                response = JSONResponse(status_code=413, content={"detail": "Request body is too large."})
                await response(scope, receive, send)
                return

        received_bytes = 0
        async def receive_limited() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > limit:
                    raise HTTPException(status_code=413, detail="Request body is too large.")
            return message

        await self.app(scope, receive_limited, send)


class RequestLoggingMiddleware:
    """Log HTTP requests without BaseHTTPMiddleware's task/stream wrapper."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id(Headers(scope=scope).get("x-request-id"))
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "")
        started = time.perf_counter()
        response_status: int | None = None
        response_started = False
        cancelled = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                response_status = message["status"]
                response_headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in {
                        b"x-request-id",
                        b"x-content-type-options",
                        b"referrer-policy",
                        b"content-security-policy",
                    }
                ]
                response_headers.append((b"x-request-id", request_id.encode("ascii")))
                response_headers.append((b"x-content-type-options", b"nosniff"))
                response_headers.append((b"referrer-policy", b"no-referrer"))
                if path.startswith("/api/"):
                    response_headers.append((b"content-security-policy", API_CSP))
                elif path not in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
                    # FastAPI's interactive docs load their UI from a CDN, so only
                    # the application frontend gets the strict page policy.
                    response_headers.append((b"content-security-policy", FRONTEND_CSP))
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except asyncio.CancelledError:
            # Cancellation is part of ASGI's normal disconnect/shutdown flow.
            # Record it without treating it as an application error, then keep
            # cancellation semantics intact for the server and child tasks.
            cancelled = True
            raise
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            stack = "".join(traceback.format_tb(exc.__traceback__)).rstrip()
            logger.error(
                "Unhandled request exception (method=%s path=%s %s duration_ms=%.1f)\n%s",
                method,
                path,
                safe_exception_context(exc),
                elapsed_ms,
                stack or "<no traceback available>",
            )
            if response_started:
                raise

            response = JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error. Check backend logs using the X-Request-ID response header."
                },
                headers={"X-Request-ID": request_id},
            )
            await response(scope, receive, send_with_request_id)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            if cancelled:
                logger.info(
                    "Request cancelled (method=%s path=%s duration_ms=%.1f)",
                    method,
                    path,
                    elapsed_ms,
                )
            elif response_status is not None and response_status < 400:
                logger.info(
                    "Request completed (method=%s path=%s status=%s duration_ms=%.1f)",
                    method,
                    path,
                    response_status,
                    elapsed_ms,
                )
            elif response_status is not None and response_status >= 400:
                logger.warning(
                    "Request completed (method=%s path=%s status=%s duration_ms=%.1f)",
                    method,
                    path,
                    response_status,
                    elapsed_ms,
                )
            request_id_context.reset(token)


def create_app() -> FastAPI:
    application = FastAPI(
        title="SpyMonk Encyclopedia API",
        description="Research, evidence-bounded answers, and PDF ingestion.",
        version="2.3.0",
        lifespan=lifespan,
        docs_url="/docs" if config.API_DOCS else None,
        redoc_url="/redoc" if config.API_DOCS else None,
        openapi_url="/openapi.json" if config.API_DOCS else None,
    )
    if not config.APP_API_TOKEN:
        logger.warning("APP_API_TOKEN is unset; API access is restricted to loopback clients")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )

    application.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)
    application.add_middleware(RequestBodyLimitMiddleware)

    application.add_middleware(RequestLoggingMiddleware)

    application.include_router(system.router)
    protected = [Depends(require_api_access)]
    application.include_router(research.router, dependencies=protected)
    application.include_router(questions.router, dependencies=protected)
    application.include_router(documents.router, dependencies=protected)
    application.include_router(memory.router, dependencies=protected)
    application.include_router(inquiries.router, dependencies=protected)

    if FRONTEND_DIST.exists():
        assets_dir = FRONTEND_DIST / "assets"
        if assets_dir.exists():
            application.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @application.get("/", include_in_schema=False)
        def frontend_root() -> FileResponse:
            return FileResponse(FRONTEND_DIST / "index.html")

        @application.get("/{full_path:path}", include_in_schema=False)
        def frontend_routes(full_path: str) -> FileResponse:
            if full_path == "api" or full_path.startswith("api/"):
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="API route not found.")
            return FileResponse(FRONTEND_DIST / "index.html")
    else:

        @application.get("/", include_in_schema=False)
        def frontend_not_built() -> dict[str, str]:
            return {
                "message": "SpyMonk API is ready. Run the React frontend with `npm --prefix frontend run dev`.",
            }

    return application


app = create_app()
