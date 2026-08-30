"""Tests for the command-line interface."""

import sqlite3
from datetime import UTC, datetime

import httpx

import main as cli
from app.database import connect_database, initialize_database, save_competitions
from app.discovery import CompetitionCandidate
from app.fetching import FetchedPage


def make_candidate() -> CompetitionCandidate:
    return CompetitionCandidate(
        url="https://www.kilpailumaailma.com/voita-palkinto/",
        source="kilpailumaailma.com",
        title="Voita palkinto",
        published_date="30.8.2026",
        platforms=("Facebook", "Instagram"),
        organizer="Test Oy",
        deadline="1.9.2026",
        prize="Testipalkinto",
        entry_urls=("https://instagram.com/example",),
    )


def make_stored_database():
    connection = connect_database(":memory:")
    initialize_database(connection)
    save_competitions(
        connection,
        [make_candidate()],
        observed_at=datetime(2026, 8, 30, 10, tzinfo=UTC),
    )
    return connection


def test_fetch_command_prints_response_summary(monkeypatch, capsys) -> None:
    def fake_fetch_page(url: str, *, timeout: float) -> FetchedPage:
        assert timeout == 5.0
        return FetchedPage(
            requested_url=url,
            final_url="https://example.test/final",
            status_code=200,
            html="<html>Test</html>",
        )

    monkeypatch.setattr(cli, "fetch_page", fake_fetch_page)

    exit_code = cli.main(["fetch", "https://example.test/start", "--timeout", "5"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "Final URL: https://example.test/final" in output.out
    assert "Status: 200" in output.out
    assert "HTML length: 17 characters" in output.out
    assert output.err == ""


def test_help_command_lists_available_commands(capsys) -> None:
    exit_code = cli.main(["help"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "snapshot-serve" in output.out
    assert "extension-inspect" in output.out
    assert "Show all available commands." in output.out


def test_discover_command_saves_and_prints_competition_metadata(
    monkeypatch,
    capsys,
) -> None:
    html = """
        <article class="post">
          <h2 class="entry-title">
            <a href="/voita-palkinto/">Voita palkinto</a>
          </h2>
          <time class="entry-date">28.8.2026</time>
          <div class="entry-content">
            <p>Alusta: Instagram Kilpailun järjestäjä: Test Oy Arvonta päättyy: 1.9.2026 Palkinto: Testipalkinto</p>
            <a href="https://instagram.com/example">Enter</a>
          </div>
        </article>
    """

    def fake_fetch_page(url: str, *, timeout: float) -> FetchedPage:
        return FetchedPage(
            requested_url=url,
            final_url="https://www.kilpailumaailma.com/",
            status_code=200,
            html=html,
        )

    monkeypatch.setattr(cli, "fetch_page", fake_fetch_page)

    exit_code = cli.main(["discover", "--database", ":memory:"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "Found 1 competition(s) from kilpailumaailma.com." in output.out
    assert "New: 1" in output.out
    assert "Updated: 0" in output.out
    assert "Database: :memory:" in output.out
    assert "1. Voita palkinto" in output.out
    assert "Platforms: Instagram" in output.out
    assert "Organizer: Test Oy" in output.out
    assert "Deadline: 1.9.2026" in output.out
    assert "Prize: Testipalkinto" in output.out
    assert "Entry URL: https://instagram.com/example" in output.out


def test_list_command_prints_stored_competitions(monkeypatch, capsys) -> None:
    connection = make_stored_database()
    monkeypatch.setattr(cli, "connect_database", lambda path: connection)

    exit_code = cli.main(["list", "--database", ":memory:"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "Stored competitions: 1" in output.out
    assert "Deadline" in output.out
    assert "Facebook, Instagram" in output.out
    assert "Voita palkinto" in output.out


def test_show_command_prints_all_stored_fields(monkeypatch, capsys) -> None:
    connection = make_stored_database()
    monkeypatch.setattr(cli, "connect_database", lambda path: connection)

    exit_code = cli.main(["show", "1", "--database", ":memory:"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "ID: 1" in output.out
    assert "Title: Voita palkinto" in output.out
    assert "Platforms: Facebook, Instagram" in output.out
    assert "Organizer: Test Oy" in output.out
    assert "Prize: Testipalkinto" in output.out
    assert "Discovered at: 2026-08-30T10:00:00+00:00" in output.out
    assert "  - https://instagram.com/example" in output.out


def test_show_command_reports_unknown_id(monkeypatch, capsys) -> None:
    connection = connect_database(":memory:")
    initialize_database(connection)
    monkeypatch.setattr(cli, "connect_database", lambda path: connection)

    exit_code = cli.main(["show", "999", "--database", ":memory:"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "Competition with ID 999 was not found." in output.err


def test_command_reports_network_error(monkeypatch, capsys) -> None:
    def fake_fetch_page(url: str, *, timeout: float) -> FetchedPage:
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("Connection refused", request=request)

    monkeypatch.setattr(cli, "fetch_page", fake_fetch_page)

    exit_code = cli.main(["fetch", "https://example.test"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "Request failed: Connection refused" in output.err


def test_discover_command_reports_database_error(monkeypatch, capsys) -> None:
    def fake_fetch_page(url: str, *, timeout: float) -> FetchedPage:
        return FetchedPage(
            requested_url=url,
            final_url="https://www.kilpailumaailma.com/",
            status_code=200,
            html="<html></html>",
        )

    def fail_to_connect(database_path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(cli, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(cli, "connect_database", fail_to_connect)

    exit_code = cli.main(["discover"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "Database save failed: database is locked" in output.err


def test_command_rejects_non_positive_timeout(capsys) -> None:
    exit_code = cli.main(["discover", "--timeout", "0"])

    output = capsys.readouterr()
    assert exit_code == 2
    assert "timeout must be greater than zero" in output.err
