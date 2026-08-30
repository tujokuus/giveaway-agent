"""Tests for the authenticated read-only Chrome Extension snapshot API."""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.snapshot_api import create_app


TOKEN = "test-token-with-enough-entropy"


def client_for(path: Path) -> TestClient:
    return TestClient(create_app(database_path=path, api_token=TOKEN))


def headers() -> dict[str, str]:
    return {"X-Giveaway-Agent-Token": TOKEN}


def test_task_endpoints_require_token(tmp_path) -> None:
    client = client_for(tmp_path / "snapshots.sqlite3")
    response = client.post(
        "/api/v1/tasks",
        json={"competition_id": None, "url": "https://example.test/giveaway"},
    )
    assert response.status_code == 401


def test_extension_can_claim_and_store_read_only_snapshot(tmp_path) -> None:
    database_path = tmp_path / "snapshots.sqlite3"
    client = client_for(database_path)
    created = client.post(
        "/api/v1/tasks",
        headers=headers(),
        json={"competition_id": None, "url": "https://example.test/giveaway"},
    )
    task_id = created.json()["id"]
    claimed = client.get("/api/v1/tasks/next", headers=headers())
    submitted = client.post(
        f"/api/v1/tasks/{task_id}/snapshot",
        headers=headers(),
        json={
            "schema_version": 1,
            "task_id": task_id,
            "requested_url": "https://example.test/giveaway",
            "final_url": "https://example.test/giveaway/form",
            "title": "Win a prize",
            "captured_at": "2026-08-30T12:00:00Z",
            "status": "captured",
            "manual_verification_required": False,
            "visible_text": "Enter the giveaway",
            "fields": [{
                "element_ref": "f0_e1",
                "frame_url": "https://example.test/giveaway/form",
                "tag": "input",
                "field_type": "email",
                "name": "email",
                "label": "Email",
                "required": True,
                "disabled": False,
                "checked": None,
                "value_present": False,
                "options": [],
            }],
            "links": [],
            "buttons": [],
            "iframe_urls": [],
        },
    )
    assert claimed.json()["id"] == task_id
    assert claimed.json()["status"] == "opening"
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "captured"
    fetched = client.get(
        f"/api/v1/tasks/{task_id}/snapshot",
        headers=headers(),
    )
    assert fetched.status_code == 200
    assert fetched.json()["fields"][0]["label"] == "Email"

    connection = sqlite3.connect(database_path)
    stored = connection.execute(
        "SELECT payload_json FROM browser_snapshots WHERE task_id = ?", (task_id,)
    ).fetchone()
    connection.close()
    assert stored is not None
    assert '"value_present":false' in stored[0]
    assert "Enter the giveaway" in stored[0]


def test_snapshot_rejects_task_url_mismatch(tmp_path) -> None:
    client = client_for(tmp_path / "snapshots.sqlite3")
    created = client.post(
        "/api/v1/tasks",
        headers=headers(),
        json={"url": "https://example.test/expected"},
    )
    task_id = created.json()["id"]
    response = client.post(
        f"/api/v1/tasks/{task_id}/snapshot",
        headers=headers(),
        json={
            "schema_version": 1,
            "task_id": task_id,
            "requested_url": "https://evil.example/wrong",
            "final_url": "https://evil.example/wrong",
            "title": "Wrong",
            "captured_at": "2026-08-30T12:00:00Z",
            "status": "captured",
            "manual_verification_required": False,
            "visible_text": "",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Requested URL mismatch"
