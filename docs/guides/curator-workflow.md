---
title: "Curator workflow: domain operations, API, and CLI"
project_id: paperflow
doc_uuid: 9fd3503e-4e91-4ac8-9b6b-e37bead807b3
tags: [guides]
---

# Curator workflow: domain operations, API, and CLI

## Progressive disclosure in the CLI

Run `paperflow curate` for an offline four-step guide. Start with discovery and
collection; use `--help` to reveal the assessment and source-import command groups.
Existing command names remain available.

```bash
paperflow curate items --query Rust
paperflow curate item ITEM_ID
paperflow curate bucket BUCKET_ID
paperflow curate claim-show CLAIM_ID
paperflow curate export BUCKET_ID --output brief.md
```

The default output is compact, human-readable text. Discovery fetches 10 items
at a time; `--offset` and `--limit` control the API page. Compact lists show at most
10 returned records, item views show up to three revisions, and bucket/claim
views bound their collections to 10. Counts and disclosure hints make those
limits explicit. IDs remain complete and copyable. Source previews are labeled
with their evidence basis; claims and assessments retain their review status.

Place output controls **before** the command:

```bash
paperflow curate --measure bucket BUCKET_ID
paperflow curate --full item ITEM_ID
paperflow curate --json bucket BUCKET_ID
```

`--full` shows every returned field and source text as formatted JSON. `--json`
is the scripting contract; scripts that previously parsed default JSON must add
this option. For item, bucket, and claim detail they request the legacy full projection.
Collection endpoints remain paginated; these options do not fetch all pages. `paperflow api` continues to return complete responses by default.

`--measure` writes one JSON measurement to stderr for a rendered response,
keeping stdout usable by scripts. Measurements include output characters and
UTF-8 bytes (excluding the final CLI newline), the expanded response's character
count, and the percentage reduction. Estimated tokens use `ceil(characters/4)`;
this is a rough context-budget aid, not a tokenizer count or a model context-limit
check. `expanded_chars` measures the formatted response **for the requested
projection**, not the size of a hypothetical full response. The `backend` object
also reports actual response bytes, research SQL statement count, and server
latency from API headers. Compare compact and full requests explicitly to measure
backend savings. SQL counts cover the research adapter, not monitor-store SQL;
latency ends when the API response is ready, excluding network transfer.
File exports print their path and do not emit a response-size measurement.

### Initial CLI journey validation (before backend projections)

An 18-command HTTP-backed journey on `artifacts/radar.db` created the explicitly
named `CLI usability validation: Rust safety` bucket, reused existing sources,
annotated a source revision, recorded a scoped claim and evidence, marked the
machine-created work `needs_review`, inspected the result, and exported a brief.
The validation server was stopped afterward. This is a functional walkthrough,
not a human usability study.

| View | Expanded characters | Compact characters | Reduction |
| --- | ---: | ---: | ---: |
| Item | 3,440 | 328 | 90.5% |
| Bucket | 8,053 | 834 | 89.6% |
| Claim | 4,455 | 910 | 79.6% |

Receipts, measurements, the replay script, and the exported brief are in
`artifacts/curator-cli-validation/`. Replay creates additional validation records.


Status: implemented and exercised against the configured local database.

## Architecture

```text
paperflow curate / future reader UI
    → FastAPI /api/research
    → CurationService
    → ResearchStorage / ResearchRepository
    → SQLite adapter
```

`paperflow_curation.py` owns the curator operations and typed request/read models.
`paperflow_curation_api.py` only binds those operations to HTTP.
`paperflow_curate_cli.py` is a dedicated API client: it does not open SQLite,
perform independent domain writes, or start an embedded server. The generic
`paperflow api` group shares its HTTP transport.

## Backend contracts and lifecycle

The API now supports `?view=compact` on item, bucket, and claim detail. The CLI
uses it by default. Compact bucket views query at most 10 memberships and 10
claim summaries without loading their evidence trees. Claim drill-down pages
load at most 10 assessments and the latest review for each. Item views return
three revision previews and observation counts. `RevisionPreview` has
`preview_text` and `character_count`; it is not a truncated `ContentRevision`.
Fetch `/revisions/REVISION_ID` to obtain full text with its matching hash.
Full detail responses remain available for compatibility and explicit exports.

Collections return `{records, total, limit, offset, next_offset}` with a maximum
page size of 200. This is a response-shape change for bucket lists and annotation
lists. Paginated endpoints include:

- `/buckets`, `/buckets/ID/members`, `/buckets/ID/claims`
- `/items/ID/revisions`, `/items/ID/observations`, `/items/ID/identifiers`
- `/items/ID/relationships`, `/items/ID/incoming-relationships`
- `/revisions/ID/annotations`, `/claims/ID/evidence`
- `/claims/ID/reviews`, `/evidence/ID/reviews`
- `/buckets/ID/history`, `/memberships/ID/history`, `/claims/ID/history`

Offset pagination is a live view; concurrent writes may shift page boundaries.
It is not a snapshot/export cursor. The generic API CLI can fetch any page:

```bash
paperflow api request GET '/api/research/claims/CLAIM_ID/evidence?limit=10&offset=10'
```

### Retry safety

Every research mutation accepts `Idempotency-Key`. A receipt stores the operation,
request fingerprint, and result in the same transaction as its writes. Reusing
that key and payload returns the original result, including after restart;
reusing it for another payload/operation returns 409. Failed transactions do not
reserve keys. Different keys permit intentional repeated reviews. Keys are
local-database scoped and retained without expiry in this initial implementation.
The CLI does not automatically retry writes:

```bash
paperflow curate --request-id collect-article-001 add BUCKET_ID ITEM_ID
```

Repeat that exact command after an uncertain connection failure. Omitting the key
retains ordinary POST behavior; UUIDs generated for records are not a substitute
for an operation retry key.

### Correcting research without erasing history

```bash
paperflow curate member-update MEMBERSHIP_ID --version 1 --creator me --note 'Corrected context'
paperflow curate member-update MEMBERSHIP_ID --version 2 --creator me --removed
paperflow curate member-update MEMBERSHIP_ID --version 3 --creator me --active
paperflow curate supersede OLD_CLAIM_ID REPLACEMENT_CLAIM_ID --version 1 --creator me
paperflow curate bucket-status BUCKET_ID archived --version 1 --creator me
```

Every lifecycle operation requires `expected_version` in HTTP (`--version` in
CLI); stale writes return 409. The original and replacement claims remain
separate: evidence and review decisions are never transferred automatically.
Supersession cycles are rejected. Each change atomically appends a lifecycle
event with before/after snapshots and actor provenance. Membership removal is
reversible and excludes that source from active collections. The membership
itself remains readable at `/memberships/ID`, with its current version.

Authored records consistently carry creator kind, processor version, and an
optional processing-run UUID. Reviews carry the equivalent reviewer fields.
Processor-created records require a version. The run UUID is a correlation ID,
not yet a foreign key to a processing-run registry. Authorship is supplied by the
local caller; it is not authenticated identity. Processor operations in the CLI
accept `--creator-kind processor` (or `--reviewer-kind processor`),
`--processor-version VERSION`, and `--processing-run-id UUID`.

### Authoritative identity and source relationships

```bash
paperflow curate identify ITEM_ID doi '10.1234/example'
paperflow curate resolve doi 'https://doi.org/10.1234/example'
paperflow curate relate DISCUSSION_ID ARTICLE_ID links_to --creator me
```

DOI URL/prefix and case variants normalize at the application boundary. Other
namespace values are preserved after trimming. Conflicting authoritative aliases
return 409; these operations do not merge items automatically. Relationships are
attributed `links_to`, `discusses`, `cites`, or `version_of` edges, with an optional
source revision anchor. They do not assert evidentiary support; use an assessment
for supports/challenges/qualifies.

### Migration and verification

Schema v3 adds command receipts, item relationships, and lifecycle history.
Source revisions and assessments remain immutable. Existing processor claims
without a captured version receive the explicit marker `legacy-unrecorded`;
legacy annotations/reviews without actor type receive `unknown`. Missing
processing-run IDs remain absent. No historical human approval is inferred.

The 32-command validation in `artifacts/architecture-validation/` exercised the
new API through CLI subprocesses against `artifacts/radar.db`, including retry
replay after restart, stale-version rejection, membership removal/restoration,
claim supersession, identities, relationships, and archival. The validation
bucket is archived; its machine reviews are `needs_review`. All 40 existing
source observations remained present, and integrity/foreign-key checks passed.
A pre-migration SQLite backup is retained alongside the receipts.

For the existing research bucket, compact versus full requests returned 5,185
versus 10,577 bytes and executed 15 versus 22 research SQL statements. The single
latency samples were 2.189 ms versus 1.799 ms: these demonstrate smaller responses
and fewer queries, **not** an established latency improvement. OTel exports
`paperflow.api.duration`, `paperflow.api.response.size`, and
`paperflow.api.db.queries`, with route templates rather than per-record labels.

## Start and inspect

```bash
uv run --extra telemetry paperflow serve
uv run paperflow api routes
uv run paperflow api request GET /api/monitor/channels
uv run paperflow api request POST /api/monitor/channels/hackernews/runs
uv run paperflow curate items --channel hackernews
uv run paperflow curate items --query Rust
```

The API URL defaults to the service host/port in `arxiv-radar.yaml`. To use
another service, place `--api-url http://HOST:PORT` before the subcommand, for
example `paperflow curate --api-url http://127.0.0.1:8000 buckets`.
Item search currently matches creation titles, not full-text or semantic content.
Pagination uses `--limit` and `--offset`.

## Critical user journeys

The IDs below are placeholders: use IDs returned as JSON by earlier commands.

### Discover and inspect a source

```bash
uv run paperflow curate items --channel hackernews
uv run paperflow curate item ITEM_ID
```

Item detail includes its source observations, signals, sampled comments, and
available content revisions. A discussion URL and its outbound article URL
remain distinct. Feed excerpts can be empty or contain only navigation text;
inspect availability before treating a revision as research evidence.

### Collect into a research bucket

```bash
uv run paperflow curate bucket-create "Research question" --question "What evidence answers this?"
uv run paperflow curate add BUCKET_ID ITEM_ID --role background --note "Starting source"
uv run paperflow curate bucket BUCKET_ID
```

An item can belong to several buckets with distinct notes and roles. Repeating
an identical membership request returns the existing membership. A changed
role/note conflicts rather than silently overwriting editorial work.

To connect research to a paper being written, create an item with
`item-add "Draft title" --kind manuscript`, then pass `--manuscript ITEM_ID`
when creating the bucket.

### Import and annotate source material

```bash
uv run paperflow curate item-add "Paper title" --kind paper --url https://SOURCE-URL
uv run paperflow curate revision-add ITEM_ID --file source-excerpt.md --basis excerpt
uv run paperflow curate annotate REVISION_ID --quote "Exact passage" --creator me
```

`item-add` records metadata; it does not fetch a URL. `revision-add` imports a
local Markdown/text file. Choose `excerpt`, `abstract`, or `full_text` to reflect
what was actually read. Identical content with the same processor and evidence
basis reuses the existing revision. Changed content creates a new revision.

Annotations bind exact Unicode character offsets to that revision. Unique
quotes are located automatically; repeated quotes require `--start`. Incorrect
quotes and cross-revision evidence links are rejected. Older annotations remain
anchored to their original content when a new revision is added.

### Frame claims and assess evidence

```bash
uv run paperflow curate claim BUCKET_ID "A scoped claim" --creator me --annotation ANNOTATION_ID --scope "Conditions and limits"
uv run paperflow curate evidence CLAIM_ID REVISION_ID --stance supports --rationale "Why this passage bears on the claim" --creator me --annotation ANNOTATION_ID
uv run paperflow curate review ASSESSMENT_ID accepted --reviewer me --note "Reviewed within the stated scope"
uv run paperflow curate review-claim CLAIM_ID needs_review --reviewer me
```

Stances are `supports`, `challenges`, `qualifies`, `provides_context`, and
`inconclusive`. Support for one claim does not label the entire source as true.
Opposing evidence can coexist. Reviews are append-only; the latest review
determines current status while preserving earlier decisions. No review means
`proposed`.

Machine-created claims/assessments should specify `--creator-kind processor`;
assessments also require `--processor-version`. Actors/reviewers are explicit
attribution fields in this trusted local service, not authenticated identities.

### Synthesize for writing

```bash
uv run paperflow curate bucket BUCKET_ID
uv run paperflow curate export BUCKET_ID --output artifacts/research-brief.md
```

The export is a deterministic Markdown research brief: collected sources,
claims, evidence rationale, exact revision IDs, passages, source links, and
review status. Proposed, rejected, and needs-review material remain labeled;
it never silently converts candidates into approved findings. This is an
evidence inventory for writing, not an automatically authored paper.

## API surface

All paths below are under `/api/research`:

| Operation | HTTP route |
| --- | --- |
| Browse/search or create items | `GET /items`, `POST /items` |
| Inspect item and provenance | `GET /items/{id}` |
| Import a revision | `POST /items/{id}/revisions` |
| Annotate or inspect annotations | `POST /revisions/{id}/annotations`, `GET /revisions/{id}/annotations` |
| List/create buckets | `GET /buckets`, `POST /buckets` |
| Read a complete bucket | `GET /buckets/{id}` |
| Collect an item | `POST /buckets/{id}/members` |
| Create a claim in a bucket atomically | `POST /buckets/{id}/claims` |
| Inspect a claim and evidence | `GET /claims/{id}` |
| Add evidence | `POST /claims/{id}/evidence` |
| Review a claim or assessment | `POST /claims/{id}/reviews`, `POST /evidence/{id}/reviews` |
| Export a brief | `GET /buckets/{id}/export` |

Creation returns 201. Missing entities return 404, conflicting identity or
membership returns 409, invalid references/quotes/input return 422, and storage
unavailability returns 503. Membership and revision imports support replay;
general create/review operations are append operations, not automatic retries.

For operations outside the focused CLI, use a JSON file or stdin:

```bash
uv run paperflow api request POST /api/research/items --body item.json
uv run paperflow api request GET /openapi.json
```

The generic CLI reports non-2xx responses on stderr with a nonzero exit code.
It does not silently retry writes.

## Live validation and remaining gaps

The 2026-11-15 validation initialized the configured `artifacts/radar.db`,
collected all four monitor channels, then performed the curator journey via
actual CLI subprocesses over HTTP. It used a live HN discussion about Rust
library safety as its starting point and primary sources discovered manually:

- [FORGE Lab article](https://forge-blog.github.io/posts/rust-std-memory-safety/)
- [Verifying the Rust Standard Library](https://arxiv.org/abs/2606.17374)
- [RustBelt author publication page](https://plv.mpi-sws.org/rustbelt/popl18/)

The two research papers were manually imported as separate items with short,
explicitly labeled source excerpts. The actual bucket has supporting and
qualifying assessments of a scoped claim, plus a challenge to an explicitly
marked overbroad curator hypothesis. All machine-created assessments and claims
are marked `needs_review`, with `codex-curator-validation` attribution. No human
approval or full-paper review is implied.

Receipts, per-channel run history, item details, database checks, and the
exported brief are saved in `artifacts/curator-validation/`.

Remaining gaps exposed by the journey:

- Automated paper discovery and citation resolution are not yet API operations;
  this pass used external research and manual import.
- Feed discovery does not extract the linked full article or generate a summary.
  HN feed descriptions can be just a link labeled “Comments.”
- The Mbin all-threads feed ingests successfully but is broad and noisy. A
  curated magazine feed would be a better default for focused research.
- Feed identity remains scoped to a feed and its supplied identifier/link;
  cross-feed/federated deduplication and manual-item identity reconciliation
  remain future work.
- Imported research papers live in the new research model; they are not yet
  mirrored into legacy paper manifests or the TeX publication pipeline.
- Source-content deletion and source-deletion reconciliation remain future work.
  Membership edits/removal/restoration, bucket archival, and claim supersession
  are now supported with version checks and audit history.
- Reddit live ingestion remains blocked on API approval. Legacy arXiv/Hugging
  Face radar commands are separate from these four monitored feed integrations.

## Threads of study

During source assessment the curator can open a recurring research question or
add a reading to an existing question. For example, two reports on different
retrieval benchmarks may belong to separate stories while both contributing to
“How does retrieval latency change as the index grows?” A source can also add a
reading while its story assessment is unresolved.

The curator sees up to eight existing questions and reuses the wording when a
reading belongs to one. Each reading records what the source contributes and an
exact passage from its revision. The assessment and reading are saved together.
New assessments can start threads immediately. Completed assessments retain
their original decisions.

The published feed links to **Threads of study** when a selected source contributes
a reading. Expand a question to read its public sources in the order they were
added, including older readings outside the current feed window.
