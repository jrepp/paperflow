---
title: "Using the local reader"
project_id: paperflow
doc_uuid: 945ff7d4-42ed-406d-a797-bab484d50773
tags: [guides, reader]
---

# Using the local reader

Run `uv run --locked paperflow serve`, then open <http://127.0.0.1:8000/>.
The existing research settings select the local database and ingestion monitor.
No identity service is required. Keep this single-workspace service on loopback;
hosted identity remains a later release slice.

Today shows a finite, arrival-ordered batch. Filter by channel or title, read a
source, save it without choosing a research bucket, and return to your list.
Saved holds the same items with their saved reading positions. Source and
Discussion are separate views. Excerpts and missing or sampled comments are
labeled, with links to the original content. Research lists existing collections
and links to their briefs; detailed curator editing remains in the CLI/API.

Reading settings provide text size, spacing, measure, typeface and theme. The
settings and reading state are installation-local SQLite records. Saved state
and revision anchors survive service/browser restarts. If a source changes,
resume opens the original revision and offers the latest version separately.
Reading position is stored at paragraph boundaries after scrolling settles.
A forced close before a write completes can lose the most recent position.

Sources shows observed content freshness and monitor status, with pause/resume
and fetch controls when the monitor is enabled. An unavailable monitor does not
remove previously ingested reading. Errors are shown explicitly; retry after
restoring the service. Conflicting reading-state changes are rejected instead
of silently overwriting another tab's work; reload before retrying.

## Frontend development and checks

Run `scripts/bootstrap` to install the locked Python and npm dependencies.
The shared toolchain file pins Node as well as uv/Python. `npm run build` compiles
Tailwind to the packaged CSS asset; the running reader needs no CDN or npm server.

- `scripts/check reader`: JavaScript syntax and reproducible CSS.
- `npx playwright install chromium webkit`: install the test browsers once.
- `scripts/check browser`: real FastAPI/SQLite browser journeys and axe checks.
- `scripts/check`: backend, docs, frontend assets, workflows and release contract.

Browser tests own a disposable database and server on port 8765. They never
mutate the user's reading database. CI runs the same browser command after
installing Chromium and WebKit. The package smoke check verifies that built wheels contain
the reader assets.

## Current verification limits

See the [reader validation record](../validation/reader-mvp.md). Automated
accessibility checks are useful evidence, not a screen-reader audit or a WCAG
conformance claim. The full [acceptance baseline](../plans/reader-acceptance.md)
remains open until its browser and manual verification requirements are met.

## Embedded document views

The source reader now has Markdown, PDF, Diagrams and Discussion destinations.
Markdown renders headings, lists, tables, links and code with raw HTML disabled.
Remote images are represented by their alternative text. Fenced `mermaid` blocks
render inline and appear in Diagrams; their definitions remain available, including
when rendering fails. Source-controlled Mermaid configuration directives are rejected.

On PDF, choose **PDF file**, then **Attach and read PDF** to attach a local document
(up to 20 MB, without a password). The PDF.js viewer provides page selection,
previous/next controls, fit-width/zoom, extracted page text and an Open PDF file
fallback. Scanned pages may have no extractable text. PDF page position is not yet
a durable reading anchor; Markdown resume remains revision-aware.

The attachment is stored with a SHA-256 digest in the local SQLite database, so it
is included in database backups. Reattaching identical bytes is safe; attaching
a different PDF to the same item returns a conflict. Use a separate item for a
different PDF. PDF attachments and source Markdown revisions remain separate;
attachment does not imply that extracted Markdown matches the PDF.

All viewer libraries, workers, fonts and character maps ship as local package
assets. There is no CDN requirement or automatic fetching of remote PDF URLs.
The API accepts raw PDF bytes at `PUT /api/reader/items/{item_id}/pdf` with
`Content-Type: application/pdf`; the corresponding GET serves the stored PDF.

## Navigation and channel statistics

The compact header contains Paperflow, Today/Saved/Research, title search, Sources
and Settings. The channel picker uses a styled listbox with keyboard navigation,
Escape dismissal, per-view item/unread/saved counts and observation freshness.
Its counts honor the current search; repeated observations are deduplicated.
The About page contains the introduction, workflow explanation and live per-channel
statistics. About is available from the footer and directly at `/about`.

The Today toolbar is a single row with the view title and channel picker.
Choose **✓ All set** to clear an item from the Today inbox. Its row collapses
to a short confirmation with **Undo**; saved state and the reading anchor remain
intact. Undo restores the previous reading status and full row.
