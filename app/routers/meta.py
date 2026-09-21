"""Health and the route index."""

from __future__ import annotations

import time

from fastapi import APIRouter

from app.core.config import settings
from app.core.database import get_db, is_connected
from app.models.responses import HealthResponse
from app.services.registry import catalogue_summary

router = APIRouter(tags=["meta"])

_STARTED_AT = time.monotonic()


@router.get("/health", response_model=HealthResponse, summary="Service and database health")
def health():
    ping_ms: float | None = None
    collections: int | None = None

    if is_connected():
        started = time.perf_counter()
        get_db().command("ping")
        ping_ms = round((time.perf_counter() - started) * 1000, 3)
        collections = len(get_db().list_collection_names())

    return {
        "success": True,
        "service": "parklane-backend",
        "env": settings.app_env,
        "uptimeSeconds": int(time.monotonic() - _STARTED_AT),
        "database": {
            "name": settings.mongodb_db,
            "connected": is_connected(),
            "pingMs": ping_ms,
            "collections": collections,
        },
        "catalogue": catalogue_summary(),
        "granularities": settings.granularities,
    }


def index_payload() -> dict:
    """Self-describing index, for anyone poking at the API with curl.
    The generated OpenAPI docs at /docs are the fuller reference.

    Mounted at /api by `app.main`; a prefix-less router cannot own an empty path.
    """
    return {
        "success": True,
        "service": "Parklane BMS Backend",
        "database": settings.mongodb_db,
        "granularities": settings.granularities,
        "collectionNaming": "<devicetype>_<index>_<granularity> e.g. transformer_1_instant",
        "docs": {"swagger": "/docs", "redoc": "/redoc", "openapi": "/openapi.json"},
        "routes": {
            "health": "GET /api/health",
            "catalogue": [
                "GET    /api/devices/catalogue",
                "GET    /api/devices/catalogue/{device_type}",
            ],
            "devices": [
                "GET    /api/devices",
                "POST   /api/devices",
                "GET    /api/devices/{device_type}/{index}",
                "PATCH  /api/devices/{device_type}/{index}",
                "DELETE /api/devices/{device_type}/{index}?dropCollections=true",
            ],
            "readings": [
                "POST   /api/readings/{device_type}/{index}/{granularity}        (object or array)",
                "POST   /api/readings/{device_type}/{index}/{granularity}/upsert (idempotent by timestamp)",
                "GET    /api/readings/{device_type}/{index}/{granularity}"
                "        ?page&limit&sort&order&fields&from&to&year&month",
                "GET    /api/readings/{device_type}/{index}/{granularity}/latest",
                "GET    /api/readings/{device_type}/{index}/{granularity}/stats  ?from&to",
                "GET    /api/readings/{device_type}/{index}/{granularity}/{id}",
                "PUT    /api/readings/{device_type}/{index}/{granularity}/{id}",
                "PATCH  /api/readings/{device_type}/{index}/{granularity}/{id}",
                "DELETE /api/readings/{device_type}/{index}/{granularity}/{id}",
                "DELETE /api/readings/{device_type}/{index}/{granularity}        ?from&to | ?confirm=true",
                "POST   /api/readings/{device_type}/{index}/rollup/{granularity} ?from&to",
            ],
            "collections": [
                "GET    /api/collections",
                "DELETE /api/collections/{name}?confirm=true",
            ],
        },
    }
