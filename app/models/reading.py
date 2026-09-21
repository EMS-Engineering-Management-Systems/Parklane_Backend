"""Request models for readings.

Writes are deliberately permissive - commissioning data is messy and rejecting
it loses it. A reading is refused only when it is structurally wrong. Tags that
are unknown or outside their engineering range are stored and reported back in
`warnings` instead (see `app/services/schema.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Keys that are part of the envelope rather than the tag payload.
_ENVELOPE_KEYS = {"timestamp", "Timestamp", "tags", "source", "meta", "period"}


class ReadingIn(BaseModel):
    """A reading to create or replace.

    Accepts either the explicit shape::

        {"timestamp": "...", "tags": {"Voltage": 10476}}

    or a flat one, which is what a BMS export row looks like::

        {"Timestamp": "...", "Voltage": 10476, "Current": 912}
    """

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "timestamp": "2026-03-15T10:00:00Z",
                    "tags": {"Voltage": 10476, "Current": 912, "Frequency": 50, "Trip_Status": 0},
                }
            ]
        },
    )

    timestamp: datetime | None = Field(
        default=None,
        description="ISO-8601. Defaults to now when omitted.",
    )
    tags: dict[str, Any] | None = Field(
        default=None,
        description="Tag name to value. Short (`Voltage`) or fully qualified (`Transformer_Voltage`).",
    )
    source: str | None = Field(default=None, description="api, seed or rollup. Defaults to api.")
    meta: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_flat_payload(cls, data: Any) -> Any:
        """Folds a flat `{Timestamp, Voltage, Current}` body into `tags`."""
        if not isinstance(data, dict):
            return data

        if data.get("tags") is not None:
            # `Timestamp` is what the BMS CSV export calls it.
            if "timestamp" not in data and "Timestamp" in data:
                data = {**data, "timestamp": data["Timestamp"]}
            return data

        loose = {key: value for key, value in data.items() if key not in _ENVELOPE_KEYS}
        if not loose:
            return data

        rebuilt = {key: value for key, value in data.items() if key in _ENVELOPE_KEYS}
        rebuilt["tags"] = loose
        if "timestamp" not in rebuilt and "Timestamp" in data:
            rebuilt["timestamp"] = data["Timestamp"]
        return rebuilt


class ReadingPatch(BaseModel):
    """A partial update. Tags are merged one by one, so a PATCH carrying a
    single tag leaves every other tag on the document untouched."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"tags": {"Voltage": 10500}}]},
    )

    timestamp: datetime | None = None
    tags: dict[str, Any] | None = None
    source: str | None = None
    meta: dict[str, Any] | None = None


class DeviceIn(BaseModel):
    """Registers a device ahead of any reading being posted."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"deviceType": "Transformer", "index": 2, "location": "Substation B"}]
        },
    )

    deviceType: str = Field(description="A catalogue type such as Transformer, or any new one.")
    index: int = Field(default=1, ge=1, le=9999)
    label: str | None = None
    category: str | None = None
    location: str | None = None
    description: str | None = None
    status: str = "active"


class DevicePatch(BaseModel):
    """Device metadata only. Identity fields cannot be changed."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    category: str | None = None
    location: str | None = None
    description: str | None = None
    status: str | None = None
    meta: dict[str, Any] | None = None
