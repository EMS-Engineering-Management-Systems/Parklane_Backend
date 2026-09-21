"""Raw view of the Atal database."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import ApiError
from app.models.responses import CollectionListResponse, DropResponse
from app.services.naming import parse_collection_name

router = APIRouter(prefix="/collections", tags=["collections"])


@router.get("", response_model=CollectionListResponse, summary="Every collection with its document count")
def list_collections(
    deviceType: Annotated[str | None, Query(description="Only collections for this device type")] = None,
):
    db = get_db()
    prefix = f"{deviceType.lower()}_" if deviceType else None

    data: list[dict[str, Any]] = []
    for name in sorted(db.list_collection_names()):
        if prefix and not name.startswith(prefix):
            continue
        data.append(
            {
                "name": name,
                **(parse_collection_name(name) or {}),
                "documents": db[name].count_documents({}),
            }
        )

    return {"success": True, "database": settings.mongodb_db, "total": len(data), "data": data}


@router.delete(
    "/{name}",
    response_model=DropResponse,
    summary="Drop a collection",
    description="Irreversible, so confirm=true is required.",
)
def drop_collection(
    name: Annotated[str, Path(description="Collection name, e.g. transformer_1_instant")],
    confirm: Annotated[bool, Query(description="Must be true")] = False,
):
    db = get_db()

    if name not in db.list_collection_names(filter={"name": name}):
        raise ApiError.not_found(f'collection "{name}" does not exist')
    if not confirm:
        raise ApiError.bad_request(
            "dropping a collection is irreversible; pass confirm=true", {"collection": name}
        )

    db[name].drop()
    return {"success": True, "dropped": name}
