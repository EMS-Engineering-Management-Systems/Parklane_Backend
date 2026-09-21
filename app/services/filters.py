"""Query-string parsing: pagination, projection and the MongoDB filter.

Starlette hands query parameters through verbatim, so `tags.Voltage[gte]=9000`
arrives with the operator still in the key - it is parsed here rather than by
the framework.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.core.errors import ApiError

OPERATORS = {"gt", "gte", "lt", "lte", "ne", "in", "nin"}

_TAG_KEY = re.compile(r"^(tags\.[^\[\]]+)(?:\[(\w+)\])?$")

# Parameters that steer the request rather than filter the data.
CONTROL_PARAMS = {"page", "limit", "sort", "order", "fields", "confirm"}


class Pagination:
    __slots__ = ("page", "limit", "skip", "sort_field", "order")

    def __init__(self, page: int, limit: int, sort_field: str, order: int) -> None:
        self.page = page
        self.limit = limit
        self.skip = (page - 1) * limit
        self.sort_field = sort_field
        self.order = order

    @property
    def sort(self) -> list[tuple[str, int]]:
        return [(self.sort_field, self.order)]

    def meta(self, total: int) -> dict[str, Any]:
        pages = -(-total // self.limit) if self.limit else 0
        return {
            "total": total,
            "page": self.page,
            "limit": self.limit,
            "pages": pages,
            "hasNext": self.page < pages,
            "hasPrev": self.page > 1,
        }


def _to_int(value: str | None, fallback: int) -> int:
    try:
        return int(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def parse_pagination(params: Mapping[str, str]) -> Pagination:
    page = max(1, _to_int(params.get("page"), 1))

    limit = _to_int(params.get("limit"), settings.default_page_size)
    if limit <= 0:
        limit = settings.default_page_size
    if limit > settings.max_page_size:
        raise ApiError.bad_request(
            f"limit cannot exceed {settings.max_page_size}",
            {"limit": limit, "maxLimit": settings.max_page_size},
        )

    sort_field = (params.get("sort") or "timestamp").strip() or "timestamp"
    order = 1 if (params.get("order") or "desc").lower() == "asc" else -1

    return Pagination(page, limit, sort_field, order)


def parse_projection(fields: str | None) -> dict[str, int] | None:
    """`fields=timestamp,tags.Voltage` -> a MongoDB projection."""
    if not fields:
        return None
    projection = {field.strip(): 1 for field in fields.split(",") if field.strip()}
    return projection or None


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError.bad_request("timestamp is not a valid date", {"timestamp": value}) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _cast(raw: str) -> Any:
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw == "null":
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def build_filter(params: Mapping[str, str]) -> dict[str, Any]:
    """Builds the Mongo filter from the query string.

    ?from=2026-01-01&to=2026-02-01
    ?year=2026&month=3
    ?tags.Voltage=10476
    ?tags.Voltage[gte]=9000&tags.Voltage[lte]=11000
    ?source=seed
    """
    mongo_filter: dict[str, Any] = {}

    if params.get("from") or params.get("to"):
        window: dict[str, Any] = {}
        if params.get("from"):
            window["$gte"] = parse_timestamp(params["from"])
        if params.get("to"):
            window["$lte"] = parse_timestamp(params["to"])
        mongo_filter["timestamp"] = window

    if params.get("year"):
        mongo_filter["period.year"] = _to_int(params["year"], 0)
    if params.get("month"):
        mongo_filter["period.month"] = _to_int(params["month"], 0)
    if params.get("source"):
        mongo_filter["source"] = params["source"]

    for key, value in params.items():
        if not key.startswith("tags."):
            continue

        match = _TAG_KEY.match(key)
        if not match:
            continue

        field, operator = match.groups()

        if not operator:
            mongo_filter[field] = _cast(value)
            continue
        if operator not in OPERATORS:
            raise ApiError.bad_request(
                f'unsupported filter operator "{operator}"',
                {"field": field, "allowed": sorted(OPERATORS)},
            )

        existing = mongo_filter.get(field)
        if not isinstance(existing, dict):
            existing = {}
            mongo_filter[field] = existing
        existing[f"${operator}"] = (
            [_cast(item) for item in value.split(",")] if operator in {"in", "nin"} else _cast(value)
        )

    return mongo_filter
