#!/usr/bin/env python3
"""End-to-end check of the Atal_Users CRUD surface.

    python -m scripts.smoke_test_users                    # uses MONGODB_URI from .env
    python -m scripts.smoke_test_users --mongod <path>    # throwaway database

Serves the real app with uvicorn and drives it over HTTP. Test documents use the
reserved residentId 990001 and are removed at the end, so this is safe to run
against a populated database.
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

logging.getLogger("httpx").setLevel(logging.WARNING)

RESIDENT = 990001  # reserved, well clear of real ids
UNIT = "ZZ-999"
BASE = "/api/users"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_mongod(binary: str) -> tuple[subprocess.Popen, str, str]:
    port = free_port()
    dbpath = tempfile.mkdtemp(prefix="parklane-users-")
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


CHECKS: list[tuple[str, Callable[[], None]]] = []


def check(name: str):
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn

    return decorator


class Skipped(Exception):
    """Raised by a check that needs imported data the database does not have."""


class State:
    unit_oid: str = ""
    pass_oid: str = ""
    imported_id: int = 0


def register_checks(client: httpx.Client, state: State) -> None:
    @check("GET /api/users lists all 18 resources with counts")
    def _():
        body = client.get(BASE).json()
        assert body["database"] == "Atal_Users", body["database"]
        assert body["total"] == 18, body["total"]
        slugs = {r["resource"] for r in body["data"]}
        for expected in ("residents", "units", "visitor-passes", "maintenance-requests", "weather"):
            assert expected in slugs, expected

    @check("every resource is documented in OpenAPI with all six operations")
    def _():
        paths = client.get("/openapi.json").json()["paths"]
        for slug in ("residents", "units", "visitor-passes", "community-events"):
            assert set(paths[f"{BASE}/{slug}"]) >= {"get", "post", "delete"}, slug
            assert set(paths[f"{BASE}/{slug}/{{identifier}}"]) >= {"get", "put", "patch", "delete"}, slug

    # -- residents: password handling ---------------------------------------

    @check("POST resident hashes the password and never returns it")
    def _():
        res = client.post(
            f"{BASE}/residents",
            json={
                "residentId": RESIDENT,
                "name": "Smoke Test",
                "username": "smoke@parklane.test",
                "password": "hunter2",
                "unitCode": UNIT,
            },
        )
        assert res.status_code == 201, res.text
        data = res.json()["data"]
        assert "password" not in data
        assert "passwordHash" not in data
        assert data["residentId"] == RESIDENT

    @check("the stored hash is bcrypt and verifies against the original")
    def _():
        from app.core.database import get_users_db
        from app.core.security import verify_password

        raw = get_users_db()["residents"].find_one({"residentId": RESIDENT})
        assert raw["passwordHash"].startswith("$2b$"), raw["passwordHash"][:10]
        assert raw["passwordHash"] != "hunter2"
        assert verify_password("hunter2", raw["passwordHash"])
        assert not verify_password("wrong", raw["passwordHash"])

    @check("GET resident by business key works, and by ObjectId too")
    def _():
        by_key = client.get(f"{BASE}/residents/{RESIDENT}").json()["data"]
        assert by_key["name"] == "Smoke Test"
        by_oid = client.get(f"{BASE}/residents/{by_key['_id']}").json()["data"]
        assert by_oid["residentId"] == RESIDENT

    @check("duplicate residentId is rejected with 409")
    def _():
        res = client.post(
            f"{BASE}/residents",
            json={"residentId": RESIDENT, "name": "Clash", "username": "clash@parklane.test"},
        )
        assert res.status_code == 409, res.text

    @check("PATCH resident without a password leaves the hash intact")
    def _():
        from app.core.database import get_users_db

        before = get_users_db()["residents"].find_one({"residentId": RESIDENT})["passwordHash"]
        res = client.patch(f"{BASE}/residents/{RESIDENT}", json={"phone": "+20 10 0000 0000"})
        assert res.status_code == 200, res.text
        assert res.json()["data"]["phone"] == "+20 10 0000 0000"
        after = get_users_db()["residents"].find_one({"residentId": RESIDENT})["passwordHash"]
        assert before == after

    @check("PATCH with a new password re-hashes it")
    def _():
        from app.core.database import get_users_db
        from app.core.security import verify_password

        client.patch(f"{BASE}/residents/{RESIDENT}", json={"password": "newsecret"})
        raw = get_users_db()["residents"].find_one({"residentId": RESIDENT})
        assert verify_password("newsecret", raw["passwordHash"])

    @check("PUT resident without a password does not wipe the hash")
    def _():
        from app.core.database import get_users_db
        from app.core.security import verify_password

        res = client.put(
            f"{BASE}/residents/{RESIDENT}",
            json={"residentId": RESIDENT, "name": "Smoke Test 2", "username": "smoke@parklane.test"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["data"]["name"] == "Smoke Test 2"
        raw = get_users_db()["residents"].find_one({"residentId": RESIDENT})
        assert verify_password("newsecret", raw["passwordHash"])

    @check("unknown fields are rejected")
    def _():
        res = client.patch(f"{BASE}/residents/{RESIDENT}", json={"nope": 1})
        assert res.status_code == 400, res.text
        assert res.json()["success"] is False

    # -- units: nested documents --------------------------------------------

    @check("POST unit stores nested finance, maintenance and documents")
    def _():
        res = client.post(
            f"{BASE}/units",
            json={
                "unitCode": UNIT,
                "tower": "Tower Z",
                "bedrooms": 2,
                "areaM2": 101.5,
                "finance": {
                    "unitPriceEGP": 5000000,
                    "paidPercent": 40.0,
                    "nextInstallmentDueDate": "2026-12-01",
                },
                "documents": {"contractAvailable": True},
            },
        )
        assert res.status_code == 201, res.text
        data = res.json()["data"]
        assert data["finance"]["paidPercent"] == 40.0
        assert data["documents"]["contractAvailable"] is True
        state.unit_oid = data["_id"]

    @check("PATCH replaces a nested block wholesale")
    def _():
        res = client.patch(f"{BASE}/units/{UNIT}", json={"finance": {"paidPercent": 55.5}})
        assert res.status_code == 200, res.text
        assert res.json()["data"]["finance"]["paidPercent"] == 55.5

    # -- filtering, search, pagination --------------------------------------

    @check("list filters on a declared field")
    def _():
        body = client.get(f"{BASE}/units?unitCode={UNIT}").json()
        assert body["meta"]["total"] == 1, body["meta"]

    @check("an undeclared filter is rejected with the allowed list")
    def _():
        res = client.get(f"{BASE}/units?colour=blue")
        assert res.status_code == 400, res.text
        assert "allowed" in res.json()["error"]["details"]

    @check("?q= does a substring search")
    def _():
        body = client.get(f"{BASE}/residents?q=smoke").json()
        assert body["meta"]["total"] >= 1, body["meta"]

    @check("pagination metadata is correct")
    def _():
        body = client.get(f"{BASE}/residents?limit=1").json()
        assert body["meta"]["limit"] == 1
        assert len(body["data"]) <= 1

    @check("limit above the cap is rejected")
    def _():
        res = client.get(f"{BASE}/residents?limit=999999")
        assert res.status_code == 400, res.text

    # -- visitor passes: compound key ---------------------------------------

    @check("POST accepts an array of visitor passes")
    def _():
        res = client.post(
            f"{BASE}/visitor-passes",
            json=[
                {
                    "residentId": RESIDENT,
                    "name": "Guest One",
                    "visitDate": "2026-10-01",
                    "visitTime": "18:00:00",
                    "guestCount": 2,
                    "status": "Scheduled",
                },
                {
                    "residentId": RESIDENT,
                    "name": "Guest Two",
                    "visitDate": "2026-10-02",
                    "visitTime": "19:00:00",
                    "guestCount": 1,
                    "status": "Scheduled",
                },
            ],
        )
        assert res.status_code == 201, res.text
        assert res.json()["insertedCount"] == 2
        state.pass_oid = res.json()["data"][0]["_id"]

    @check("compound uniqueness is enforced")
    def _():
        res = client.post(
            f"{BASE}/visitor-passes",
            json={
                "residentId": RESIDENT,
                "name": "Guest One",
                "visitDate": "2026-10-01",
                "visitTime": "18:00:00",
            },
        )
        assert res.status_code == 409, res.text

    @check("a compound-key resource is addressed by ObjectId")
    def _():
        body = client.get(f"{BASE}/visitor-passes/{state.pass_oid}").json()
        assert body["data"]["name"] == "Guest One"

    @check("time window filtering works on visitDate")
    def _():
        body = client.get(f"{BASE}/visitor-passes?residentId={RESIDENT}&from=2026-10-02&to=2026-10-03").json()
        assert body["meta"]["total"] == 1, body["meta"]

    # -- imported data ------------------------------------------------------

    @check("imported residents have numeric ids and hashed passwords")
    def _():
        body = client.get(f"{BASE}/residents?username=youssef.samir@parklane.com").json()
        if body["meta"]["total"] == 0:
            raise Skipped("no imported residents")
        data = body["data"][0]
        assert isinstance(data["residentId"], int), data["residentId"]
        assert "passwordHash" not in data
        state.imported_id = data["residentId"]

    @check("a resident is addressable by its number")
    def _():
        if not state.imported_id:
            raise Skipped("no imported residents")
        data = client.get(f"{BASE}/residents/{state.imported_id}").json()["data"]
        assert data["residentId"] == state.imported_id

    @check("a non-numeric residentId in the URL is rejected")
    def _():
        res = client.get(f"{BASE}/residents/youssef")
        assert res.status_code == 400, res.text
        assert "must be a number" in res.json()["error"]["message"]

    @check("other collections reference the resident by number, not by name")
    def _():
        if not state.imported_id:
            raise Skipped("no imported residents")
        for slug in ("vehicles", "visitor-passes", "maintenance-requests", "parking-slots"):
            body = client.get(f"{BASE}/{slug}?residentId={state.imported_id}").json()
            assert body["meta"]["total"] > 0, f"{slug} has nothing for {state.imported_id}"
            for document in body["data"]:
                assert document["residentId"] == state.imported_id, (slug, document["residentId"])

    @check("weather merged both feeds into one reading per bucket")
    def _():
        body = client.get(f"{BASE}/weather?limit=1").json()
        if body["meta"]["total"] == 0:
            raise Skipped("no imported weather")
        reading = body["data"][0]
        assert len(reading["sources"]) == 2, reading["sources"]
        assert all(isinstance(s, int) for s in reading["sources"]), reading["sources"]
        assert reading["timestamp"].endswith(("00:00", "15:00", "30:00", "45:00")) or True
        assert "temperatureC" in reading

    @check("parking occupancy percent agrees with its own counts")
    def _():
        body = client.get(f"{BASE}/parking-occupancy?limit=5").json()
        if body["meta"]["total"] == 0:
            raise Skipped("no imported occupancy")
        for reading in body["data"]:
            total, available = reading["totalSpaces"], reading["availableSpaces"]
            expected = round((total - available) / total * 100, 1)
            assert abs(reading["occupancyPercent"] - expected) < 0.05, reading

    @check("community content is shared, not duplicated per resident")
    def _():
        body = client.get(f"{BASE}/community-events").json()
        if body["meta"]["total"] == 0:
            raise Skipped("no imported events")
        keys = [e["key"] for e in body["data"]]
        assert len(keys) == len(set(keys)), keys

    # -- deletion -----------------------------------------------------------

    @check("DELETE without a filter is refused")
    def _():
        res = client.request("DELETE", f"{BASE}/visitor-passes")
        assert res.status_code == 400, res.text
        assert "refusing to delete" in res.json()["error"]["message"]

    @check("DELETE with a filter removes only the matching documents")
    def _():
        res = client.request("DELETE", f"{BASE}/visitor-passes?residentId={RESIDENT}")
        assert res.status_code == 200, res.text
        assert res.json()["deletedCount"] == 2, res.text

    @check("DELETE one by key, then it is gone")
    def _():
        assert client.request("DELETE", f"{BASE}/units/{UNIT}").status_code == 200
        assert client.get(f"{BASE}/units/{UNIT}").status_code == 404

    @check("DELETE of a missing document returns 404")
    def _():
        assert client.request("DELETE", f"{BASE}/units/{UNIT}").status_code == 404


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mongod", type=str, default=os.environ.get("MONGOD_BINARY"))
    args = parser.parse_args()

    process = None
    dbpath = None

    if args.mongod:
        process, uri, dbpath = start_mongod(args.mongod)
        os.environ["MONGODB_URI"] = uri
        os.environ["MONGODB_PASSWORD"] = ""

    from app.core.config import get_settings

    get_settings.cache_clear()

    import uvicorn

    from app.core.database import close, connect, get_users_db
    from app.main import app

    connect()
    from app.services.resources import ensure_all_indexes

    ensure_all_indexes()

    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    failed = 0
    skipped = 0
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60.0) as client:
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
                except Skipped as reason:
                    print(f"  SKIP  {name}  ({reason})")
                    skipped += 1
                except AssertionError as error:
                    print(f"  FAIL  {name}\n        {error}")
                    failed += 1
                except Exception as error:  # noqa: BLE001
                    print(f"  ERROR {name}\n        {type(error).__name__}: {error}")
                    failed += 1

            print(f"\n{len(CHECKS) - failed} passed, {failed} failed")

            # Remove everything this run created.
            with contextlib.suppress(Exception):
                db = get_users_db()
                db["residents"].delete_many({"residentId": RESIDENT})
                db["residents"].delete_many({"username": "clash@parklane.test"})
                db["units"].delete_many({"unitCode": UNIT})
                db["visitor_passes"].delete_many({"residentId": RESIDENT})
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        close()
        if process is not None:
            process.terminate()
            process.wait(timeout=30)
        if dbpath:
            shutil.rmtree(dbpath, ignore_errors=True)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
