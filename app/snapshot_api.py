"""Read-only localhost API for Chrome Extension page snapshots."""

import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from app.snapshot_prepare import initialize_prepared_schema, load_prepared_package
from app.snapshot_compact import initialize_compact_schema, load_compact_package
from app.llm_analysis import initialize_analysis_schema, load_llm_analysis


DEFAULT_TOKEN_PATH = DEFAULT_DATABASE_PATH.parent / "extension_api.token"
SNAPSHOT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS extension_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competition_id INTEGER,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    parent_task_id INTEGER,
    document_type TEXT NOT NULL DEFAULT 'entry',
    FOREIGN KEY (parent_task_id) REFERENCES extension_tasks (id) ON DELETE CASCADE,
    FOREIGN KEY (competition_id) REFERENCES competitions (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_extension_tasks_status
ON extension_tasks (status, id);

CREATE TABLE IF NOT EXISTS browser_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    competition_id INTEGER,
    requested_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    page_title TEXT NOT NULL,
    status TEXT NOT NULL,
    manual_verification_required INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES extension_tasks (id) ON DELETE CASCADE,
    FOREIGN KEY (competition_id) REFERENCES competitions (id) ON DELETE SET NULL
);
"""


class SnapshotField(BaseModel):
    """One read-only form control observed by the extension."""

    model_config = ConfigDict(extra="forbid")

    element_ref: str = Field(min_length=1, max_length=100)
    frame_url: str = Field(max_length=4_000)
    tag: str = Field(max_length=30)
    field_type: str = Field(max_length=50)
    role: str | None = Field(default=None, max_length=50)
    name: str | None = Field(default=None, max_length=500)
    label: str | None = Field(default=None, max_length=2_000)
    context: str | None = Field(default=None, max_length=1_500)
    purpose: Literal["generic", "privacy", "rules", "consent"] = "generic"
    required: bool
    disabled: bool
    checked: bool | None = None
    value_present: bool
    options: list[str] = Field(default_factory=list, max_length=200)


class SnapshotLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_ref: str = Field(min_length=1, max_length=100)
    frame_url: str = Field(max_length=4_000)
    text: str = Field(max_length=2_000)
    url: str = Field(max_length=4_000)
    purpose: Literal["generic", "privacy", "rules", "consent"] = "generic"


class SnapshotButton(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_ref: str = Field(min_length=1, max_length=100)
    frame_url: str = Field(max_length=4_000)
    text: str = Field(max_length=2_000)
    button_type: str = Field(max_length=50)
    disabled: bool
    purpose: Literal["generic", "privacy", "rules", "consent"] = "generic"


class SnapshotTextBlock(BaseModel):
    """One referenced visible text block from the DOM."""

    model_config = ConfigDict(extra="forbid")

    element_ref: str = Field(min_length=1, max_length=100)
    frame_url: str = Field(max_length=4_000)
    tag: str = Field(max_length=30)
    text: str = Field(min_length=1, max_length=5_000)
    visibility: Literal["visible"] = "visible"
    purpose: Literal["generic", "privacy", "rules", "consent"] = "generic"


class EmbeddedLegalSection(BaseModel):
    """Legal text already present in a page without interacting with it."""

    model_config = ConfigDict(extra="forbid")

    element_ref: str = Field(min_length=1, max_length=100)
    frame_url: str = Field(max_length=4_000)
    document_types: list[Literal["privacy", "rules"]] = Field(max_length=2)
    text: str = Field(min_length=1, max_length=30_000)
    visibility: Literal["visible", "hidden"]


class SnapshotLegalInteraction(BaseModel):
    """One predefined attempt to reveal rules or privacy text without form input."""

    model_config = ConfigDict(extra="forbid")

    frame_url: str = Field(max_length=4_000)
    text: str = Field(max_length=2_000)
    document_type: Literal["privacy", "rules"]
    result: Literal[
        "content_revealed", "clicked_no_readable_change", "click_failed"
    ]


class BrowserSnapshot(BaseModel):
    """Validated page data captured without filling or submitting a form."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2]
    task_id: int = Field(gt=0)
    requested_url: HttpUrl
    final_url: HttpUrl
    title: str = Field(max_length=2_000)
    captured_at: datetime
    status: Literal["captured", "manual_verification_required"]
    manual_verification_required: bool
    visible_text: str = Field(max_length=100_000)
    fields: list[SnapshotField] = Field(default_factory=list, max_length=1_000)
    links: list[SnapshotLink] = Field(default_factory=list, max_length=2_000)
    buttons: list[SnapshotButton] = Field(default_factory=list, max_length=1_000)
    text_blocks: list[SnapshotTextBlock] = Field(default_factory=list, max_length=2_000)
    embedded_legal_sections: list[EmbeddedLegalSection] = Field(
        default_factory=list, max_length=50
    )
    legal_interactions: list[SnapshotLegalInteraction] = Field(
        default_factory=list, max_length=10
    )
    iframe_urls: list[str] = Field(default_factory=list, max_length=500)


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    competition_id: int | None = Field(default=None, gt=0)
    url: HttpUrl
    parent_task_id: int | None = Field(default=None, gt=0)
    document_type: Literal["entry", "privacy", "rules"] = "entry"


class TaskResponse(BaseModel):
    id: int
    competition_id: int | None
    url: str
    status: str
    created_at: str
    parent_task_id: int | None = None
    document_type: Literal["entry", "privacy", "rules"] = "entry"


def load_or_create_api_token(token_path: Path = DEFAULT_TOKEN_PATH) -> str:
    """Return the local extension token, creating it with restrictive defaults."""

    token_path.parent.mkdir(parents=True, exist_ok=True)
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    token_path.write_text(token, encoding="utf-8")
    return token


def initialize_snapshot_schema(connection: sqlite3.Connection) -> None:
    with connection:
        connection.executescript(SNAPSHOT_SCHEMA_SQL)
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(extension_tasks)")
        }
        if "parent_task_id" not in columns:
            connection.execute("ALTER TABLE extension_tasks ADD COLUMN parent_task_id INTEGER")
        if "document_type" not in columns:
            connection.execute(
                "ALTER TABLE extension_tasks "
                "ADD COLUMN document_type TEXT NOT NULL DEFAULT 'entry'"
            )
        initialize_prepared_schema(connection)
        initialize_compact_schema(connection)
        initialize_analysis_schema(connection)
        connection.execute("PRAGMA user_version = 9")


def create_app(
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    api_token: str | None = None,
) -> FastAPI:
    """Create a localhost API with an explicit shared token."""

    database_path = Path(database_path)
    expected_token = api_token or load_or_create_api_token()
    with closing(connect_database(database_path)) as connection:
        initialize_database(connection)
        initialize_snapshot_schema(connection)

    application = FastAPI(title="Giveaway Agent Snapshot API", version="0.1.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"chrome-extension://[a-z]+",
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Giveaway-Agent-Token"],
    )

    def authorize(
        supplied: Annotated[str | None, Header(alias="X-Giveaway-Agent-Token")] = None,
    ) -> None:
        if supplied is None or not secrets.compare_digest(supplied, expected_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post(
        "/api/v1/tasks",
        response_model=TaskResponse,
        dependencies=[Depends(authorize)],
    )
    def create_task(payload: TaskCreate) -> TaskResponse:
        timestamp = _timestamp()
        with closing(connect_database(database_path)) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO extension_tasks (
                    competition_id, url, status, created_at, parent_task_id, document_type
                ) VALUES (?, ?, 'queued', ?, ?, ?)
                """,
                (
                    payload.competition_id, str(payload.url), timestamp,
                    payload.parent_task_id, payload.document_type,
                ),
            )
            task_id = cursor.lastrowid
        return TaskResponse(
            id=task_id,
            competition_id=payload.competition_id,
            url=str(payload.url),
            status="queued",
            created_at=timestamp,
            parent_task_id=payload.parent_task_id,
            document_type=payload.document_type,
        )

    @application.get(
        "/api/v1/tasks/next",
        response_model=TaskResponse | None,
        dependencies=[Depends(authorize)],
    )
    def claim_next_task() -> TaskResponse | None:
        with closing(connect_database(database_path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM extension_tasks WHERE status = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            claimed_at = _timestamp()
            connection.execute(
                "UPDATE extension_tasks SET status = 'opening', claimed_at = ? WHERE id = ?",
                (claimed_at, row["id"]),
            )
        return TaskResponse(
            id=row["id"],
            competition_id=row["competition_id"],
            url=row["url"],
            status="opening",
            created_at=row["created_at"],
            parent_task_id=row["parent_task_id"],
            document_type=row["document_type"],
        )

    @application.post(
        "/api/v1/tasks/{task_id}/snapshot",
        dependencies=[Depends(authorize)],
    )
    def submit_snapshot(task_id: int, snapshot: BrowserSnapshot) -> dict[str, object]:
        if task_id != snapshot.task_id:
            raise HTTPException(status_code=400, detail="Task ID mismatch")
        with closing(connect_database(database_path)) as connection, connection:
            task = connection.execute(
                "SELECT * FROM extension_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found")
            if _normalized_url(task["url"]) != _normalized_url(str(snapshot.requested_url)):
                raise HTTPException(status_code=400, detail="Requested URL mismatch")
            payload_json = snapshot.model_dump_json()
            connection.execute(
                """
                INSERT INTO browser_snapshots (
                    task_id, competition_id, requested_url, final_url, page_title,
                    status, manual_verification_required, payload_json, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    task["competition_id"],
                    str(snapshot.requested_url),
                    str(snapshot.final_url),
                    snapshot.title,
                    snapshot.status,
                    int(snapshot.manual_verification_required),
                    payload_json,
                    snapshot.captured_at.astimezone(UTC).isoformat(),
                ),
            )
            task_status = (
                "manual_verification_required"
                if snapshot.manual_verification_required
                else "captured"
            )
            connection.execute(
                "UPDATE extension_tasks SET status = ?, completed_at = ? WHERE id = ?",
                (task_status, _timestamp(), task_id),
            )
            queued_documents = (
                _queue_legal_documents(connection, task, snapshot)
                if task["document_type"] == "entry"
                else []
            )
        return {
            "task_id": task_id,
            "status": task_status,
            "stored": True,
            "queued_legal_documents": queued_documents,
        }

    @application.get(
        "/api/v1/tasks/{task_id}",
        response_model=TaskResponse,
        dependencies=[Depends(authorize)],
    )
    def get_task(task_id: int) -> TaskResponse:
        with closing(connect_database(database_path)) as connection:
            row = connection.execute(
                "SELECT * FROM extension_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return TaskResponse(
            id=row["id"],
            competition_id=row["competition_id"],
            url=row["url"],
            status=row["status"],
            created_at=row["created_at"],
            parent_task_id=row["parent_task_id"],
            document_type=row["document_type"],
        )

    @application.get(
        "/api/v1/tasks/{task_id}/snapshot",
        response_model=BrowserSnapshot,
        dependencies=[Depends(authorize)],
    )
    def get_snapshot(task_id: int) -> BrowserSnapshot:
        """Return the latest validated snapshot for a later analysis agent."""

        with closing(connect_database(database_path)) as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM browser_snapshots
                WHERE task_id = ? ORDER BY id DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        return BrowserSnapshot.model_validate_json(row["payload_json"])

    @application.get(
        "/api/v1/tasks/{task_id}/prepared",
        dependencies=[Depends(authorize)],
    )
    def get_prepared(task_id: int) -> dict:
        """Return a persisted LLM-ready package without running an LLM."""

        with closing(connect_database(database_path)) as connection:
            package = load_prepared_package(connection, task_id)
        if package is None:
            raise HTTPException(status_code=404, detail="Prepared snapshot not found")
        return package

    @application.get(
        "/api/v1/tasks/{task_id}/compact",
        dependencies=[Depends(authorize)],
    )
    def get_compact(task_id: int) -> dict:
        """Return a persisted compact evidence package for later analysis."""

        with closing(connect_database(database_path)) as connection:
            package = load_compact_package(connection, task_id)
        if package is None:
            raise HTTPException(status_code=404, detail="Compact snapshot not found")
        return package

    @application.get(
        "/api/v1/tasks/{task_id}/analysis",
        dependencies=[Depends(authorize)],
    )
    def get_analysis(task_id: int) -> dict:
        """Return the persisted validated local-LLM analysis."""

        with closing(connect_database(database_path)) as connection:
            analysis = load_llm_analysis(connection, task_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail="LLM analysis not found")
        return analysis.model_dump(mode="json")

    return application


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _normalized_url(url: str) -> str:
    parts = urlsplit(url)
    return parts._replace(fragment="").geturl().rstrip("/")


def _queue_legal_documents(
    connection: sqlite3.Connection,
    task: sqlite3.Row,
    snapshot: BrowserSnapshot,
) -> list[dict[str, object]]:
    """Queue linked privacy and rules pages as related read-only tasks."""

    candidates: list[tuple[str, str]] = []
    for link in snapshot.links:
        url = link.url.strip()
        if link.purpose in {"privacy", "rules"} and url.startswith(("http://", "https://")):
            candidates.append((link.purpose, url))
    queued = []
    seen: set[tuple[str, str]] = set()
    for document_type, url in candidates[:10]:
        key = (document_type, _normalized_url(url))
        if key in seen or key[1] in {
            _normalized_url(str(snapshot.requested_url)),
            _normalized_url(str(snapshot.final_url)),
        }:
            continue
        seen.add(key)
        existing = connection.execute(
            """
            SELECT id FROM extension_tasks
            WHERE parent_task_id = ? AND document_type = ? AND url = ?
            """,
            (task["id"], document_type, url),
        ).fetchone()
        if existing:
            continue
        cursor = connection.execute(
            """
            INSERT INTO extension_tasks (
                competition_id, url, status, created_at, parent_task_id, document_type
            ) VALUES (?, ?, 'queued', ?, ?, ?)
            """,
            (task["competition_id"], url, _timestamp(), task["id"], document_type),
        )
        queued.append({
            "task_id": cursor.lastrowid,
            "document_type": document_type,
            "url": url,
        })
    return queued
