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
python -m playwright install chromium
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

The command saves discovered listing data to the local SQLite database and prints the number of new and updated competitions.

## View stored competitions

List all stored competitions in a compact table:

```powershell
python main.py list
```

Show every stored field for one competition using an ID from the list:

```powershell
python main.py show 1
```

Inspect the entry pages for one competition with a headless Chromium browser:

```powershell
python main.py inspect 1
```

The inspector reads visible page text, form fields, required-field markers, and
links that appear to lead to privacy notices, rules, or terms. It saves the
structured result in SQLite. It never fills or submits a form. Instagram,
Facebook, and TikTok URLs are recorded as `skipped_social` in MVP 1 instead of
being opened or automated.

## Local database

The SQLite persistence layer stores competition metadata in:

```text
data/giveaway_agent.sqlite3
```

The database file is local and excluded from Git. The `discover` command initializes the schema automatically, inserts new competitions, updates previously seen competitions, and reports both counts.

To use a different database file for one command:

```powershell
python main.py discover --database data/another.sqlite3
python main.py list --database data/another.sqlite3
python main.py show 1 --database data/another.sqlite3
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
