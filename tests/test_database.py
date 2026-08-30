"""Tests for local SQLite competition persistence."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from app.database import (
    connect_database,
    get_competition,
    initialize_database,
    list_competitions,
    list_page_inspections,
    normalize_url,
    save_competitions,
    save_page_inspections,
)
from app.discovery import CompetitionCandidate
from app.page_inspection import FormField, PageInspection


@pytest.fixture
def database() -> Iterator[sqlite3.Connection]:
    # An in-memory database keeps every test isolated without writing local files.
    connection = connect_database(":memory:")
    initialize_database(connection)
    yield connection
    connection.close()


def make_candidate(**changes) -> CompetitionCandidate:
    values = {
        "url": "https://www.kilpailumaailma.com/voita-lahjakortti/",
        "source": "kilpailumaailma.com",
        "title": "Voita lahjakortti",
        "published_date": "30.8.2026",
        "platforms": ("Facebook", "Instagram"),
        "organizer": "Esimerkki Oy",
        "deadline": "05.09.2026 klo 12:00",
        "prize": "100 euron lahjakortti",
        "entry_urls": (
            "https://www.facebook.com/example",
            "https://www.instagram.com/example",
        ),
    }
    values.update(changes)
    return CompetitionCandidate(**values)


def test_initialize_database_creates_competitions_table(database) -> None:
    table = database.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'competitions'"
    ).fetchone()

    assert table["name"] == "competitions"
    assert database.execute("PRAGMA user_version").fetchone()[0] == 7


def test_save_page_inspection_preserves_fields_and_relevant_links(database) -> None:
    save_competitions(database, [make_candidate()])
    inspection = PageInspection(
        requested_url="https://example.test/enter",
        final_url="https://example.test/form",
        title="Competition form",
        status="completed",
        page_text="Phone numbers are used to contact the winner.",
        fields=(
            FormField(
                name="phone",
                field_type="tel",
                label="Puhelinnumero",
                required=True,
                placeholder=None,
                autocomplete="tel",
                frame_url="https://example.test/form",
            ),
        ),
        privacy_urls=("https://example.test/privacy",),
        rules_urls=("https://example.test/rules",),
        ai_snapshot='- textbox "Puhelinnumero" [ref=e1]',
        inspection_method="httpx_beautifulsoup",
        network_urls=("https://example.test/api/form",),
        iframe_urls=("https://forms.example.test/embed",),
    )

    save_page_inspections(database, 1, [inspection])
    stored = list_page_inspections(database, 1)

    assert len(stored) == 1
    assert stored[0].fields[0].field_type == "tel"
    assert stored[0].fields[0].required is True
    assert stored[0].privacy_urls == ("https://example.test/privacy",)
    assert stored[0].rules_urls == ("https://example.test/rules",)
    assert stored[0].ai_snapshot == '- textbox "Puhelinnumero" [ref=e1]'
    assert stored[0].inspection_method == "httpx_beautifulsoup"
    assert stored[0].network_urls == ("https://example.test/api/form",)
    assert stored[0].iframe_urls == ("https://forms.example.test/embed",)


def test_initialize_database_migrates_existing_inspection_table() -> None:
    connection = connect_database(":memory:")
    connection.execute(
        """
        CREATE TABLE page_inspections (
            id INTEGER PRIMARY KEY,
            competition_id INTEGER NOT NULL,
            requested_url TEXT NOT NULL
        )
        """
    )

    initialize_database(connection)
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(page_inspections)")
    }

    assert "ai_snapshot" in columns
    assert "inspection_method" in columns
    assert "manual_review_required" in columns
    assert "network_urls_json" in columns
    assert "iframe_urls_json" in columns
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
    connection.close()


def test_save_and_get_competition_preserves_metadata(database) -> None:
    observed_at = datetime(2026, 8, 30, 10, 15, tzinfo=UTC)

    summary = save_competitions(
        database,
        [make_candidate()],
        observed_at=observed_at,
    )
    stored = get_competition(database, 1)

    assert summary.inserted == 1
    assert summary.updated == 0
    assert stored is not None
    assert stored.title == "Voita lahjakortti"
    assert stored.platforms == ("Facebook", "Instagram")
    assert stored.organizer == "Esimerkki Oy"
    assert stored.deadline == "05.09.2026 klo 12:00"
    assert stored.prize == "100 euron lahjakortti"
    assert stored.entry_urls == (
        "https://www.facebook.com/example",
        "https://www.instagram.com/example",
    )
    assert stored.discovered_at == "2026-08-30T10:15:00+00:00"
    assert stored.last_seen_at == "2026-08-30T10:15:00+00:00"


def test_existing_competition_is_updated_without_changing_discovery_time(database) -> None:
    first_seen = datetime(2026, 8, 30, 10, 15, tzinfo=UTC)
    seen_again = datetime(2026, 8, 31, 11, 30, tzinfo=UTC)

    save_competitions(database, [make_candidate()], observed_at=first_seen)
    summary = save_competitions(
        database,
        [make_candidate(title="Updated title", prize="Updated prize")],
        observed_at=seen_again,
    )
    stored = get_competition(database, 1)

    assert summary.inserted == 0
    assert summary.updated == 1
    assert stored is not None
    assert stored.title == "Updated title"
    assert stored.prize == "Updated prize"
    assert stored.discovered_at == "2026-08-30T10:15:00+00:00"
    assert stored.last_seen_at == "2026-08-31T11:30:00+00:00"
    assert len(list_competitions(database)) == 1


def test_list_competitions_returns_most_recently_seen_first(database) -> None:
    first_time = datetime(2026, 8, 30, 10, tzinfo=UTC)
    second_time = datetime(2026, 8, 30, 11, tzinfo=UTC)

    save_competitions(database, [make_candidate()], observed_at=first_time)
    save_competitions(
        database,
        [
            make_candidate(
                url="https://www.kilpailumaailma.com/voita-puhelin/",
                title="Voita puhelin",
            )
        ],
        observed_at=second_time,
    )

    stored = list_competitions(database)

    assert [competition.title for competition in stored] == [
        "Voita puhelin",
        "Voita lahjakortti",
    ]


def test_get_competition_returns_none_for_unknown_id(database) -> None:
    assert get_competition(database, 999) is None


def test_normalize_url_removes_fragment_and_normalizes_host_case() -> None:
    normalized = normalize_url(
        "HTTPS://WWW.KILPAILUMAAILMA.COM/voita-palkinto/#details"
    )

    assert normalized == "https://www.kilpailumaailma.com/voita-palkinto/"


def test_normalize_url_rejects_non_http_url() -> None:
    with pytest.raises(ValueError, match="Invalid HTTP URL"):
        normalize_url("file:///tmp/competition.html")


def test_save_requires_timezone_aware_timestamp(database) -> None:
    with pytest.raises(ValueError, match="timezone"):
        save_competitions(
            database,
            [make_candidate()],
            observed_at=datetime(2026, 8, 30, 10, 15),
        )
