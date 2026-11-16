"""AgeCare — API de la Consola de Administración (FastAPI + PostgreSQL)."""
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.billing import router_admin as billing_admin
from app.billing import router_app as billing_app
from app.config import get_settings
from app.logs import setup_logging
from app.errors import RequestIdMiddleware, register_error_handlers
from app.routers import (auth, content, marketplace, metrics_commercial, metrics_features,
                         metrics_roles, moderation, ops, support)
from app.routers import system as system_router
from app.routers import games_apps
from app.game_static import GameStaticFiles

_s = get_settings()
setup_logging(_s.log_level, _s.log_format, _s.environment)   # solo el logger "agecare" (pasarela de pago)
_s.validate_billing()   # impide arrancar en prod con credenciales de integración de Transbank

app = FastAPI(
    title="AgeCare Admin API",
    version="1.0.0",
    description="Backend de la Consola de Administración de AgeCare · Wellq Co",
    docs_url="/api/v1/admin/docs",
    openapi_url="/api/v1/admin/openapi.json",
)
app.add_middleware(RequestIdMiddleware)
register_error_handlers(app)

if get_settings().allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().allowed_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )

api = APIRouter(prefix="/api/v1/admin")
api.include_router(auth.router)
api.include_router(metrics_commercial.router)
api.include_router(ops.router)
api.include_router(metrics_roles.router)
api.include_router(support.router)
api.include_router(metrics_features.router)
api.include_router(content.router)
api.include_router(marketplace.router)
api.include_router(moderation.router)
api.include_router(system_router.router)
api.include_router(games_apps.router)
api.include_router(billing_admin.router)
app.include_router(api)
# Pasarela de pago para la app (usuarios finales con JWT de la app; no es parte de /admin)
app.include_router(billing_app.router)


_default_openapi = app.openapi


def custom_openapi():
    """OpenAPI de FastAPI + resúmenes y errores documentados de las rutas de pago (solo /billing)."""
    if app.openapi_schema is None:
        from app.billing.api_docs import apply_to_openapi
        app.openapi_schema = apply_to_openapi(_default_openapi())
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]

app.mount("/static", GameStaticFiles(directory=Path(__file__).resolve().parent / "static"),
          name="game-static")

app.mount("/demo", StaticFiles(directory=Path(__file__).resolve().parents[1] / "frontend-demo",
                              html=True), name="demo")


@app.get("/", include_in_schema=False)
async def demo_home():
    return RedirectResponse("/demo/")


@app.get("/health", tags=["Salud"])
async def health():
    return {"status": "ok", "service": get_settings().app_name}
