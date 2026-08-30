"""Local SQLite persistence for discovered competitions."""

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.discovery import CompetitionCandidate
from app.page_inspection import FormField, PageInspection


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "giveaway_agent.sqlite3"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS competitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_url TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    published_date TEXT,
    platforms_json TEXT NOT NULL DEFAULT '[]',
    organizer TEXT,
    deadline_raw TEXT,
    prize TEXT,
    entry_urls_json TEXT NOT NULL DEFAULT '[]',
    discovered_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_competitions_source
ON competitions (source);

CREATE INDEX IF NOT EXISTS idx_competitions_last_seen_at
ON competitions (last_seen_at);

CREATE TABLE IF NOT EXISTS page_inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competition_id INTEGER NOT NULL,
    requested_url TEXT NOT NULL,
    final_url TEXT,
    page_title TEXT,
    status TEXT NOT NULL,
    inspection_method TEXT NOT NULL DEFAULT 'unknown',
    manual_review_required INTEGER NOT NULL DEFAULT 0,
    page_text TEXT NOT NULL DEFAULT '',
    ai_snapshot TEXT NOT NULL DEFAULT '',
    fields_json TEXT NOT NULL DEFAULT '[]',
    privacy_urls_json TEXT NOT NULL DEFAULT '[]',
    rules_urls_json TEXT NOT NULL DEFAULT '[]',
    network_urls_json TEXT NOT NULL DEFAULT '[]',
    iframe_urls_json TEXT NOT NULL DEFAULT '[]',
    error_message TEXT,
    inspected_at TEXT NOT NULL,
    FOREIGN KEY (competition_id) REFERENCES competitions (id) ON DELETE CASCADE,
    UNIQUE (competition_id, requested_url)
);

CREATE INDEX IF NOT EXISTS idx_page_inspections_competition_id
ON page_inspections (competition_id);
"""

UPSERT_SQL = """
INSERT INTO competitions (
    normalized_url,
    url,
    source,
    title,
    published_date,
    platforms_json,
    organizer,
    deadline_raw,
    prize,
    entry_urls_json,
    discovered_at,
    last_seen_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(normalized_url) DO UPDATE SET
    url = excluded.url,
    source = excluded.source,
    title = excluded.title,
    published_date = excluded.published_date,
    platforms_json = excluded.platforms_json,
    organizer = excluded.organizer,
    deadline_raw = excluded.deadline_raw,
    prize = excluded.prize,
    entry_urls_json = excluded.entry_urls_json,
    last_seen_at = excluded.last_seen_at;
"""


@dataclass(frozen=True, slots=True)
class SaveSummary:
    """Counts produced by one batch save operation."""

    inserted: int
    updated: int


@dataclass(frozen=True, slots=True)
class StoredCompetition:
    """A competition read from the local database."""

    id: int
    normalized_url: str
    url: str
    source: str
    title: str
    published_date: str | None
    platforms: tuple[str, ...]
    organizer: str | None
    deadline: str | None
    prize: str | None
    entry_urls: tuple[str, ...]
    discovered_at: str
    last_seen_at: str


@dataclass(frozen=True, slots=True)
class StoredPageInspection:
    """A previously saved browser inspection."""

    competition_id: int
    requested_url: str
    final_url: str | None
    title: str | None
    status: str
    inspection_method: str
    manual_review_required: bool
    page_text: str
    ai_snapshot: str
    fields: tuple[FormField, ...]
    privacy_urls: tuple[str, ...]
    rules_urls: tuple[str, ...]
    network_urls: tuple[str, ...]
    iframe_urls: tuple[str, ...]
    error_message: str | None
    inspected_at: str


def connect_database(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> sqlite3.Connection:
    """Open a SQLite database and configure rows for named-column access."""

    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    """Create the current database schema when it does not exist."""

    with connection:
        connection.executescript(SCHEMA_SQL)
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(page_inspections)")
        }
        if "ai_snapshot" not in columns:
            connection.execute(
                "ALTER TABLE page_inspections "
                "ADD COLUMN ai_snapshot TEXT NOT NULL DEFAULT ''"
            )
        if "inspection_method" not in columns:
            connection.execute(
                "ALTER TABLE page_inspections "
                "ADD COLUMN inspection_method TEXT NOT NULL DEFAULT 'unknown'"
            )
        migrations = {
            "manual_review_required": "INTEGER NOT NULL DEFAULT 0",
            "network_urls_json": "TEXT NOT NULL DEFAULT '[]'",
            "iframe_urls_json": "TEXT NOT NULL DEFAULT '[]'",
        }
        for column, definition in migrations.items():
            if column not in columns:
                connection.execute(
                    f"ALTER TABLE page_inspections ADD COLUMN {column} {definition}"
                )
        connection.execute("PRAGMA user_version = 7")


def save_page_inspections(
    connection: sqlite3.Connection,
    competition_id: int,
    inspections: Iterable[PageInspection],
    *,
    inspected_at: datetime | None = None,
) -> None:
    """Insert or replace the latest inspection for each entry URL."""

    timestamp = _timestamp(inspected_at)
    with connection:
        for inspection in inspections:
            connection.execute(
                """
                INSERT INTO page_inspections (
                    competition_id, requested_url, final_url, page_title, status,
                    inspection_method, manual_review_required, page_text,
                    ai_snapshot, fields_json, privacy_urls_json, rules_urls_json,
                    network_urls_json, iframe_urls_json, error_message, inspected_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(competition_id, requested_url) DO UPDATE SET
                    final_url = excluded.final_url,
                    page_title = excluded.page_title,
                    status = excluded.status,
                    inspection_method = excluded.inspection_method,
                    manual_review_required = excluded.manual_review_required,
                    page_text = excluded.page_text,
                    ai_snapshot = excluded.ai_snapshot,
                    fields_json = excluded.fields_json,
                    privacy_urls_json = excluded.privacy_urls_json,
                    rules_urls_json = excluded.rules_urls_json,
                    network_urls_json = excluded.network_urls_json,
                    iframe_urls_json = excluded.iframe_urls_json,
                    error_message = excluded.error_message,
                    inspected_at = excluded.inspected_at
                """,
                (
                    competition_id,
                    inspection.requested_url,
                    inspection.final_url,
                    inspection.title,
                    inspection.status,
                    inspection.inspection_method,
                    int(inspection.manual_review_required),
                    inspection.page_text,
                    inspection.ai_snapshot,
                    json.dumps([asdict(field) for field in inspection.fields], ensure_ascii=False),
                    _to_json(inspection.privacy_urls),
                    _to_json(inspection.rules_urls),
                    _to_json(inspection.network_urls),
                    _to_json(inspection.iframe_urls),
                    inspection.error_message,
                    timestamp,
                ),
            )


def list_page_inspections(
    connection: sqlite3.Connection,
    competition_id: int,
) -> list[StoredPageInspection]:
    """Return saved page inspections for one competition."""

    rows = connection.execute(
        """
        SELECT * FROM page_inspections
        WHERE competition_id = ?
        ORDER BY id
        """,
        (competition_id,),
    ).fetchall()
    return [_row_to_page_inspection(row) for row in rows]


def save_competitions(
    connection: sqlite3.Connection,
    candidates: Iterable[CompetitionCandidate],
    *,
    observed_at: datetime | None = None,
) -> SaveSummary:
    """Insert new competitions and update previously discovered ones."""

    timestamp = _timestamp(observed_at)
    inserted = 0
    updated = 0

    # Save the complete scan as one transaction so partial writes are avoided.
    with connection:
        for candidate in candidates:
            normalized_url = normalize_url(candidate.url)
            already_exists = connection.execute(
                "SELECT 1 FROM competitions WHERE normalized_url = ?",
                (normalized_url,),
            ).fetchone()

            if already_exists is None:
                inserted += 1
            else:
                updated += 1

            connection.execute(
                UPSERT_SQL,
                (
                    normalized_url,
                    candidate.url,
                    candidate.source,
                    candidate.title,
                    candidate.published_date,
                    _to_json(candidate.platforms),
                    candidate.organizer,
                    candidate.deadline,
                    candidate.prize,
                    _to_json(candidate.entry_urls),
                    timestamp,
                    timestamp,
                ),
            )

    return SaveSummary(inserted=inserted, updated=updated)


def list_competitions(connection: sqlite3.Connection) -> list[StoredCompetition]:
    """Return all competitions with the most recently seen first."""

    rows = connection.execute(
        """
        SELECT *
        FROM competitions
        ORDER BY last_seen_at DESC, id DESC
        """
    ).fetchall()
    return [_row_to_competition(row) for row in rows]


def get_competition(
    connection: sqlite3.Connection,
    competition_id: int,
) -> StoredCompetition | None:
    """Return one competition by database ID, or None when it is missing."""

    row = connection.execute(
        "SELECT * FROM competitions WHERE id = ?",
        (competition_id,),
    ).fetchone()
    return _row_to_competition(row) if row is not None else None


def normalize_url(url: str) -> str:
    """Normalize stable URL components used for duplicate detection."""

    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"Invalid HTTP URL: {url}")

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            parts.query,
            "",
        )
    )


def _timestamp(value: datetime | None) -> str:
    """Return an aware datetime in a stable UTC ISO 8601 format."""

    moment = value or datetime.now(UTC)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("observed_at must include timezone information")
    return moment.astimezone(UTC).isoformat()


def _to_json(values: tuple[str, ...]) -> str:
    """Serialize tuple fields without escaping Finnish characters."""

    return json.dumps(values, ensure_ascii=False)


def _row_to_competition(row: sqlite3.Row) -> StoredCompetition:
    """Convert one SQLite row into the public stored model."""

    return StoredCompetition(
        id=row["id"],
        normalized_url=row["normalized_url"],
        url=row["url"],
        source=row["source"],
        title=row["title"],
        published_date=row["published_date"],
        platforms=tuple(json.loads(row["platforms_json"])),
        organizer=row["organizer"],
        deadline=row["deadline_raw"],
        prize=row["prize"],
        entry_urls=tuple(json.loads(row["entry_urls_json"])),
        discovered_at=row["discovered_at"],
        last_seen_at=row["last_seen_at"],
    )


def _row_to_page_inspection(row: sqlite3.Row) -> StoredPageInspection:
    """Convert one inspection row into the public stored model."""

    return StoredPageInspection(
        competition_id=row["competition_id"],
        requested_url=row["requested_url"],
        final_url=row["final_url"],
        title=row["page_title"],
        status=row["status"],
        inspection_method=row["inspection_method"],
        manual_review_required=bool(row["manual_review_required"]),
        page_text=row["page_text"],
        ai_snapshot=row["ai_snapshot"],
        fields=tuple(FormField(**item) for item in json.loads(row["fields_json"])),
        privacy_urls=tuple(json.loads(row["privacy_urls_json"])),
        rules_urls=tuple(json.loads(row["rules_urls_json"])),
        network_urls=tuple(json.loads(row["network_urls_json"])),
        iframe_urls=tuple(json.loads(row["iframe_urls_json"])),
        error_message=row["error_message"],
        inspected_at=row["inspected_at"],
    )
