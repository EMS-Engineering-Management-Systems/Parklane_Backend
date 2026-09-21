"""Resident and community CRUD, over the `Atal_Users` database.

Every collection gets the same six operations. They are generated from the
`Resource` registry rather than written out sixteen times, so the contract
cannot drift between one collection and the next - and each still appears in
`/docs` with its own models and examples.
"""

from typing import Annotated

from fastapi import APIRouter, Body, Path, Request, status
from pymongo import ASCENDING, DESCENDING

from app.core.config import settings
from app.core.errors import ApiError
from app.models.users import (
    ResourceCreateResponse,
    ResourceDeleteResponse,
    ResourceIndexResponse,
    ResourceListResponse,
    ResourceResponse,
)
from app.services import resources as svc
from app.services.resources import RESOURCES, Resource

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "",
    response_model=ResourceIndexResponse,
    summary="Every resource in Atal_Users, with its document count",
)
def index():
    data = svc.overview()
    return {"success": True, "database": settings.mongodb_users_db, "total": len(data), "data": data}


def _pagination(request: Request) -> tuple[int, int, list[tuple[str, int]]]:
    params = request.query_params

    page = max(1, int(params.get("page") or 1))
    limit = int(params.get("limit") or settings.default_page_size)
    if limit <= 0:
        limit = settings.default_page_size
    if limit > settings.max_page_size:
        raise ApiError.bad_request(
            f"limit cannot exceed {settings.max_page_size}",
            {"limit": limit, "maxLimit": settings.max_page_size},
        )
    return page, limit, _sort(request)


def _sort(request: Request) -> list[tuple[str, int]] | None:
    field = request.query_params.get("sort")
    if not field:
        return None  # the resource's own default is used
    order = ASCENDING if (request.query_params.get("order") or "asc").lower() == "asc" else DESCENDING
    return [(field, order)]


def _filters(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.query_params.items() if k not in svc.CONTROL_PARAMS}


def register(resource: Resource) -> None:
    """Builds the six routes for one resource and attaches them to the router."""

    base = f"/{resource.slug}"
    identifier_desc = f"{resource.key} or the ObjectId" if resource.key else "the document ObjectId"
    Identifier = Annotated[str, Path(description=identifier_desc)]
    Model = resource.model
    PatchModel = resource.patch_model

    filter_help = ", ".join(resource.filterable) or "none"
    search_help = (
        f" Substring search with ?q= over {', '.join(resource.searchable)}." if resource.searchable else ""
    )
    window_help = f" Time window with ?from= / ?to= on {resource.time_field}." if resource.time_field else ""

    @router.get(
        base,
        response_model=ResourceListResponse,
        tags=[resource.tag],
        summary=f"List {resource.slug}",
        description=(
            f"{resource.summary}\n\n"
            f"Filters: {filter_help}.{search_help}{window_help}\n\n"
            f"Controls: page, limit, sort, order."
        ),
        name=f"list_{resource.collection}",
    )
    def list_(request: Request):
        page, limit, sort = _pagination(request)
        return {
            "success": True,
            **svc.list_documents(resource, _filters(request), page, limit, sort or list(resource.sort)),
        }

    @router.post(
        base,
        response_model=ResourceCreateResponse,
        status_code=status.HTTP_201_CREATED,
        tags=[resource.tag],
        summary=f"Create {resource.slug}",
        description="Accepts a single object or an array.",
        name=f"create_{resource.collection}",
    )
    def create_(payload: Annotated[Model | list[Model], Body()]):
        return {"success": True, **svc.create(resource, payload)}

    @router.delete(
        base,
        response_model=ResourceDeleteResponse,
        tags=[resource.tag],
        summary=f"Delete many {resource.slug}",
        description="Refuses to run without a filter unless confirm=true is passed.",
        name=f"delete_many_{resource.collection}",
    )
    def delete_many_(request: Request):
        return {"success": True, **svc.delete_many(resource, dict(request.query_params))}

    @router.get(
        base + "/{identifier}",
        response_model=ResourceResponse,
        tags=[resource.tag],
        summary=f"Read one {resource.slug}",
        name=f"get_{resource.collection}",
    )
    def get_(identifier: Identifier):
        return {"success": True, **svc.get_one(resource, identifier)}

    @router.put(
        base + "/{identifier}",
        response_model=ResourceResponse,
        tags=[resource.tag],
        summary=f"Replace one {resource.slug}",
        name=f"replace_{resource.collection}",
    )
    def replace_(
        identifier: Identifier,
        payload: Annotated[Model, Body()],
    ):
        return {"success": True, **svc.replace(resource, identifier, payload)}

    @router.patch(
        base + "/{identifier}",
        response_model=ResourceResponse,
        tags=[resource.tag],
        summary=f"Update one {resource.slug}",
        description="Only the supplied fields change.",
        name=f"update_{resource.collection}",
    )
    def update_(
        identifier: Identifier,
        payload: Annotated[PatchModel, Body()],
    ):
        return {"success": True, **svc.update(resource, identifier, payload)}

    @router.delete(
        base + "/{identifier}",
        response_model=ResourceDeleteResponse,
        tags=[resource.tag],
        summary=f"Delete one {resource.slug}",
        name=f"delete_{resource.collection}",
    )
    def delete_(identifier: Identifier):
        return {"success": True, **svc.delete_one(resource, identifier)}


for _resource in RESOURCES:
    register(_resource)
