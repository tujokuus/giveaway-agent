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

After activating the virtual environment, the installed short commands are:

```powershell
help.exe
list
show 1
inspect 1
discover
fetch https://example.com
```

PowerShell already reserves the plain name `help` for its own help function.
Use `giveaway-help` or `giveaway-agent help` if you do not want to write
`help.exe`. The unified command also supports every operation, for example
`giveaway-agent show 1`.

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

The `inspect` command first downloads each regular web page with HTTPX and
parses its HTML with Beautiful Soup. If the static response contains a usable
form and enough page text, that result is saved immediately. Otherwise the
command falls back to Playwright so JavaScript can run. Every result stores and
prints its loading method as `httpx_beautifulsoup`, `playwright_fallback`, or
`none` for deliberately skipped social URLs.

The form classifier ignores site search fields and navigation controls and
requires multiple personal-data signals before accepting a competition form.
Static pages may follow one explicit participation link. Playwright fallback
also stores observed XHR/fetch and iframe URLs. Unresolved, blocked, empty, and
social results are marked as requiring manual review.

The inspector reads visible page text, form fields, required-field markers, and
links that appear to lead to privacy notices, rules, or terms. It saves the
structured result in SQLite. It never fills or submits a form. Instagram,
Facebook, and TikTok URLs are recorded as `skipped_social` in MVP 1 instead of
being opened or automated.

For every page that Playwright can open, the inspector also captures an
AI-optimized ARIA snapshot. The YAML-like snapshot preserves headings, links,
buttons, form controls, accessible names, element references, and iframe
structure in a format suitable for later LLM analysis. It is stored locally in
SQLite and capped at 100,000 characters per inspected URL.

Inspection statuses distinguish successful pages from access and setup errors:

- `completed_with_form`: the page loaded and editable form fields were found
- `completed_no_form`: the page loaded but no editable form was found
- `blocked_by_cloudflare`: a Cloudflare challenge page was detected
- `blocked_access`: the server returned HTTP 401, 403, or 429 without clear Cloudflare markers
- `http_error`: another HTTP error was returned
- `timeout`: the page did not load before the configured timeout
- `browser_not_installed`: the required Playwright browser executable is missing
- `failed`: another browser error occurred
- `skipped_social`: social-platform inspection was deliberately skipped

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

## Read-only Chrome Extension snapshot pipeline

This MVP uses a separate normal Chrome profile instead of Incognito. Chrome
profiles keep cookies, extensions, history, and settings isolated from the
user's normal profile while allowing a human verification session to persist.
The extension manifest explicitly disables Incognito access.

Install dependencies and start the localhost API in the first PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
snapshot-serve
```

The shorter PowerShell command for starting the same localhost server is:

```powershell
server
```

The server creates `data/extension_api.token` and prints its path. In the
dedicated Chrome profile, open `chrome://extensions`, enable Developer mode,
choose **Load unpacked**, and select this repository's `extension` directory.
Open the extension options and paste the token file's contents. Keep the
backend URL as `http://127.0.0.1:8765`.

Queue all entry URLs for one stored competition in a second PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
extension-inspect 4
```

The shorter command is:

```powershell
read-page 4
```

List all extension tasks and show a stored snapshot directly from SQLite:

```powershell
snapshots
snapshot-show 1
snapshot-check 1
```

The number passed to `snapshot-show` is the task ID printed by `read-page` and
listed by `snapshots`. The localhost server is not required when reading an
already stored snapshot.

`snapshot-check` prints a deterministic coverage report for fields, custom ARIA
controls, consent controls, privacy and rules elements, and manual verification.
It does not use an LLM and does not interact with the form.

When an entry snapshot contains direct HTTP(S) links classified as privacy or
rules documents, the backend automatically queues those URLs as related
read-only tasks. The extension opens and captures them normally. It never clicks
modal dialogs; a legal element without a URL is retained as unresolved.

After the entry page and its queued legal-document tasks have completed, build
and persist the grouped LLM-ready package:

```powershell
snapshot-prepare 2
prepared-show 2
```

Use the entry task ID, not a privacy/rules child task ID. The prepared JSON is
stored in SQLite and includes grouped identity, contact, address, choice and
consent fields, source references, legal-document text, collection warnings and
an explicit warning that all webpage content is untrusted. No LLM is called.

The same persisted package is available to a later local analysis client at:

```text
GET /api/v1/tasks/{entry_task_id}/prepared
```

## Compact evidence package

New snapshots contain referenced visible text blocks and legal text that is
already present inside hidden DOM dialogs or templates. Reading hidden DOM does
not click or execute the page. Each block has a stable source reference.

Create a smaller analysis input after the entry and linked legal documents have
been captured:

```powershell
snapshot-compact 10
compact-show 10
```

The compact operation is deterministic and uses no LLM. It removes exact
normalized duplicates, retains evidence about participation, prizes, deadlines,
eligibility, phone use, marketing, personal-data use, recipients, retention,
winner contact, privacy and rules, and applies per-source and total character
limits. The complete snapshots and prepared package remain unchanged in SQLite.
The compact package is stored in `compact_snapshots` and is also available at:

```text
GET /api/v1/tasks/{entry_task_id}/compact
```

Run the complete read-only pipeline with one command. The argument is the
competition ID printed by `list`, not a snapshot task ID:

```powershell
snapshot-run 4
```

This queues every entry URL for competition 4, remembers the returned entry
task IDs, waits for the entry and related privacy/rules tasks, prepares and
compacts each entry task, and prints each compact JSON package. The default wait
limit is 180 seconds and can be changed with `--wait`, for example
`snapshot-run 4 --wait 300`.

## Local Ollama analysis

Install Ollama separately, start it, and download the default lightweight model:

```powershell
ollama pull qwen3.5:4b
```

Analyze a compact package using its entry snapshot task ID:

```powershell
llm-analyze 11
```

Use another installed model or Ollama address when needed:

```powershell
llm-analyze 11 --model qwen3.5:9b --ollama http://127.0.0.1:11434
```

The model receives only the compact package. It has no browser actions, tools,
JavaScript or shell access. The Ollama structured-output schema constrains the
response, Pydantic validates it, and evidence references not present in the
compact input are rejected. A valid result is saved in SQLite and printed. Show
the stored result later without running the model again:

```powershell
analysis-show 11
```

The stored analysis is also available from the authenticated local API:

```text
GET /api/v1/tasks/{entry_task_id}/analysis
```

For a legal modal whose content appears only after interaction, open the modal
yourself in the tab originally opened by Giveaway Agent. Click the extension
icon and choose **Capture current tab**. The extension reads the now-visible DOM
and stores a new snapshot for the same task; it does not open the modal itself.

The extension polls the API, opens the queued URL in a normal tab, reads the
visible DOM in every permitted frame, and stores a validated JSON snapshot in
the local SQLite database. Stored data includes visible text, field metadata,
labels, checkboxes, links, buttons, iframe URLs, and whether a field already
contains a value. Actual field values are never included.

A later local analysis agent can read a stored snapshot through the authenticated
endpoint `GET /api/v1/tasks/{task_id}/snapshot`. Returned page content remains
untrusted data and must never be treated as agent instructions.

The extension contains no clicking, filling, selecting, checking, JavaScript
evaluation requested by a model, or form submission features. CAPTCHA and
Cloudflare pages are marked `manual_verification_required` for the user.
