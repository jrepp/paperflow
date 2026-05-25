# API Quick Start

## Purpose

This repo exposes two direct CLIs:

- `arxiv-radar`: host-independent research pipeline
- `boox-sync`: one sync target that consumes a generic sync contract

Shared implementation lives in internal Python modules. The primary user-facing interfaces are the two direct CLIs above.

The repo contains only generic templates. Keep real device values in a local `.env` file, environment variables, or command-line arguments.

## Files

- `booxdrop_cli.py`
- `arxiv_radar_cli.py`
- `boox_sync_cli.py`
- `arxiv-radar`
- `boox-sync`
- `pyproject.toml`
- `.env.example`
- `sync-contract.example.yaml`
- `arxiv-reading-list.example.txt`
- `arxiv-radar.yaml`

## Install

Development environment with `uv`:

```bash
uv sync
```

Repo-local direct commands:

```bash
./arxiv-radar --help
./boox-sync --help
```

Run via `uv` without installing globally:

```bash
uv run arxiv-radar --help
uv run boox-sync --help
```

Install commands into your user tool directory with `uv`:

```bash
uv tool install --editable .
```

After that, use the commands directly:

```bash
arxiv-radar --help
boox-sync --help
```

## Environment Inputs

Supported variables:

- `BOOXDROP_HOST`
- `BOOXDROP_TOKEN`
- `BOOXDROP_PASSWORD`
- `BOOXDROP_CONTRACT`

Example local `.env`:

```dotenv
BOOXDROP_HOST=http://DEVICE_HOST:PORT
BOOXDROP_TOKEN=
BOOXDROP_PASSWORD=
BOOXDROP_CONTRACT=/absolute/path/to/sync-contract.yaml
```

## Token Encoding

BOOX Drop login stores a Basic token in the form `base64(":" + password)`.

Generate it locally with Python:

```bash
python -c 'import base64, os; print(base64.b64encode(f":{os.environ["BOOX_PASSWORD"]}".encode()).decode())'
```

## Sync Contract

Copy `sync-contract.example.yaml` and replace the example paths with your own targets.

Keep the real contract file out of version control.

## Common Commands

Inspect a BOOX target:

```bash
boox-sync inventory --host http://DEVICE_HOST:PORT
```

Dry-run a sync contract against BOOX:

```bash
boox-sync sync --host http://DEVICE_HOST:PORT --contract /path/to/sync-contract.yaml
```

Apply the sync contract:

```bash
boox-sync sync --host http://DEVICE_HOST:PORT --contract /path/to/sync-contract.yaml --apply
```

Validate the sync contract:

```bash
boox-sync validate --host http://DEVICE_HOST:PORT --contract /path/to/sync-contract.yaml
```

Use a local `.env` file instead of repeating inputs:

```bash
boox-sync inventory
boox-sync sync
boox-sync sync --apply
boox-sync validate
```

## arXiv Ingestion

Build a reading-list manifest from arXiv ids or URLs:

```bash
arxiv-radar ingest --input arxiv-reading-list.example.txt
```

Write the manifest to a file:

```bash
arxiv-radar ingest --input /path/to/reading-list.txt --output /path/to/arxiv-manifest.json
```

Use direct ids and a different category:

```bash
arxiv-radar ingest 1706.03762v7 2512.24601v2 --category AI
```

The CLI uses `typer`, so command help is structured and color-capable in the terminal.

The output includes:

- resolved arXiv metadata
- suggested title-based filenames
- suggested BOOX target paths
- a `sync_contract` fragment you can route to BOOX or another target

Export all highly cited papers from the latest radar into a manifest:

```bash
arxiv-radar export --section highly_cited
```

Filter to one or more categories:

```bash
arxiv-radar export --section highly_cited --category AI --category Data
```

Exclude categories:

```bash
arxiv-radar export --section all --exclude-category Data
```

Limit to the top N by citation count:

```bash
arxiv-radar export --section highly_cited --top 10
```

Require a minimum citation count:

```bash
arxiv-radar export --section highly_cited --min-citations 25
```

Cap citation count if needed:

```bash
arxiv-radar export --section all --max-citations 100
```

Filter by a stable lookback or an explicit date:

```bash
arxiv-radar export --section highly_cited --lookback-days 30
arxiv-radar export --section highly_cited --since 2026-03-01
```

Export from a specific report:

```bash
arxiv-radar export --radar-json /path/to/arxiv-radar-YYYY-MM-DD.json --section recent
```

These export defaults can also live in `arxiv-radar.yaml` under the `export:` block, including a stable `lookback_days` and `min_citations`.

The checked-in default config now targets:

- categories: `AI`, `Data`
- section: `all`
- lookback: `28` days
- minimum citations: `10`

## Research Radar

Generate a weekly radar for AI and Data from arXiv, including:

- recent papers from the last `lookback_days`
- highly cited papers from the configured arXiv query

The radar step is intentionally host-independent. It generates review artifacts first, so different users can run the survey once and sync selected papers to different devices later.

Recommended pipeline:

1. run `arxiv-radar generate`
2. review and curate with `arxiv-radar` or `arxiv-radar tui`
3. export a curated `sync_contract`
4. route that contract to `boox-sync sync` or another target implementation

If the curated manifest contains arXiv `pdf_url` entries, prepare them with `arxiv-radar` and then sync the staged manifest to BOOX:

```bash
arxiv-radar prepare --radar-json /path/to/arxiv-radar-YYYY-MM-DD.json
boox-sync sync-manifest --host http://DEVICE_HOST:PORT --manifest /path/to/curated-radar-staged.json --apply
```

Or run the steps explicitly:

```bash
arxiv-radar export
arxiv-radar cache-prime --manifest artifacts/arxiv-radar-manifest.json
arxiv-radar stage --manifest artifacts/arxiv-radar-manifest.json
```

That pipeline will:

- download missing PDFs from arXiv
- attach local staged file paths to the manifest
- upload those local files into the target BOOX folders
- run the normal BOOX shelf sync and validation

Downloaded PDFs are cached locally by default in `artifacts/pdf-cache`, so repeated staging does not re-download the same arXiv papers.

If the current stable export policy yields zero papers, `arxiv-radar` prints a warning banner with the active filters before opening the TUI.

Inspect a manifest before syncing:

```bash
arxiv-radar manifest-summary --manifest /path/to/curated-radar.json
arxiv-radar cache-summary --manifest /path/to/curated-radar.json
arxiv-radar cache-prime --manifest /path/to/curated-radar.json
```

Use the default config in the repo:

```bash
arxiv-radar generate
```

Open the TUI curator on the latest report:

```bash
arxiv-radar
```

Inside the TUI:

- `space` toggles selection
- `c` toggles citation sorting
- `e` exports the curated manifest

Generate a fresh report and open the TUI in one step:

```bash
arxiv-radar --refresh
```

Open a specific report and export to a chosen manifest path:

```bash
arxiv-radar --radar-json /path/to/arxiv-radar-YYYY-MM-DD.json --output /path/to/curated-radar.json
```

Stable local artifact defaults:

- curated manifest: `artifacts/arxiv-radar-curated.json`
- export manifest: `artifacts/arxiv-radar-manifest.json`
- staged manifest: `artifacts/arxiv-radar-staged.json`
- markdown cache: `artifacts/markdown-cache/`
- TeX summary: `artifacts/arxiv-radar-summary.tex`
- PDF summary: `artifacts/arxiv-radar-summary.pdf`
- summary cache index: `artifacts/summary-cache/index.json`

Generate a TeX-backed PDF summary from the current manifest:

```bash
arxiv-radar report
```

The report command uses the stable local `arxiv-radar.yaml` config for model, prompt version, markdown cache, summary cache, TeX path, PDF path, and build directory.

The checked-in default report model is now `openai/gpt-5.4`.

The report pipeline converts cached PDFs into Markdown first using the same `pymupdf4llm` style approach used in the Metro KB pipeline, then sends that extracted Markdown to the configured model.

The summary cache stores per-paper:

- prompt text
- summary JSON output
- metadata sidecar
- a visible cache index in `artifacts/summary-cache/index.json`

The PDF summary now includes:

- executive summary
- category-level LLM summaries
- per-paper evidence basis and confidence flags
- bibliography appendix

Override for a focused run:

```bash
arxiv-radar report --manifest /tmp/arxiv-radar-prepare-top2-manifest.json --model opencode/gpt-5-nano --max-papers 1
```

Override the output directory:

```bash
arxiv-radar generate --config /path/to/arxiv-radar.yaml --output-dir /path/to/arxiv-radar-output
```

Outputs:

- `arxiv-radar-YYYY-MM-DD.json`
- `arxiv-radar-YYYY-MM-DD.md`
- `artifacts/arxiv-radar-YYYY-MM-DD-curated.json`

These are intended as local working artifacts and the default output directories are git-ignored.
