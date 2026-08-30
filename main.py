"""Command-line entry point for Giveaway Agent."""

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

import httpx

from app.database import (
    DEFAULT_DATABASE_PATH,
    StoredCompetition,
    connect_database,
    get_competition,
    initialize_database,
    list_competitions,
    list_page_inspections,
    save_competitions,
    save_page_inspections,
)
from app.discovery import CompetitionCandidate
from app.fetching import DEFAULT_TIMEOUT_SECONDS, FetchedPage, fetch_page
from app.sources.kilpailumaailma import SOURCE
from app.page_inspection import PageInspection, inspect_pages


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
    _add_database_argument(discover_parser)
    _add_timeout_argument(discover_parser)

    list_parser = subparsers.add_parser(
        "list",
        help="List competitions stored in the local database.",
    )
    _add_database_argument(list_parser)

    show_parser = subparsers.add_parser(
        "show",
        help="Show all stored information for one competition.",
    )
    show_parser.add_argument("id", type=int, help="Competition database ID.")
    _add_database_argument(show_parser)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Inspect the entry pages and forms for one stored competition.",
    )
    inspect_parser.add_argument("id", type=int, help="Competition database ID.")
    _add_database_argument(inspect_parser)
    _add_timeout_argument(inspect_parser)

    return parser


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared SQLite database path option to a subcommand."""

    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH}).",
    )


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

    if args.command == "list":
        return _list_stored_competitions(args.database)
    if args.command == "show":
        return _show_stored_competition(args.database, args.id)

    if args.timeout <= 0:
        print("Error: timeout must be greater than zero.", file=sys.stderr)
        return 2

    if args.command == "inspect":
        return _inspect_stored_competition(args.database, args.id, args.timeout)

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
        with closing(connect_database(database_path)) as connection:
            initialize_database(connection)
            summary = save_competitions(connection, candidates)
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


def _list_stored_competitions(database_path: Path) -> int:
    """Print a compact table of all stored competitions."""

    try:
        with closing(connect_database(database_path)) as connection:
            initialize_database(connection)
            competitions = list_competitions(connection)
    except (OSError, sqlite3.Error) as error:
        print(f"Database read failed: {error}", file=sys.stderr)
        return 1

    print(f"Stored competitions: {len(competitions)}")
    print(f"Database: {_display_database_path(database_path)}")

    if not competitions:
        print("No competitions found. Run 'python main.py discover' first.")
        return 0

    print()
    print(f"{'ID':>4}  {'Deadline':<20}  {'Platforms':<20}  Title")
    print(f"{'-' * 4}  {'-' * 20}  {'-' * 20}  {'-' * 50}")

    for competition in competitions:
        deadline = _truncate(competition.deadline or "-", 20)
        platforms = _truncate(", ".join(competition.platforms) or "-", 20)
        title = _truncate(competition.title, 70)
        print(f"{competition.id:>4}  {deadline:<20}  {platforms:<20}  {title}")

    return 0


def _show_stored_competition(database_path: Path, competition_id: int) -> int:
    """Print every stored field for one competition."""

    try:
        with closing(connect_database(database_path)) as connection:
            initialize_database(connection)
            competition = get_competition(connection, competition_id)
            inspections = list_page_inspections(connection, competition_id)
    except (OSError, sqlite3.Error) as error:
        print(f"Database read failed: {error}", file=sys.stderr)
        return 1

    if competition is None:
        print(f"Competition with ID {competition_id} was not found.", file=sys.stderr)
        return 1

    _print_stored_competition(competition)
    if inspections:
        print("Inspections:")
        for inspection in inspections:
            print(f"  - {inspection.status}: {inspection.requested_url}")
            for privacy_url in inspection.privacy_urls:
                print(f"    Privacy: {privacy_url}")
            for rules_url in inspection.rules_urls:
                print(f"    Rules: {rules_url}")
    return 0


def _inspect_stored_competition(
    database_path: Path,
    competition_id: int,
    timeout_seconds: float,
) -> int:
    """Inspect and save every entry page for one stored competition."""

    try:
        with closing(connect_database(database_path)) as connection:
            initialize_database(connection)
            competition = get_competition(connection, competition_id)
    except (OSError, sqlite3.Error) as error:
        print(f"Database read failed: {error}", file=sys.stderr)
        return 1

    if competition is None:
        print(f"Competition with ID {competition_id} was not found.", file=sys.stderr)
        return 1
    if not competition.entry_urls:
        print("This competition has no entry URLs to inspect.", file=sys.stderr)
        return 1

    try:
        inspections = inspect_pages(
            competition.entry_urls,
            timeout_seconds=timeout_seconds,
        )
    except ImportError:
        print(
            "Playwright is not installed. Run 'python -m pip install -e \".[dev]\"'.",
            file=sys.stderr,
        )
        return 1

    try:
        with closing(connect_database(database_path)) as connection:
            initialize_database(connection)
            save_page_inspections(connection, competition_id, inspections)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"Database save failed: {error}", file=sys.stderr)
        return 1

    print(f"Inspected {len(inspections)} entry URL(s) for competition {competition_id}.")
    for inspection in inspections:
        _print_page_inspection(inspection)
    return 0


def _print_page_inspection(inspection: PageInspection) -> None:
    """Print the useful parts of one browser inspection."""

    print()
    print(f"Status: {inspection.status}")
    print(f"URL: {inspection.requested_url}")
    if inspection.final_url and inspection.final_url != inspection.requested_url:
        print(f"Final URL: {inspection.final_url}")
    if inspection.title:
        print(f"Title: {inspection.title}")
    if inspection.error_message:
        print(f"Note: {inspection.error_message}")
    print(f"Form fields: {len(inspection.fields)}")
    for field in inspection.fields:
        required = "required" if field.required else "optional"
        identity = field.label or field.name or "unlabelled"
        print(f"  - {identity} ({field.field_type}, {required})")
    print("Privacy links:")
    _print_urls(inspection.privacy_urls)
    print("Rules or terms links:")
    _print_urls(inspection.rules_urls)


def _print_urls(urls: tuple[str, ...]) -> None:
    if urls:
        for url in urls:
            print(f"  - {url}")
    else:
        print("  -")


def _print_stored_competition(competition: StoredCompetition) -> None:
    """Print one stored competition without truncating field values."""

    print(f"ID: {competition.id}")
    print(f"Title: {competition.title}")
    print(f"Source: {competition.source}")
    print(f"URL: {competition.url}")
    print(f"Published: {competition.published_date or '-'}")
    print(f"Platforms: {', '.join(competition.platforms) or '-'}")
    print(f"Organizer: {competition.organizer or '-'}")
    print(f"Deadline: {competition.deadline or '-'}")
    print(f"Prize: {competition.prize or '-'}")
    print(f"Discovered at: {competition.discovered_at}")
    print(f"Last seen at: {competition.last_seen_at}")
    print("Entry URLs:")

    if competition.entry_urls:
        for entry_url in competition.entry_urls:
            print(f"  - {entry_url}")
    else:
        print("  -")


def _display_database_path(database_path: Path) -> str:
    """Return a readable database path without changing SQLite special names."""

    if str(database_path) == ":memory:":
        return ":memory:"
    return str(database_path.resolve())


def _truncate(value: str, width: int) -> str:
    """Truncate one table value while preserving the requested column width."""

    if len(value) <= width:
        return value
    return f"{value[: width - 1]}…"


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
