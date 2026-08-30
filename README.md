# Giveaway Agent

A learning project for discovering and locally analyzing online giveaways and competitions.

MVP 1 currently uses Kilpailumaailma as its default source. The application downloads its listing page with `httpx` and extracts competition metadata with Beautiful Soup.

## Requirements

- Python 3.12 or newer

## Development setup

Create and activate a virtual environment in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Discover competitions

The default source is `https://www.kilpailumaailma.com/`:

```powershell
python main.py discover
```

You may also provide a Kilpailumaailma listing URL explicitly:

```powershell
python main.py discover https://www.kilpailumaailma.com/
```

For each card, the command prints all metadata available on the listing page:

- title and Kilpailumaailma detail URL
- publication date
- participation platforms
- organizer
- deadline
- prize
- direct participation links

The application does not yet download individual detail pages. Discovered listing data is saved to the local SQLite database.

## Local database

The SQLite persistence layer stores competition metadata in:

```text
data/giveaway_agent.sqlite3
```

The database file is local and excluded from Git. The `discover` command initializes the schema automatically, inserts new competitions, updates previously seen competitions, and reports both counts.

To use a different database file for one run:

```powershell
python main.py discover --database data/another.sqlite3
```

## Source adapters

Shared downloading, command-line handling, models, and database storage are independent of any one website. Each supported website implements the small `CompetitionSource` interface in its own adapter under `app/sources/`.

## Download a page

```powershell
python main.py fetch https://example.com
```

The command prints the requested URL, final URL after redirects, HTTP status code, and downloaded HTML length. Use `--timeout` to override the default ten-second timeout.

```powershell
python main.py fetch https://example.com --timeout 20
```

## Run tests

```powershell
python -m pytest
```

