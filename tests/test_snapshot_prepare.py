"""Tests for deterministic LLM-ready snapshot packages."""

import json

from app.database import connect_database, initialize_database
from app.snapshot_api import initialize_snapshot_schema
from app.snapshot_prepare import load_prepared_package, prepare_snapshot_package
from app.snapshot_compact import compact_snapshot_package, load_compact_package


def test_prepare_groups_fields_and_includes_legal_documents(tmp_path) -> None:
    database_path = tmp_path / "prepared.sqlite3"
    connection = connect_database(database_path)
    initialize_database(connection)
    initialize_snapshot_schema(connection)
    with connection:
        root_id = connection.execute(
            """
            INSERT INTO extension_tasks (
                url, status, created_at, document_type
            ) VALUES ('https://example.test/giveaway', 'captured', 'now', 'entry')
            """
        ).lastrowid
        privacy_id = connection.execute(
            """
            INSERT INTO extension_tasks (
                url, status, created_at, parent_task_id, document_type
            ) VALUES ('https://example.test/privacy', 'captured', 'now', ?, 'privacy')
            """,
            (root_id,),
        ).lastrowid
        root_payload = {
            "requested_url": "https://example.test/giveaway",
            "final_url": "https://example.test/giveaway",
            "title": "Win",
            "visible_text": "Enter and accept marketing contact.",
            "manual_verification_required": False,
            "fields": [
                {
                    "element_ref": "f0_e1", "frame_url": "https://example.test/giveaway",
                    "tag": "input", "field_type": "tel", "role": None,
                    "name": "phone", "label": "Phone", "context": "We may call you",
                    "purpose": "generic", "required": True, "disabled": False,
                    "checked": None, "value_present": False, "options": [],
                },
                {
                    "element_ref": "f0_e2", "frame_url": "https://example.test/giveaway",
                    "tag": "input", "field_type": "checkbox", "role": None,
                    "name": "marketing", "label": "Marketing consent",
                    "context": "I accept marketing", "purpose": "consent",
                    "required": False, "disabled": False, "checked": False,
                    "value_present": True, "options": [],
                },
            ],
            "links": [], "buttons": [], "iframe_urls": [],
            "text_blocks": [
                {
                    "element_ref": "f0_t1", "text": "Accept marketing contact by phone.",
                    "visibility": "visible",
                },
                {
                    "element_ref": "f0_t2", "text": "Accept marketing contact by phone.",
                    "visibility": "visible",
                },
            ],
            "embedded_legal_sections": [],
        }
        privacy_payload = {
            "requested_url": "https://example.test/privacy",
            "final_url": "https://example.test/privacy",
            "title": "Privacy policy", "visible_text": "We process contact details.",
            "manual_verification_required": False,
            "fields": [], "links": [], "buttons": [], "iframe_urls": [],
            "text_blocks": [{
                "element_ref": "f0_t1",
                "text": "We process personal data and retain it for the competition.",
                "visibility": "visible",
            }],
            "embedded_legal_sections": [],
        }
        for task_id, payload in ((root_id, root_payload), (privacy_id, privacy_payload)):
            connection.execute(
                """
                INSERT INTO browser_snapshots (
                    task_id, requested_url, final_url, page_title, status,
                    manual_verification_required, payload_json, captured_at
                ) VALUES (?, ?, ?, ?, 'captured', 0, ?, 'now')
                """,
                (
                    task_id, payload["requested_url"], payload["final_url"],
                    payload["title"], json.dumps(payload),
                ),
            )

    package = prepare_snapshot_package(connection, root_id)
    stored = load_prepared_package(connection, root_id)
    connection.close()

    assert package["form"]["groups"]["contact"][0]["kind"] == "phone"
    assert package["form"]["consents"][0]["source_ref"] == "f0_e2"
    assert package["legal_documents"][0]["visible_text"] == "We process contact details."
    assert stored == package

    compact = compact_snapshot_package(connection := connect_database(database_path), root_id)
    compact_stored = load_compact_package(connection, root_id)
    connection.close()
    assert compact["compaction"]["duplicates_removed"] == 1
    assert {topic for item in compact["evidence"] for topic in item["topics"]} >= {
        "phone", "marketing", "data_use", "retention"
    }
    assert compact_stored == compact
