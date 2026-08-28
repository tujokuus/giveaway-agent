"""Tests for HTTP page fetching."""

import httpx
import pytest

from app.fetching import fetch_page


def test_fetch_page_returns_downloaded_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"].startswith("giveaway-agent/")
        return httpx.Response(200, text="<html><h1>Giveaway</h1></html>")

    page = fetch_page(
        "https://example.test/competitions",
        transport=httpx.MockTransport(handler),
    )

    assert page.requested_url == "https://example.test/competitions"
    assert page.final_url == "https://example.test/competitions"
    assert page.status_code == 200
    assert page.html == "<html><h1>Giveaway</h1></html>"


def test_fetch_page_follows_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"Location": "/competitions"})
        return httpx.Response(200, text="<html>Competitions</html>")

    page = fetch_page(
        "https://example.test/old",
        transport=httpx.MockTransport(handler),
    )

    assert page.final_url == "https://example.test/competitions"
    assert page.status_code == 200


def test_fetch_page_raises_for_http_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(404, text="Not found")
    )

    with pytest.raises(httpx.HTTPStatusError):
        fetch_page("https://example.test/missing", transport=transport)

