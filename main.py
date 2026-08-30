"""Command-line entry point for Giveaway Agent."""

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx

from app.database import (
    DEFAULT_DATABASE_PATH,
    connect_database,
    initialize_database,
    save_competitions,
)
from app.discovery import CompetitionCandidate
from app.fetching import DEFAULT_TIMEOUT_SECONDS, FetchedPage, fetch_page
from app.sources.kilpailumaailma import SOURCE


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(prog="giveaway-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Download one web page and print a response summary.",
    )
    fetch_parser.add_argument("url", help="The HTTP or HTTPS URL to download.")
    _add_timeout_argument(fetch_parser)

    discover_parser = subparsers.add_parser(
        "discover",
        help=f"Find and save competition cards from {SOURCE.name}.",
    )
    discover_parser.add_argument(
        "url",
        nargs="?",
        default=SOURCE.default_url,
        help=f"Listing page URL (default: {SOURCE.default_url}).",
    )
    discover_parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH}).",
    )
    _add_timeout_argument(discover_parser)

    return parser


def _add_timeout_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared request timeout option to a subcommand."""

    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS:g}).",
    )


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
        print(f"Request failed: {error}", file=sys.stderr)
        return 1

    if args.command == "fetch":
        _print_fetch_summary(page)
        return 0

    return _discover_and_save(page, args.database)


def _print_fetch_summary(page: FetchedPage) -> None:
    """Print a short summary of a downloaded page."""

    print(f"Requested URL: {page.requested_url}")
    print(f"Final URL: {page.final_url}")
    print(f"Status: {page.status_code}")
    print(f"HTML length: {len(page.html)} characters")


def _discover_and_save(page: FetchedPage, database_path: Path) -> int:
    """Extract competitions and persist the complete discovery batch."""

    candidates = SOURCE.discover(page.html, page_url=page.final_url)

    try:
        connection = connect_database(database_path)
        try:
            initialize_database(connection)
            summary = save_competitions(connection, candidates)
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"Database save failed: {error}", file=sys.stderr)
        return 1

    print(f"Found {len(candidates)} competition(s) from {SOURCE.name}.")
    print(f"New: {summary.inserted}")
    print(f"Updated: {summary.updated}")
    print(f"Database: {_display_database_path(database_path)}")

    for index, candidate in enumerate(candidates, start=1):
        _print_candidate(index, candidate)

    return 0


def _display_database_path(database_path: Path) -> str:
    """Return a readable database path without changing SQLite special names."""

    if str(database_path) == ":memory:":
        return ":memory:"
    return str(database_path.resolve())


def _print_candidate(index: int, candidate: CompetitionCandidate) -> None:
    """Print one discovered competition in a readable format."""

    print()
    print(f"{index}. {candidate.title}")
    print(f"   URL: {candidate.url}")

    if candidate.published_date:
        print(f"   Published: {candidate.published_date}")
    if candidate.platforms:
        print(f"   Platforms: {', '.join(candidate.platforms)}")
    if candidate.organizer:
        print(f"   Organizer: {candidate.organizer}")
    if candidate.deadline:
        print(f"   Deadline: {candidate.deadline}")
    if candidate.prize:
        print(f"   Prize: {candidate.prize}")
    for entry_url in candidate.entry_urls:
        print(f"   Entry URL: {entry_url}")


def _configure_output_encoding() -> None:
    """Use UTF-8 for competition text printed in Windows terminals."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    _configure_output_encoding()
    raise SystemExit(main())
