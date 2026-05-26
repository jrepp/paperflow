# New Paper Sources and Radar Refresh Plan

## Goal

Bring additional paper discovery sources into `arxiv-radar` without making the
refresh path slower or more tangled. The target shape is a source-neutral radar
pipeline where each source adapter fetches, normalizes, and incrementally
refreshes papers into the shared radar report shape already consumed by export,
prepare, report, periodical, and sync.

Candidate sources:

- Papers with Code: `https://paperswithcode.co/`
- Google AI Mode share: `https://share.google/aimode/FIFNkqcM1208B8kHy`
- Semantic Scholar: `https://www.semanticscholar.org/`

The Google AI Mode URL was listed twice, so this plan treats it as one source.

## Current State

- `paperflow_radar.py` owns shared radar output helpers and assumes a category
  report has `recent` and `highly_cited` paper lists.
- `paperflow_sources_huggingface.py` is the existing source-specific adapter
  pattern.
- `booxdrop_cli.py` still owns the legacy arXiv radar workflow, report export,
  manifest staging, PDF caching, and report generation.
- `arxiv_radar_cli.py` owns Typer CLI composition and calls workflow helpers.
- `radar_db.py` tracks papers, reports, manifests, retrievals, extractions,
  summaries, references, periodical builds, and sync state.
- The current DB paper key is `arxiv_id`, which works for arXiv-first sources
  but is too narrow for non-arXiv records and cross-source deduplication.

## Source Feasibility

### Semantic Scholar

Use Semantic Scholar as the first new integration. It has an official API with:

- paper search and bulk search
- paper detail lookup by identifier
- citations and references
- recommendation endpoints
- optional API key support for better limits

Primary use cases:

- keyword/category discovery across broader literature than arXiv
- enrichment of records from other sources with citation counts, open access
  PDF URLs, venue, year, DOI, Corpus ID, and external IDs
- citation graph expansion for foundational references

### Papers with Code

Treat Papers with Code as a code-availability and benchmark signal source, not
as the canonical metadata source.

The public URL supplied is `paperswithcode.co`, but current web behavior should
be verified before implementation because old Papers with Code API/doc URLs may
redirect to Hugging Face papers pages. The GitHub-hosted
`paperswithcode-client` still documents a Python client for the
`paperswithcode.com` API, including paper listing.

Primary use cases:

- discover papers with official or popular code implementations
- add repository URLs, framework/library tags, task names, dataset names, and
  benchmark result metadata
- rank papers higher when they have usable code and strong community signal

Integration should start behind a feature flag and tolerate missing or changed
API behavior.

### Google AI Mode Share

Do not build this as a direct source until the data contract is clear. The share
URL is a conversational artifact, not a stable paper feed API. The likely useful
paths are:

- manual import of links copied out of the shared result
- browser/export artifact ingestion if the share page exposes structured URLs
- replacement with the underlying source that AI Mode cites, if identifiable

Initial implementation should be a generic `manual-links` or `web-links`
adapter that accepts paper URLs, arXiv IDs, DOIs, Semantic Scholar URLs, or PDFs,
then resolves them through the normal metadata pipeline.

## Target Architecture

### Source Adapters

Add one module per source:

- `paperflow_sources_semanticscholar.py`
- `paperflow_sources_paperswithcode.py`
- `paperflow_sources_manual.py`

Each adapter should expose a small common contract:

```python
@dataclass
class SourceFetchSpec:
    source: str
    category_name: str
    query: str
    target_path: str
    since: str | None
    limit: int | None
    cursor: str | None


@dataclass
class SourceFetchResult:
    source: str
    source_url: str
    source_date: str | None
    records: list[dict]
    next_cursor: str | None
    refreshed_at: str
```

Adapters return normalized record dictionaries that can flow into the existing
report helpers. They should not own export, cache priming, TeX, BOOX sync, or
TUI behavior.

### Normalized Paper Identity

Introduce a source-neutral identity layer before adding broad non-arXiv support.

Required record fields:

- `paper_key`: stable internal key, preferably `arxiv:<id>`, `doi:<doi>`,
  `s2:<corpus_id>`, or `url:<sha256>`
- `source_ids`: map of known IDs, for example `arxiv`, `doi`, `semantic_scholar`,
  `openalex`, `paperswithcode`
- `title`
- `authors`
- `published`
- `updated`
- `summary`
- `abs_url`
- `pdf_url`
- `suggested_filename`
- `primary_category`
- `citation_count`
- `influential_citation_count`
- `source_metadata`

Compatibility requirement:

- Keep emitting `arxiv_id` where known so the current export, cache, and DB code
  continue to work during migration.
- For non-arXiv records, initially use `paper_key` as the internal identifier
  and delay full export/sync support until cache and DB paths accept it.

### Database Changes

Move from arXiv-only identity toward source-neutral papers in stages:

1. Add nullable columns to `papers`: `paper_key`, `doi`, `semantic_scholar_id`,
   `corpus_id`, `openalex_id`, `source_ids_json`, `source_metadata_json`.
2. Backfill existing rows with `paper_key = 'arxiv:' || arxiv_id`.
3. Add a unique index on `paper_key`.
4. Update ingestion to upsert by `paper_key` first, falling back to `arxiv_id`.
5. In a later migration, rename `arxiv_id` references only if the benefit
   outweighs the churn.

This avoids breaking existing manifests and sync state while enabling new
sources.

### Config

Extend `arxiv-radar.yaml` with a `sources` block:

```yaml
sources:
  arxiv:
    enabled: true
  huggingface_papers:
    enabled: true
    target_path: AI/Hugging Face Papers
    min_upvotes:
  semantic_scholar:
    enabled: false
    api_key_env: SEMANTIC_SCHOLAR_API_KEY
    categories:
      AI:
        query: '(machine learning OR language models OR agents)'
        target_path: AI/Semantic Scholar
        limit: 50
  paperswithcode:
    enabled: false
    categories:
      AI:
        query: 'large language model'
        target_path: AI/Papers with Code
        limit: 50
  manual_links:
    enabled: true
    input_path: artifacts/manual-paper-links.txt
    target_path: AI/Manual imports
```

Keep existing top-level `categories` for arXiv during the transition. Once the
multi-source runner is stable, move arXiv category config under `sources.arxiv`.

## Efficient Refresh Design

### Incremental State

Add a `source_refreshes` table:

```sql
CREATE TABLE IF NOT EXISTS source_refreshes (
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    refreshed_at TEXT NOT NULL,
    since TEXT,
    cursor TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    item_count INTEGER NOT NULL DEFAULT 0,
    error_msg TEXT,
    PRIMARY KEY (source, category, query_hash)
);
```

Use this table to avoid refetching stable windows:

- `since`: latest publication or discovery timestamp accepted for the source
- `cursor`: API continuation token when the upstream source supports it
- `query_hash`: stable hash of source, category, query, filters, and limit

### Refresh Strategy

For each source/category pair:

1. Load the last successful refresh state.
2. Compute a fetch window from `since`, config lookback, and any CLI override.
3. Fetch pages in bounded batches.
4. Normalize records and dedupe by `paper_key`, arXiv ID, DOI, Semantic Scholar
   Corpus ID, and normalized title.
5. Upsert metadata and source retrieval records.
6. Emit a merged radar report from DB state rather than only from the live API
   response.
7. Record refresh success or failure per source/category.

Failure behavior:

- A failing source should not prevent other sources from refreshing.
- The final report should include a `source_status` block with per-source
  freshness and errors.
- CLI should exit non-zero only when every requested source failed or when a
  required source is marked `strict: true`.

### Rate Limits and Caching

- Use `urllib` initially to match the repo dependency profile.
- Centralize HTTP in `paperflow_http.py` with timeout, retry-after handling,
  user agent, optional API key header, JSON parsing, and lightweight backoff.
- Cache raw JSON responses under `artifacts/source-cache/<source>/` keyed by
  request URL hash and response ETag or timestamp.
- Support `--offline` to build reports from DB/cache only.
- Support `--refresh-source SOURCE` and `--skip-source SOURCE` CLI filters.

### Cron-Safe Refresh

The refresh command should be safe to run unattended from cron or launchd. That
means it should keep source metadata fresh without requiring the TUI, device
sync, PDF download, TeX builds, or LLM summaries.

Add an explicit mode:

```bash
arxiv-radar refresh --update-only
```

Expected behavior:

- Refresh enabled paper sources and update `radar.db`.
- Write the latest source-aware radar JSON and Markdown reports.
- Skip export, PDF cache priming, report generation, periodical generation, and
  BOOX sync.
- Never open the TUI.
- Reuse cached source responses when upstream state has not changed.

Cron safety requirements:

- Use a lock file, for example `artifacts/locks/radar-refresh.lock`, so a slow
  refresh cannot overlap the next scheduled run.
- Treat an existing live lock as a clean no-op by default, with an optional
  `--fail-on-lock` flag for stricter monitoring.
- Write structured run logs under `artifacts/logs/radar-refresh-YYYY-MM-DD.log`
  and keep a stable symlink or copy at `artifacts/logs/radar-refresh-latest.log`.
- Write a small status JSON file at `artifacts/radar-refresh-status.json` with
  `started_at`, `finished_at`, `exit_code`, `sources`, `new_count`,
  `updated_count`, `error_count`, and `report_path`.
- Exit `0` when at least one requested non-strict source refreshed or all
  sources were already fresh.
- Exit `0` for lock no-op unless `--fail-on-lock` is set.
- Exit non-zero when every requested source fails, a strict source fails, config
  is invalid, the DB migration fails, or the output report cannot be written.
- Do not require an interactive shell environment. API keys should come from
  environment variables, `.env` loading, or a documented wrapper script.

Recommended local wrapper:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd /Users/jrepp/dev/boox-org
export PATH="/Users/jrepp/dev/boox-org/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

./arxiv-radar refresh --update-only --config arxiv-radar.yaml
```

Example cron entry for a morning refresh:

```cron
17 6 * * * /Users/jrepp/dev/boox-org/scripts/radar-refresh-cron.sh
```

The script path can be added in Phase 1 so cron users do not need to remember
the repo-local environment details.

### Ranking

Add source-neutral ranking fields but keep source-specific signals visible:

- recency score
- citation count and influential citation count
- code availability
- repository stars
- benchmark/task relevance
- source priority
- duplicate-source count

The first pass can sort by:

1. configured source priority
2. section-specific score, for example recency or citation count
3. publication date
4. title

## CLI Plan

Add a new multi-source command while preserving existing commands:

```bash
arxiv-radar refresh
arxiv-radar refresh --source semantic_scholar
arxiv-radar refresh --source arxiv --source huggingface_papers
arxiv-radar refresh --offline
arxiv-radar refresh --update-only
```

Expected behavior:

- `arxiv-radar generate` remains the arXiv-only path during migration.
- `arxiv-radar hf-papers` remains the direct Hugging Face path.
- `arxiv-radar refresh` becomes the new source-aware path and writes
  `radar-output/research-radar-YYYY-MM-DD.json`.
- Once stable, `generate` can delegate to `refresh --source arxiv`.

## Implementation Phases

### Phase 1: Source Contract and DB Prep

- Add `paperflow_sources.py` with shared dataclasses and normalization helpers.
- Add `paperflow_http.py`.
- Add DB migration for `paper_key`, source ID columns, source metadata, and
  `source_refreshes`.
- Update DB ingestion to preserve `paper_key` while maintaining `arxiv_id`
  compatibility.
- Add `scripts/radar-refresh-cron.sh` as the documented unattended refresh
  wrapper.
- Add unit tests for identity normalization and dedupe.

### Phase 2: Semantic Scholar Adapter

- Implement search and bulk-search fetch paths.
- Normalize Semantic Scholar papers to the shared shape.
- Resolve external IDs to arXiv IDs and DOI where available.
- Use Semantic Scholar metadata to enrich existing arXiv/HF records.
- Add `arxiv-radar refresh --source semantic_scholar`.

### Phase 3: Papers with Code Adapter

- Verify live API/client behavior.
- Implement paper search/listing if stable.
- Otherwise implement enrichment by looking up known papers and extracting code
  repository/task metadata.
- Store code metadata under `source_metadata.paperswithcode`.
- Add ranking boosts for code availability and repository signal.

### Phase 4: Manual/Google Share Ingestion

- Add `paperflow_sources_manual.py`.
- Accept newline-delimited URLs/IDs from `artifacts/manual-paper-links.txt`.
- Resolve arXiv, DOI, Semantic Scholar, and direct PDF URLs.
- Treat Google AI Mode shares as an input source only if they expose extractable
  paper URLs; otherwise require copied links from the share.

### Phase 5: Unified Report Builder

- Build reports from normalized DB records grouped by category and section.
- Include `source_status` and `source_metadata` in JSON outputs.
- Keep Markdown output compact but show source badges and code links where
  available.
- Add regression tests with fixture responses for all source adapters.

## Acceptance Criteria

- Existing `arxiv-radar generate`, `hf-papers`, `export`, `prepare`, `report`,
  `periodical`, and `deliver` flows still work.
- `arxiv-radar refresh --source semantic_scholar` writes a valid radar JSON and
  Markdown report.
- Re-running refresh with no upstream changes performs bounded API calls and
  reuses DB/cache state.
- `arxiv-radar refresh --update-only` is safe to run from cron and produces a
  status JSON file plus logs without triggering sync, TeX, or TUI behavior.
- Overlapping cron runs are prevented by a lock and do not corrupt DB or output
  artifacts.
- A failing optional source is reported but does not block other sources.
- Duplicate papers from arXiv, Hugging Face, Semantic Scholar, and Papers with
  Code collapse into one paper record with multiple source IDs.
- Manifests still include valid `pdf_url`, `suggested_filename`, and
  `target_path` fields for every exportable paper.

## Open Questions

- Should non-arXiv papers be syncable immediately, or should the first release
  only export records with a usable `pdf_url` and stable metadata?
- Should `arxiv-radar generate` be renamed after multi-source support lands, or
  kept as a compatibility alias?
- What exactly is the intended Google AI Mode source: the shared answer itself,
  the cited papers inside it, or a repeatable Google search/query workflow?
- Should Semantic Scholar API key usage be required in CI-like runs, or only
  recommended for local use?
