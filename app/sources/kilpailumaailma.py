"""Competition discovery from Kilpailumaailma listing pages."""

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from app.discovery import CompetitionCandidate, CompetitionSource


DETAIL_LABEL_PATTERN = re.compile(
    r"(Alusta|Kilpailun järjestäjä|Arvonta päättyy|Palkinto):"
)


class KilpailumaailmaSource(CompetitionSource):
    """Extract competition cards shown on Kilpailumaailma."""

    name = "kilpailumaailma.com"
    default_url = "https://www.kilpailumaailma.com/"
    card_selector = "article.post"
    title_link_selector = "h2.entry-title a[href]"
    details_selector = ".entry-content p"

    def discover(
        self,
        html: str,
        *,
        page_url: str,
    ) -> list[CompetitionCandidate]:
        """Extract unique competition cards from Kilpailumaailma HTML."""

        soup = BeautifulSoup(html, "html.parser")
        source_host = urlsplit(page_url).hostname
        candidates: list[CompetitionCandidate] = []
        seen_urls: set[str] = set()

        for card in soup.select(self.card_selector):
            title_link = card.select_one(self.title_link_selector)
            if title_link is None:
                continue

            href = title_link.get("href")
            title = title_link.get_text(" ", strip=True)
            if not isinstance(href, str) or not title:
                continue

            detail_url = _normalize_url(urljoin(page_url, href))
            if urlsplit(detail_url).hostname != source_host:
                continue
            if detail_url in seen_urls:
                continue

            details_text = _find_details_text(card, self.details_selector)
            details = _parse_labeled_details(details_text)

            # Promotional articles can share the same card markup as competitions.
            if not details.get("Arvonta päättyy") or not details.get("Palkinto"):
                continue

            platforms = tuple(
                platform.strip()
                for platform in details.get("Alusta", "").split(",")
                if platform.strip()
            )

            published_element = card.select_one("time.entry-date")
            published_date = (
                published_element.get_text(" ", strip=True)
                if published_element is not None
                else None
            )

            seen_urls.add(detail_url)
            candidates.append(
                CompetitionCandidate(
                    url=detail_url,
                    source=self.name,
                    title=title,
                    published_date=published_date or None,
                    platforms=platforms,
                    organizer=details.get("Kilpailun järjestäjä"),
                    deadline=details.get("Arvonta päättyy"),
                    prize=details.get("Palkinto"),
                    entry_urls=_extract_entry_urls(card, page_url, detail_url),
                )
            )

        return candidates


def _find_details_text(card, selector: str) -> str:
    """Find the content paragraph that contains labeled competition details."""

    for element in card.select(selector):
        text = element.get_text(" ", strip=True)
        if DETAIL_LABEL_PATTERN.search(text):
            return text

    return ""


def _parse_labeled_details(text: str) -> dict[str, str]:
    """Split a card summary into values identified by Finnish labels."""

    matches = list(DETAIL_LABEL_PATTERN.finditer(text))
    details: dict[str, str] = {}

    for index, match in enumerate(matches):
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[value_start:value_end].strip()
        if value:
            details[match.group(1)] = value

    return details


def _extract_entry_urls(card, page_url: str, detail_url: str) -> tuple[str, ...]:
    """Collect unique participation links from a competition card."""

    urls: list[str] = []
    seen_urls: set[str] = set()

    for link in card.select(".entry-content a[href]"):
        href = link.get("href")
        if not isinstance(href, str):
            continue

        url = _normalize_url(urljoin(page_url, href))
        if url == detail_url or url in seen_urls:
            continue
        if urlsplit(url).scheme not in {"http", "https"}:
            continue

        seen_urls.add(url)
        urls.append(url)

    return tuple(urls)


def _normalize_url(url: str) -> str:
    """Remove a fragment because it does not identify a different page."""

    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


SOURCE = KilpailumaailmaSource()
