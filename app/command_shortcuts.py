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


def show_help() -> int:
    return _run("help")
