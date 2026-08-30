"""Create compact, sourced evidence packages without using an LLM."""

import json
import re
import sqlite3
from datetime import UTC, datetime

from app.snapshot_prepare import prepare_snapshot_package


COMPACT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS compact_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_task_id INTEGER NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    compacted_at TEXT NOT NULL,
    FOREIGN KEY (root_task_id) REFERENCES extension_tasks (id) ON DELETE CASCADE
);
"""

TOPICS = {
    "phone": ("puhel", "phone", "sms", "tekstiviest"),
    "marketing": ("markkin", "marketing", "tarjous", "uutiskir", "suostu", "consent"),
    "data_use": ("henkilöt", "tietoja käsit", "personal data", "process data"),
    "data_sharing": ("luovut", "yhteistyökumpp", "kolmann", "recipient", "share data"),
    "retention": ("säilyt", "poistet", "retention", "retain"),
    "winner_contact": ("voittaj", "winner", "yhteyttä", "contacted"),
    "eligibility": ("ikä", "vuotia", "osallistua", "eligible", "resident"),
    "deadline": ("kilpailuaika", "päätty", "deadline", "closing date"),
    "prize": ("palkinto", "voita", "prize"),
    "rules": ("käyttöeh", "osallistumiseh", "säänn", "terms", "rules"),
    "privacy": ("tietosuoja", "privacy", "rekisterinpitä"),
}
MAX_TOTAL_CHARS = 18_000
MAX_SOURCE_CHARS = 6_000


def initialize_compact_schema(connection: sqlite3.Connection) -> None:
    """Create storage for compact evidence packages."""

    connection.executescript(COMPACT_SCHEMA_SQL)


def compact_snapshot_package(
    connection: sqlite3.Connection,
    root_task_id: int,
) -> dict:
    """Build, deduplicate, cap, and persist an evidence-focused package."""

    prepared = prepare_snapshot_package(connection, root_task_id)
    candidates = []
    entry = prepared["entry_page"]
    candidates.extend(_source_candidates(
        source_type="entry_page",
        document_type="entry",
        task_id=root_task_id,
        text_blocks=entry.get("text_blocks", []),
        fallback_text=entry.get("visible_text", ""),
    ))
    for section in entry.get("embedded_legal_sections", []):
        for document_type in section.get("document_types", ["legal"]):
            candidates.extend(_text_candidates(
                section.get("text", ""),
                source_type="embedded_legal",
                document_type=document_type,
                source_prefix=section.get("element_ref") or "embedded",
                visibility=section.get("visibility"),
            ))
    for document in prepared["legal_documents"]:
        if not document["source_available"]:
            continue
        candidates.extend(_source_candidates(
            source_type="linked_legal_document",
            document_type=document["document_type"],
            task_id=document["task_id"],
            text_blocks=document.get("text_blocks", []),
            fallback_text=document.get("visible_text", ""),
        ))
        for section in document.get("embedded_legal_sections", []):
            candidates.extend(_text_candidates(
                section.get("text", ""),
                source_type="embedded_legal",
                document_type=document["document_type"],
                source_prefix=section.get("element_ref") or f"task:{document['task_id']}",
                visibility=section.get("visibility"),
            ))

    evidence, duplicate_count, omitted_count = _select_evidence(candidates)
    compacted_at = datetime.now(UTC).isoformat()
    package = {
        "schema_version": 1,
        "source_task_id": root_task_id,
        "competition_id": prepared["competition_id"],
        "compacted_at": compacted_at,
        "content_trust": prepared["content_trust"],
        "entry_page": {
            "requested_url": entry["requested_url"],
            "final_url": entry["final_url"],
            "title": entry["title"],
            "manual_verification_required": entry["manual_verification_required"],
        },
        "form": {
            "field_count": prepared["form"]["field_count"],
            "groups": prepared["form"]["groups"],
            "consents": prepared["form"]["consents"],
            "relevant_buttons": [
                button for button in prepared["form"].get("buttons", [])
                if button.get("button_type") == "submit"
                or button.get("purpose") in {"consent", "privacy", "rules"}
            ],
        },
        "evidence": evidence,
        "legal_document_status": [
            {
                "task_id": document["task_id"],
                "document_type": document["document_type"],
                "status": document["status"],
                "requested_url": document["requested_url"],
                "source_available": document["source_available"],
            }
            for document in prepared["legal_documents"]
        ],
        "unresolved_legal_elements": prepared["unresolved_legal_elements"],
        "collection_warnings": prepared["collection_warnings"],
        "compaction": {
            "candidate_blocks": len(candidates),
            "included_blocks": len(evidence),
            "duplicates_removed": duplicate_count,
            "relevant_blocks_omitted_by_limits": omitted_count,
            "total_evidence_characters": sum(len(item["text"]) for item in evidence),
            "method": "deterministic_keyword_and_deduplication_v1",
        },
    }
    with connection:
        initialize_compact_schema(connection)
        connection.execute(
            """
            INSERT INTO compact_snapshots (
                root_task_id, schema_version, payload_json, compacted_at
            ) VALUES (?, 1, ?, ?)
            ON CONFLICT(root_task_id) DO UPDATE SET
                schema_version = excluded.schema_version,
                payload_json = excluded.payload_json,
                compacted_at = excluded.compacted_at
            """,
            (root_task_id, json.dumps(package, ensure_ascii=False), compacted_at),
        )
    return package


def load_compact_package(
    connection: sqlite3.Connection,
    root_task_id: int,
) -> dict | None:
    """Load a previously persisted compact package."""

    initialize_compact_schema(connection)
    row = connection.execute(
        "SELECT payload_json FROM compact_snapshots WHERE root_task_id = ?",
        (root_task_id,),
    ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def _source_candidates(
    *, source_type: str, document_type: str, task_id: int,
    text_blocks: list[dict], fallback_text: str,
) -> list[dict]:
    if text_blocks:
        return [
            {
                "source_ref": block.get("element_ref") or f"task:{task_id}:block:{index}",
                "source_type": source_type,
                "document_type": document_type,
                "visibility": block.get("visibility", "visible"),
                "text": block.get("text", ""),
            }
            for index, block in enumerate(text_blocks, start=1)
            if block.get("text")
        ]
    return _text_candidates(
        fallback_text, source_type=source_type, document_type=document_type,
        source_prefix=f"task:{task_id}", visibility="visible",
    )


def _text_candidates(
    text: str, *, source_type: str, document_type: str,
    source_prefix: str, visibility: str | None,
) -> list[dict]:
    parts = [part.strip() for part in re.split(r"\n+|(?<=[.!?])\s+(?=[A-ZÅÄÖ])", text)]
    return [
        {
            "source_ref": f"{source_prefix}:part:{index}",
            "source_type": source_type,
            "document_type": document_type,
            "visibility": visibility or "visible",
            "text": part[:2_000],
        }
        for index, part in enumerate(parts, start=1)
        if len(part) >= 20
    ]


def _select_evidence(candidates: list[dict]) -> tuple[list[dict], int, int]:
    seen = set()
    duplicates = 0
    relevant = []
    for candidate in candidates:
        normalized = _normalize(candidate["text"])
        if not normalized:
            continue
        if normalized in seen:
            duplicates += 1
            continue
        seen.add(normalized)
        topics = _topics(candidate["text"])
        if not topics:
            continue
        relevant.append({**candidate, "topics": topics})

    included = []
    source_sizes: dict[str, int] = {}
    total = 0
    omitted = 0
    for candidate in relevant:
        source_key = f"{candidate['source_type']}:{candidate['document_type']}"
        length = len(candidate["text"])
        if total + length > MAX_TOTAL_CHARS:
            omitted += 1
            continue
        if source_sizes.get(source_key, 0) + length > MAX_SOURCE_CHARS:
            omitted += 1
            continue
        included.append(candidate)
        total += length
        source_sizes[source_key] = source_sizes.get(source_key, 0) + length
    return included, duplicates, omitted


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9åäö]+", " ", text.casefold()).strip()


def _topics(text: str) -> list[str]:
    lowered = text.casefold()
    return [topic for topic, markers in TOPICS.items() if any(marker in lowered for marker in markers)]
