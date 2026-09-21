"""Reading CRUD: /api/readings/{device_type}/{index}/{granularity}

Maps onto the collection <devicetype>_<index>_<granularity>, for example
transformer_1_instant.

Routes are plain `def`, so FastAPI runs them in its threadpool and the blocking
PyMongo calls never touch the event loop.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Path, Request, status

from app.models.reading import ReadingIn, ReadingPatch
from app.models.responses import (
    CreateResponse,
    DeleteResponse,
    ReadingListResponse,
    ReadingResponse,
    RollupResponse,
    StatsResponse,
)
from app.services import readings as service
from app.services.filters import CONTROL_PARAMS, parse_pagination, parse_projection
from app.services.naming import resolve_target
from app.services.rollup import rollup as run_rollup

router = APIRouter(prefix="/readings", tags=["readings"])

DeviceType = Annotated[str, Path(description="Device type, e.g. Transformer. Case-insensitive.")]
DeviceIndex = Annotated[int, Path(ge=1, le=9999, description="Device instance, e.g. 1")]
Granularity = Annotated[str, Path(description="instant, monthly or yearly")]
ReadingId = Annotated[str, Path(description="24-character hex ObjectId")]


def _filters(request: Request) -> dict[str, str]:
    """Query parameters with the control ones removed, leaving only filters."""
    return {key: value for key, value in request.query_params.items() if key not in CONTROL_PARAMS}


# ---------------------------------------------------------------------------
# Rollup and the named sub-resources come first: they must not be swallowed by
# the /{reading_id} route, which matches any single segment.
# ---------------------------------------------------------------------------


@router.post(
    "/{device_type}/{index}/rollup/{granularity}",
    response_model=RollupResponse,
    summary="Build the monthly or yearly collection from the instant one",
)
def rollup_endpoint(device_type: DeviceType, index: DeviceIndex, granularity: Granularity, request: Request):
    result = run_rollup(device_type, index, granularity, request.query_params)
    return {"success": True, **result}


@router.get(
    "/{device_type}/{index}/{granularity}/latest",
    response_model=ReadingResponse,
    summary="Newest reading matching the filter",
)
def latest(device_type: DeviceType, index: DeviceIndex, granularity: Granularity, request: Request):
    target = resolve_target(device_type, index, granularity)
    return {"success": True, **service.latest(target, _filters(request))}


@router.get(
    "/{device_type}/{index}/{granularity}/stats",
    response_model=StatsResponse,
    summary="min / max / avg / sum / last per tag",
)
def stats(device_type: DeviceType, index: DeviceIndex, granularity: Granularity, request: Request):
    target = resolve_target(device_type, index, granularity)
    return {"success": True, **service.stats(target, _filters(request))}


@router.post(
    "/{device_type}/{index}/{granularity}/upsert",
    response_model=ReadingResponse,
    summary="Create or update, keyed on timestamp (idempotent ingestion)",
)
def upsert(
    device_type: DeviceType,
    index: DeviceIndex,
    granularity: Granularity,
    payload: Annotated[ReadingIn, Body()],
):
    target = resolve_target(device_type, index, granularity)
    return {"success": True, **service.upsert_by_timestamp(target, payload)}


# ---------------------------------------------------------------------------
# Collection-level CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/{device_type}/{index}/{granularity}",
    response_model=CreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create one reading or a batch",
    description=(
        "Accepts a single object or an array. The collection and the device "
        "registration are created automatically if they do not exist yet."
    ),
)
def create(
    device_type: DeviceType,
    index: DeviceIndex,
    granularity: Granularity,
    payload: Annotated[ReadingIn | list[ReadingIn], Body()],
):
    target = resolve_target(device_type, index, granularity)
    return {"success": True, **service.create(target, payload)}


@router.get(
    "/{device_type}/{index}/{granularity}",
    response_model=ReadingListResponse,
    summary="List readings",
    description=(
        "Filters: from, to, year, month, source, tags.<Name>, "
        "tags.<Name>[gt|gte|lt|lte|ne|in|nin]. "
        "Controls: page, limit, sort, order, fields."
    ),
)
def list_readings(device_type: DeviceType, index: DeviceIndex, granularity: Granularity, request: Request):
    target = resolve_target(device_type, index, granularity)
    pagination = parse_pagination(request.query_params)
    projection = parse_projection(request.query_params.get("fields"))

    return {"success": True, **service.list_readings(target, _filters(request), pagination, projection)}


@router.delete(
    "/{device_type}/{index}/{granularity}",
    response_model=DeleteResponse,
    summary="Delete a range of readings",
    description="Refuses to run without a filter unless confirm=true is passed.",
)
def delete_many(device_type: DeviceType, index: DeviceIndex, granularity: Granularity, request: Request):
    target = resolve_target(device_type, index, granularity)
    return {"success": True, **service.remove_many(target, request.query_params)}


# ---------------------------------------------------------------------------
# Document-level CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/{device_type}/{index}/{granularity}/{reading_id}",
    response_model=ReadingResponse,
    summary="Read one reading",
)
def get_one(device_type: DeviceType, index: DeviceIndex, granularity: Granularity, reading_id: ReadingId):
    target = resolve_target(device_type, index, granularity)
    return {"success": True, **service.get_by_id(target, reading_id)}


@router.put(
    "/{device_type}/{index}/{granularity}/{reading_id}",
    response_model=ReadingResponse,
    summary="Replace a reading",
)
def replace(
    device_type: DeviceType,
    index: DeviceIndex,
    granularity: Granularity,
    reading_id: ReadingId,
    payload: Annotated[ReadingIn, Body()],
):
    target = resolve_target(device_type, index, granularity)
    return {"success": True, **service.replace(target, reading_id, payload)}


@router.patch(
    "/{device_type}/{index}/{granularity}/{reading_id}",
    response_model=ReadingResponse,
    summary="Update a reading",
    description="Merges at tag level, so a body with one tag leaves the others alone.",
)
def update(
    device_type: DeviceType,
    index: DeviceIndex,
    granularity: Granularity,
    reading_id: ReadingId,
    payload: Annotated[ReadingPatch, Body()],
):
    target = resolve_target(device_type, index, granularity)
    return {"success": True, **service.update(target, reading_id, payload)}


@router.delete(
    "/{device_type}/{index}/{granularity}/{reading_id}",
    response_model=DeleteResponse,
    summary="Delete one reading",
)
def delete_one(device_type: DeviceType, index: DeviceIndex, granularity: Granularity, reading_id: ReadingId):
    target = resolve_target(device_type, index, granularity)
    return {"success": True, **service.remove(target, reading_id)}
