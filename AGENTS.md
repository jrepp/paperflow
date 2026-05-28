# CLI Guide

The preferred project-centric CLI is `paperflow`.

The legacy command names remain available for compatibility:

- `arxiv-radar`: compatibility wrapper for radar/library/publishing commands
- `boox-sync`: BOOX device synchronization from staged local artifacts
- `tex/`: research radar periodical publishing pipeline

The primary `paperflow` command groups are:

- `paperflow radar`: generate, refresh, curate, and export radar reports
- `paperflow sources`: source-specific ingest commands
- `paperflow library`: prepare manifests, cache PDFs, stage artifacts, and build summaries
- `paperflow publish`: build publishing corpora, propose durable threads, manage the editorial queue, and build periodical issues
- `paperflow device`: BOOX device operations
- `paperflow project`: inspect and maintain local project state

## Pipeline

The intended pipeline is:

1. generate or reuse a radar report
2. curate or export a manifest from the radar
3. prepare local state: cache PDFs and stage local file paths
4. build a periodical or TeX/PDF radar summary
5. sync the staged manifest to a target device

`paperflow` owns steps 1-4.

`paperflow device` owns step 5. `boox-sync` remains as the compatibility device-only entrypoint.

## Module Boundaries

- `paperflow_radar.py` owns source-neutral radar report helpers.
- `paperflow_sources_*.py` modules own source-specific fetch, parse, and report adaptation.
- `booxdrop_cli.py` still contains legacy shared workflow implementation and BOOX transport behavior.
- `arxiv_radar_cli.py` owns CLI composition and should call source modules through workflow helpers.

New radar sources should be added as `paperflow_sources_<source>.py` modules that emit the shared radar report shape.

## Stable Local Config

`arxiv-radar.yaml` is the stable local config and should be treated as the source of truth for:

- export defaults
- report model and variant
- report cache/output locations
- stable lookback and citation thresholds

## Major Commands

### paperflow

Core report/radar workflow:

- `paperflow radar generate`
  Produces JSON and Markdown radar outputs.
- `paperflow sources hf-papers`
  Produces JSON and Markdown radar outputs from Hugging Face daily papers.
- `paperflow radar export`
  Produces a manifest from a radar report using config and CLI filters.
- `paperflow library prepare`
  Runs `export -> cache-prime -> stage`. Auto-detects curated manifest.
- `paperflow library report`
  Builds the TeX and PDF summary from the current manifest.
- `paperflow publish issue build`
  Builds a numbered research radar periodical issue from a focal paper plus supporting context using the `tex/` publishing pipeline.
- `paperflow publish topics`
  Presents candidate evergreen periodical topics from the current manifest.
- `paperflow publish queue add`
  Adds an approved focal topic to the local periodical issue queue.
- `paperflow publish queue list`
  Lists queued periodical issues.
- `paperflow publish corpus`
  Builds a deduplicated publishing corpus from multiple radar reports and manifests.
- `paperflow publish threads`
  Presents cross-corpus thread candidates for future periodical issues.
- `arxiv-radar deliver`
  Convenience wrapper that runs `prepare` and then calls `boox-sync sync-manifest`.

Inspection/state commands:

- `paperflow library manifest-summary --manifest <path>`
- `paperflow library cache-summary --manifest <path>`
- `paperflow library cache-prime --manifest <path>`
- `paperflow library stage --manifest <path>`

### Device Operations

Device-only commands:

- `paperflow device inventory --host <url>`
- `paperflow device sync --host <url> --contract <path>`
- `paperflow device sync-manifest --host <url> --manifest <path>`
- `paperflow device validate --host <url> --contract <path>`

Device commands should not own radar generation, manifest export, cache management, or report generation.

## Default Local Artifacts

These are local working files and are git-ignored under `artifacts/`:

- `artifacts/arxiv-radar-curated.json`
- `artifacts/arxiv-radar-manifest.json`
- `artifacts/arxiv-radar-staged.json`
- `artifacts/arxiv-radar-summary.tex`
- `artifacts/arxiv-radar-summary.pdf`
- `artifacts/pdf-cache/`
- `artifacts/markdown-cache/`
- `artifacts/summary-cache/`

## Report Pipeline

The report pipeline uses:

- cached PDFs from `artifacts/pdf-cache`
- PDF-to-Markdown extraction cached in `artifacts/markdown-cache`
  - Enhanced cleanup from metro baseline: heading normalization, blank-line enforcement, code block fixes
- LLM summaries cached in `artifacts/summary-cache`
- TeX build outputs in the configured build dir

The report should include:

- executive summary
- category-level summaries
- per-paper results summary and intake framing
- citation signal
- confidence and evidence basis
- bibliography appendix

## Publishing Pipeline

The publishing pipeline separates corpus discovery from issue production:

1. ingest multiple radar outputs and manifests into a merged publishing corpus
2. propose durable threads across all available source artifacts
3. add selected threads or focal papers to the periodical queue
4. build numbered periodical issues from approved queue items

`publish-corpus` accepts JSON artifacts from arXiv radar, Hugging Face papers radar, curated manifests, and future source-specific radar reports that emit the shared radar report shape. It deduplicates papers through source-neutral identity keys and writes `artifacts/publishing-corpus.json`.

`publish-threads` is the first deterministic topic discovery pass. It is intentionally separate from periodical generation so this layer can later use LLM synthesis over the merged corpus without changing the issue-building contract.

## Periodical Pipeline

The periodical pipeline is editorial-first:

1. propose candidate evergreen topics from a radar manifest
2. choose a focal paper and add it to the numbered issue queue
3. build the periodical issue from that queued topic

`paperflow publish issue build` generates a structured TeX publication under `tex/research-radar/`:

- Numbered series metadata
- A focal paper that defines the issue's through line
- Supporting papers selected from the radar manifest
- Optional foundational references discovered through citation traversal
- Full intake summaries for focal and supporting papers
- Executive summary and bibliography appendices
- Built with `research-radar.cls` (shared from `tex/shared/`)
- Output PDF at `tex/dist/research-radar.pdf`
- Build metadata at `tex/research-radar/build-metadata.json`

Generated files in `tex/research-radar/chapters/` and the main `research-radar.tex` are git-ignored and regenerated each run.
The local periodical queue is stored at `artifacts/periodical-queue.json` and is git-ignored with other artifacts.

The `tex/` directory is a self-contained publishing pipeline adapted from the tex monorepo:
- `tex/shared/research-radar.cls` -- shared LaTeX document class for the periodical
- `tex/shared/exec-report.cls` -- shared LaTeX document class for executive report-style outputs
- `tex/research-radar/justfile` -- build recipe (pdflatex, 3-pass)
- `tex/justfile` -- top-level build-all orchestration

## Typical Flows

### End-to-End Radar To Device

```bash
paperflow library prepare
paperflow library report
paperflow device sync-manifest --host http://DEVICE_HOST:PORT --manifest artifacts/arxiv-radar-staged.json --apply
```

### Export + Inspect + Sync

```bash
paperflow radar export
paperflow library manifest-summary --manifest artifacts/arxiv-radar-manifest.json
paperflow library cache-summary --manifest artifacts/arxiv-radar-manifest.json
paperflow library stage --manifest artifacts/arxiv-radar-manifest.json
paperflow device sync-manifest --host http://DEVICE_HOST:PORT --manifest artifacts/arxiv-radar-staged.json --apply
```

### One-Step Convenience

```bash
arxiv-radar deliver --host http://DEVICE_HOST:PORT --apply
```

## Operational Notes

- BOOX hosts can sleep or drop off Wi-Fi. `paperflow device` and `boox-sync` commands should fail gracefully and be safe to retry.
- `paperflow` should prefer stable local config defaults unless a CLI override is explicitly provided.
- Hugging Face daily papers reports should be explicit source artifacts that can flow through the same TUI, export, prepare, report, and sync stages.
- For paper analysis, prefer extracted Markdown over raw PDF attachment behavior.
