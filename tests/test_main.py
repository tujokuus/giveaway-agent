"""Tests for the command-line interface."""

import httpx

import main as cli
from app.fetching import FetchedPage


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


def test_discover_command_prints_competition_metadata(monkeypatch, capsys) -> None:
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

    exit_code = cli.main(["discover"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "Found 1 competition(s) from kilpailumaailma.com." in output.out
    assert "1. Voita palkinto" in output.out
    assert "Platforms: Instagram" in output.out
    assert "Organizer: Test Oy" in output.out
    assert "Deadline: 1.9.2026" in output.out
    assert "Prize: Testipalkinto" in output.out
    assert "Entry URL: https://instagram.com/example" in output.out


def test_command_reports_network_error(monkeypatch, capsys) -> None:
    def fake_fetch_page(url: str, *, timeout: float) -> FetchedPage:
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("Connection refused", request=request)

    monkeypatch.setattr(cli, "fetch_page", fake_fetch_page)

    exit_code = cli.main(["fetch", "https://example.test"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "Request failed: Connection refused" in output.err


def test_command_rejects_non_positive_timeout(capsys) -> None:
    exit_code = cli.main(["discover", "--timeout", "0"])

    output = capsys.readouterr()
    assert exit_code == 2
    assert "timeout must be greater than zero" in output.err

