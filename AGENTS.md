# CLI Guide

This repo has three primary modes:

- `arxiv-radar`: host-independent research pipeline
- `arxiv-radar hf-papers`: Hugging Face daily papers radar
- `boox-sync`: BOOX device synchronization from staged local artifacts
- `tex/`: research radar periodical publishing pipeline

## Pipeline

The intended pipeline is:

1. generate or reuse a radar report
2. curate or export a manifest from the radar
3. prepare local state: cache PDFs and stage local file paths
4. build a periodical or TeX/PDF radar summary
5. sync the staged manifest to a target device

`arxiv-radar` owns steps 1-4.

`boox-sync` owns step 5.

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

### arxiv-radar

Core report/radar workflow:

- `arxiv-radar`
  Opens the TUI on the latest radar report.
- `arxiv-radar --refresh`
  Regenerates the radar first, then opens the TUI.
- `arxiv-radar generate`
  Produces JSON and Markdown radar outputs.
- `arxiv-radar hf-papers`
  Produces JSON and Markdown radar outputs from Hugging Face daily papers.
- `arxiv-radar export`
  Produces a manifest from a radar report using config and CLI filters.
- `arxiv-radar prepare`
  Runs `export -> cache-prime -> stage`. Auto-detects curated manifest.
- `arxiv-radar report`
  Builds the TeX and PDF summary from the current manifest.
- `arxiv-radar periodical`
  Builds a research radar periodical with per-paper TeX chapters using `tex/` publishing pipeline.
- `arxiv-radar deliver`
  Convenience wrapper that runs `prepare` and then calls `boox-sync sync-manifest`.

Inspection/state commands:

- `arxiv-radar manifest-summary --manifest <path>`
- `arxiv-radar cache-summary --manifest <path>`
- `arxiv-radar cache-prime --manifest <path>`
- `arxiv-radar stage --manifest <path>`

### boox-sync

Device-only commands:

- `boox-sync inventory --host <url>`
- `boox-sync sync --host <url> --contract <path>`
- `boox-sync sync-manifest --host <url> --manifest <path>`
- `boox-sync validate --host <url> --contract <path>`

`boox-sync` should not own radar generation, manifest export, cache management, or report generation.

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

## Periodical Pipeline

The periodical pipeline (`arxiv-radar periodical`) generates a structured TeX publication under `tex/research-radar/`:

- Per-paper chapters with full intake summaries
- Category-level overview chapters with executive summaries
- Executive summary and bibliography appendices
- Built with `exec-report.cls` (shared from `tex/shared/`)
- Output PDF at `tex/dist/research-radar.pdf`

Generated files in `tex/research-radar/chapters/` and the main `research-radar.tex` are git-ignored and regenerated each run.

The `tex/` directory is a self-contained publishing pipeline adapted from the tex monorepo:
- `tex/shared/exec-report.cls` -- shared LaTeX document class
- `tex/research-radar/justfile` -- build recipe (pdflatex, 3-pass)
- `tex/justfile` -- top-level build-all orchestration

## Typical Flows

### End-to-End Radar To Device

```bash
arxiv-radar prepare
arxiv-radar report
boox-sync sync-manifest --host http://DEVICE_HOST:PORT --manifest artifacts/arxiv-radar-staged.json --apply
```

### Export + Inspect + Sync

```bash
arxiv-radar export
arxiv-radar manifest-summary --manifest artifacts/arxiv-radar-manifest.json
arxiv-radar cache-summary --manifest artifacts/arxiv-radar-manifest.json
arxiv-radar stage --manifest artifacts/arxiv-radar-manifest.json
boox-sync sync-manifest --host http://DEVICE_HOST:PORT --manifest artifacts/arxiv-radar-staged.json --apply
```

### One-Step Convenience

```bash
arxiv-radar deliver --host http://DEVICE_HOST:PORT --apply
```

## Operational Notes

- BOOX hosts can sleep or drop off Wi-Fi. `boox-sync` commands should fail gracefully and be safe to retry.
- `arxiv-radar` should prefer stable local config defaults unless a CLI override is explicitly provided.
- Hugging Face daily papers reports should be explicit source artifacts that can flow through the same TUI, export, prepare, report, and sync stages.
- For paper analysis, prefer extracted Markdown over raw PDF attachment behavior.
