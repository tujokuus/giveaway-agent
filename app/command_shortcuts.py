"""Short installed commands for the Giveaway Agent command-line interface."""

import sys

from main import _configure_output_encoding, main


def _run(command: str | None = None) -> int:
    """Run the main parser with an optional command inserted first."""

    _configure_output_encoding()
    arguments = list(sys.argv[1:])
    if command is not None:
        arguments.insert(0, command)
    return main(arguments)


def giveaway_agent() -> int:
    """Run the complete command-line interface."""

    return _run()


def fetch() -> int:
    return _run("fetch")


def discover() -> int:
    return _run("discover")


def list_items() -> int:
    return _run("list")


def show() -> int:
    return _run("show")


def inspect() -> int:
    return _run("inspect")


def snapshot_serve() -> int:
    return _run("snapshot-serve")


def extension_inspect() -> int:
    return _run("extension-inspect")


def server() -> int:
    """Start the localhost snapshot server."""

    return _run("snapshot-serve")


def read_page() -> int:
    """Queue a competition for the Chrome Extension."""

    return _run("extension-inspect")


def snapshots() -> int:
    """List Chrome Extension snapshot tasks."""

    return _run("snapshots")


def snapshot_show() -> int:
    """Show one stored Chrome Extension snapshot."""

    return _run("snapshot-show")


def snapshot_check() -> int:
    """Show a deterministic quality report for one snapshot."""

    return _run("snapshot-check")


def snapshot_prepare() -> int:
    """Build and store an LLM-ready snapshot package."""

    return _run("snapshot-prepare")


def prepared_show() -> int:
    """Show a stored LLM-ready snapshot package."""

    return _run("prepared-show")


def snapshot_compact() -> int:
    """Build and store a compact evidence package."""

    return _run("snapshot-compact")


def compact_show() -> int:
    """Show a stored compact evidence package."""

    return _run("compact-show")


def snapshot_run() -> int:
    """Run the complete read-only pipeline for one competition."""

    return _run("snapshot-run")


def show_help() -> int:
    return _run("help")
