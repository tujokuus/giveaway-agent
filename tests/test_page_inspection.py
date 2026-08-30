"""Tests for generic browser-inspection helpers."""

from app.page_inspection import (
    PageInspection,
    classify_inspection_status,
    classify_relevant_links,
    has_sufficient_data,
    inspect_pages,
    is_social_url,
    parse_static_html,
)
import app.page_inspection as inspection_module


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


def test_static_html_parser_extracts_form_and_relevant_links() -> None:
    result = parse_static_html(
        requested_url="https://example.test/start",
        final_url="https://example.test/competition",
        http_status=200,
        html="""
            <html><head><title>Win a prize</title></head><body>
              <h1>Competition</h1>
              <p>Enter the competition by completing all requested details below.</p>
              <form>
                <label for="phone">Puhelinnumero</label>
                <input id="phone" name="phone" type="tel" required>
                <label for="email">Sähköposti</label>
                <input id="email" name="email" type="email" required>
              </form>
              <a href="/privacy">Tietosuojaseloste</a>
              <a href="/rules">Kilpailun säännöt</a>
            </body></html>
        """,
    )

    assert result.status == "completed_with_form"
    assert result.inspection_method == "httpx_beautifulsoup"
    assert result.fields[0].label == "Puhelinnumero"
    assert result.fields[0].required is True
    assert result.privacy_urls == ("https://example.test/privacy",)
    assert result.rules_urls == ("https://example.test/rules",)
    assert "heading" in result.ai_snapshot
    assert has_sufficient_data(result)


def test_inspection_uses_playwright_when_static_data_is_insufficient(
    monkeypatch,
) -> None:
    static_result = PageInspection(
        requested_url="https://example.test",
        final_url="https://example.test",
        title="Empty shell",
        status="completed_no_form",
        page_text="Loading",
        fields=(),
        privacy_urls=(),
        rules_urls=(),
        inspection_method="httpx_beautifulsoup",
    )
    browser_result = PageInspection(
        requested_url="https://example.test",
        final_url="https://example.test",
        title="Competition",
        status="completed_with_form",
        page_text="Competition form loaded by JavaScript",
        fields=(),
        privacy_urls=(),
        rules_urls=(),
        inspection_method="playwright_fallback",
    )
    monkeypatch.setattr(
        inspection_module,
        "inspect_page_with_httpx",
        lambda url, timeout_seconds: static_result,
    )
    monkeypatch.setattr(
        inspection_module,
        "_inspect_pages_with_playwright",
        lambda urls, timeout_seconds: [browser_result],
    )

    results = inspect_pages(("https://example.test",))

    assert results[0].inspection_method == "playwright_fallback"
    assert results[0].manual_review_required is True


def test_static_parser_ignores_search_and_navigation_controls() -> None:
    result = parse_static_html(
        requested_url="https://example.test/article",
        final_url="https://example.test/article",
        http_status=200,
        html="""
            <html><body>
              <header><input type="search" name="s"></header>
              <nav><label><input type="checkbox">Valikko</label></nav>
              <main><p>This is a competition article without an entry form.</p></main>
            </body></html>
        """,
    )

    assert result.status == "completed_no_form"
    assert result.fields == ()
    assert has_sufficient_data(result) is False
