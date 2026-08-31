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
MAX_TOTAL_CHARS = 12_000
MAX_TOPIC_BLOCKS_PER_DOCUMENT = 3
SCOPE_CHAR_LIMITS = {
    "competition_page": 7_000,
    "competition_specific_rules": 5_000,
    "competition_privacy_policy": 3_000,
    "general_service_terms": 1_500,
    "general_privacy_policy": 1_000,
    "unknown": 1_500,
}
SCOPE_PRIORITIES = {
    "competition_page": 0,
    "competition_specific_rules": 1,
    "competition_privacy_policy": 2,
    "general_service_terms": 3,
    "general_privacy_policy": 4,
    "unknown": 5,
}


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
        source_scope="competition_page",
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
                source_task_id=root_task_id,
                source_scope=(
                    "competition_specific_rules"
                    if document_type == "rules"
                    else "competition_privacy_policy"
                ),
            ))
    for document in prepared["legal_documents"]:
        if not document["source_available"]:
            continue
        document_scope = _document_scope(
            document["document_type"], document.get("requested_url", "")
        )
        candidates.extend(_source_candidates(
            source_type="linked_legal_document",
            document_type=document["document_type"],
            task_id=document["task_id"],
            source_scope=document_scope,
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
                source_task_id=document["task_id"],
                source_scope=document_scope,
            ))

    evidence, duplicate_count, omitted_count = _select_evidence(candidates)
    compacted_at = datetime.now(UTC).isoformat()
    package = {
        "schema_version": 4,
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
            "relevant_buttons": _relevant_buttons(prepared["form"]),
        },
        "evidence": evidence,
        "legal_document_status": [
            {
                "task_id": document["task_id"],
                "document_type": document["document_type"],
                "status": document["status"],
                "requested_url": document["requested_url"],
                "source_available": document["source_available"],
                "scope": _document_scope(
                    document["document_type"], document.get("requested_url", "")
                ),
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
            "method": "topic_capped_legal_evidence_v4",
        },
    }
    with connection:
        initialize_compact_schema(connection)
        connection.execute(
            """
            INSERT INTO compact_snapshots (
                root_task_id, schema_version, payload_json, compacted_at
            ) VALUES (?, 4, ?, ?)
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
    source_scope: str, text_blocks: list[dict], fallback_text: str,
) -> list[dict]:
    if text_blocks:
        return [
            {
                "source_ref": block.get("element_ref") or f"task:{task_id}:block:{index}",
                "source_task_id": task_id,
                "source_type": source_type,
                "document_type": document_type,
                "scope": source_scope,
                "visibility": block.get("visibility", "visible"),
                "text": block.get("text", ""),
            }
            for index, block in enumerate(text_blocks, start=1)
            if block.get("text")
        ]
    return _text_candidates(
        fallback_text, source_type=source_type, document_type=document_type,
        source_prefix=f"task:{task_id}", visibility="visible",
        source_task_id=task_id,
        source_scope=source_scope,
    )


def _relevant_buttons(form: dict) -> list[dict]:
    """Keep legal controls and submit buttons associated with captured form fields."""

    field_frames = {
        str(field.get("frame_url"))
        for fields in form.get("groups", {}).values()
        for field in fields
        if field.get("frame_url")
    }
    return [
        button
        for button in form.get("buttons", [])
        if button.get("purpose") in {"consent", "privacy", "rules"}
        or (
            button.get("button_type") == "submit"
            and str(button.get("frame_url")) in field_frames
        )
    ]


def _text_candidates(
    text: str, *, source_type: str, document_type: str,
    source_prefix: str, visibility: str | None,
    source_task_id: int | None = None,
    source_scope: str = "unknown",
) -> list[dict]:
    parts = [part.strip() for part in re.split(r"\n+|(?<=[.!?])\s+(?=[A-ZÅÄÖ])", text)]
    return [
        {
            "source_ref": f"{source_prefix}:part:{index}",
            "source_task_id": source_task_id,
            "source_type": source_type,
            "document_type": document_type,
            "scope": source_scope,
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

    relevant.sort(key=lambda item: SCOPE_PRIORITIES.get(item["scope"], 99))
    included = []
    document_sizes: dict[str, int] = {}
    topic_counts: dict[tuple[str, str], int] = {}
    total = 0
    omitted = 0
    for candidate in relevant:
        document_key = (
            f"{candidate.get('source_task_id')}:{candidate['scope']}:"
            f"{candidate['document_type']}"
        )
        available_topics = [
            topic for topic in candidate["topics"]
            if topic_counts.get((document_key, topic), 0)
            < MAX_TOPIC_BLOCKS_PER_DOCUMENT
        ]
        if not available_topics:
            omitted += 1
            continue
        block_limit = 1_500 if candidate["scope"] == "competition_page" else 800
        selected_candidate = {**candidate, "text": candidate["text"][:block_limit]}
        length = len(selected_candidate["text"])
        if total + length > MAX_TOTAL_CHARS:
            omitted += 1
            continue
        document_limit = SCOPE_CHAR_LIMITS.get(candidate["scope"], 1_500)
        if document_sizes.get(document_key, 0) + length > document_limit:
            omitted += 1
            continue
        included.append(selected_candidate)
        total += length
        document_sizes[document_key] = document_sizes.get(document_key, 0) + length
        for topic in available_topics:
            topic_counts[(document_key, topic)] = (
                topic_counts.get((document_key, topic), 0) + 1
            )
    return included, duplicates, omitted


def _document_scope(document_type: str, requested_url: str) -> str:
    """Classify linked documents conservatively for evidence budgets and LLM use."""

    lowered = requested_url.casefold()
    if document_type == "rules":
        if any(marker in lowered for marker in ("palvelun-kayttoehdot", "terms-of-use")):
            return "general_service_terms"
        return "competition_specific_rules"
    if document_type == "privacy":
        if any(marker in lowered for marker in ("k_ruoka_kilpailut", "competition", "arvonta")):
            return "competition_privacy_policy"
        return "general_privacy_policy"
    return "unknown"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9åäö]+", " ", text.casefold()).strip()


def _topics(text: str) -> list[str]:
    lowered = text.casefold()
    return [topic for topic, markers in TOPICS.items() if any(marker in lowered for marker in markers)]
