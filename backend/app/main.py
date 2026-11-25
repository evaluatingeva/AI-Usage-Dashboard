import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import init_db
from .routers import admin, analytics, auth_routes, ingest

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

if settings.environment == "prod":
    if settings.auth_mode != "oidc":
        raise RuntimeError("AUTH_MODE must be 'oidc' when ENVIRONMENT=prod")
    if settings.jwt_secret == "change-me-dev-only" or settings.pseudonymization_key == "change-me":
        raise RuntimeError("Set real JWT_SECRET and PSEUDONYMIZATION_KEY values for prod")


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(
    lifespan=lifespan,
    title=settings.app_name,
    version="1.0.0",
    description="Central metering for enterprise AI usage: tokens, RAG, seats and non-token units.",
    docs_url="/api/docs" if settings.environment != "prod" else None,
    openapi_url="/api/openapi.json" if settings.environment != "prod" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return resp


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Log the full traceback; outside prod, also return the cause so the dashboard can show it."""
    logging.getLogger("api").exception("unhandled error on %s %s", request.method, request.url.path)
    detail = "Internal Server Error" if settings.environment == "prod" else f"{type(exc).__name__}: {exc}"[:500]
    return JSONResponse(status_code=500, content={"detail": detail})


@app.get("/api/health")
def health():
    return {"status": "ok", "environment": settings.environment, "auth_mode": settings.auth_mode}


app.include_router(auth_routes.router)
app.include_router(ingest.router)
app.include_router(analytics.router)
app.include_router(admin.router)
