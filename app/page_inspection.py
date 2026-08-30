"""Inspect public competition pages and forms with a headless browser."""

from dataclasses import dataclass, replace
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag


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
MIN_SUFFICIENT_TEXT_LENGTH = 100
CLOUDFLARE_MARKERS = (
    "just a moment",
    "attention required! | cloudflare",
    "checking your browser",
    "performing security verification",
    "verify you are human",
    "cf-chl-",
)
PARTICIPATION_LINK_KEYWORDS = (
    "osallistu",
    "siirry arvontaan",
    "täytä lomake",
    "tayta lomake",
    "enter competition",
    "enter now",
)
PERSONAL_FIELD_KEYWORDS = (
    "email", "e-mail", "sähköposti", "sahkoposti", "phone", "puhelin",
    "first_name", "last_name", "etunimi", "sukunimi", "address", "osoite",
    "city", "kaupunki", "zip", "postal", "postinumero",
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
    network_urls: tuple[str, ...] = ()
    iframe_urls: tuple[str, ...] = ()
    manual_review_required: bool = False
    inspection_method: str = "unknown"
    ai_snapshot: str = ""
    error_message: str | None = None


def inspect_pages(
    urls: tuple[str, ...],
    *,
    timeout_seconds: float = 15.0,
) -> list[PageInspection]:
    """Try static HTML first and use Playwright only when more data is needed."""

    results: list[PageInspection] = []
    fallback_urls: list[str] = []

    for url in urls:
        if is_social_url(url):
            results.append(_social_result(url))
            continue

        static_result = inspect_page_with_httpx(
            url,
            timeout_seconds=timeout_seconds,
        )
        if has_sufficient_data(static_result):
            results.append(static_result)
        else:
            fallback_urls.append(url)

    if not fallback_urls:
        return _order_results(urls, results)

    browser_results = _inspect_pages_with_playwright(
            tuple(fallback_urls),
            timeout_seconds=timeout_seconds,
    )
    results.extend(_mark_manual_review(result) for result in browser_results)
    return _order_results(urls, results)


def inspect_page_with_httpx(
    url: str,
    *,
    timeout_seconds: float = 15.0,
) -> PageInspection:
    """Download and parse one page without launching a browser."""

    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=timeout_seconds,
            headers={"User-Agent": "giveaway-agent/0.1"},
        )
    except httpx.TimeoutException as error:
        return _failed_result(
            url,
            str(error) or "HTTP request timed out.",
            status="timeout",
            method="httpx_beautifulsoup",
        )
    except httpx.RequestError as error:
        return _failed_result(
            url,
            str(error),
            status="failed",
            method="httpx_beautifulsoup",
        )

    result = parse_static_html(
        requested_url=url,
        final_url=str(response.url),
        http_status=response.status_code,
        html=response.text,
    )
    if has_sufficient_data(result):
        return result

    follow_url = _find_participation_url(response.text, str(response.url))
    if follow_url and follow_url != str(response.url) and not is_social_url(follow_url):
        try:
            followed = httpx.get(
                follow_url,
                follow_redirects=True,
                timeout=timeout_seconds,
                headers={"User-Agent": "giveaway-agent/0.1"},
            )
        except httpx.RequestError:
            return result
        followed_result = parse_static_html(
            requested_url=url,
            final_url=str(followed.url),
            http_status=followed.status_code,
            html=followed.text,
        )
        return replace(
            followed_result,
            inspection_method="httpx_beautifulsoup_followed_link",
        )
    return result


def parse_static_html(
    *,
    requested_url: str,
    final_url: str,
    http_status: int,
    html: str,
) -> PageInspection:
    """Extract page text, links, and form fields from downloaded HTML."""

    soup = BeautifulSoup(html, "html.parser")
    title = _clean_text(soup.title.get_text(" ", strip=True)) if soup.title else None

    for element in soup.select("script, style, noscript, template"):
        element.decompose()
    page_text = soup.get_text(" ", strip=True)
    fields = tuple(_filter_meaningful_fields(_static_form_fields(soup, final_url)))
    links = [
        {
            "url": urljoin(final_url, str(anchor.get("href", ""))),
            "text": anchor.get_text(" ", strip=True),
        }
        for anchor in soup.select("a[href]")
    ]
    privacy_urls, rules_urls = classify_relevant_links(links)
    iframe_urls = _deduplicate(
        [
            urljoin(final_url, str(frame.get("src")))
            for frame in soup.select("iframe[src]")
            if urljoin(final_url, str(frame.get("src"))).startswith(("http://", "https://"))
        ]
    )
    status, error_message = classify_inspection_status(
        http_status=http_status,
        title=title,
        page_text=page_text,
        field_count=len(fields),
    )
    return PageInspection(
        requested_url=requested_url,
        final_url=final_url,
        title=title,
        status=status,
        page_text=page_text[:MAX_PAGE_TEXT_LENGTH],
        fields=fields,
        privacy_urls=privacy_urls,
        rules_urls=rules_urls,
        iframe_urls=iframe_urls,
        inspection_method="httpx_beautifulsoup",
        ai_snapshot=_build_static_snapshot(soup, fields)[:MAX_AI_SNAPSHOT_LENGTH],
        error_message=error_message,
    )


def has_sufficient_data(inspection: PageInspection) -> bool:
    """Decide whether static HTML contains enough data to skip Playwright."""

    return (
        inspection.status == "completed_with_form"
        and _has_competition_form(inspection.fields)
        and len(inspection.page_text.strip()) >= MIN_SUFFICIENT_TEXT_LENGTH
    )


def _inspect_pages_with_playwright(
    urls: tuple[str, ...],
    *,
    timeout_seconds: float,
) -> list[PageInspection]:
    """Inspect fallback URLs with a JavaScript-capable browser."""

    results: list[PageInspection] = []

    # Import lazily so other commands still work before Playwright is installed.
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=False)
            try:
                for url in urls:
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
        results.extend(
            _failed_result(
                url,
                message,
                status=status,
                method="playwright_fallback",
            )
            for url in urls
        )

    return results


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
    network_urls: list[str] = []
    page.on(
        "response",
        lambda response: network_urls.append(response.url)
        if response.request.resource_type in {"xhr", "fetch"}
        else None,
    )
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

        fields = _filter_meaningful_fields(fields)
        privacy_urls, rules_urls = classify_relevant_links(links)
        iframe_urls = _deduplicate(
            [
                frame.url
                for frame in page.frames
                if frame != page.main_frame
                and frame.url.startswith(("http://", "https://"))
            ]
        )
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
            network_urls=_deduplicate(network_urls),
            iframe_urls=iframe_urls,
            inspection_method="playwright_fallback",
            ai_snapshot=ai_snapshot[:MAX_AI_SNAPSHOT_LENGTH],
            error_message=error_message,
        )
    except timeout_error as error:
        return _failed_result(
            url,
            str(error).splitlines()[0],
            status="timeout",
            method="playwright_fallback",
        )
    except playwright_error as error:
        return _failed_result(
            url,
            str(error).splitlines()[0],
            status="failed",
            method="playwright_fallback",
        )
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
        manual_review_required=True,
        inspection_method="none",
        error_message="Social-platform inspection is outside MVP 1.",
    )


def _failed_result(
    url: str,
    message: str,
    *,
    status: str,
    method: str,
) -> PageInspection:
    return PageInspection(
        requested_url=url,
        final_url=None,
        title=None,
        status=status,
        page_text="",
        fields=(),
        privacy_urls=(),
        rules_urls=(),
        inspection_method=method,
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


def _mark_manual_review(inspection: PageInspection) -> PageInspection:
    """Flag every unresolved or deliberately skipped inspection for a person."""

    resolved = inspection.status == "completed_with_form" and _has_competition_form(
        inspection.fields
    )
    return replace(inspection, manual_review_required=not resolved)


def _filter_meaningful_fields(fields: list[FormField]) -> list[FormField]:
    """Remove common site search and navigation controls."""

    filtered: list[FormField] = []
    for field in fields:
        searchable = f"{field.name or ''} {field.label or ''}".lower()
        if field.field_type == "search":
            continue
        if field.field_type == "checkbox" and any(
            word in searchable for word in ("valikko", "menu", "navigation")
        ):
            continue
        filtered.append(field)
    return filtered


def _has_competition_form(fields: tuple[FormField, ...] | list[FormField]) -> bool:
    """Require multiple personal-data signals instead of any page control."""

    signals = 0
    for field in fields:
        searchable = f"{field.name or ''} {field.label or ''}".lower()
        if field.field_type in {"email", "tel"} or any(
            keyword in searchable for keyword in PERSONAL_FIELD_KEYWORDS
        ):
            signals += 1
    return signals >= 2


def _find_participation_url(html: str, base_url: str) -> str | None:
    """Find one explicit participation link without clicking or submitting."""

    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href]"):
        searchable = f"{anchor.get_text(' ', strip=True)} {anchor.get('href', '')}".lower()
        if any(keyword in searchable for keyword in PARTICIPATION_LINK_KEYWORDS):
            candidate = urljoin(base_url, str(anchor.get("href")))
            if candidate.startswith(("http://", "https://")):
                return candidate
    return None


def _static_form_fields(soup: BeautifulSoup, frame_url: str) -> list[FormField]:
    """Convert editable HTML controls into the shared field model."""

    fields: list[FormField] = []
    ignored_types = {"hidden", "button", "submit", "reset", "image"}
    for element in soup.select("input, select, textarea"):
        field_type = str(element.get("type") or element.name).lower()
        if field_type in ignored_types:
            continue
        fields.append(
            FormField(
                name=_attribute_text(element, "name"),
                field_type=field_type,
                label=_static_label(soup, element),
                required=(
                    element.has_attr("required")
                    or str(element.get("aria-required", "")).lower() == "true"
                ),
                placeholder=_attribute_text(element, "placeholder"),
                autocomplete=_attribute_text(element, "autocomplete"),
                frame_url=frame_url,
            )
        )
    return fields


def _static_label(soup: BeautifulSoup, element: Tag) -> str | None:
    element_id = element.get("id")
    if element_id:
        explicit = soup.find("label", attrs={"for": element_id})
        if isinstance(explicit, Tag):
            return _clean_text(explicit.get_text(" ", strip=True))
    parent = element.find_parent("label")
    if isinstance(parent, Tag):
        return _clean_text(parent.get_text(" ", strip=True))
    return _attribute_text(element, "aria-label") or _attribute_text(
        element, "placeholder"
    )


def _attribute_text(element: Tag, name: str) -> str | None:
    value = element.get(name)
    if isinstance(value, list):
        value = " ".join(str(part) for part in value)
    return _clean_text(str(value)) if value is not None else None


def _clean_text(value: str) -> str | None:
    cleaned = " ".join(value.split())
    return cleaned or None


def _build_static_snapshot(
    soup: BeautifulSoup,
    fields: tuple[FormField, ...],
) -> str:
    """Build a compact LLM-readable snapshot when no browser is needed."""

    lines: list[str] = []
    for heading in soup.select("h1, h2, h3"):
        text = _clean_text(heading.get_text(" ", strip=True))
        if text:
            lines.append(f'- heading "{text}" [level={heading.name[1]}]')
    for index, field in enumerate(fields, start=1):
        name = field.label or field.name or "unlabelled"
        required = " [required]" if field.required else ""
        lines.append(
            f'- {field.field_type} "{name}" [ref=static{index}]{required}'
        )
    return "\n".join(lines)


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
    .filter((element) => !element.closest("header, nav, footer, [role=navigation]"))
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
