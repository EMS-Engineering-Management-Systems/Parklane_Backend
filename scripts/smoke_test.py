#!/usr/bin/env python3
"""End-to-end check of every CRUD route against a real MongoDB.

    python -m scripts.smoke_test                    # uses MONGODB_URI from .env
    python -m scripts.smoke_test --mongod <path>    # starts a throwaway mongod

It serves the real app with uvicorn and drives it over HTTP, so a green run
means create / read / update / delete / rollup all work end to end.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

# httpx logs every request at INFO; the PASS/FAIL lines are the output that matters.
logging.getLogger("httpx").setLevel(logging.WARNING)

DEVICE = "Transformer"
INDEX = 991  # out of the way of real data
BASE = f"/api/readings/{DEVICE}/{INDEX}"


# ---------------------------------------------------------------------------
# Optional throwaway mongod
# ---------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_mongod(binary: str) -> tuple[subprocess.Popen, str, str]:
    port = free_port()
    dbpath = tempfile.mkdtemp(prefix="parklane-mongo-")

    process = subprocess.Popen(
        [binary, "--dbpath", dbpath, "--port", str(port), "--bind_ip", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(120):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    else:
        process.kill()
        raise SystemExit("mongod did not start in time")

    return process, f"mongodb://127.0.0.1:{port}", dbpath


# ---------------------------------------------------------------------------
# Tiny test harness
# ---------------------------------------------------------------------------

CHECKS: list[tuple[str, Callable[[], None]]] = []


def check(name: str):
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn

    return decorator


class State:
    """Carries the id created by one check into the ones that follow."""

    reading_id: str = ""


def register_checks(client: httpx.Client, state: State) -> None:
    @check("GET /api/health reports the Atal database")
    def _():
        res = client.get("/api/health")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["database"]["name"] == "Atal"
        assert body["database"]["connected"] is True

    @check("GET /api/devices/catalogue lists all 28 device types")
    def _():
        body = client.get("/api/devices/catalogue").json()
        assert body["deviceCount"] == 28, body["deviceCount"]
        assert body["tagCount"] == 174, body["tagCount"]
        assert any(d["type"] == "Transformer" for d in body["data"])

    @check("OpenAPI schema is generated and every route is documented")
    def _():
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/api/readings/{device_type}/{index}/{granularity}" in paths
        assert "/api/readings/{device_type}/{index}/rollup/{granularity}" in paths
        assert set(paths["/api/readings/{device_type}/{index}/{granularity}"]) >= {
            "get",
            "post",
            "delete",
        }

    @check("GET on a missing collection returns 404 with a hint")
    def _():
        res = client.get(f"{BASE}/instant")
        assert res.status_code == 404, res.text
        assert "does not exist yet" in res.json()["error"]["message"]

    @check("POST creates the collection and the reading")
    def _():
        res = client.post(
            f"{BASE}/instant",
            json={
                "timestamp": "2026-03-15T10:00:00Z",
                "tags": {
                    "Voltage": 10476,
                    "Current": 912,
                    "Frequency": 50,
                    "Temperature": 37,
                    "Trip_Status": 0,
                },
            },
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["collection"] == f"transformer_{INDEX}_instant"
        assert body["insertedCount"] == 1
        assert body["warnings"] == []
        state.reading_id = body["data"]["_id"]
        assert state.reading_id

    @check("POST auto-registers the device")
    def _():
        body = client.get(f"/api/devices/{DEVICE}/{INDEX}").json()
        assert body["data"]["deviceId"] == f"{DEVICE}_{INDEX}"
        assert body["data"]["category"] == "Electrical"
        assert body["data"]["documentCounts"]["instant"] == 1

    @check("POST accepts an array and fully-qualified tag names")
    def _():
        res = client.post(
            f"{BASE}/instant",
            json=[
                {"timestamp": "2026-03-15T10:01:00Z", "tags": {"Transformer Voltage": 11000, "Current": 800}},
                {"timestamp": "2026-04-15T10:02:00Z", "tags": {"Transformer_Voltage": 9000, "Current": 850}},
            ],
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["insertedCount"] == 2
        assert body["data"][0]["tags"]["Voltage"] == 11000

    @check("POST accepts a flat BMS-export row")
    def _():
        res = client.post(
            f"{BASE}/instant",
            json={"Timestamp": "2026-03-15T10:05:00Z", "Voltage": 10400, "Current": 880},
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["data"]["tags"] == {"Voltage": 10400, "Current": 880}
        assert body["data"]["timestamp"].startswith("2026-03-15T10:05")
        client.delete(f"{BASE}/instant/{body['data']['_id']}")

    @check("POST warns about unknown and out-of-range tags but still stores them")
    def _():
        res = client.post(
            f"{BASE}/instant",
            json={"timestamp": "2026-04-15T11:00:00Z", "tags": {"Voltage": 99999, "Not_A_Real_Tag": 1}},
        )
        assert res.status_code == 201, res.text
        codes = {w["code"] for w in res.json()["warnings"][0]["warnings"]}
        assert "UNKNOWN_TAGS" in codes
        assert "OUT_OF_RANGE" in codes

    @check("POST rejects a body with no tags")
    def _():
        res = client.post(f"{BASE}/instant", json={"timestamp": "2026-03-15T10:00:00Z", "tags": {}})
        assert res.status_code == 400, res.text

    @check("POST rejects an unknown granularity")
    def _():
        res = client.post(f"{BASE}/hourly", json={"tags": {"Voltage": 1}})
        assert res.status_code == 400, res.text
        assert "granularity must be one of" in res.json()["error"]["message"]

    @check("validation errors come back as 400 in the standard envelope")
    def _():
        res = client.patch(f"{BASE}/instant/{state.reading_id}", json={"deviceId": "Hacked_1"})
        assert res.status_code == 400, res.text
        body = res.json()
        assert body["success"] is False
        assert body["error"]["status"] == 400

    @check("device type casing is canonicalised to one deviceId")
    def _():
        res = client.post(
            f"/api/readings/{DEVICE.lower()}/{INDEX}/instant",
            json={"timestamp": "2026-03-15T10:03:00Z", "tags": {"Voltage": 10000}},
        )
        assert res.status_code == 201, res.text
        assert res.json()["data"]["deviceId"] == f"{DEVICE}_{INDEX}"

        devices = client.get(f"/api/devices?deviceType={DEVICE}").json()["data"]
        assert len([d for d in devices if d["index"] == INDEX]) == 1

        client.delete(f"{BASE}/instant/{res.json()['data']['_id']}")

    @check("GET lists readings with pagination metadata")
    def _():
        body = client.get(f"{BASE}/instant?limit=2&sort=timestamp&order=asc").json()
        assert len(body["data"]) == 2
        assert body["meta"]["total"] == 4, body["meta"]
        assert body["meta"]["pages"] == 2
        assert body["meta"]["hasNext"] is True

    @check("GET filters by time window")
    def _():
        body = client.get(f"{BASE}/instant?from=2026-04-01&to=2026-05-01").json()
        assert body["meta"]["total"] == 2, body["meta"]

    @check("GET filters by tag comparison")
    def _():
        body = client.get(f"{BASE}/instant?tags.Voltage[gte]=11000").json()
        assert body["meta"]["total"] == 2, body["meta"]  # 11000 and 99999

    @check("GET supports field projection")
    def _():
        body = client.get(f"{BASE}/instant?fields=timestamp,tags.Voltage&limit=1").json()
        assert "Voltage" in body["data"][0]["tags"]
        assert "Current" not in body["data"][0]["tags"]

    @check("GET /latest returns the newest reading")
    def _():
        body = client.get(f"{BASE}/instant/latest").json()
        assert body["data"]["timestamp"].startswith("2026-04-15T11:00")

    @check("GET /{id} returns one reading")
    def _():
        body = client.get(f"{BASE}/instant/{state.reading_id}").json()
        assert body["data"]["tags"]["Voltage"] == 10476

    @check("GET /{id} with a malformed id returns 400")
    def _():
        res = client.get(f"{BASE}/instant/not-an-id")
        assert res.status_code == 400, res.text

    @check("GET /stats summarises every numeric tag")
    def _():
        body = client.get(f"{BASE}/instant/stats").json()
        assert body["count"] == 4, body["count"]
        assert body["tags"]["Voltage"]["min"] == 9000
        assert body["tags"]["Voltage"]["max"] == 99999

    @check("PATCH updates a single tag and leaves the rest")
    def _():
        res = client.patch(f"{BASE}/instant/{state.reading_id}", json={"tags": {"Voltage": 10500}})
        assert res.status_code == 200, res.text
        tags = res.json()["data"]["tags"]
        assert tags["Voltage"] == 10500
        assert tags["Current"] == 912

    @check("PUT replaces the whole reading")
    def _():
        res = client.put(
            f"{BASE}/instant/{state.reading_id}",
            json={"timestamp": "2026-03-15T10:00:00Z", "tags": {"Voltage": 12000}},
        )
        assert res.status_code == 200, res.text
        tags = res.json()["data"]["tags"]
        assert tags["Voltage"] == 12000
        assert "Current" not in tags

    @check("POST /upsert is idempotent on timestamp")
    def _():
        payload = {"timestamp": "2026-05-01T00:00:00Z", "tags": {"Voltage": 10000}}
        first = client.post(f"{BASE}/instant/upsert", json=payload)
        assert first.status_code == 200, first.text

        second = client.post(
            f"{BASE}/instant/upsert",
            json={"timestamp": "2026-05-01T00:00:00Z", "tags": {"Voltage": 10100}},
        )
        assert second.json()["data"]["_id"] == first.json()["data"]["_id"]
        assert second.json()["data"]["tags"]["Voltage"] == 10100

        listed = client.get(f"{BASE}/instant?from=2026-05-01&to=2026-05-02").json()
        assert listed["meta"]["total"] == 1

    @check("POST /rollup/monthly builds the monthly collection")
    def _():
        res = client.post(f"/api/readings/{DEVICE}/{INDEX}/rollup/monthly")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["destination"] == f"transformer_{INDEX}_monthly"
        assert body["periods"] == 3, body  # March, April, May 2026

        monthly = client.get(f"/api/readings/{DEVICE}/{INDEX}/monthly?year=2026&month=3").json()
        assert monthly["meta"]["total"] == 1
        tags = monthly["data"][0]["tags"]
        assert tags["Voltage"]["count"] == 2
        assert tags["Voltage"]["max"] == 12000
        assert monthly["data"][0]["period"]["month"] == 3

    @check("POST /rollup/yearly builds the yearly collection")
    def _():
        body = client.post(f"/api/readings/{DEVICE}/{INDEX}/rollup/yearly").json()
        assert body["periods"] == 1

        yearly = client.get(f"/api/readings/{DEVICE}/{INDEX}/yearly?year=2026").json()
        assert yearly["meta"]["total"] == 1
        assert yearly["data"][0]["tags"]["Voltage"]["count"] == 5

    @check("rollup is idempotent - a second run updates instead of duplicating")
    def _():
        client.post(f"/api/readings/{DEVICE}/{INDEX}/rollup/monthly")
        monthly = client.get(f"/api/readings/{DEVICE}/{INDEX}/monthly").json()
        assert monthly["meta"]["total"] == 3

    @check("POST /rollup rejects instant as a destination")
    def _():
        res = client.post(f"/api/readings/{DEVICE}/{INDEX}/rollup/instant")
        assert res.status_code == 400, res.text

    @check("GET /api/collections shows the three collections for this device")
    def _():
        body = client.get(f"/api/collections?deviceType={DEVICE}").json()
        names = {c["name"] for c in body["data"]}
        for granularity in ("instant", "monthly", "yearly"):
            assert f"transformer_{INDEX}_{granularity}" in names, granularity

    @check("PATCH /api/devices updates device metadata")
    def _():
        res = client.patch(
            f"/api/devices/{DEVICE}/{INDEX}", json={"location": "Substation B", "status": "maintenance"}
        )
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        assert data["location"] == "Substation B"
        assert data["status"] == "maintenance"

    @check("POST /api/devices rejects a duplicate registration")
    def _():
        res = client.post("/api/devices", json={"deviceType": DEVICE, "index": INDEX})
        assert res.status_code == 409, res.text

    @check("DELETE /{id} removes one reading")
    def _():
        res = client.delete(f"{BASE}/instant/{state.reading_id}")
        assert res.status_code == 200, res.text
        assert client.get(f"{BASE}/instant/{state.reading_id}").status_code == 404

    @check("DELETE without a filter is refused")
    def _():
        res = client.delete(f"{BASE}/instant")
        assert res.status_code == 400, res.text
        assert "refusing to delete" in res.json()["error"]["message"]

    @check("DELETE with a time window removes only that window")
    def _():
        res = client.delete(f"{BASE}/instant?from=2026-04-01&to=2026-04-30T23:59:59Z")
        assert res.status_code == 200, res.text
        assert res.json()["deletedCount"] == 2, res.text
        assert client.get(f"{BASE}/instant").json()["meta"]["total"] == 2

    @check("DELETE /api/devices drops the collections when asked")
    def _():
        res = client.delete(f"/api/devices/{DEVICE}/{INDEX}?dropCollections=true")
        assert res.status_code == 200, res.text
        assert len(res.json()["droppedCollections"]) == 3
        assert client.get(f"{BASE}/instant").status_code == 404

    @check("unknown routes return a 404 envelope")
    def _():
        res = client.get("/api/nope")
        assert res.status_code == 404
        assert res.json()["success"] is False


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mongod",
        type=str,
        default=os.environ.get("MONGOD_BINARY"),
        help="path to a mongod binary; without it MONGODB_URI from .env is used",
    )
    args = parser.parse_args()

    process = None
    dbpath = None

    if args.mongod:
        process, uri, dbpath = start_mongod(args.mongod)
        os.environ["MONGODB_URI"] = uri
        os.environ["MONGODB_PASSWORD"] = ""
        os.environ["MONGODB_DB"] = "Atal"

    # Settings are cached, so clear the cache before the app reads them.
    from app.core.config import get_settings

    get_settings.cache_clear()

    import uvicorn

    from app.core.database import get_db
    from app.main import app

    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    failed = 0
    try:
        base_url = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            for _ in range(120):
                if server.started:
                    break
                time.sleep(0.25)
            else:
                raise SystemExit("server did not start in time")

            state = State()
            register_checks(client, state)

            for name, fn in CHECKS:
                try:
                    fn()
                    print(f"  PASS  {name}")
                except AssertionError as error:
                    print(f"  FAIL  {name}\n        {error}")
                    failed += 1
                except Exception as error:  # noqa: BLE001
                    print(f"  ERROR {name}\n        {type(error).__name__}: {error}")
                    failed += 1

            print(f"\n{len(CHECKS) - failed} passed, {failed} failed")

            # Leave no trace behind when pointed at a real database.
            with contextlib.suppress(Exception):
                get_db()["devices"].delete_many({"index": INDEX})
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        if process is not None:
            process.terminate()
            process.wait(timeout=30)
        if dbpath:
            shutil.rmtree(dbpath, ignore_errors=True)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
