"""Request models for the `Atal_Users` database.

The source CSV is a flat snapshot of a resident's app screen, with dot-paths
(`Unit.Finance.PaidPercent`) and indexed arrays (`Visitor.Upcoming[2].Name`).
That flat shape is decomposed here into the entities it actually describes, so
each one can be created and edited on its own.

Every model forbids unknown fields: these are curated entities, unlike the BMS
readings where the tag set is open-ended by design.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Patchable(BaseModel):
    """Base for PATCH bodies: every field optional, unknown fields rejected."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Residents
# ---------------------------------------------------------------------------


class ResidentIn(Strict):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "residentId": 1001,
                    "name": "Youssef",
                    "username": "youssef.samir@parklane.com",
                    "password": "choose-a-real-one",
                    "unitCode": "H-601",
                }
            ]
        },
    )

    residentId: int = Field(
        ge=1,
        description="Unique number assigned to the resident; referenced by every other collection",
    )
    name: str
    username: str = Field(description="Login identifier; unique")
    password: str | None = Field(
        default=None,
        description="Write-only. Stored as a bcrypt hash and never returned.",
    )
    unitCode: str | None = None
    phone: str | None = None
    status: str = "active"


class ResidentPatch(Patchable):
    name: str | None = None
    username: str | None = None
    password: str | None = None
    unitCode: str | None = None
    phone: str | None = None
    status: str | None = None


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


class UnitFinance(Strict):
    unitPriceEGP: float | None = None
    amountPaidEGP: float | None = None
    remainingBalanceEGP: float | None = None
    paidPercent: float | None = None
    nextInstallmentEGP: float | None = None
    nextInstallmentDueDate: dt.date | None = None
    remainingInstallments: int | None = None


class UnitMaintenance(Strict):
    annualFeeEGP: float | None = None
    paidEGP: float | None = None
    outstandingEGP: float | None = None
    nextDueDate: dt.date | None = None


class UnitDocuments(Strict):
    contractAvailable: bool = False
    receiptsAvailable: bool = False
    floorPlanAvailable: bool = False


class UnitIn(Strict):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "unitCode": "H-601",
                    "tower": "Tower H",
                    "bedrooms": 3,
                    "bathrooms": 2,
                    "floor": 6,
                    "areaM2": 182.2,
                    "unitType": "Apartment",
                    "handoverStatus": "Active",
                    "finance": {"unitPriceEGP": 6750000, "paidPercent": 65.3},
                }
            ]
        },
    )

    unitCode: str
    tower: str | None = None
    building: str | None = None
    floor: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    areaM2: float | None = None
    unitType: str | None = None
    handoverStatus: str | None = None
    ownershipVerified: bool | None = None
    parkingSlotCount: int | None = None
    parkingSlotCode: str | None = None
    finance: UnitFinance | None = None
    maintenance: UnitMaintenance | None = None
    documents: UnitDocuments | None = None


class UnitPatch(Patchable):
    tower: str | None = None
    building: str | None = None
    floor: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    areaM2: float | None = None
    unitType: str | None = None
    handoverStatus: str | None = None
    ownershipVerified: bool | None = None
    parkingSlotCount: int | None = None
    parkingSlotCode: str | None = None
    finance: UnitFinance | None = None
    maintenance: UnitMaintenance | None = None
    documents: UnitDocuments | None = None


# ---------------------------------------------------------------------------
# Parking
# ---------------------------------------------------------------------------


class VehicleIn(Strict):
    plate: str
    residentId: int
    model: str | None = None
    color: str | None = None


class VehiclePatch(Patchable):
    residentId: int | None = None
    model: str | None = None
    color: str | None = None


class ParkingSlotIn(Strict):
    slotCode: str
    residentId: int | None = None
    tower: str | None = None
    level: str | None = None
    unitCode: str | None = None


class ParkingSlotPatch(Patchable):
    residentId: int | None = None
    tower: str | None = None
    level: str | None = None
    unitCode: str | None = None


class ParkingOccupancyIn(Strict):
    """Building-wide, one reading per 15-minute bucket."""

    timestamp: dt.datetime
    availableSpaces: int | None = None
    totalSpaces: int | None = None
    guestUsedSpaces: int | None = None
    guestTotalSpaces: int | None = None
    evUsedChargers: int | None = None
    evTotalChargers: int | None = None
    occupancyPercent: float | None = None
    sources: list[int] | None = Field(
        default=None, description="residentIds of the feeds merged into this reading"
    )


class ParkingOccupancyPatch(Patchable):
    timestamp: dt.datetime | None = None
    availableSpaces: int | None = None
    totalSpaces: int | None = None
    guestUsedSpaces: int | None = None
    guestTotalSpaces: int | None = None
    evUsedChargers: int | None = None
    evTotalChargers: int | None = None
    occupancyPercent: float | None = None


class ParkingSlotStatusIn(Strict):
    """Per-resident: is that resident's own slot occupied at this moment."""

    residentId: int
    timestamp: dt.datetime
    slotCode: str | None = None
    isActive: bool = False


class ParkingSlotStatusPatch(Patchable):
    slotCode: str | None = None
    isActive: bool | None = None


class ParkingActivityIn(Strict):
    residentId: int
    eventAt: dt.datetime
    type: str = Field(description="car_entered | guest_parking | vehicle_exit")
    status: str | None = None
    guestName: str | None = None
    guestCount: int | None = None


class ParkingActivityPatch(Patchable):
    status: str | None = None
    guestName: str | None = None
    guestCount: int | None = None


class ParkingGuestRequestIn(Strict):
    residentId: int
    date: dt.date
    startTime: str | None = None
    endTime: str | None = None
    guestCount: int | None = None
    status: str = "Pending"


class ParkingGuestRequestPatch(Patchable):
    date: dt.date | None = None
    startTime: str | None = None
    endTime: str | None = None
    guestCount: int | None = None
    status: str | None = None


# ---------------------------------------------------------------------------
# Visitors
# ---------------------------------------------------------------------------


class VisitorPassIn(Strict):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "residentId": 1001,
                    "name": "Ali Saeed",
                    "visitDate": "2026-09-21",
                    "visitTime": "17:46:00",
                    "guestCount": 3,
                    "carPlate": "EGY 6716",
                    "status": "Scheduled",
                }
            ]
        },
    )

    residentId: int
    name: str
    visitDate: dt.date
    visitTime: str | None = None
    guestCount: int | None = None
    carPlate: str | None = None
    status: str = "Scheduled"
    kind: str = Field(default="upcoming", description="current | upcoming")


class VisitorPassPatch(Patchable):
    name: str | None = None
    visitDate: dt.date | None = None
    visitTime: str | None = None
    guestCount: int | None = None
    carPlate: str | None = None
    status: str | None = None
    kind: str | None = None


class VisitorPassRequestIn(Strict):
    """The 'new pass' form a resident submits before the pass is issued."""

    residentId: int
    mobileNumber: str
    visitDate: dt.date
    guestCount: int | None = None
    status: str = "Pending"


class VisitorPassRequestPatch(Patchable):
    mobileNumber: str | None = None
    visitDate: dt.date | None = None
    guestCount: int | None = None
    status: str | None = None


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


class Technician(Strict):
    name: str | None = None
    contact: str | None = None


class MaintenanceVisit(Strict):
    date: dt.date | None = None
    startTime: str | None = None
    endTime: str | None = None


class MaintenanceRequestIn(Strict):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "requestId": "REQ-2026-83328",
                    "residentId": 1001,
                    "unitCode": "H-601",
                    "category": "Appliances",
                    "title": "Built-in oven not heating",
                    "status": "Assigned",
                    "submittedAt": "2026-09-21T12:13:08Z",
                }
            ]
        },
    )

    requestId: str
    residentId: int
    unitCode: str | None = None
    category: str | None = None
    title: str | None = None
    status: str = "Submitted"
    submittedAt: dt.datetime | None = None
    assignedAt: dt.datetime | None = None
    inProgressAt: dt.datetime | None = None
    completedAt: dt.datetime | None = None
    technician: Technician | None = None
    visit: MaintenanceVisit | None = None


class MaintenanceRequestPatch(Patchable):
    unitCode: str | None = None
    category: str | None = None
    title: str | None = None
    status: str | None = None
    submittedAt: dt.datetime | None = None
    assignedAt: dt.datetime | None = None
    inProgressAt: dt.datetime | None = None
    completedAt: dt.datetime | None = None
    technician: Technician | None = None
    visit: MaintenanceVisit | None = None


# ---------------------------------------------------------------------------
# Community
# ---------------------------------------------------------------------------


class AnnouncementIn(Strict):
    key: str = Field(description="Stable slug derived from the title")
    title: str
    date: dt.date | None = None
    startTime: str | None = None
    endTime: str | None = None
    openTime: str | None = None
    closeTime: str | None = None


class AnnouncementPatch(Patchable):
    title: str | None = None
    date: dt.date | None = None
    startTime: str | None = None
    endTime: str | None = None
    openTime: str | None = None
    closeTime: str | None = None


class EventIn(Strict):
    key: str
    title: str
    date: dt.date | None = None
    startTime: str | None = None
    endTime: str | None = None
    location: str | None = None


class EventPatch(Patchable):
    title: str | None = None
    date: dt.date | None = None
    startTime: str | None = None
    endTime: str | None = None
    location: str | None = None


class EventParticipationIn(Strict):
    """A resident's relationship to an event: registered, going, and so on."""

    residentId: int
    eventKey: str
    actionState: str


class EventParticipationPatch(Patchable):
    actionState: str | None = None


class AlertIn(Strict):
    key: str
    title: str
    effectiveDate: dt.date | None = None
    startTime: str | None = None
    endTime: str | None = None


class AlertPatch(Patchable):
    title: str | None = None
    effectiveDate: dt.date | None = None
    startTime: str | None = None
    endTime: str | None = None


class PollOption(Strict):
    name: str
    percent: float | None = None


class PollIn(Strict):
    key: str
    title: str | None = None
    options: list[PollOption] = []


class PollPatch(Patchable):
    title: str | None = None
    options: list[PollOption] | None = None


class PollVoteIn(Strict):
    residentId: int
    pollKey: str
    optionName: str


class PollVotePatch(Patchable):
    optionName: str | None = None


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------


class WeatherIn(Strict):
    """Building-wide, one reading per 15-minute bucket."""

    timestamp: dt.datetime
    temperatureC: float | None = None
    condition: str | None = None
    sources: list[int] | None = None


class WeatherPatch(Patchable):
    temperatureC: float | None = None
    condition: str | None = None


# ---------------------------------------------------------------------------


class ResourceListResponse(BaseModel):
    success: bool = True
    resource: str
    collection: str
    filter: dict[str, Any]
    meta: dict[str, Any]
    data: list[dict[str, Any]]


class ResourceResponse(BaseModel):
    success: bool = True
    resource: str
    collection: str
    data: dict[str, Any]


class ResourceCreateResponse(BaseModel):
    success: bool = True
    resource: str
    collection: str
    insertedCount: int
    data: dict[str, Any] | list[dict[str, Any]]


class ResourceDeleteResponse(BaseModel):
    success: bool = True
    resource: str
    collection: str
    deletedCount: int
    data: dict[str, Any] | None = None
    filter: dict[str, Any] | None = None


class ResourceIndexResponse(BaseModel):
    success: bool = True
    database: str
    total: int
    data: list[dict[str, Any]]
