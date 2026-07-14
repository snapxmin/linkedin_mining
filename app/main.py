"""FastAPI application factory and JSON API routes."""

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.analytics import aggregate, export_csv, summary
from app.db import MEMORY_DB_URI, DatabasePath, connect, init_db
from app.imports import import_csv
from app.profiles import list_profiles, update_profile
from app.search import (
    MAX_ROLE_LENGTH,
    MAX_SEARCH_ROLES,
    DemoSearchProvider,
    SearchProvider,
    SearchProviderError,
    run_search,
    validate_search_roles,
)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ReviewStatus = Literal["pending", "verified", "rejected"]
AnalyticsScope = Literal["verified", "all"]
TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _normalized_required_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("must not be blank")
    return normalized


class PayloadTooLarge(Exception):
    """Raised when a request body exceeds the configured upload limit."""


class RequestBodyLimitMiddleware:
    """Reject oversized request bodies before route handlers consume them."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", ()))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    response = JSONResponse(
                        status_code=413,
                        content={"detail": "CSV upload exceeds 5 MiB limit"},
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                pass

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise PayloadTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except PayloadTooLarge:
            response = JSONResponse(
                status_code=413,
                content={"detail": "CSV upload exceeds 5 MiB limit"},
            )
            await response(scope, receive, send)


class ProjectCreate(BaseModel):
    """Fields accepted when creating a research project."""

    model_config = ConfigDict(extra="forbid")

    name: str
    company: str

    @field_validator("name", "company")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _normalized_required_text(value)


class ProjectResponse(BaseModel):
    """A persisted research project."""

    id: int
    name: str
    company: str
    created_at: str


class SearchRequest(BaseModel):
    """Optional role refinements for a public metadata search."""

    model_config = ConfigDict(extra="forbid")

    roles: list[str] = Field(default_factory=list)

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, value: list[str]) -> list[str]:
        return validate_search_roles(value)


class SearchRunResponse(BaseModel):
    """Recorded state of a search-provider run."""

    id: int
    project_id: int
    query: str
    provider: str
    status: str
    result_count: int
    error_message: str | None
    created_at: str


class ProfileResponse(BaseModel):
    """A normalized candidate profile."""

    id: int
    project_id: int
    name: str | None
    current_company: str | None
    university: str | None
    degree: str | None
    location: str | None
    role: str | None
    years_experience: float | None
    profile_url: str | None
    normalized_url: str | None
    source: str
    source_query: str | None
    review_status: ReviewStatus
    created_at: str
    updated_at: str


class ProfilePage(BaseModel):
    """One page of project profiles."""

    items: list[ProfileResponse]
    page: int
    page_size: int
    total: int


class ProfileUpdate(BaseModel):
    """Editable review and enrichment fields for a profile."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    current_company: str | None = None
    university: str | None = None
    degree: str | None = None
    location: str | None = None
    role: str | None = None
    years_experience: float | None = Field(default=None, ge=0)
    profile_url: str | None = None
    review_status: ReviewStatus | None = None

    @field_validator("review_status")
    @classmethod
    def reject_null_review_status(cls, value: ReviewStatus | None) -> ReviewStatus:
        if value is None:
            raise ValueError("review_status must not be null")
        return value

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one profile field is required")
        return self


class ImportResponse(BaseModel):
    """Result of an atomic CSV import."""

    imported_count: int
    items: list[ProfileResponse]


def _project_or_404(connection: sqlite3.Connection, project_id: int) -> sqlite3.Row:
    project = connection.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _list_projects(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM projects ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def create_app(
    database_path: DatabasePath | None = None,
    search_provider: SearchProvider | None = None,
) -> FastAPI:
    """Create an initialized application with injectable storage and search."""
    resolved_path: DatabasePath = database_path or os.environ.get(
        "DATABASE_PATH", "talent.db"
    )
    memory_mode = str(resolved_path) == ":memory:"
    memory_keeper: sqlite3.Connection | None = None
    if memory_mode:
        memory_keeper = connect(MEMORY_DB_URI, check_same_thread=False, uri=True)
        init_db(MEMORY_DB_URI)
    else:
        if isinstance(resolved_path, Path):
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
        init_db(resolved_path)

    application = FastAPI(
        title="Compliant Talent Research API",
        version="0.1.0",
        description=(
            "Research public search metadata and user-provided CSV records "
            "without fetching profile pages."
        ),
    )
    application.state.database_path = resolved_path
    application.state.memory_keeper = memory_keeper
    if search_provider is None:
        application.state.search_provider = DemoSearchProvider()
    else:
        application.state.search_provider = search_provider

    def connection_factory() -> sqlite3.Connection:
        if memory_mode:
            return connect(MEMORY_DB_URI, check_same_thread=False, uri=True)
        return connect(resolved_path, check_same_thread=False)

    application.state.connection_factory = connection_factory
    application.add_middleware(
        RequestBodyLimitMiddleware, max_bytes=MAX_UPLOAD_BYTES
    )
    application.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )

    def get_connection(request: Request) -> Generator[sqlite3.Connection, None, None]:
        connection = request.app.state.connection_factory()
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    Connection = Annotated[sqlite3.Connection, Depends(get_connection)]

    @application.exception_handler(Exception)
    async def unexpected_error(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500, content={"detail": "Internal server error"}
        )

    @application.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Report that the local API process is ready."""
        return {"status": "ok"}

    @application.get("/", response_class=HTMLResponse, tags=["web"])
    def home(request: Request, connection: Connection) -> HTMLResponse:
        """Render the project list and creation form."""
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {"projects": _list_projects(connection)},
        )

    @application.get(
        "/projects/{project_id}",
        response_class=HTMLResponse,
        tags=["web"],
    )
    def project_dashboard(
        project_id: int,
        request: Request,
        connection: Connection,
    ) -> HTMLResponse:
        """Render the project review and analytics dashboard."""
        project = _project_or_404(connection, project_id)
        return TEMPLATES.TemplateResponse(
            request,
            "project.html",
            {
                "project": dict(project),
                "max_search_roles": MAX_SEARCH_ROLES,
                "max_role_length": MAX_ROLE_LENGTH,
            },
        )

    @application.post(
        "/api/projects",
        response_model=ProjectResponse,
        status_code=201,
        tags=["projects"],
    )
    def create_project(payload: ProjectCreate, connection: Connection) -> dict[str, Any]:
        """Create a company talent-research project."""
        row = connection.execute(
            """
            INSERT INTO projects (name, company)
            VALUES (?, ?)
            RETURNING *
            """,
            (payload.name, payload.company),
        ).fetchone()
        return dict(row)

    @application.get(
        "/api/projects/{project_id}",
        response_model=ProjectResponse,
        tags=["projects"],
    )
    def get_project(project_id: int, connection: Connection) -> dict[str, Any]:
        """Return one project or a not-found response."""
        return dict(_project_or_404(connection, project_id))

    @application.post(
        "/api/projects/{project_id}/search",
        response_model=SearchRunResponse,
        status_code=201,
        tags=["search"],
    )
    def search_project(
        project_id: int,
        payload: SearchRequest,
        request: Request,
        connection: Connection,
    ) -> dict[str, Any]:
        """Run the injected public-search metadata provider for a project."""
        _project_or_404(connection, project_id)
        try:
            return run_search(
                connection,
                project_id,
                request.app.state.search_provider,
                payload.roles,
            )
        except SearchProviderError:
            connection.commit()
            raise HTTPException(
                status_code=502, detail="Search provider unavailable"
            ) from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @application.post(
        "/api/projects/{project_id}/imports/csv",
        response_model=ImportResponse,
        status_code=201,
        tags=["imports"],
    )
    def upload_csv(
        project_id: int,
        connection: Connection,
        file: Annotated[UploadFile, File(description="UTF-8 CSV profile data")],
    ) -> dict[str, Any]:
        """Atomically import a UTF-8 CSV file no larger than 5 MiB."""
        _project_or_404(connection, project_id)
        try:
            content = file.file.read(MAX_UPLOAD_BYTES + 1)
        finally:
            file.file.close()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413, detail="CSV upload exceeds 5 MiB limit"
            )
        try:
            imported = import_csv(connection, project_id, content)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        return {"imported_count": len(imported), "items": imported}

    @application.get(
        "/api/projects/{project_id}/profiles",
        response_model=ProfilePage,
        tags=["profiles"],
    )
    def get_profiles(
        project_id: int,
        connection: Connection,
        review_status: Annotated[ReviewStatus | None, Query()] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        """List and filter project profiles with bounded pagination."""
        _project_or_404(connection, project_id)
        parameters: list[Any] = [project_id]
        where = "project_id = ?"
        if review_status is not None:
            where += " AND review_status = ?"
            parameters.append(review_status)
        total = connection.execute(
            f"SELECT COUNT(*) FROM profiles WHERE {where}", parameters
        ).fetchone()[0]
        items = list_profiles(
            connection,
            project_id,
            review_status=review_status,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    @application.patch(
        "/api/profiles/{profile_id}",
        response_model=ProfileResponse,
        tags=["profiles"],
    )
    def patch_profile(
        profile_id: int, payload: ProfileUpdate, connection: Connection
    ) -> dict[str, Any]:
        """Apply validated review or enrichment changes to one profile."""
        changes = payload.model_dump(exclude_unset=True)
        try:
            return update_profile(connection, profile_id, changes)
        except LookupError:
            raise HTTPException(status_code=404, detail="Profile not found") from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409, detail="Profile conflicts with an existing record"
            ) from None

    @application.get(
        "/api/projects/{project_id}/analytics",
        tags=["analytics"],
    )
    def get_analytics(
        project_id: int,
        connection: Connection,
        scope: Annotated[AnalyticsScope, Query()] = "verified",
    ) -> dict[str, Any]:
        """Return scoped sample counts and profile distributions."""
        _project_or_404(connection, project_id)
        return {
            "summary": summary(connection, project_id, scope),
            "distributions": aggregate(connection, project_id, scope),
        }

    @application.get(
        "/api/projects/{project_id}/export.csv",
        response_class=Response,
        tags=["exports"],
    )
    def download_csv(
        project_id: int,
        connection: Connection,
        scope: Annotated[AnalyticsScope, Query()] = "verified",
    ) -> Response:
        """Download a UTF-8 CSV using the same scope as analytics."""
        _project_or_404(connection, project_id)
        content = export_csv(connection, project_id, scope)
        filename = f"project-{project_id}-profiles-{scope}.csv"
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    return application


app = create_app()
