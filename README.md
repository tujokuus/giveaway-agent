# Giveaway Agent

Giveaway Agent is a privacy-first, local AI pipeline for discovering and analyzing online giveaways. It uses **Ollama and a local Qwen 3.5 LLM** to turn browser-captured competition pages, forms, rules, and privacy disclosures into validated structured data—without sending personal data or webpage content to a cloud model.

The project combines Python, FastAPI, SQLite, a Chrome Extension, deterministic evidence reduction, schema-constrained LLM output, and a safe chat interface. This portfolio MVP demonstrates a reliable, inspectable approach to agentic browser workflows rather than opaque end-to-end automation.

## Highlights

- **Local AI:** Ollama with `qwen3.5:9b` by default.
- **Normal Chrome:** a read-only extension works in a dedicated Chrome profile, retaining legitimate cookies and human verification sessions.
- **Structured extraction:** visible text, forms, labels, checkboxes, buttons, iframes, rules, privacy links, and modal disclosures.
- **Evidence pipeline:** deterministic prepare and compact stages reduce duplication and legal-document clutter before LLM analysis.
- **Validated output:** Ollama returns JSON constrained by a schema and validated with Pydantic before storage.
- **Queryable results:** SQLite stores versioned snapshots, evidence packages, and lightweight summaries.
- **Local chat:** deterministic aggregate questions and LLM-assisted comparisons over the latest successful summaries.
- **Safety by design:** no form filling, submission, CAPTCHA bypass, arbitrary JavaScript, model-generated SQL, or shell execution.

## Architecture

```text
Kilpailumaailma discovery
          │
          ▼
  Python + SQLite
          │ creates read tasks
          ▼
 FastAPI on localhost ◄──── Chrome Extension in a dedicated profile
          │                         │
          │                 reads DOM and safe disclosures
          ▼
 Versioned browser snapshots
          │
          ▼
 Prepare → compact and deduplicate evidence
          │
          ▼
 Ollama + Qwen 3.5 → schema-constrained JSON
          │
          ▼
 Pydantic validation → SQLite summaries → CLI chat
```

Webpage content is always treated as untrusted data. The LLM has no browser actions or system tools. Browser interactions are predefined and limited to read-only legal disclosures; form state is compared before and after each interaction and restored if a page handler changes it.

## Technology

- Python 3.12+
- FastAPI and Uvicorn
- SQLite
- Ollama and Qwen 3.5
- Chrome Extension, Manifest V3
- HTTPX and Beautiful Soup
- Playwright as an optional inspection fallback
- Pydantic structured-output validation

## Quick start

Create the environment and install the project:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
ollama pull qwen3.5:9b
```

Playwright is optional and is not the primary browser pipeline:

```powershell
python -m playwright install chromium
```

Discover giveaways from the current MVP source and inspect the stored IDs:

```powershell
discover
list
show 4
```

## Chrome Extension setup

Use a dedicated normal Chrome profile rather than your everyday profile or Incognito.

1. Start the local API once:

   ```powershell
   server
   ```

2. Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select the repository's `extension` directory.
3. Open the extension options.
4. Set the backend URL to `http://127.0.0.1:8765`.
5. Copy the local token from:

   ```powershell
   Get-Content .\data\extension_api.token
   ```

6. Paste the token into the extension options and save.

The token authenticates only the localhost extension API and is excluded from Git. After initial setup, `giveaway-run` can start a temporary API automatically when a server is not already running.

## Analyze giveaways

Run the complete browser-to-LLM pipeline for one competition ID:

```powershell
giveaway-run 4
```

The command queues Chrome, captures the entry page and relevant legal sources, prepares and compacts evidence, calls the local model, validates the result, and stores every stage in SQLite.

Process pending competitions sequentially:

```powershell
giveaway-run-next 10 --llm-timeout 3600
giveaway-run-all --llm-timeout 3600
```

Create new runs even when older analyses exist:

```powershell
giveaway-run-next 10 --force --llm-timeout 3600
giveaway-run-all --force --llm-timeout 3600
```

Forced runs preserve earlier snapshots and analyses as history.

## Ask questions

Ask a single question over the latest successful summary for each competition:

```powershell
giveaway-ask "Kuinka monessa arvonnassa puhelinnumero on pakollinen?"
```

Open an interactive session:

```powershell
giveaway-chat
```

Phone requirement and request counts use fixed Python logic instead of model-generated queries. Open-ended questions, such as prize comparisons, receive only validated lightweight summaries. The model cannot generate or execute SQL.

Example questions:

```text
Missä kilpailussa on arvokkain palkinto?
Missä kilpailuissa puhelinnumero on pakollinen?
Mihin puhelinnumeroa käytetään kilpailussa 4?
Vertaa kilpailujen 4 ja 28 osallistumisehtoja.
```

Local inference speed depends heavily on hardware. A 9B model with a large context can be slow on CPU-only systems; GPU offload or a smaller Ollama model improves interactive latency.

## Core commands

| Command | Purpose |
| --- | --- |
| `discover` | Discover and update competitions from Kilpailumaailma. |
| `list` | List competition database IDs. |
| `show ID` | Show stored discovery data. |
| `giveaway-run ID` | Run capture, evidence preparation, and local LLM analysis. |
| `giveaway-run-next N` | Analyze the next pending competitions. |
| `giveaway-run-all` | Analyze every pending competition. |
| `snapshots` | List Chrome Extension tasks. |
| `snapshot-show ID` | Print a stored raw snapshot. |
| `snapshot-check ID` | Print deterministic capture coverage. |
| `compact-show ID` | Print the compact evidence package. |
| `summary-show ID` | Print a validated lightweight result. |
| `giveaway-ask "..."` | Ask one question over current results. |
| `giveaway-chat` | Start an interactive local chat. |
| `giveaway-help` | Show all available commands. |

PowerShell reserves the plain name `help`; use `giveaway-help` or `giveaway-agent help`.

## Data and output

All application data is stored locally in:

```text
data/giveaway_agent.sqlite3
```

The main persisted stages are:

1. discovered competition metadata
2. browser snapshots and linked legal-document tasks
3. grouped prepared packages
4. capped and deduplicated compact evidence
5. schema-validated local LLM summaries

Stable evidence references connect model findings back to captured page blocks. Old schema versions remain readable, while new runs use the current lightweight summary schema with explicit phone requirements, purposes, and channels.

Actual form values are never captured. The extension stores only metadata such as field type, label, required status, and whether a value is present.

## Safety boundaries

The current MVP intentionally does not:

- fill or submit competition forms
- automate Instagram, Facebook, or TikTok participation
- bypass CAPTCHA, Cloudflare, login, or access controls
- allow the LLM to control Chrome directly
- execute webpage instructions, arbitrary JavaScript, SQL, or shell commands

Unsupported or blocked pages are marked for manual review. Tabs created by successful extension tasks are closed after their snapshots have been uploaded; unreadable tabs remain open for diagnosis.

## Project structure

```text
app/
  database.py           SQLite persistence
  discovery.py          source-independent discovery models
  giveaway_chat.py      safe questions over validated summaries
  llm_analysis.py       Ollama, schemas, validation, and normalization
  snapshot_api.py       authenticated localhost task API
  snapshot_prepare.py   deterministic field and document grouping
  snapshot_compact.py   evidence selection and size limits
  sources/              site-specific discovery adapters
extension/
  content.js            generic DOM and form extraction
  legal_interactions.js scored read-only disclosure detection
  service_worker.js     task polling, capture, upload, and tab lifecycle
main.py                 command-line orchestration
```

## Development

Run the automated test suite:

```powershell
python -m pytest
```

The current MVP uses [Kilpailumaailma](https://www.kilpailumaailma.com/) as its discovery source. Source adapters isolate site-specific listing extraction from the shared capture, persistence, analysis, and chat pipeline.

## Status

This is an actively developed MVP. The core read-only pipeline is functional, but social platforms, inaccessible forms, dynamic privacy modals, and CPU-only LLM latency still require further work. The next architectural step is retrieval-based chat that sends only question-relevant fields or competitions to the local model.
