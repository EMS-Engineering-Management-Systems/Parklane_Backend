#!/usr/bin/env python3
"""Import the resident CSV exports into `Atal_Users`.

Each CSV is one resident's app screen, re-snapshotted every 15 minutes. Only 25
of its 142 columns ever change, so loading it row-for-row would store the same
unit price, contract and technician details ten thousand times over. Instead the
flat rows are decomposed into the entities they describe:

    entities     residents, units, vehicles, parking slots, visitor passes,
                 maintenance requests, community content - deduplicated
    time-series  weather and parking occupancy, one reading per 15 minutes
    events       parking activity, deduplicated by (resident, type, moment)

Building-wide figures (parking capacity, EV chargers, weather, poll results)
appear in both files with different values, because the two were generated
independently. They are merged into one reading per 15-minute bucket, numeric
fields averaged, and `sources` records which feeds contributed.

    python -m scripts.import_users
    python -m scripts.import_users --drop
    python -m scripts.import_users --files ../Parklane_Dummy_Data_Ali.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import chain
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import UpdateOne  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import close, connect, get_users_db  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.services.resources import RESOURCES, ensure_all_indexes  # noqa: E402

DEFAULT_FILES = [
    Path("../Parklane_Dummy_Data_youssef.csv"),
    Path("../Parklane_Dummy_Data_Ali.csv"),
]

BUCKET = timedelta(minutes=15)

# residentId is a surrogate key: a unique number, not a name. The CSV's stable
# natural key is the login username, so an id already assigned to a username is
# reused on re-import rather than reallocated.
FIRST_RESIDENT_ID = 1001


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def blank(value: str | None) -> bool:
    return value is None or str(value).strip() == ""


def as_int(value: str | None) -> int | None:
    if blank(value):
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def as_float(value: str | None) -> float | None:
    if blank(value):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def as_bool(value: str | None) -> bool | None:
    if blank(value):
        return None
    return str(value).strip() in ("1", "true", "True", "Yes", "yes")


def as_datetime(value: str | None) -> datetime | None:
    if blank(value):
        return None
    text = str(value).strip().replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def as_date(value: str | None) -> datetime | None:
    parsed = as_datetime(value)
    return datetime(parsed.year, parsed.month, parsed.day) if parsed else None


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-")


def floor_to_bucket(moment: datetime) -> datetime:
    """Youssef samples at :16/:31/:46, Ali at :17/:32/:47 - one minute apart, so
    the two feeds never share a timestamp. Flooring to the 15-minute boundary
    puts them in the same bucket and gives the building one series."""
    minute = (moment.minute // 15) * 15
    return moment.replace(minute=minute, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Row -> entities
# ---------------------------------------------------------------------------


def resolve_resident_id(username: str, claimed: set[int]) -> int:
    """Reuse the id already assigned to this login, or allocate the next free one."""
    residents = get_users_db()["residents"]

    existing = residents.find_one({"username": username}, {"residentId": 1})
    if existing and isinstance(existing.get("residentId"), int):
        return existing["residentId"]

    highest = residents.find_one(
        {"residentId": {"$type": "number"}}, {"residentId": 1}, sort=[("residentId", -1)]
    )
    candidate = (highest["residentId"] + 1) if highest else FIRST_RESIDENT_ID
    candidate = max(candidate, FIRST_RESIDENT_ID)

    # Two new residents in the same run must not collide before either is written.
    while candidate in claimed:
        candidate += 1
    return candidate


def resident_from(row: dict, resident_id: int) -> dict:
    return {
        "residentId": resident_id,
        "name": row["Home.Resident.Name"],
        "username": row["Login.Username"],
        # The CSV carries a plaintext DummyPass#NNNN that increments every row.
        # Only the first is meaningful; it is stored hashed and never returned.
        "passwordHash": hash_password(row["Login.Password"]),
        "unitCode": row["Unit.Code"],
        "status": "active",
    }


def unit_from(row: dict) -> dict:
    return {
        "unitCode": row["Unit.Code"],
        "tower": row["Unit.Tower"],
        "building": row["Unit.Building"],
        "floor": as_int(row["Unit.Floor"]),
        "bedrooms": as_int(row["Unit.Bedrooms"]),
        "bathrooms": as_int(row["Unit.Bathrooms"]),
        "areaM2": as_float(row["Unit.AreaM2"]),
        "unitType": row["Unit.UnitType"],
        "handoverStatus": row["Unit.HandoverStatus"],
        "ownershipVerified": as_bool(row["Unit.OwnershipVerified"]),
        "parkingSlotCount": as_int(row["Unit.ParkingSlotCount"]),
        "parkingSlotCode": row["Unit.ParkingSlotCode"],
        "finance": {
            "unitPriceEGP": as_float(row["Unit.Finance.UnitPriceEGP"]),
            "amountPaidEGP": as_float(row["Unit.Finance.AmountPaidEGP"]),
            "remainingBalanceEGP": as_float(row["Unit.Finance.RemainingBalanceEGP"]),
            "paidPercent": as_float(row["Unit.Finance.PaidPercent"]),
            "nextInstallmentEGP": as_float(row["Unit.Finance.NextInstallmentEGP"]),
            "nextInstallmentDueDate": as_date(row["Unit.Finance.NextInstallmentDueDate"]),
            "remainingInstallments": as_int(row["Unit.Finance.RemainingInstallments"]),
        },
        "maintenance": {
            "annualFeeEGP": as_float(row["Unit.Maintenance.AnnualFeeEGP"]),
            "paidEGP": as_float(row["Unit.Maintenance.PaidEGP"]),
            "outstandingEGP": as_float(row["Unit.Maintenance.OutstandingEGP"]),
            "nextDueDate": as_date(row["Unit.Maintenance.NextDueDate"]),
        },
        "documents": {
            "contractAvailable": bool(as_bool(row["Unit.Documents.ContractAvailable"])),
            "receiptsAvailable": bool(as_bool(row["Unit.Documents.ReceiptsAvailable"])),
            "floorPlanAvailable": bool(as_bool(row["Unit.Documents.FloorPlanAvailable"])),
        },
    }


def vehicle_from(row: dict, resident_id: int) -> dict:
    return {
        "plate": row["Parking.Vehicle.Plate"],
        "residentId": resident_id,
        "model": row["Parking.Vehicle.Model"],
        "color": row["Parking.Vehicle.Color"],
    }


def slot_from(row: dict, resident_id: int) -> dict:
    return {
        "slotCode": row["Parking.MyParking.SlotCode"],
        "residentId": resident_id,
        "tower": row["Parking.MyParking.Tower"],
        "level": row["Parking.MyParking.Level"],
        "unitCode": row["Unit.Code"],
    }


def maintenance_from(row: dict, resident_id: int) -> dict:
    return {
        "requestId": row["Maintenance.Request.ID"],
        "residentId": resident_id,
        "unitCode": row["Maintenance.Request.UnitCode"],
        "category": row["Maintenance.Request.Category"],
        "title": row["Maintenance.Request.Title"],
        "status": row["Maintenance.Request.Status"],
        "submittedAt": as_datetime(row["Maintenance.Request.SubmittedAt"]),
        "assignedAt": as_datetime(row["Maintenance.Request.AssignedAt"]),
        "inProgressAt": as_datetime(row["Maintenance.Request.InProgressAt"]),
        "completedAt": as_datetime(row["Maintenance.Request.CompletedAt"]),
        "technician": {
            "name": row["Maintenance.Technician.Name"],
            "contact": row["Maintenance.Technician.Contact"],
        },
        "visit": {
            "date": as_date(row["Maintenance.Visit.Date"]),
            "startTime": row["Maintenance.Visit.StartTime"],
            "endTime": row["Maintenance.Visit.EndTime"],
        },
    }


def visitor_passes_from(row: dict, resident_id: int) -> list[dict]:
    passes = []

    if not blank(row.get("Visitor.Pass.Name")):
        passes.append(
            {
                "residentId": resident_id,
                "name": row["Visitor.Pass.Name"],
                "visitDate": as_date(row["Visitor.Pass.VisitDate"]),
                "visitTime": row["Visitor.Pass.ArrivalTime"],
                "guestCount": as_int(row["Visitor.Pass.GuestCount"]),
                "carPlate": row["Visitor.Pass.CarPlate"],
                "status": row["Visitor.Pass.Status"],
                "kind": "current",
            }
        )

    for n in (1, 2, 3):
        name = row.get(f"Visitor.Upcoming[{n}].Name")
        if blank(name):
            continue
        passes.append(
            {
                "residentId": resident_id,
                "name": name,
                "visitDate": as_date(row[f"Visitor.Upcoming[{n}].VisitDate"]),
                "visitTime": row[f"Visitor.Upcoming[{n}].VisitTime"],
                "guestCount": as_int(row[f"Visitor.Upcoming[{n}].GuestCount"]),
                "carPlate": row[f"Visitor.Upcoming[{n}].CarPlate"],
                "status": row[f"Visitor.Upcoming[{n}].Status"],
                "kind": "upcoming",
            }
        )

    return passes


def community_from(row: dict) -> dict[str, list[dict]]:
    """Announcements, events, alerts and polls are identical in both files -
    they are building-wide content, keyed by a slug of the title."""
    announcements = []
    if not blank(row.get("Community.Announcement[1].Title")):
        announcements.append(
            {
                "key": slugify(row["Community.Announcement[1].Title"]),
                "title": row["Community.Announcement[1].Title"],
                "date": as_date(row["Community.Announcement[1].Date"]),
                "startTime": row["Community.Announcement[1].StartTime"],
                "endTime": row["Community.Announcement[1].EndTime"],
            }
        )
    if not blank(row.get("Community.Announcement[2].Title")):
        announcements.append(
            {
                "key": slugify(row["Community.Announcement[2].Title"]),
                "title": row["Community.Announcement[2].Title"],
                "openTime": row["Community.Announcement[2].OpenTime"],
                "closeTime": row["Community.Announcement[2].CloseTime"],
            }
        )

    events = []
    for n in (1, 2, 3):
        title = row.get(f"Community.Event[{n}].Title")
        if blank(title):
            continue
        events.append(
            {
                "key": slugify(title),
                "title": title,
                "date": as_date(row[f"Community.Event[{n}].Date"]),
                "startTime": row[f"Community.Event[{n}].StartTime"],
                "endTime": row[f"Community.Event[{n}].EndTime"],
                "location": row[f"Community.Event[{n}].Location"],
            }
        )

    alerts = []
    if not blank(row.get("Community.Alert[1].Title")):
        alerts.append(
            {
                "key": slugify(row["Community.Alert[1].Title"]),
                "title": row["Community.Alert[1].Title"],
                "effectiveDate": as_date(row["Community.Alert[1].EffectiveDate"]),
            }
        )
    if not blank(row.get("Community.Alert[2].Title")):
        alerts.append(
            {
                "key": slugify(row["Community.Alert[2].Title"]),
                "title": row["Community.Alert[2].Title"],
                "startTime": row["Community.Alert[2].StartTime"],
                "endTime": row["Community.Alert[2].EndTime"],
            }
        )

    return {"announcements": announcements, "events": events, "alerts": alerts}


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class Accumulator:
    """Collects entities across every file before a single bulk write."""

    def __init__(self) -> None:
        self.residents: dict[int, dict] = {}
        self.units: dict[str, dict] = {}
        self.vehicles: dict[str, dict] = {}
        self.slots: dict[str, dict] = {}
        self.maintenance: dict[str, dict] = {}
        self.visitor_passes: dict[tuple, dict] = {}
        self.visitor_requests: dict[tuple, dict] = {}
        self.guest_requests: dict[tuple, dict] = {}
        self.announcements: dict[str, dict] = {}
        self.events: dict[str, dict] = {}
        self.alerts: dict[str, dict] = {}
        self.participation: dict[tuple, dict] = {}
        self.polls: dict[str, dict] = {}
        self.poll_votes: dict[tuple, dict] = {}
        self.activity: dict[tuple, dict] = {}
        self.slot_status: dict[tuple, dict] = {}

        # Building-wide series: bucket -> list of per-file samples, merged later.
        self.weather: dict[datetime, list[dict]] = defaultdict(list)
        self.occupancy: dict[datetime, list[dict]] = defaultdict(list)
        self.poll_percent: dict[tuple, list[float]] = defaultdict(list)


def read_file(path: Path, acc: Accumulator) -> tuple[int, str, int]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        first = next(reader, None)
        if first is None:
            return 0, "", 0

        resident_id = resolve_resident_id(first["Login.Username"], set(acc.residents))
        display_name = first["Home.Resident.Name"]

        rows = 0
        for row in chain([first], reader):
            rows += 1
            moment = as_datetime(row["Timestamp"])
            if moment is None:
                continue
            bucket = floor_to_bucket(moment)

            if rows == 1:
                acc.residents[resident_id] = resident_from(row, resident_id)
                acc.units[row["Unit.Code"]] = unit_from(row)
                acc.vehicles[row["Parking.Vehicle.Plate"]] = vehicle_from(row, resident_id)
                acc.slots[row["Parking.MyParking.SlotCode"]] = slot_from(row, resident_id)

                if not blank(row.get("Visitor.NewPass.MobileNumber")):
                    acc.visitor_requests[(resident_id, row["Visitor.NewPass.MobileNumber"])] = {
                        "residentId": resident_id,
                        "mobileNumber": row["Visitor.NewPass.MobileNumber"],
                        "visitDate": as_date(row["Visitor.NewPass.VisitDate"]),
                        "guestCount": as_int(row["Visitor.NewPass.GuestCount"]),
                        "status": "Pending",
                    }

                content = community_from(row)
                for item in content["announcements"]:
                    acc.announcements[item["key"]] = item
                for item in content["events"]:
                    acc.events[item["key"]] = item
                for item in content["alerts"]:
                    acc.alerts[item["key"]] = item

                for n in (1, 2, 3):
                    name = row.get(f"Community.Poll.Option[{n}].Name")
                    if blank(name):
                        continue
                    acc.poll_percent[("resident-priorities", name)].append(
                        as_float(row[f"Community.Poll.Option[{n}].Percent"]) or 0.0
                    )
                    if as_bool(row[f"Community.Poll.Option[{n}].Selected"]):
                        acc.poll_votes[(resident_id, "resident-priorities")] = {
                            "residentId": resident_id,
                            "pollKey": "resident-priorities",
                            "optionName": name,
                        }

            # -- entities whose state changes; last row wins ------------------
            if not blank(row.get("Maintenance.Request.ID")):
                acc.maintenance[row["Maintenance.Request.ID"]] = maintenance_from(row, resident_id)

            for item in visitor_passes_from(row, resident_id):
                acc.visitor_passes[
                    (item["residentId"], item["name"], item["visitDate"], item["visitTime"])
                ] = item

            if not blank(row.get("Parking.GuestRequest.Date")):
                key = (
                    resident_id,
                    as_date(row["Parking.GuestRequest.Date"]),
                    row["Parking.GuestRequest.StartTime"],
                )
                acc.guest_requests[key] = {
                    "residentId": resident_id,
                    "date": key[1],
                    "startTime": row["Parking.GuestRequest.StartTime"],
                    "endTime": row["Parking.GuestRequest.EndTime"],
                    "guestCount": as_int(row["Parking.GuestRequest.GuestCount"]),
                    "status": row["Parking.GuestRequest.Status"],
                }

            for n in (1, 2, 3):
                title = row.get(f"Community.Event[{n}].Title")
                state = row.get(f"Community.Event[{n}].ActionState")
                if blank(title) or blank(state):
                    continue
                acc.participation[(resident_id, slugify(title))] = {
                    "residentId": resident_id,
                    "eventKey": slugify(title),
                    "actionState": state,
                }

            # -- events -------------------------------------------------------
            day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
            for column, kind, status_col in (
                (
                    "Parking.Activity.CarEntered.Time",
                    "car_entered",
                    "Parking.Activity.CarEntered.Status",
                ),
                (
                    "Parking.Activity.GuestParking.Time",
                    "guest_parking",
                    "Parking.Activity.GuestParking.Status",
                ),
                (
                    "Parking.Activity.VehicleExit.Time",
                    "vehicle_exit",
                    "Parking.Activity.VehicleExit.Status",
                ),
            ):
                clock = row.get(column)
                if blank(clock):
                    continue
                try:
                    hh, mm, ss = (int(part) for part in str(clock).split(":"))
                except ValueError:
                    continue
                event_at = day + timedelta(hours=hh, minutes=mm, seconds=ss)
                entry = {
                    "residentId": resident_id,
                    "type": kind,
                    "eventAt": event_at,
                    "status": row.get(status_col),
                }
                if kind == "guest_parking":
                    entry["guestName"] = row.get("Parking.Activity.GuestParking.GuestName")
                    entry["guestCount"] = as_int(row.get("Parking.Activity.GuestParking.GuestCount"))
                acc.activity[(resident_id, kind, event_at)] = entry

            # -- per-resident time-series --------------------------------------
            acc.slot_status[(resident_id, bucket)] = {
                "residentId": resident_id,
                "timestamp": bucket,
                "slotCode": row["Parking.MyParking.SlotCode"],
                "isActive": bool(as_bool(row["Parking.MyParking.IsActive"])),
            }

            # -- building-wide series, merged across files ---------------------
            acc.weather[bucket].append(
                {
                    "source": resident_id,
                    "temperatureC": as_float(row["Home.Weather.TemperatureC"]),
                    "condition": row["Home.Weather.Condition"],
                }
            )
            acc.occupancy[bucket].append(
                {
                    "source": resident_id,
                    "availableSpaces": as_int(row["Parking.Capacity.AvailableSpaces"]),
                    "totalSpaces": as_int(row["Parking.Capacity.TotalSpaces"]),
                    "guestUsedSpaces": as_int(row["Parking.GuestParking.UsedSpaces"]),
                    "guestTotalSpaces": as_int(row["Parking.GuestParking.TotalSpaces"]),
                    "evUsedChargers": as_int(row["Parking.EVCharging.UsedChargers"]),
                    "evTotalChargers": as_int(row["Parking.EVCharging.TotalChargers"]),
                }
            )

    return resident_id, display_name, rows


def merge_weather(acc: Accumulator) -> list[dict]:
    documents = []
    for bucket, samples in acc.weather.items():
        temps = [s["temperatureC"] for s in samples if s["temperatureC"] is not None]
        conditions = [s["condition"] for s in samples if not blank(s["condition"])]
        documents.append(
            {
                "timestamp": bucket,
                "temperatureC": round(mean(temps), 1) if temps else None,
                # Ties fall to the first feed read, which is deterministic.
                "condition": max(set(conditions), key=conditions.count) if conditions else None,
                "sources": sorted({s["source"] for s in samples}),
            }
        )
    return documents


def merge_occupancy(acc: Accumulator) -> list[dict]:
    fields = (
        "availableSpaces",
        "totalSpaces",
        "guestUsedSpaces",
        "guestTotalSpaces",
        "evUsedChargers",
        "evTotalChargers",
    )
    documents = []

    for bucket, samples in acc.occupancy.items():
        merged: dict = {"timestamp": bucket, "sources": sorted({s["source"] for s in samples})}
        for name in fields:
            values = [s[name] for s in samples if s[name] is not None]
            merged[name] = round(mean(values)) if values else None

        # Recompute rather than averaging the two files' percentages, so the
        # stored percent always agrees with the stored counts.
        total, available = merged.get("totalSpaces"), merged.get("availableSpaces")
        merged["occupancyPercent"] = round((total - available) / total * 100, 1) if total else None
        documents.append(merged)

    return documents


def bulk_upsert(collection_name: str, documents: list[dict], key_fields: tuple[str, ...]) -> int:
    if not documents:
        return 0

    now = datetime.now()
    operations = [
        UpdateOne(
            {field: document[field] for field in key_fields},
            {"$set": {**document, "updatedAt": now}, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )
        for document in documents
    ]
    result = get_users_db()[collection_name].bulk_write(operations, ordered=False)
    return result.upserted_count + result.modified_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--files", nargs="*", type=Path, default=None)
    parser.add_argument("--drop", action="store_true", help="empty every collection first")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    files = [root / f for f in (args.files or DEFAULT_FILES)]

    for path in files:
        if not path.exists():
            raise SystemExit(f"File not found: {path}")

    connect()
    db = get_users_db()
    print(f'[import] database "{settings.mongodb_users_db}"')

    if args.drop:
        for resource in RESOURCES:
            db[resource.collection].drop()
        print(f"[import] dropped {len(RESOURCES)} collections")

    ensure_all_indexes()

    acc = Accumulator()
    for path in files:
        resident_id, display_name, rows = read_file(path, acc)
        print(f"  read {path.name:<38} {rows:>6} rows  -> residentId {resident_id} ({display_name})")

    polls = (
        [
            {
                "key": "resident-priorities",
                "title": "What should the community invest in next?",
                "options": [
                    {"name": name, "percent": round(mean(values), 1)}
                    for (poll_key, name), values in acc.poll_percent.items()
                ],
            }
        ]
        if acc.poll_percent
        else []
    )

    plan = [
        ("residents", list(acc.residents.values()), ("residentId",)),
        ("units", list(acc.units.values()), ("unitCode",)),
        ("vehicles", list(acc.vehicles.values()), ("plate",)),
        ("parking_slots", list(acc.slots.values()), ("slotCode",)),
        ("maintenance_requests", list(acc.maintenance.values()), ("requestId",)),
        (
            "visitor_passes",
            list(acc.visitor_passes.values()),
            ("residentId", "name", "visitDate", "visitTime"),
        ),
        (
            "visitor_pass_requests",
            list(acc.visitor_requests.values()),
            ("residentId", "mobileNumber", "visitDate"),
        ),
        ("parking_guest_requests", list(acc.guest_requests.values()), ("residentId", "date", "startTime")),
        ("community_announcements", list(acc.announcements.values()), ("key",)),
        ("community_events", list(acc.events.values()), ("key",)),
        ("community_alerts", list(acc.alerts.values()), ("key",)),
        (
            "community_event_participation",
            list(acc.participation.values()),
            ("residentId", "eventKey"),
        ),
        ("community_polls", polls, ("key",)),
        ("community_poll_votes", list(acc.poll_votes.values()), ("residentId", "pollKey")),
        ("parking_activity", list(acc.activity.values()), ("residentId", "type", "eventAt")),
        ("parking_slot_status", list(acc.slot_status.values()), ("residentId", "timestamp")),
        ("weather", merge_weather(acc), ("timestamp",)),
        ("parking_occupancy", merge_occupancy(acc), ("timestamp",)),
    ]

    print()
    total = 0
    for collection_name, documents, key_fields in plan:
        written = bulk_upsert(collection_name, documents, key_fields)
        total += len(documents)
        print(f"  {collection_name:<34} {len(documents):>6} documents ({written} written)")

    print(f"\n[import] done: {total} documents across {len(plan)} collections")
    close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
