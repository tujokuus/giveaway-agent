"""HTTP page fetching for Giveaway Agent."""

from dataclasses import dataclass

import httpx


DEFAULT_TIMEOUT_SECONDS = 10.0
USER_AGENT = "giveaway-agent/0.1 (local learning project)"


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """The essential result of a successful page request."""

    requested_url: str
    final_url: str
    status_code: int
    html: str


def fetch_page(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
) -> FetchedPage:
    """Download one page and raise an HTTPX error if the request fails."""

    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": USER_AGENT,
    }

    # A client reuses connections and gives all requests the same safe defaults.
    with httpx.Client(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
        transport=transport,
    ) as client:
        response = client.get(url)
        response.raise_for_status()

    return FetchedPage(
        requested_url=url,
        final_url=str(response.url),
        status_code=response.status_code,
        html=response.text,
    )

