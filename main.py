"""Command-line entry point for Giveaway Agent."""

import argparse
import sys
from collections.abc import Sequence

import httpx

from app.fetching import DEFAULT_TIMEOUT_SECONDS, fetch_page


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(prog="giveaway-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Download one web page and print a response summary.",
    )
    fetch_parser.add_argument("url", help="The HTTP or HTTPS URL to download.")
    fetch_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS:g}).",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested command and return a process exit code."""

    args = build_parser().parse_args(argv)

    if args.timeout <= 0:
        print("Error: timeout must be greater than zero.", file=sys.stderr)
        return 2

    try:
        page = fetch_page(args.url, timeout=args.timeout)
    except httpx.HTTPError as error:
        # Keep expected network failures readable instead of showing a traceback.
        print(f"Fetch failed: {error}", file=sys.stderr)
        return 1

    print(f"Requested URL: {page.requested_url}")
    print(f"Final URL: {page.final_url}")
    print(f"Status: {page.status_code}")
    print(f"HTML length: {len(page.html)} characters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

