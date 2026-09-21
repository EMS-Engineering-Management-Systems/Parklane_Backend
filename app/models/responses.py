"""Response models.

These exist mainly so `/docs` describes what each route returns; the payloads
themselves are plain dicts because `tags` is open-ended by design.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Warning_(BaseModel):
    """Non-blocking feedback about a write that was accepted anyway."""

    code: str = Field(description="UNKNOWN_TAGS, OUT_OF_RANGE or UNKNOWN_DEVICE_TYPE")
    message: str
    tags: list[str] | None = None
    values: list[dict[str, Any]] | None = None


class IndexedWarnings(BaseModel):
    """Warnings for one item of a batch insert, by its position in the array."""

    index: int
    warnings: list[Warning_]


class PageMeta(BaseModel):
    total: int
    page: int
    limit: int
    pages: int
    hasNext: bool
    hasPrev: bool


class CreateResponse(BaseModel):
    success: bool = True
    collection: str
    insertedCount: int
    warnings: list[IndexedWarnings] = []
    data: dict[str, Any] | list[dict[str, Any]]


class ReadingResponse(BaseModel):
    success: bool = True
    collection: str
    warnings: list[Warning_] = []
    data: dict[str, Any]


class ReadingListResponse(BaseModel):
    success: bool = True
    collection: str
    deviceId: str
    filter: dict[str, Any]
    meta: PageMeta
    data: list[dict[str, Any]]


class DeleteResponse(BaseModel):
    success: bool = True
    collection: str
    deletedCount: int
    filter: dict[str, Any] | None = None
    data: dict[str, Any] | None = None


class TagStats(BaseModel):
    count: int
    min: float
    max: float
    sum: float
    avg: float
    last: float


class StatsResponse(BaseModel):
    success: bool = True
    collection: str
    count: int
    firstTimestamp: datetime | None = None
    lastTimestamp: datetime | None = None
    filter: dict[str, Any]
    tags: dict[str, TagStats]


class RollupResponse(BaseModel):
    success: bool = True
    source: str
    destination: str
    periods: int
    upserted: int = 0
    modified: int = 0


class CatalogueEntry(BaseModel):
    type: str
    label: str
    category: str
    tagCount: int
    collections: list[str]


class CatalogueResponse(BaseModel):
    success: bool = True
    database: str
    source: str
    deviceCount: int
    tagCount: int
    granularities: list[str]
    data: list[CatalogueEntry]


class CatalogueDetailResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class DeviceResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]
    droppedCollections: list[str] | None = None


class DeviceListResponse(BaseModel):
    success: bool = True
    total: int
    data: list[dict[str, Any]]


class CollectionListResponse(BaseModel):
    success: bool = True
    database: str
    total: int
    data: list[dict[str, Any]]


class DropResponse(BaseModel):
    success: bool = True
    dropped: str


class DatabaseHealth(BaseModel):
    name: str
    connected: bool
    pingMs: float | None = None
    collections: int | None = None


class HealthResponse(BaseModel):
    success: bool = True
    service: str
    env: str
    uptimeSeconds: int
    database: DatabaseHealth
    catalogue: dict[str, Any]
    granularities: list[str]


class ErrorBody(BaseModel):
    status: int
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    """The shape of every failure."""

    success: bool = False
    error: ErrorBody
