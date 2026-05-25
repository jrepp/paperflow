# Runbook

## Scope

This runbook separates the research pipeline from target-specific sync.

## Inputs

- BOOX Drop host
- optional auth token or password
- a local sync contract file
- optional local arXiv reading-list file

Provide these through command-line flags, environment variables, or a local `.env` file.

## Install Options

Package install:

```bash
uv sync
```

Run directly from the repo with `uv`:

```bash
uv run arxiv-radar --help
uv run boox-sync --help
```

Install the CLIs into your user tool directory:

```bash
uv tool install --editable .
```

Preferred daily commands after that:

```bash
arxiv-radar --help
boox-sync --help
```

## arXiv Reading List Ingestion

1. Create a local text file with one arXiv id or URL per line.
2. Generate a manifest.

```bash
arxiv-radar ingest --input /path/to/reading-list.txt --output /path/to/arxiv-manifest.json
```

3. Review the suggested filenames and `sync_contract`.
4. Merge the relevant targets into your local sync contract file.
5. Run the normal organize and validate flow.

Or export directly from the latest radar report:

```bash
arxiv-radar export --section highly_cited
```

Examples:

```bash
arxiv-radar export --section highly_cited --category AI
arxiv-radar export --section highly_cited --category Data --top 5
arxiv-radar export --section highly_cited --min-citations 25 --lookback-days 30
arxiv-radar export --section all --exclude-category Data --max-citations 100
```

You can set stable defaults for `section`, `categories`, `exclude_categories`, `top`, `lookback_days`, `min_citations`, and `max_citations` in `arxiv-radar.yaml`.

## Weekly Research Radar

1. Copy `arxiv-radar.yaml` to a local config file if needed.
2. Adjust the category queries and target paths.
3. Generate the weekly radar.

The radar stage is separate from device sync. That lets one person generate the weekly survey while another person syncs selected papers to a different BOOX device or a different target implementation.

```bash
arxiv-radar generate
```

Or use a different config:

```bash
arxiv-radar generate --config /path/to/arxiv-radar.yaml
```

Generate a radar from Hugging Face daily papers:

```bash
arxiv-radar hf-papers --date 2026-05-22
```

Hugging Face papers reports are written to `hf-papers-output/` by default. They use the same report shape as the arXiv radar, so pass the generated JSON to the TUI, export, prepare, report, or sync stages:

```bash
arxiv-radar tui --radar-json hf-papers-output/hf-papers-radar-2026-05-22.json
arxiv-radar prepare --radar-json hf-papers-output/hf-papers-radar-2026-05-22.json --section recent
```

4. Review the generated Markdown summary.
5. Curate the report interactively.

```bash
arxiv-radar
```

Useful TUI keys:

- `space` to select
- `c` to sort by citations
- `e` to export

Or regenerate first and then open the curator:

```bash
arxiv-radar --refresh
```

6. Export a curated sync contract.
7. Run the target-specific sync steps against the desired device.

For curated arXiv manifests, prepare local state first and then sync the staged manifest:

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

By default, staged PDFs are cached locally in `artifacts/pdf-cache` to avoid re-downloading the same arXiv papers.
The report pipeline also maintains a Markdown extraction cache in `artifacts/markdown-cache` and a summary cache in `artifacts/summary-cache`.

Generate a TeX and PDF summary from the current manifest:

```bash
arxiv-radar report
```

The report command uses the stable local `arxiv-radar.yaml` config for model, output paths, build dir, markdown cache, summary cache, and prompt version.

The checked-in default report model is `openai/gpt-5.4`.

Per-paper summary cache artifacts are written under `artifacts/summary-cache`, including a visible `index.json` that links prompt, metadata, and summary outputs. The report itself now includes category-level LLM summaries, per-paper confidence flags, and a bibliography appendix.

Inspect the manifest first if needed:

```bash
arxiv-radar manifest-summary --manifest /path/to/curated-radar.json
arxiv-radar cache-summary --manifest /path/to/curated-radar.json
```

Expected output:

- a JSON artifact for automation
- a Markdown artifact for manual review
- a curated JSON manifest with a `sync_contract` for downstream sync

The default output directories are ignored by git.

## Replay Steps

1. Inspect the current BOOX target state.

```bash
boox-sync inventory --host http://DEVICE_HOST:PORT
```

2. Generate a dry-run sync.

```bash
boox-sync sync --host http://DEVICE_HOST:PORT --contract /path/to/sync-contract.yaml
```

3. Review the output.

Look for:

- missing storage folders
- pending physical file moves
- missing BOOX shelves
- pending shelf moves
- warnings for missing files or missing indexed library items

4. Apply the sync contract.

```bash
boox-sync sync --host http://DEVICE_HOST:PORT --contract /path/to/sync-contract.yaml --apply
```

5. Validate the result.

```bash
boox-sync validate --host http://DEVICE_HOST:PORT --contract /path/to/sync-contract.yaml
```

## Focused UAT Pattern

1. Confirm the CLI can read device state.
2. Confirm the dry-run sync matches expectations.
3. Apply the sync contract.
4. Re-run validation until it exits successfully.
5. Re-run `sync --apply` once more to confirm idempotence.

Expected end state:

- no missing storage folders
- no pending physical moves
- no missing shelves
- no pending shelf moves
- no warnings

The sync contract is the interface boundary. BOOX is just one implementation of that contract.

## Failure Handling

### Missing Physical File

If validation reports `missing physical file`, inspect whether:

- the file was renamed outside the CLI
- the file was deleted
- the local sync contract is stale

Update the local sync contract before retrying.

### Missing Library Item

If validation reports `missing library item`, the physical move may be complete while the BOOX library index has not settled yet.

Actions:

1. wait a few seconds
2. re-run `boox-sync validate`
3. if it persists, re-run `boox-sync sync --apply`

### Auth Needed

If writes fail on your instance, derive the Basic token from the password and retry with either `--token` or `--password`.

## Notes

- the CLI uses websocket reads for folder-aware storage and shelf-aware library listings
- the CLI uses HTTP writes for BOOX mutation endpoints
- the sync contract is target-agnostic
- the replay is designed to be idempotent
- keep `.env` and real contract files out of version control
