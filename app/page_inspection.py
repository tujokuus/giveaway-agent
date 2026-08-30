"""Inspect public competition pages and forms with a headless browser."""

from dataclasses import dataclass
from urllib.parse import urlsplit


SOCIAL_HOSTS = (
    "facebook.com",
    "fb.com",
    "instagram.com",
    "tiktok.com",
)
PRIVACY_KEYWORDS = ("privacy", "tietosuoja", "rekisteriseloste")
RULES_KEYWORDS = ("rules", "terms", "conditions", "säännöt", "saannot", "ehdot")
MAX_PAGE_TEXT_LENGTH = 50_000
MAX_AI_SNAPSHOT_LENGTH = 100_000
CLOUDFLARE_MARKERS = (
    "just a moment",
    "attention required! | cloudflare",
    "checking your browser",
    "performing security verification",
    "verify you are human",
    "cf-chl-",
)


@dataclass(frozen=True, slots=True)
class FormField:
    """One user-editable field found on a page or embedded frame."""

    name: str | None
    field_type: str
    label: str | None
    required: bool
    placeholder: str | None
    autocomplete: str | None
    frame_url: str


@dataclass(frozen=True, slots=True)
class PageInspection:
    """Structured result from inspecting one competition entry URL."""

    requested_url: str
    final_url: str | None
    title: str | None
    status: str
    page_text: str
    fields: tuple[FormField, ...]
    privacy_urls: tuple[str, ...]
    rules_urls: tuple[str, ...]
    ai_snapshot: str = ""
    error_message: str | None = None


def inspect_pages(
    urls: tuple[str, ...],
    *,
    timeout_seconds: float = 15.0,
) -> list[PageInspection]:
    """Inspect regular web pages and deliberately skip social platforms."""

    results: list[PageInspection] = []
    browser_urls = [url for url in urls if not is_social_url(url)]

    for url in urls:
        if is_social_url(url):
            results.append(_social_result(url))

    if not browser_urls:
        return _order_results(urls, results)

    # Import lazily so other commands still work before Playwright is installed.
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=False)
            try:
                for url in browser_urls:
                    results.append(
                        _inspect_page(
                            context,
                            url,
                            timeout_ms=timeout_seconds * 1000,
                            playwright_error=PlaywrightError,
                            timeout_error=PlaywrightTimeoutError,
                        )
                    )
            finally:
                context.close()
                browser.close()
    except PlaywrightError as error:
        message = str(error).splitlines()[0]
        status = (
            "browser_not_installed"
            if "executable doesn't exist" in message.lower()
            else "failed"
        )
        results.extend(_failed_result(url, message, status=status) for url in browser_urls)

    return _order_results(urls, results)


def is_social_url(url: str) -> bool:
    """Return whether a URL belongs to a social platform skipped in MVP 1."""

    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in SOCIAL_HOSTS)


def classify_relevant_links(
    links: list[dict[str, str]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Classify privacy and rules links using their text and URL."""

    privacy_urls: list[str] = []
    rules_urls: list[str] = []

    for link in links:
        url = link.get("url", "").strip()
        searchable = f"{link.get('text', '')} {url}".lower()
        if not url.startswith(("http://", "https://")):
            continue
        if any(keyword in searchable for keyword in PRIVACY_KEYWORDS):
            privacy_urls.append(url)
        if any(keyword in searchable for keyword in RULES_KEYWORDS):
            rules_urls.append(url)

    return _deduplicate(privacy_urls), _deduplicate(rules_urls)


def classify_inspection_status(
    *,
    http_status: int | None,
    title: str | None,
    page_text: str,
    field_count: int,
) -> tuple[str, str | None]:
    """Classify a loaded page without claiming every access denial is Cloudflare."""

    searchable = f"{title or ''} {page_text}".lower()
    if any(marker in searchable for marker in CLOUDFLARE_MARKERS):
        note = "Cloudflare challenge page detected"
        if http_status is not None:
            note = f"{note} (HTTP {http_status})"
        return "blocked_by_cloudflare", note
    if http_status in {401, 403, 429}:
        return "blocked_access", f"HTTP {http_status}"
    if http_status is not None and http_status >= 400:
        return "http_error", f"HTTP {http_status}"
    if field_count:
        return "completed_with_form", None
    return "completed_no_form", None


def _inspect_page(
    context,
    url: str,
    *,
    timeout_ms: float,
    playwright_error,
    timeout_error,
) -> PageInspection:
    """Inspect one page and every same- or cross-origin frame Playwright can read."""

    page = context.new_page()
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(500)
        fields: list[FormField] = []
        links: list[dict[str, str]] = []

        for frame in page.frames:
            try:
                snapshot = frame.evaluate(_SNAPSHOT_SCRIPT)
            except playwright_error:
                continue
            links.extend(snapshot["links"])
            fields.extend(
                FormField(
                    name=item["name"],
                    field_type=item["type"],
                    label=item["label"],
                    required=item["required"],
                    placeholder=item["placeholder"],
                    autocomplete=item["autocomplete"],
                    frame_url=frame.url,
                )
                for item in snapshot["fields"]
            )

        privacy_urls, rules_urls = classify_relevant_links(links)
        page_text = page.locator("body").inner_text(timeout=timeout_ms)
        try:
            ai_snapshot = page.aria_snapshot(mode="ai", timeout=timeout_ms)
        except playwright_error:
            # The other inspection data remains useful if snapshot capture fails.
            ai_snapshot = ""
        http_status = response.status if response is not None else None
        title = page.title() or None
        status, error_message = classify_inspection_status(
            http_status=http_status,
            title=title,
            page_text=page_text,
            field_count=len(fields),
        )
        return PageInspection(
            requested_url=url,
            final_url=page.url,
            title=title,
            status=status,
            page_text=page_text[:MAX_PAGE_TEXT_LENGTH],
            fields=tuple(fields),
            privacy_urls=privacy_urls,
            rules_urls=rules_urls,
            ai_snapshot=ai_snapshot[:MAX_AI_SNAPSHOT_LENGTH],
            error_message=error_message,
        )
    except timeout_error as error:
        return _failed_result(url, str(error).splitlines()[0], status="timeout")
    except playwright_error as error:
        return _failed_result(url, str(error).splitlines()[0], status="failed")
    finally:
        page.close()


def _social_result(url: str) -> PageInspection:
    return PageInspection(
        requested_url=url,
        final_url=None,
        title=None,
        status="skipped_social",
        page_text="",
        fields=(),
        privacy_urls=(),
        rules_urls=(),
        error_message="Social-platform inspection is outside MVP 1.",
    )


def _failed_result(url: str, message: str, *, status: str) -> PageInspection:
    return PageInspection(
        requested_url=url,
        final_url=None,
        title=None,
        status=status,
        page_text="",
        fields=(),
        privacy_urls=(),
        rules_urls=(),
        error_message=message,
    )


def _order_results(
    urls: tuple[str, ...], results: list[PageInspection]
) -> list[PageInspection]:
    """Restore source URL order after social URLs were handled separately."""

    positions = {url: index for index, url in enumerate(urls)}
    return sorted(results, key=lambda result: positions[result.requested_url])


def _deduplicate(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


_SNAPSHOT_SCRIPT = r"""
() => {
  const clean = (value) => value ? value.replace(/\s+/g, " ").trim() || null : null;
  const labelFor = (element) => {
    if (element.id) {
      const explicit = document.querySelector(`label[for="${CSS.escape(element.id)}"]`);
      if (explicit) return clean(explicit.innerText);
    }
    const wrapping = element.closest("label");
    return clean(element.getAttribute("aria-label")) ||
      (wrapping ? clean(wrapping.innerText) : null) ||
      clean(element.getAttribute("placeholder"));
  };
  const ignoredTypes = new Set(["hidden", "button", "submit", "reset", "image"]);
  const fields = [...document.querySelectorAll("input, select, textarea")]
    .filter((element) => !ignoredTypes.has((element.type || "").toLowerCase()))
    .map((element) => ({
      name: clean(element.getAttribute("name")),
      type: (element.type || element.tagName).toLowerCase(),
      label: labelFor(element),
      required: element.required || element.getAttribute("aria-required") === "true",
      placeholder: clean(element.getAttribute("placeholder")),
      autocomplete: clean(element.getAttribute("autocomplete")),
    }));
  const links = [...document.querySelectorAll("a[href]")].map((element) => ({
    url: element.href,
    text: clean(element.innerText) || "",
  }));
  return {fields, links};
}
"""
