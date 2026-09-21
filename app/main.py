"""Parklane BMS backend - application factory and entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.core import database
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.models.responses import ErrorResponse
from app.routers import collections, devices, meta, readings, users

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("parklane")

DESCRIPTION = """
CRUD backend for the Parklane Integrated Smart Building. Two databases:

* `Atal` - BMS readings, one collection per device per granularity:
  `<devicetype>_<index>_<granularity>`, e.g. `transformer_1_instant`.
* `Atal_Users` - residents, units, parking, visitors, maintenance and
  community, each an entity collection under `/api/users`.

The real devices are not commissioned yet, so nothing is pre-provisioned: a
collection is created the first time you POST a reading into it, and the device
registers itself at the same moment. Device types outside the shipped catalogue
are accepted too - the response just carries a warning saying tags were not
validated.

Writes are deliberately permissive. A reading is rejected only when it is
structurally wrong; unknown or out-of-range tags are stored and reported in
`warnings`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        database.connect()
    except Exception as error:
        logger.error("database connection failed: %s", error)
        raise

    # Indexes for Atal_Users are declared per resource; create them on startup
    # so a fresh database is correctly constrained before the first write.
    from app.services.resources import ensure_all_indexes

    ensure_all_indexes()

    yield
    database.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Parklane BMS Backend",
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        responses={
            400: {"model": ErrorResponse, "description": "Bad request"},
            404: {"model": ErrorResponse, "description": "Not found"},
            409: {"model": ErrorResponse, "description": "Conflict"},
        },
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    api = APIRouter(prefix="/api")
    api.include_router(meta.router)
    api.include_router(devices.router)
    api.include_router(readings.router)
    api.include_router(collections.router)
    api.include_router(users.router)
    app.include_router(api)

    @app.get("/api", include_in_schema=False)
    def api_index():
        return meta.index_payload()

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/api")

    return app


app = create_app()


def check_port_available(host: str, port: int) -> None:
    """Bind the port before handing it to uvicorn.

    Uvicorn's own failure is a bare `[WinError 10013] An attempt was made to
    access a socket in a way forbidden by its access permissions`, which says
    nothing about which port or what is holding it. On Windows a service that
    owns the socket exclusively (NoMachine's nxd on 4000, for one) produces
    10013 rather than the usual "address already in use", so the message is
    especially misleading.
    """
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("" if host == "0.0.0.0" else host, port))
    except OSError as error:
        raise SystemExit(
            f"\nCannot start: port {port} is not available on {host}.\n"
            f"  {error}\n\n"
            f"Something else is already listening, or Windows has reserved the port.\n"
            f"Find the owner:   netstat -ano | findstr :{port}\n"
            f"                  Get-Process -Id <PID>\n"
            f"Reserved ranges:  netsh interface ipv4 show excludedportrange protocol=tcp\n"
            f"Then set PORT to a free port in .env, or free this one.\n"
        ) from error
    finally:
        probe.close()


def main() -> None:
    import uvicorn

    check_port_available(settings.host, settings.port)

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=not settings.is_production,
    )


if __name__ == "__main__":
    main()
