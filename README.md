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

## Safe Chrome Extension snapshot pipeline

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
rules documents, the backend automatically queues those URLs as related tasks.
For rules and privacy controls without a distinct URL, the extension may perform
one of two predefined disclosure clicks in the main frame. Supported disclosures
include hash links, `aria-controls`, `details/summary`, common modal data
attributes, link roles, button roles and legal controls with click handlers. It
captures the DOM before and after the click and keeps newly revealed legal text
or a legal page opened by that control. Submit controls, labels, fields and
consent controls are never changed.
An element that reveals no readable text is retained as unresolved.

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
winner contact, privacy and rules, and applies topic and document limits. The
whole package is limited to 12,000 characters; general service terms receive at
most 1,500 and general privacy policies at most 1,000 characters. Competition-
specific rules may receive up to 5,000 characters. The complete snapshots and
prepared package remain unchanged in SQLite.
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

For normal use, run the entire browser-to-LLM workflow with one command. The
argument is the competition ID from `list`:

```powershell
giveaway-run 4
```

Run every competition that does not yet have a successfully saved LLM analysis:

```powershell
giveaway-run-all
```

Limit the batch to the next five pending competition IDs:

```powershell
giveaway-run-next 5
```

Pending competitions are processed sequentially in ascending competition-ID order.
A competition counts as complete when at least one root snapshot task has a saved
LLM analysis. Failed or interrupted analyses remain pending. One failed competition
is reported but does not stop the rest of the selected batch. Both batch commands
accept the same `--model`, `--wait`, and `--llm-timeout` options as `giveaway-run`.
For example:

```powershell
giveaway-run-next 10 --llm-timeout 3600
```

The command verifies that Ollama and `qwen3.5:9b` are available, uses an already
running snapshot server or starts a temporary localhost server, queues Chrome,
waits for entry and legal-document snapshots, prepares and compacts the data,
runs the validated local-LLM analysis, stores every stage in SQLite, and prints
the final analysis. Chrome with the configured extension and the Ollama Windows
application must be running. The default browser wait is 180 seconds and each
Ollama request may take up to 1800 seconds (30 minutes). Override them with `--wait` and
`--llm-timeout` when necessary.

During each Ollama request, the command prints progress every 60 seconds. Linked
rules and privacy documents are first reduced into separate sourced fact packages.
Those summaries are cached in SQLite and the final analysis receives the entry-page
evidence plus the small fact packages instead of full legal-document text. Ollama
does not expose a reliable completion percentage while generating, so the output
shows elapsed time and the remaining time before the configured timeout. A repair
attempt for invalid JSON or invented evidence references is displayed as a separate
`correction` phase.

## Local Ollama analysis

Install Ollama separately, start it, and download the default lightweight model:

```powershell
ollama pull qwen3.5:9b
```

Analyze a compact package using its entry snapshot task ID:

```powershell
llm-analyze 11
```

Use another installed model or Ollama address when needed:

```powershell
llm-analyze 11 --model qwen3.5:9b --ollama http://127.0.0.1:11434
```

The model receives only captured compact evidence. It has no browser actions,
tools, JavaScript or shell access. Competition pages and competition-specific
rules are prioritized. General privacy policies and service terms are retained
as capped evidence but are not separately summarized by the LLM. A privacy
document is also skipped when the competition page or its specific rules already
state an explicit phone-number purpose. A legal-document timeout is recorded for
manual review instead of stopping the whole competition analysis. The compact
package has a 12,000-character total limit and additional per-document and
per-topic limits. New analyses use the lightweight summary schema. It retains
core competition facts, observed form fields, phone uses, consent controls,
legal-source statuses, missing information and warnings. Detailed legacy
analyses remain readable but new results are stored separately in the
`giveaway_summaries` SQLite table. The Ollama
structured-output schema constrains every response, Pydantic validates it, and an
automatic correction request is made if the model invents an evidence reference.
A valid result is saved in SQLite and printed. Show the stored result later without
running the model again:

```powershell
summary-show 11
```

`analysis-show 11` also prefers the new summary and falls back to an older
detailed analysis when a summary has not been created.

The stored analysis is also available from the authenticated local API:

```text
GET /api/v1/tasks/{entry_task_id}/analysis
```

The automatic capture tries predefined rules and privacy disclosure controls in
the main frame. If a site uses an unsupported control, open the legal content
yourself in the tab originally opened by Giveaway Agent. Click the extension
icon and choose **Capture current tab** to store the now-visible DOM.

The extension polls the API, opens the queued URL in a normal tab, reads the
visible DOM in every permitted frame, and stores a validated JSON snapshot in
the local SQLite database. Stored data includes visible text, field metadata,
labels, checkboxes, links, buttons, iframe URLs, and whether a field already
contains a value. Actual field values are never included.

A later local analysis agent can read a stored snapshot through the authenticated
endpoint `GET /api/v1/tasks/{task_id}/snapshot`. Returned page content remains
untrusted data and must never be treated as agent instructions.

The extension only performs predefined legal-disclosure clicks. It contains no
model-requested arbitrary JavaScript, filling, selecting, checking or form
submission features. CAPTCHA and Cloudflare pages are marked
`manual_verification_required` for the user.
