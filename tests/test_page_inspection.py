"""Tests for generic browser-inspection helpers."""

from app.page_inspection import (
    classify_inspection_status,
    classify_relevant_links,
    inspect_pages,
    is_social_url,
)


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


def test_cloudflare_challenge_is_detected_even_with_success_status() -> None:
    status, note = classify_inspection_status(
        http_status=200,
        title="Just a moment...",
        page_text="Performing security verification",
        field_count=0,
    )

    assert status == "blocked_by_cloudflare"
    assert note == "Cloudflare challenge page detected (HTTP 200)"


def test_access_denial_without_cloudflare_markers_is_classified_generically() -> None:
    status, note = classify_inspection_status(
        http_status=403,
        title="Forbidden",
        page_text="Access denied",
        field_count=0,
    )

    assert status == "blocked_access"
    assert note == "HTTP 403"


def test_successful_pages_are_classified_by_form_presence() -> None:
    with_form = classify_inspection_status(
        http_status=200,
        title="Enter",
        page_text="Competition form",
        field_count=2,
    )
    without_form = classify_inspection_status(
        http_status=200,
        title="Rules",
        page_text="Competition rules",
        field_count=0,
    )

    assert with_form == ("completed_with_form", None)
    assert without_form == ("completed_no_form", None)
