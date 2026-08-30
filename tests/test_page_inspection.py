"""Tests for generic browser-inspection helpers."""

from app.page_inspection import classify_relevant_links, inspect_pages, is_social_url


def test_social_urls_are_recognized_without_matching_similar_hosts() -> None:
    assert is_social_url("https://www.instagram.com/example/")
    assert is_social_url("https://m.facebook.com/example/")
    assert not is_social_url("https://notinstagram.com/example/")


def test_social_page_is_skipped_without_starting_playwright() -> None:
    results = inspect_pages(("https://www.instagram.com/example/",))

    assert len(results) == 1
    assert results[0].status == "skipped_social"
    assert results[0].fields == ()


def test_relevant_links_are_classified_and_deduplicated() -> None:
    privacy, rules = classify_relevant_links(
        [
            {"text": "Tietosuojaseloste", "url": "https://example.test/privacy"},
            {"text": "Privacy", "url": "https://example.test/privacy"},
            {"text": "Kilpailun säännöt", "url": "https://example.test/rules"},
            {"text": "Other", "url": "mailto:test@example.test"},
        ]
    )

    assert privacy == ("https://example.test/privacy",)
    assert rules == ("https://example.test/rules",)
