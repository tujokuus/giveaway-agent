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


def test_fetch_command_reports_network_error(monkeypatch, capsys) -> None:
    def fake_fetch_page(url: str, *, timeout: float) -> FetchedPage:
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("Connection refused", request=request)

    monkeypatch.setattr(cli, "fetch_page", fake_fetch_page)

    exit_code = cli.main(["fetch", "https://example.test"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "Fetch failed: Connection refused" in output.err


def test_fetch_command_rejects_non_positive_timeout(capsys) -> None:
    exit_code = cli.main(["fetch", "https://example.test", "--timeout", "0"])

    output = capsys.readouterr()
    assert exit_code == 2
    assert "timeout must be greater than zero" in output.err

