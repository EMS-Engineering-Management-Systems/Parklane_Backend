"""MongoDB connection handling.

Synchronous PyMongo. FastAPI runs every `def` (non-async) path operation in a
worker threadpool, so blocking driver calls do not stall the event loop - the
routers in this project are deliberately plain `def` for that reason.
"""

from __future__ import annotations

import logging
from urllib.parse import quote_plus

from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import settings

logger = logging.getLogger("parklane")

_client: MongoClient | None = None
_db: Database | None = None
_users_db: Database | None = None


def build_uri() -> str:
    """Substitutes a literal `<password>` placeholder so the secret can live in
    its own variable rather than inside the connection string."""
    uri = settings.mongodb_uri
    if "<password>" not in uri:
        return uri
    if not settings.mongodb_password:
        raise RuntimeError("MONGODB_URI contains <password> but MONGODB_PASSWORD is not set.")
    return uri.replace("<password>", quote_plus(settings.mongodb_password))


def connect() -> Database:
    """Opens the connection and prepares the fixed indexes. Idempotent.

    One client serves both databases: `Atal` for BMS readings and `Atal_Users`
    for resident and community data.
    """
    global _client, _db, _users_db

    if _db is not None:
        return _db

    _client = MongoClient(
        build_uri(),
        serverSelectionTimeoutMS=10_000,
        retryWrites=True,
        tz_aware=True,
    )
    _db = _client[settings.mongodb_db]

    # Fail fast with a readable message rather than on the first query.
    _db.command("ping")

    # One registration per device, whatever route created it.
    _db["devices"].create_index("deviceId", unique=True, name="deviceId_unique")

    _users_db = _client[settings.mongodb_users_db]

    logger.info('connected to "%s" and "%s"', settings.mongodb_db, settings.mongodb_users_db)
    return _db


def get_db() -> Database:
    """The BMS database (`Atal`)."""
    if _db is None:
        raise RuntimeError("Database not initialised. Call connect() first.")
    return _db


def get_users_db() -> Database:
    """The resident / community database (`Atal_Users`)."""
    if _users_db is None:
        raise RuntimeError("Database not initialised. Call connect() first.")
    return _users_db


def is_connected() -> bool:
    return _db is not None


def close() -> None:
    global _client, _db, _users_db
    if _client is not None:
        _client.close()
    _client = None
    _db = None
    _users_db = None
