"""Command-line entry point for Giveaway Agent."""

import argparse
import json
import sqlite3
import sys
import threading
import time
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

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
from app.snapshot_api import (
    DEFAULT_TOKEN_PATH,
    create_app,
    initialize_snapshot_schema,
    load_or_create_api_token,
)
from app.snapshot_prepare import load_prepared_package, prepare_snapshot_package
from app.snapshot_compact import compact_snapshot_package, load_compact_package
from app.llm_analysis import analyze_compact_package, load_llm_analysis


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(
        prog="giveaway-agent",
        description="Discover and locally inspect online competitions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "help",
        help="Show all available commands.",
    )

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

    serve_parser = subparsers.add_parser(
        "snapshot-serve",
        help="Run the read-only Chrome Extension snapshot API on localhost.",
    )
    _add_database_argument(serve_parser)
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_PATH)

    extension_parser = subparsers.add_parser(
        "extension-inspect",
        help="Queue a stored competition for the read-only Chrome Extension.",
    )
    extension_parser.add_argument("id", type=int, help="Competition database ID.")
    _add_database_argument(extension_parser)
    extension_parser.add_argument("--server", default="http://127.0.0.1:8765")
    extension_parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_PATH)

    run_parser = subparsers.add_parser(
        "snapshot-run",
        help="Read, prepare, compact, and show one stored competition.",
    )
    run_parser.add_argument(
        "id",
        type=int,
        help="Competition database ID from the 'list' command.",
    )
    _add_database_argument(run_parser)
    run_parser.add_argument("--server", default="http://127.0.0.1:8765")
    run_parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_PATH)
    run_parser.add_argument(
        "--wait",
        type=float,
        default=180,
        help="Maximum seconds to wait for Chrome snapshots (default: 180).",
    )

    easy_run_parser = subparsers.add_parser(
        "giveaway-run",
        help="Run the complete browser, compact, and local-LLM pipeline.",
    )
    easy_run_parser.add_argument(
        "id",
        type=int,
        help="Competition database ID from the 'list' command.",
    )
    _add_database_argument(easy_run_parser)
    easy_run_parser.add_argument("--server", default="http://127.0.0.1:8765")
    easy_run_parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_PATH)
    easy_run_parser.add_argument("--model", default="qwen3.5:9b")
    easy_run_parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    easy_run_parser.add_argument(
        "--wait",
        type=float,
        default=180,
        help="Maximum seconds to wait for Chrome snapshots (default: 180).",
    )
    easy_run_parser.add_argument(
        "--llm-timeout",
        type=float,
        default=1800,
        help="Maximum seconds for each Ollama request (default: 1800).",
    )

    run_all_parser = subparsers.add_parser(
        "giveaway-run-all",
        help="Analyze every competition that has no saved LLM analysis.",
    )
    _add_batch_run_arguments(run_all_parser)

    run_next_parser = subparsers.add_parser(
        "giveaway-run-next",
        help="Analyze a limited number of competitions with no saved LLM analysis.",
    )
    run_next_parser.add_argument(
        "count",
        type=int,
        help="Maximum number of pending competitions to analyze.",
    )
    _add_batch_run_arguments(run_next_parser)

    snapshots_parser = subparsers.add_parser(
        "snapshots",
        help="List Chrome Extension snapshot tasks stored in SQLite.",
    )
    _add_database_argument(snapshots_parser)

    snapshot_show_parser = subparsers.add_parser(
        "snapshot-show",
        help="Show one Chrome Extension snapshot stored in SQLite.",
    )
    snapshot_show_parser.add_argument("id", type=int, help="Snapshot task ID.")
    _add_database_argument(snapshot_show_parser)

    snapshot_check_parser = subparsers.add_parser(
        "snapshot-check",
        help="Check the coverage and useful findings of one stored snapshot.",
    )
    snapshot_check_parser.add_argument("id", type=int, help="Snapshot task ID.")
    _add_database_argument(snapshot_check_parser)

    snapshot_prepare_parser = subparsers.add_parser(
        "snapshot-prepare",
        help="Build and save an LLM-ready package for one entry snapshot.",
    )
    snapshot_prepare_parser.add_argument("id", type=int, help="Entry snapshot task ID.")
    _add_database_argument(snapshot_prepare_parser)

    prepared_show_parser = subparsers.add_parser(
        "prepared-show",
        help="Show a previously prepared snapshot package.",
    )
    prepared_show_parser.add_argument("id", type=int, help="Entry snapshot task ID.")
    _add_database_argument(prepared_show_parser)

    compact_parser = subparsers.add_parser(
        "snapshot-compact",
        help="Build and save a compact sourced evidence package.",
    )
    compact_parser.add_argument("id", type=int, help="Entry snapshot task ID.")
    _add_database_argument(compact_parser)

    compact_show_parser = subparsers.add_parser(
        "compact-show",
        help="Show a previously compacted evidence package.",
    )
    compact_show_parser.add_argument("id", type=int, help="Entry snapshot task ID.")
    _add_database_argument(compact_show_parser)

    llm_parser = subparsers.add_parser(
        "llm-analyze",
        help="Analyze one compact package with a local Ollama model.",
    )
    llm_parser.add_argument("id", type=int, help="Entry snapshot task ID.")
    _add_database_argument(llm_parser)
    llm_parser.add_argument("--model", default="qwen3.5:9b")
    llm_parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    llm_parser.add_argument("--timeout", type=float, default=1800)

    analysis_show_parser = subparsers.add_parser(
        "analysis-show",
        help="Show a previously saved validated LLM analysis.",
    )
    analysis_show_parser.add_argument("id", type=int, help="Entry snapshot task ID.")
    _add_database_argument(analysis_show_parser)

    return parser


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared SQLite database path option to a subcommand."""

    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH}).",
    )


def _add_batch_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Add shared browser and Ollama options to pending batch commands."""

    _add_database_argument(parser)
    parser.add_argument("--server", default="http://127.0.0.1:8765")
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_PATH)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--wait",
        type=float,
        default=180,
        help="Maximum seconds to wait for each Chrome snapshot tree (default: 180).",
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=1800,
        help="Maximum seconds for each Ollama request (default: 1800).",
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

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "help":
        parser.print_help()
        return 0

    if args.command == "list":
        return _list_stored_competitions(args.database)
    if args.command == "show":
        return _show_stored_competition(args.database, args.id)
    if args.command == "snapshot-serve":
        return _serve_snapshot_api(args.database, args.port, args.token_file)
    if args.command == "extension-inspect":
        return _queue_extension_inspection(
            args.database, args.id, args.server, args.token_file
        )
    if args.command == "snapshot-run":
        return _run_complete_snapshot_pipeline(
            args.database, args.id, args.server, args.token_file, args.wait
        )
    if args.command == "giveaway-run":
        return _run_easy_giveaway_pipeline(
            args.database,
            args.id,
            args.server,
            args.token_file,
            args.wait,
            args.model,
            args.ollama,
            args.llm_timeout,
        )
    if args.command in {"giveaway-run-all", "giveaway-run-next"}:
        return _run_pending_giveaway_batch(
            args.database,
            None if args.command == "giveaway-run-all" else args.count,
            args.server,
            args.token_file,
            args.wait,
            args.model,
            args.ollama,
            args.llm_timeout,
        )
    if args.command == "snapshots":
        return _list_snapshot_tasks(args.database)
    if args.command == "snapshot-show":
        return _show_browser_snapshot(args.database, args.id)
    if args.command == "snapshot-check":
        return _check_browser_snapshot(args.database, args.id)
    if args.command == "snapshot-prepare":
        return _prepare_browser_snapshot(args.database, args.id)
    if args.command == "prepared-show":
        return _show_prepared_snapshot(args.database, args.id)
    if args.command == "snapshot-compact":
        return _compact_browser_snapshot(args.database, args.id)
    if args.command == "compact-show":
        return _show_compact_snapshot(args.database, args.id)
    if args.command == "llm-analyze":
        return _analyze_compact_snapshot(
            args.database, args.id, args.model, args.ollama, args.timeout
        )
    if args.command == "analysis-show":
        return _show_llm_analysis(args.database, args.id)

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


def _serve_snapshot_api(database_path: Path, port: int, token_path: Path) -> int:
    """Run the authenticated local API used by the Chrome Extension."""

    if not 1 <= port <= 65535:
        print("Error: port must be between 1 and 65535.", file=sys.stderr)
        return 2
    import uvicorn

    token = load_or_create_api_token(token_path)
    application = create_app(database_path=database_path, api_token=token)
    print(f"Snapshot API: http://127.0.0.1:{port}")
    print(f"Extension token file: {token_path.resolve()}")
    print("This server accepts read-only page snapshots; press Ctrl+C to stop.")
    uvicorn.run(application, host="127.0.0.1", port=port, log_level="info")
    return 0


def _queue_extension_inspection(
    database_path: Path,
    competition_id: int,
    server: str,
    token_path: Path,
) -> int:
    """Queue every entry URL for a competition through the localhost API."""

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
        print("This competition has no entry URLs to queue.", file=sys.stderr)
        return 1
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        print(f"Token read failed: {error}", file=sys.stderr)
        return 1
    try:
        for url in competition.entry_urls:
            response = httpx.post(
                f"{server.rstrip('/')}/api/v1/tasks",
                headers={"X-Giveaway-Agent-Token": token},
                json={"competition_id": competition_id, "url": url},
                timeout=10,
            )
            response.raise_for_status()
            task = response.json()
            print(f"Queued task {task['id']}: {url}")
    except (httpx.HTTPError, ValueError, KeyError) as error:
        print(f"Queue request failed: {error}", file=sys.stderr)
        return 1
    print("The dedicated Chrome profile will open and read the queued URL.")
    return 0


def _run_complete_snapshot_pipeline(
    database_path: Path,
    competition_id: int,
    server: str,
    token_path: Path,
    wait_seconds: float,
    *,
    show_compact: bool = True,
    llm_model: str | None = None,
    ollama_url: str = "http://127.0.0.1:11434",
    llm_timeout: float = 1800,
) -> int:
    """Run the complete read-only snapshot pipeline using tracked task IDs."""

    if wait_seconds <= 0:
        print("Error: wait time must be greater than zero.", file=sys.stderr)
        return 2
    try:
        with closing(connect_database(database_path)) as connection:
            initialize_database(connection)
            competition = get_competition(connection, competition_id)
    except (OSError, sqlite3.Error) as error:
        print(f"Database read failed: {error}", file=sys.stderr)
        return 1
    if competition is None:
        print(
            f"Competition with ID {competition_id} was not found. "
            "Run 'list' to see valid competition IDs.",
            file=sys.stderr,
        )
        return 1
    if not competition.entry_urls:
        print("This competition has no entry URLs to read.", file=sys.stderr)
        return 1
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        print(f"Token read failed: {error}", file=sys.stderr)
        return 1

    root_task_ids = []
    try:
        for url in competition.entry_urls:
            response = httpx.post(
                f"{server.rstrip('/')}/api/v1/tasks",
                headers={"X-Giveaway-Agent-Token": token},
                json={"competition_id": competition_id, "url": url},
                timeout=10,
            )
            response.raise_for_status()
            task_id = int(response.json()["id"])
            root_task_ids.append(task_id)
            print(f"Queued entry task {task_id}: {url}")
    except (httpx.HTTPError, ValueError, KeyError) as error:
        print(f"Queue request failed: {error}", file=sys.stderr)
        print("Make sure 'server' is running.", file=sys.stderr)
        return 1

    for root_task_id in root_task_ids:
        print(f"Waiting for entry task {root_task_id} and its legal documents...")
        error = _wait_for_snapshot_tree(
            database_path,
            root_task_id,
            time.monotonic() + wait_seconds,
        )
        if error:
            print(error, file=sys.stderr)
            return 1
        print(f"Using snapshot task ID {root_task_id} for prepare and compact.")
        if _prepare_browser_snapshot(database_path, root_task_id) != 0:
            return 1
        if _compact_browser_snapshot(database_path, root_task_id) != 0:
            return 1
        if show_compact:
            print()
            print(f"Compact package for snapshot task {root_task_id}:")
            if _show_compact_snapshot(database_path, root_task_id) != 0:
                return 1
        if llm_model is not None:
            print()
            print(f"Analyzing snapshot task {root_task_id} with {llm_model}...")
            if _analyze_compact_snapshot(
                database_path,
                root_task_id,
                llm_model,
                ollama_url,
                llm_timeout,
            ) != 0:
                return 1
    return 0


def _run_easy_giveaway_pipeline(
    database_path: Path,
    competition_id: int,
    server_url: str,
    token_path: Path,
    wait_seconds: float,
    model_name: str,
    ollama_url: str,
    llm_timeout: float,
) -> int:
    """Run the user-facing one-command pipeline with local services checked."""

    validation = _validate_ollama_model(model_name, ollama_url, llm_timeout)
    if validation != 0:
        return validation

    local_server = None
    server_thread = None
    try:
        local_server, server_thread = _ensure_snapshot_server(
            database_path,
            server_url,
            token_path,
        )
        return _run_complete_snapshot_pipeline(
            database_path,
            competition_id,
            server_url,
            token_path,
            wait_seconds,
            show_compact=False,
            llm_model=model_name,
            ollama_url=ollama_url,
            llm_timeout=llm_timeout,
        )
    except RuntimeError as error:
        print(f"Pipeline startup failed: {error}", file=sys.stderr)
        return 1
    finally:
        if local_server is not None:
            local_server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=10)


def _run_pending_giveaway_batch(
    database_path: Path,
    limit: int | None,
    server_url: str,
    token_path: Path,
    wait_seconds: float,
    model_name: str,
    ollama_url: str,
    llm_timeout: float,
) -> int:
    """Run pending competitions sequentially while allowing individual failures."""

    if limit is not None and limit <= 0:
        print("Error: count must be greater than zero.", file=sys.stderr)
        return 2
    if wait_seconds <= 0:
        print("Error: wait time must be greater than zero.", file=sys.stderr)
        return 2
    try:
        with closing(connect_database(database_path)) as connection:
            initialize_database(connection)
            initialize_snapshot_schema(connection)
            rows = connection.execute(
                """
                SELECT c.id, c.title
                FROM competitions AS c
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM extension_tasks AS task
                    JOIN llm_analyses AS analysis
                      ON analysis.root_task_id = task.id
                    WHERE task.competition_id = c.id
                      AND task.parent_task_id IS NULL
                )
                ORDER BY c.id
                """
            ).fetchall()
    except (OSError, sqlite3.Error) as error:
        print(f"Pending competition lookup failed: {error}", file=sys.stderr)
        return 1

    selected = rows if limit is None else rows[:limit]
    if not selected:
        print("No pending competitions found. Every competition has a saved analysis.")
        return 0

    print(
        f"Pending competitions selected: {len(selected)}"
        + (f" of {len(rows)}" if limit is not None else "")
    )
    for row in selected:
        print(f"  {row['id']}: {_truncate(row['title'], 90)}")

    validation = _validate_ollama_model(model_name, ollama_url, llm_timeout)
    if validation != 0:
        return validation

    local_server = None
    server_thread = None
    succeeded = []
    failed = []
    try:
        local_server, server_thread = _ensure_snapshot_server(
            database_path,
            server_url,
            token_path,
        )
        total = len(selected)
        for position, row in enumerate(selected, start=1):
            competition_id = int(row["id"])
            print()
            print("=" * 72)
            print(
                f"Pending giveaway {position}/{total}: "
                f"competition {competition_id} - {row['title']}"
            )
            print("=" * 72)
            result = _run_complete_snapshot_pipeline(
                database_path,
                competition_id,
                server_url,
                token_path,
                wait_seconds,
                show_compact=False,
                llm_model=model_name,
                ollama_url=ollama_url,
                llm_timeout=llm_timeout,
            )
            if result == 0:
                succeeded.append(competition_id)
                print(f"Competition {competition_id} completed successfully.")
            else:
                failed.append(competition_id)
                print(
                    f"Competition {competition_id} failed; continuing with the next one.",
                    file=sys.stderr,
                )
    except RuntimeError as error:
        print(f"Batch startup failed: {error}", file=sys.stderr)
        return 1
    finally:
        if local_server is not None:
            local_server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=10)

    print()
    print("Batch complete.")
    print(f"Successful: {len(succeeded)}")
    print(f"Failed: {len(failed)}")
    if failed:
        print("Failed competition IDs: " + ", ".join(map(str, failed)))
    return 1 if failed else 0


def _validate_ollama_model(
    model_name: str,
    ollama_url: str,
    llm_timeout: float,
) -> int:
    """Check one batch or single run's Ollama configuration once."""

    if llm_timeout <= 0:
        print("Error: LLM timeout must be greater than zero.", file=sys.stderr)
        return 2
    try:
        tags_response = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=5)
        tags_response.raise_for_status()
        installed_models = {
            item.get("name") for item in tags_response.json().get("models", [])
        }
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        print(
            f"Ollama is not available at {ollama_url}: {error}",
            file=sys.stderr,
        )
        print("Start Ollama and try again.", file=sys.stderr)
        return 1
    if model_name not in installed_models:
        print(
            f"Ollama model '{model_name}' is not installed. "
            f"Run 'ollama pull {model_name}'.",
            file=sys.stderr,
        )
        return 1
    return 0


def _ensure_snapshot_server(
    database_path: Path,
    server_url: str,
    token_path: Path,
) -> tuple[object | None, threading.Thread | None]:
    """Use an existing local API or start a temporary one for this command."""

    health_url = f"{server_url.rstrip('/')}/health"
    try:
        response = httpx.get(health_url, timeout=2)
        response.raise_for_status()
        print(f"Using running snapshot server at {server_url}.")
        return None, None
    except httpx.HTTPError:
        pass

    parsed = urlsplit(server_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError(
            "Automatic server startup is allowed only for a plain localhost HTTP URL."
        )
    port = parsed.port or 80
    token = load_or_create_api_token(token_path)
    application = create_app(database_path=database_path, api_token=token)
    import uvicorn

    config = uvicorn.Config(
        application,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    local_server = uvicorn.Server(config)
    server_thread = threading.Thread(target=local_server.run, daemon=True)
    server_thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            response = httpx.get(health_url, timeout=1)
            response.raise_for_status()
            print(f"Started temporary snapshot server at {server_url}.")
            return local_server, server_thread
        except httpx.HTTPError:
            time.sleep(0.2)
    local_server.should_exit = True
    server_thread.join(timeout=5)
    raise RuntimeError(f"Could not start the snapshot server at {server_url}.")


def _wait_for_snapshot_tree(
    database_path: Path,
    root_task_id: int,
    deadline: float,
) -> str | None:
    """Wait until an entry task and all currently related tasks are terminal."""

    terminal = {"captured", "manual_verification_required"}
    previous_summary = None
    while time.monotonic() < deadline:
        try:
            with closing(connect_database(database_path)) as connection:
                initialize_snapshot_schema(connection)
                rows = connection.execute(
                    """
                    SELECT id, status, document_type
                    FROM extension_tasks
                    WHERE id = ? OR parent_task_id = ?
                    ORDER BY id
                    """,
                    (root_task_id, root_task_id),
                ).fetchall()
        except (OSError, sqlite3.Error) as error:
            return f"Snapshot status read failed: {error}"
        if not rows:
            return f"Snapshot task {root_task_id} disappeared from the database."
        summary = ", ".join(
            f"{row['id']}:{row['document_type']}={row['status']}" for row in rows
        )
        if summary != previous_summary:
            print(f"  {summary}")
            previous_summary = summary
        root = next((row for row in rows if row["id"] == root_task_id), None)
        if root and root["status"] in terminal and all(
            row["status"] in terminal for row in rows
        ):
            return None
        time.sleep(1)
    return (
        f"Timed out while waiting for snapshot task {root_task_id}. "
        "Chrome or a legal-document page may still need manual attention."
    )


def _list_snapshot_tasks(database_path: Path) -> int:
    """Print Chrome Extension tasks without requiring the API server."""

    try:
        with closing(connect_database(database_path)) as connection:
            initialize_snapshot_schema(connection)
            rows = connection.execute(
                """
                SELECT id, competition_id, status, url, created_at,
                       parent_task_id, document_type
                FROM extension_tasks ORDER BY id DESC
                """
            ).fetchall()
    except sqlite3.OperationalError as error:
        if "no such table" in str(error):
            print("No snapshot tasks found. Start 'server' once first.")
            return 0
        print(f"Database read failed: {error}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error) as error:
        print(f"Database read failed: {error}", file=sys.stderr)
        return 1

    print(f"Snapshot tasks: {len(rows)}")
    if not rows:
        print("No snapshot tasks found. Run 'read-page ID' first.")
        return 0
    print()
    print(f"{'Task':>5}  {'Parent':>6}  {'Type':<9}  {'Status':<28}  URL")
    print(f"{'-' * 5}  {'-' * 6}  {'-' * 9}  {'-' * 28}  {'-' * 50}")
    for row in rows:
        parent_id = row["parent_task_id"] or "-"
        print(
            f"{row['id']:>5}  {str(parent_id):>6}  {row['document_type']:<9}  "
            f"{_truncate(row['status'], 28):<28}  {_truncate(row['url'], 80)}"
        )
    return 0


def _show_browser_snapshot(database_path: Path, task_id: int) -> int:
    """Print the latest stored snapshot for a task as readable JSON."""

    if task_id <= 0:
        print("Error: snapshot task ID must be greater than zero.", file=sys.stderr)
        return 2
    try:
        with closing(connect_database(database_path)) as connection:
            initialize_snapshot_schema(connection)
            row = connection.execute(
                """
                SELECT payload_json FROM browser_snapshots
                WHERE task_id = ? ORDER BY id DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
    except sqlite3.OperationalError as error:
        if "no such table" in str(error):
            print("No snapshots found. Start 'server' and run 'read-page ID' first.")
            return 1
        print(f"Database read failed: {error}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error) as error:
        print(f"Database read failed: {error}", file=sys.stderr)
        return 1
    if row is None:
        print(f"Snapshot for task {task_id} was not found.", file=sys.stderr)
        print("Run 'snapshots' to see available task IDs.", file=sys.stderr)
        return 1
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError) as error:
        print(f"Stored snapshot is invalid: {error}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _load_browser_snapshot(database_path: Path, task_id: int) -> dict | None:
    """Load and decode the newest snapshot payload for a task."""

    with closing(connect_database(database_path)) as connection:
        initialize_snapshot_schema(connection)
        row = connection.execute(
            """
            SELECT payload_json FROM browser_snapshots
            WHERE task_id = ? ORDER BY id DESC LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def _check_browser_snapshot(database_path: Path, task_id: int) -> int:
    """Print a conservative, non-LLM snapshot coverage report."""

    try:
        payload = _load_browser_snapshot(database_path, task_id)
    except (OSError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"Snapshot read failed: {error}", file=sys.stderr)
        return 1
    if payload is None:
        print(f"Snapshot for task {task_id} was not found.", file=sys.stderr)
        return 1

    fields = payload.get("fields", [])
    links = payload.get("links", [])
    buttons = payload.get("buttons", [])
    text = payload.get("visible_text", "")
    searchable = " ".join(
        [text]
        + [
            f"{field.get('name', '')} {field.get('label', '')} "
            f"{field.get('context', '')}"
            for field in fields
        ]
    ).lower()
    phone = any(
        field.get("field_type") == "tel"
        or "phone" in str(field.get("name", "")).lower()
        or "puhel" in str(field.get("label", "")).lower()
        for field in fields
    )
    consent = any(field.get("purpose") == "consent" for field in fields)
    privacy = any(item.get("purpose") == "privacy" for item in [*links, *buttons])
    rules = any(item.get("purpose") == "rules" for item in [*links, *buttons])
    custom_controls = sum(bool(field.get("role")) for field in fields)
    warnings = []
    if not fields:
        warnings.append("No form controls were found.")
    if fields and not any(field.get("required") for field in fields):
        warnings.append("Required fields could not be confirmed from HTML or ARIA attributes.")
    if any(word in searchable for word in ("tietosuoja", "privacy")) and not privacy:
        warnings.append("Privacy text was visible, but no privacy element was identified.")
    if any(word in searchable for word in ("käyttöeh", "säänn", "terms")) and not rules:
        warnings.append("Rules text was visible, but no rules element was identified.")

    quality = "good" if fields and not warnings else "partial" if text else "poor"
    print(f"Snapshot task: {task_id}")
    print(f"Snapshot quality: {quality}")
    print(f"Visible text: {'yes' if text else 'no'}")
    print(f"Fields found: {len(fields)}")
    print(f"Custom controls found: {custom_controls}")
    print(f"Phone field: {'yes' if phone else 'no'}")
    print(f"Consent controls: {'yes' if consent else 'no'}")
    print(f"Privacy element: {'yes' if privacy else 'no'}")
    print(f"Rules element: {'yes' if rules else 'no'}")
    print(
        "Manual verification: "
        f"{'yes' if payload.get('manual_verification_required') else 'no'}"
    )
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    return 0


def _prepare_browser_snapshot(database_path: Path, task_id: int) -> int:
    """Build, store, and summarize an LLM-ready snapshot package."""

    try:
        with closing(connect_database(database_path)) as connection:
            initialize_snapshot_schema(connection)
            package = prepare_snapshot_package(connection, task_id)
    except (OSError, sqlite3.Error, json.JSONDecodeError, ValueError) as error:
        print(f"Snapshot prepare failed: {error}", file=sys.stderr)
        return 1
    legal_documents = package["legal_documents"]
    available = sum(document["source_available"] for document in legal_documents)
    print(f"Prepared snapshot task {task_id} and saved it to SQLite.")
    print(f"Fields: {package['form']['field_count']}")
    print(f"Field groups: {', '.join(package['form']['groups']) or '-'}")
    print(f"Legal documents ready: {available}/{len(legal_documents)}")
    print(f"Warnings: {len(package['collection_warnings'])}")
    print(f"Use 'prepared-show {task_id}' to print the complete JSON package.")
    return 0


def _show_prepared_snapshot(database_path: Path, task_id: int) -> int:
    """Print a stored LLM-ready package as readable JSON."""

    try:
        with closing(connect_database(database_path)) as connection:
            initialize_snapshot_schema(connection)
            package = load_prepared_package(connection, task_id)
    except (OSError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"Prepared snapshot read failed: {error}", file=sys.stderr)
        return 1
    if package is None:
        print(
            f"Prepared snapshot for task {task_id} was not found. "
            f"Run 'snapshot-prepare {task_id}' first.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(package, ensure_ascii=False, indent=2))
    return 0


def _compact_browser_snapshot(database_path: Path, task_id: int) -> int:
    """Build, store, and summarize a compact evidence package."""

    try:
        with closing(connect_database(database_path)) as connection:
            initialize_snapshot_schema(connection)
            package = compact_snapshot_package(connection, task_id)
    except (OSError, sqlite3.Error, json.JSONDecodeError, ValueError) as error:
        print(f"Snapshot compact failed: {error}", file=sys.stderr)
        return 1
    stats = package["compaction"]
    print(f"Compacted snapshot task {task_id} and saved it to SQLite.")
    print(f"Evidence blocks: {stats['included_blocks']}/{stats['candidate_blocks']}")
    print(f"Duplicate blocks removed: {stats['duplicates_removed']}")
    print(f"Evidence characters: {stats['total_evidence_characters']}")
    print(f"Omitted by limits: {stats['relevant_blocks_omitted_by_limits']}")
    print(f"Use 'compact-show {task_id}' to print the compact JSON package.")
    return 0


def _show_compact_snapshot(database_path: Path, task_id: int) -> int:
    """Print a stored compact evidence package as readable JSON."""

    try:
        with closing(connect_database(database_path)) as connection:
            initialize_snapshot_schema(connection)
            package = load_compact_package(connection, task_id)
    except (OSError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"Compact snapshot read failed: {error}", file=sys.stderr)
        return 1
    if package is None:
        print(
            f"Compact snapshot for task {task_id} was not found. "
            f"Run 'snapshot-compact {task_id}' first.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(package, ensure_ascii=False, indent=2))
    return 0


def _analyze_compact_snapshot(
    database_path: Path,
    task_id: int,
    model_name: str,
    ollama_url: str,
    timeout_seconds: float,
) -> int:
    """Run local schema-constrained analysis and print the validated JSON."""

    if timeout_seconds <= 0:
        print("Error: timeout must be greater than zero.", file=sys.stderr)
        return 2

    def show_progress(phase: str, elapsed: float, remaining: float) -> None:
        if elapsed == 0:
            print(
                f"LLM {phase} started. Timeout limit: "
                f"{_display_duration(timeout_seconds)}.",
                flush=True,
            )
            return
        print(
            f"LLM {phase}: elapsed {_display_duration(elapsed)}, "
            f"timeout remaining {_display_duration(remaining)}.",
            flush=True,
        )

    try:
        with closing(connect_database(database_path)) as connection:
            initialize_snapshot_schema(connection)
            analysis = analyze_compact_package(
                connection,
                task_id,
                model_name=model_name,
                ollama_url=ollama_url,
                timeout_seconds=timeout_seconds,
                progress_callback=show_progress,
            )
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
        print(f"LLM analysis failed: {error}", file=sys.stderr)
        return 1
    print(f"Validated analysis for snapshot task {task_id} using {model_name}:")
    print(json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def _display_duration(seconds: float) -> str:
    """Format an elapsed or remaining duration for progress output."""

    total_seconds = max(0, int(seconds))
    minutes, seconds_part = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes} min {seconds_part:02d} s"
    return f"{seconds_part} s"


def _show_llm_analysis(database_path: Path, task_id: int) -> int:
    """Print one analysis previously stored in SQLite."""

    try:
        with closing(connect_database(database_path)) as connection:
            initialize_snapshot_schema(connection)
            analysis = load_llm_analysis(connection, task_id)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"LLM analysis read failed: {error}", file=sys.stderr)
        return 1
    if analysis is None:
        print(
            f"Analysis for task {task_id} was not found. "
            f"Run 'llm-analyze {task_id}' first.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


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
            print(
                f"    Loading method: "
                f"{_display_inspection_method(inspection.inspection_method)}"
            )
            print(f"    AI snapshot: {len(inspection.ai_snapshot)} characters")
            print(
                f"    Manual review required: "
                f"{'yes' if inspection.manual_review_required else 'no'}"
            )
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
    print(
        f"Loading method: "
        f"{_display_inspection_method(inspection.inspection_method)}"
    )
    print(f"URL: {inspection.requested_url}")
    if inspection.final_url and inspection.final_url != inspection.requested_url:
        print(f"Final URL: {inspection.final_url}")
    if inspection.title:
        print(f"Title: {inspection.title}")
    if inspection.error_message:
        print(f"Note: {inspection.error_message}")
    print(f"AI snapshot: {len(inspection.ai_snapshot)} characters")
    print(
        "Manual review required: "
        f"{'yes' if inspection.manual_review_required else 'no'}"
    )
    print(f"Form fields: {len(inspection.fields)}")
    for field in inspection.fields:
        required = "required" if field.required else "optional"
        identity = field.label or field.name or "unlabelled"
        print(f"  - {identity} ({field.field_type}, {required})")
    print("Privacy links:")
    _print_urls(inspection.privacy_urls)
    print("Rules or terms links:")
    _print_urls(inspection.rules_urls)
    print("XHR or fetch URLs:")
    _print_urls(inspection.network_urls)
    print("Iframe URLs:")
    _print_urls(inspection.iframe_urls)


def _print_urls(urls: tuple[str, ...]) -> None:
    if urls:
        for url in urls:
            print(f"  - {url}")
    else:
        print("  -")


def _display_inspection_method(method: str) -> str:
    """Return a readable description of the page-loading path."""

    descriptions = {
        "httpx_beautifulsoup": "HTTPX + Beautiful Soup",
        "httpx_beautifulsoup_followed_link": (
            "HTTPX + Beautiful Soup -> followed participation link"
        ),
        "playwright_fallback": "HTTPX + Beautiful Soup -> Playwright fallback",
        "none": "Not loaded",
        "unknown": "Unknown",
    }
    return descriptions.get(method, method)


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
