"""Tests for the Kilpailumaailma competition source."""

from pathlib import Path

from app.discovery import CompetitionSource
from app.sources.kilpailumaailma import SOURCE, KilpailumaailmaSource


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "kilpailumaailma_listing.html"


def test_source_implements_shared_interface() -> None:
    assert isinstance(SOURCE, CompetitionSource)
    assert isinstance(SOURCE, KilpailumaailmaSource)


def test_discover_extracts_listing_metadata() -> None:
    html = FIXTURE_PATH.read_text(encoding="utf-8")

    candidates = SOURCE.discover(html, page_url=SOURCE.default_url)

    first = candidates[0]
    assert first.title == "Voita vaate"
    assert first.url == "https://www.kilpailumaailma.com/voita-vaate/"
    assert first.published_date == "28.8.2026"
    assert first.platforms == ("Facebook", "Instagram")
    assert first.organizer == "Papu Design"
    assert first.deadline == "31.08.2026"
    assert first.prize == "paita, housut tai mekko"
    assert first.entry_urls == (
        "https://www.facebook.com/example",
        "https://www.instagram.com/example",
    )


def test_discover_ignores_duplicates_external_articles_and_promotions() -> None:
    html = FIXTURE_PATH.read_text(encoding="utf-8")

    candidates = SOURCE.discover(html, page_url=SOURCE.default_url)

    assert [candidate.title for candidate in candidates] == [
        "Voita vaate",
        "Voita 100 euron lahjakortti",
    ]
    assert candidates[1].entry_urls == ("https://example.com/enter",)


def test_discover_returns_empty_list_without_cards() -> None:
    candidates = SOURCE.discover(
        "<html><body>No competitions</body></html>",
        page_url=SOURCE.default_url,
    )

    assert candidates == []

