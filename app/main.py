import uuid
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.router import api_router
from app.api.websocket.manager import manager
from app.api.websocket.routes import router as ws_router
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.redis import close_redis
from app.core.security import decode_token
from app.middleware.error_handlers import register_exception_handlers
from app.middleware.request_context import RequestContextMiddleware
from app.middleware.security import SecurityHeadersMiddleware
import app.core.public_ids  # noqa: F401 — register BWR public ID generators

setup_logging()
logger = get_logger(__name__)

limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.rate_limit_per_minute}/minute"])

OPENAPI_TAGS = [
    {"name": "Authentication", "description": "Login, register, OTP, tokens"},
    {"name": "User", "description": "Passenger app endpoints"},
    {"name": "Driver", "description": "Driver app endpoints"},
    {"name": "Admin", "description": "Admin panel endpoints"},
    {"name": "Common", "description": "Shared public resources"},
    {"name": "Public", "description": "Unauthenticated content"},
    {"name": "WebSocket", "description": "Realtime ride & location"},
]

ALLOWED_PREFIXES = (
    f"{settings.api_v1_prefix}/auth",
    f"{settings.api_v1_prefix}/user",
    f"{settings.api_v1_prefix}/driver",
    f"{settings.api_v1_prefix}/users",
    f"{settings.api_v1_prefix}/drivers",
    f"{settings.api_v1_prefix}/notifications",
    f"{settings.api_v1_prefix}/admin",
    f"{settings.api_v1_prefix}/common",
    f"{settings.api_v1_prefix}/public",
    "/ws",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.openapi_schema = None
    logger.info("application_starting", env=settings.app_env, version="2.0.0")
    try:
        from app.core.firebase import initialize_firebase

        initialize_firebase()
    except Exception as exc:
        logger.warning("firebase_startup_skipped", error=str(exc))
    yield
    await close_redis()
    logger.info("application_stopped")


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        description="Ride Booking Platform API",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
    )

    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    register_exception_handlers(application)

    cors_kwargs: dict = {
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        # Flutter web dev servers use random localhost ports — always allow them.
        "allow_origin_regex": r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    }
    if not settings.is_development:
        cors_kwargs["allow_origins"] = settings.cors_origins_list

    application.add_middleware(CORSMiddleware, **cors_kwargs)
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(RequestContextMiddleware)

    upload_path = Path(settings.upload_dir)
    upload_path.mkdir(parents=True, exist_ok=True)
    application.mount("/uploads", StaticFiles(directory=str(upload_path)), name="uploads")

    api_prefix = settings.api_v1_prefix
    application.include_router(api_router, prefix=api_prefix)
    application.include_router(ws_router, prefix="/ws", tags=["WebSocket"])

    @application.get("/health", include_in_schema=False, tags=["Health"])
    async def health_check():
        return {
            "status": "healthy",
            "app": settings.app_name,
            "env": settings.app_env,
            "version": "2.0.0",
            "modules": {
                "auth": f"{api_prefix}/auth",
                "user": f"{api_prefix}/user",
                "driver": f"{api_prefix}/driver",
                "admin": f"{api_prefix}/admin",
                "common": f"{api_prefix}/common",
                "public": f"{api_prefix}/public",
                "websocket": "/ws",
            },
        }

    @application.websocket("/ws/{token}")
    async def websocket_legacy(websocket: WebSocket, token: str):
        try:
            payload = decode_token(token)
            user_id = payload["sub"]
        except ValueError:
            await websocket.close(code=4001)
            return

        connection_id = str(uuid.uuid4())
        await manager.connect(websocket, connection_id, user_id)

        try:
            while True:
                data = await websocket.receive_json()
                event = data.get("event")
                if event == "subscribe_ride":
                    manager.subscribe_ride(connection_id, data.get("ride_id"))
                elif event == "unsubscribe_ride":
                    manager.unsubscribe_ride(connection_id, data.get("ride_id"))
                elif event == "ping":
                    await websocket.send_json({"event": "pong"})
                elif event == "location_update":
                    await manager.broadcast_ride(data.get("ride_id"), {
                        "event": "location_update",
                        "lat": data.get("lat"),
                        "lng": data.get("lng"),
                        "ride_id": data.get("ride_id"),
                    })
        except WebSocketDisconnect:
            manager.disconnect(connection_id, user_id)

    def custom_openapi():
        if application.openapi_schema:
            return application.openapi_schema

        schema = get_openapi(
            title=application.title,
            version=application.version,
            description=application.description,
            routes=application.routes,
        )

        tag_by_prefix = {
            f"{api_prefix}/auth": "Authentication",
            f"{api_prefix}/user": "User",
            f"{api_prefix}/driver": "Driver",
            f"{api_prefix}/admin": "Admin",
            f"{api_prefix}/common": "Common",
            f"{api_prefix}/public": "Public",
            "/ws": "WebSocket",
        }

        filtered_paths = {}
        for path, operations in schema.get("paths", {}).items():
            if "-panel" in path or path == "/health":
                continue
            if not any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES):
                continue
            panel_tag = next((tag for prefix, tag in tag_by_prefix.items() if path.startswith(prefix)), None)
            if panel_tag:
                for operation in operations.values():
                    if isinstance(operation, dict):
                        operation["tags"] = [panel_tag]
            filtered_paths[path] = operations

        schema["paths"] = filtered_paths
        schema["tags"] = OPENAPI_TAGS
        application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = custom_openapi
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=settings.debug)
