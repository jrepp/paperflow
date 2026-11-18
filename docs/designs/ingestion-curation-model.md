---
title: "Ingestion and Research Curation Model"
project_id: paperflow
doc_uuid: 37e2d2c0-0672-46aa-b28c-426c6fde7ef9
tags: [designs]
---

# Ingestion and Research Curation Model

Status: technical design with an implemented domain/storage foundation and
initial composable feed ingestion. API and broader editorial workflows remain
planned.
Updated: 2026-11-14.

## Intent

Make new sources inexpensive to integrate while supporting local research
buckets, article-to-paper discovery, and reviewed evidence for writing and
investigation. This model implements the curator journeys in
[Core Reader Design](core-reader.md).

The key separation is between source ingestion, reusable content, and editorial
judgment. An adapter should not need to know about buckets, ranking, writing,
or the reader interface.

## Implemented foundation

The initial implementation consists of:

- `paperflow_domain.py`: frozen Pydantic v2 records with forbidden unknown
  fields, UUID identity, timezone-aware timestamps, validated content hashes,
  typed item kinds and evidence stances, and exact revision-bound annotations.
- `paperflow_storage.py`: repository and storage protocols plus application-facing
  conflict, reference, and storage errors. Callers depend on these interfaces.
- `paperflow_storage_sqlite.py`: a SQLite implementation with explicit units of
  work, foreign keys, unique aliases and memberships, and an independently
  versioned additive `research_*` schema.
- `paperflow_settings.py`: Pydantic Settings configuration with nested storage
  options and explicit override > environment > YAML > default precedence.

Items, identifiers, Markdown revisions, research buckets, memberships,
annotations, claims, bucket claims, evidence assessments, and claim/evidence
reviews are implemented. Content is currently stored inline in SQLite as part
of validated records. External content-addressed blobs remain a later step.

Source revisions, annotations, evidence assessments, and reviews remain immutable.
Re-adding the same ID and identical content is idempotent. Bucket status,
membership context/visibility, and claim supersession now use optimistic version
checks plus append-only lifecycle events. Claim evidence is never retargeted to
a replacement claim. The shared validation function runs in application services
as well as the SQLite adapter; exact anchors, manuscript targets, cross-revision
evidence, and relationship provenance do not depend on SQLite constraints alone.

Authoritative identifier registration and resolution are exposed through the
application/API, including DOI normalization. Typed, attributed item relationships
separate source links/citations from evidence assessments. Automatic merging is
not implemented. Schema v3 adds durable command receipts for retry-safe mutations.

Review decisions remain append-only: no reviews means proposed, and the last
inserted review determines the current decision. Creator/reviewer kind, processor
version, and optional run correlation IDs are preserved. Legacy unknown actor
types and processor versions remain explicitly marked as unknown/unrecorded.

See [Curator Workflow](../guides/curator-workflow.md#backend-contracts-and-lifecycle) for
bounded API projections, pagination, lifecycle operations, migration behavior,
and the real-database validation results.

### Configuration

The optional `research` section lives in the existing `arxiv-radar.yaml`:

```yaml
research:
  storage:
    adapter: sqlite
    path: artifacts/radar.db
    busy_timeout_seconds: 5
```

For example, `PAPERFLOW_STORAGE__PATH=artifacts/research-test.db` overrides the
YAML path. Passing `storage={"path": "artifacts/experiment.db"}` to
`load_research_settings` overrides that environment value while retaining other
nested configuration. Missing `research` uses defaults; a missing selected
config file or invalid research section fails explicitly. Other top-level
legacy config sections remain owned by their existing loaders. Relative paths
remain relative to the working directory. `.env` loading is not implicit.

### Application usage

```python
from paperflow_domain import BucketMembership, Item, ResearchBucket
from paperflow_settings import load_research_settings
from paperflow_storage import ResearchStorage
from paperflow_storage_sqlite import SQLiteResearchStorage

settings = load_research_settings()
storage: ResearchStorage = SQLiteResearchStorage(settings.storage)
storage.initialize()

with storage.transaction() as repository:
    article = repository.add(Item(kind="article", title="An interesting finding"))
    bucket = repository.add(ResearchBucket(
        title="Model evaluation", research_question="When does this finding hold?",
    ))
    repository.add(BucketMembership(bucket_id=bucket.id, item_id=article.id))

with storage.transaction() as repository:
    members = repository.bucket_members(bucket.id)
```

The application composition root selects the concrete adapter; workflow code
accepts `ResearchStorage` or `ResearchRepository`. A successful transaction
commits all writes; an exception rolls them back. Network/model calls should
occur outside the transaction. SQLite operations are synchronous; a future
async service must dispatch them off the event loop. Database contention is
reported after the configured timeout rather than silently replaying writes.

Initialization is explicit, idempotent, and rejects unknown schema versions.
It can share the existing radar database without changing the legacy schema
version or paper rows. An automatic bridge from legacy paper records is not yet
implemented. FastAPI routes and an API-backed curator CLI now implement the
[curator workflow](../guides/curator-workflow.md). The initial ingestion
registry, `sources collect` command, and typed source observations are described
in [Composable source integrations](composable-ingestion.md).

The following sections describe the broader target model, including entities
and workflows beyond this initial foundation.

## Adapter boundary

The first bounded Reddit feasibility probe is documented in
[Reddit ingestion validation](../validation/reddit-ingestion.md). It does not yet persist
records or implement the generic adapter registry described below. Reddit's
deletion requirements also require a source-content removal lifecycle before
using the append-only research store for its content.

Keep source-specific modules under `paperflow_sources_<source>.py`. Extend the
existing `SourceFetchSpec` / `SourceFetchResult` pattern with a versioned generic
record envelope. Paper adapters retain a projection into the shared radar report
shape for existing consumers; articles should not pretend to be arXiv papers.

Proposed contract:

```text
AdapterDescriptor:
  name, contract_version, config_schema
  capabilities: incremental_fetch, conditional_fetch, search, comments

FetchRequest:
  source_instance_id, channel_id, config, checkpoint, limit

FetchBatch:
  records[], next_checkpoint, has_more, fetched_at, raw_artifact_refs[]

SourceRecord:
  upstream_id, kind, title, canonical_url?, authors[], published_at?, updated_at?
  identifiers[], content_or_artifact_ref?, outbound_references[]
  observed_signals[], source_metadata
```

An RSS adapter parses feed entries; a discussion adapter emits posts and linked
comments; a manual adapter accepts URLs or clippings. Unsupported capabilities
are explicit. Search is optional and should use a separate capability rather
than forcing feed adapters to implement search.

Register adapters by name in one registry instead of extending the conditional
dispatch in `paperflow_refresh.py` for every source. Begin with explicit in-repo
registration; external package loading is not required for the first version.

The shared runner owns configuration validation, HTTP policy, rate limits,
timeouts, scheduling, persistence, identity resolution, retries, and telemetry.
Adapters own upstream fetch and parsing semantics. Extraction, summarization,
ranking, and paper discovery are independently versioned processors.

New-adapter checklist:

1. Implement the descriptor and fetch contract in one source module.
2. Register it and add source/channel configuration to `arxiv-radar.yaml`.
3. Add representative recorded fixtures and contract checks for identity,
   malformed entries, paging or unchanged responses, and retry behavior.
4. Verify replay of the same batch does not duplicate content or occurrences.

Persist records and the associated checkpoint in the same database transaction.
Store referenced blobs atomically before committing their metadata. A crash may
leave an unreferenced blob, but must not advance a checkpoint past unsaved data.
Checkpoint semantics belong to the adapter: a cursor, ETag, or timestamp need
not mean the same thing across sources. Empty or unchanged successful polls
still update source freshness. Do not hold database transactions during network
or model calls.

## Local data model

Use additive SQLite tables and local content-addressed files. Relational tables
are sufficient for the relationship graph; no separate graph service is needed.
All identifiers below are stable internal IDs unless explicitly namespaced.

| Entity | Essential fields and relationships |
| --- | --- |
| Source instance | Adapter name/version, configuration reference, endpoint |
| Channel | Source instance, label, schedule, priority, filters, checkpoint, last success |
| Item | ID, kind (paper/article/discussion/note), title, canonical URL, timestamps |
| Item identifier | Item ID, namespace, external value; unique where authoritative |
| Source occurrence | Item ID, channel ID, upstream ID, retrieval metadata; unique per channel/upstream ID |
| Content revision | Item ID, content hash, raw and extracted artifact references, extraction version, availability |
| Item link | Source item, target item or unresolved reference, type such as cites, discusses, or version-of; provenance |
| Signal snapshot | Occurrence or item ID, signal type, value, observation time |
| Derived artifact | Input revision IDs, kind, processor/model/prompt version, output reference, creation time |
| Research bucket | ID, title, research question, description, status, optional target manuscript ID |
| Bucket membership | Bucket ID, item ID, role, ordering, curator note, added time; unique per bucket/item |
| Annotation | Revision ID, quote and locator, context, note, creator; optionally associated with a bucket |
| Claim | ID, statement, scope/qualifiers, origin (source or curator), source annotation if applicable, review status |
| Bucket claim | Bucket ID, claim ID, local framing and order |
| Evidence assessment | Claim ID, evidence revision and optional annotation, stance, rationale, evidence basis, provenance, review state |
| Discovery run | Bucket/claim/seed item IDs, query, provider, filters, time, processor version, job ID, outcome |
| Discovery candidate | Run ID, item ID or unresolved identifier, retrieval reason, rank, screening status |
| Synthesis | Bucket ID, version, outline or draft artifact, cited claim/assessment/revision IDs |
| Job / attempt | Stage, input version, status, lease owner/token/expiry, heartbeat, retries, timing, error, trace ID |
| Reader state | Item ID, read/saved/dismissed state and reading position |

Clippings are annotations anchored to revisions, optionally with a curator note.
Generated summaries are derived artifacts, not independent primary evidence.
User-authored notes or manuscript drafts may be items, with manuscript purpose
recorded in metadata. A target manuscript is optional: buckets must also work
for open-ended investigation.

Adding the same item to different buckets does not copy its content. Membership
roles such as background, method, or candidate citation express editorial use;
they do not imply that an entire item supports every claim in a bucket.

## Identity and provenance

Use stable opaque item IDs with aliases for DOI, arXiv ID, source IDs, and URLs.
Enriching an article with a discovered DOI must not change its internal ID or
invalidate annotations. An article describing a paper and that paper are two
items connected by a relationship, even if the article contains its DOI.

Match authoritative identifiers first. URL normalization must be conservative;
title similarity only proposes a possible match. Preserve unresolved references
until resolution is reliable. Reversible merges must retain aliases,
occurrences, bucket memberships, and provenance.

Content edits create new revisions. An annotation or evidence assessment stays
attached to the version reviewed; newer content can flag it for re-review.
Store source-supplied summaries separately from generated summaries. Record
abstract-only, excerpt-only, and full-text evidence bases explicitly.

## Claims and evidence

An evidence assessment means: **this passage or source revision bears this
relationship to this specific claim, within the stated scope**.

Initial stances: supports, challenges, qualifies, provides-context, and
inconclusive. Use challenges rather than claiming definitive refutation from
an automated retrieval or summary. Preserve the curator's explanation of
population, method, conditions, and other limits that affect applicability.

Separate stance from review status: proposed, accepted, rejected, or
needs-review. Track creator type (human or processor), reviewer, timestamps,
and assessment changes. Any model confidence is an estimate distinct from
review status and source quality. Opposing assessments may coexist; never
overwrite disagreement with the most recent score.

Citation edges are factual source relationships. Evidence assessments are
interpretations. Similarity, citation count, community votes, and inclusion in
a bibliography do not automatically create a support edge.

## Proposed story and assessment model

A Story is the published account of one event or research finding. It has a
stable ID and versioned editorial text. A Source on a story refers to an
existing Item and the exact ContentRevision assessed. SourceObservation records
retain channel and discussion provenance. A Topic can contain several stories.

A StoryAssessment records what a source revision contributes to a story at a
particular point in time. EvidenceAssessment continues to express a source's
stance toward a particular Claim. An assessment can point to those records
when its judgment relies on reviewed claims.

| Record | Information to retain |
| --- | --- |
| Story | Stable ID, title, creation time, current published version |
| Story version | Story ID, version, account, time of material change, cited revisions and assessments |
| Story source | Story ID, item ID, current assessment, first attached time |
| Story assessment | Input revision, candidate story and version if present, outcome, findings, evidence basis, author or processor attribution, rule set version, optional prior assessment |
| Assessment finding | Stable rule ID and version, result, short rationale, revision-bound passages or an explicit description of missing evidence |

### Assessment rules and actions

First determine whether the incoming source covers the same event or finding.
Then compare its contribution with the cited evidence in the current account.
Record each applicable finding. A single source may repeat a headline and
correct a measurement. The correction warrants an update.

| Outcome | Initial rule | Action |
| --- | --- | --- |
| New story | Sufficient source material identifies a distinct event or finding | Create a story, attach the source, and queue initial curation |
| Repeated coverage | The account already covers the substantive content, including material traced to a shared origin | Attach the source and preserve the analysis and feed position |
| Material contribution | Independent evidence, a correction, a qualification, or a challenge warrants revising the account or its stated confidence | Attach the source and queue an update citing the changed passage |
| Unresolved | The available text or candidate evidence leaves identity or contribution uncertain | Retain the assessment and seek fuller text or editorial review |

Treat the four outcomes as routing decisions. Extend the reasoning through
versioned rules within those outcomes. For example, a new rule for a corrected
benchmark denominator can produce a material-contribution finding using the
existing update path. Preserve the rule version and rationale used at the time.
A later review appends an assessment and links its predecessor. On retry, reuse
the decision for the same inputs, story version, and processor configuration.
Recheck the story version before applying a decision. Combine outstanding
material contributions into one curation job.

### Candidate retrieval

Index the story ID and version, headline, short account of what happened,
participants, event date when known, and distinguishing identifiers. Add the
titles and informative passages of selected sources, including corrections.
Collapse hits by story so syndicated copies cannot consume the shortlist.
Combine exact DOI, arXiv ID, and canonical source link lookup with lexical and
vector retrieval. Use recency to rank candidates while allowing late corrections
to retrieve older stories.

The proposed curator tool accepts an input revision and returns candidate story
IDs, versions, matched identifiers or passages, and the current account with its
source citations. A shared link or high similarity is a reason to inspect a
candidate. Two papers may cite the same benchmark while reporting distinct
findings. A correction may describe the same finding in very different language.
An unavailable or out-of-date index leaves the assessment pending when fallback
retrieval cannot establish adequate coverage.

Keep stories, assessments, and memberships in the research database. A Memvid
file is a candidate for a rebuildable search index. Index a new story as soon
as it has a source, including while its first curation is pending. A single
writer can replay committed changes. Resolve each hit against the current
database version before assessment. Record the embedding model, dimension,
and text preparation version so a replacement index can be built and checked
before switching readers.

### Publication and evaluation

Latest shows one card per story, ordered by its last material change. Expanding
Sources shows each source link, its contribution, and the basis for that
judgment. For a correction, show what changed with a link to the passage. Keep
candidate ranks and model diagnostics in the curator view. The static build
reads the committed projection.

Replay sources in arrival order against only the stories available at each
arrival. Compare exact and lexical retrieval with the addition of vector
retrieval. Measure how often the correct prior story appears in the first five
candidates. Evaluate wrong attachments and missed material changes separately
from full curation runs saved. Include syndicated copies, independent
confirmation, later corrections, different events involving the same actors,
and excerpts too thin to assess.

## Article-to-paper investigation

1. Save an article into a bucket and optionally select a passage or claim.
2. Extract explicit DOI, arXiv, and bibliography references and resolve them to
   paper candidates. Preserve the originating article revision and locator.
3. Propose claims and search terms for curator review. Allow direct manual
   claims so investigation does not depend on a model.
4. Search local content first; optionally search configured scholarly adapters.
   Expand queries to include alternative findings and relevant methodological
   limits, not only confirmation of the article's wording.
5. Persist query provenance and all screened candidates. Deduplicate papers
   while retaining the different routes by which they were found.
6. Retrieve available evidence and propose scoped assessments with passages and
   rationale. Abstract-only evidence is explicitly labeled.
7. Let the curator accept, edit, or reject assessments and retain open questions.
8. Assemble a bucket synthesis or manuscript outline from reviewed material,
   preserving links back to exact source revisions and assessments.

No result is distinct from a failed search, unavailable content, and evidence
against a claim. Discovery runs must retain these outcomes. Unreviewed matches
remain candidates and must not silently become accepted evidence in exports.

Example: an article claims that small models outperform larger ones. A bucket
records which tasks, resources, and evaluation conditions the claim covers.
One paper may support it on a constrained benchmark, another challenge its
generalization, and a third explain the methodological limits. The synthesis
retains all three relationships rather than assigning one global truth score.

## Service and reader implications

FastAPI and the CLI should call the same application services for ingestion,
bucket membership, discovery, and assessment review. Keep writes out of UI-only
logic. Long-running discovery returns a durable job ID for progress inspection.

The reader's minimal entry is **Add to bucket**. The bucket workspace then
reveals collected material, questions/claims, evidence, and synthesis as needed.
Offer **Find related papers** for broad discovery and **Investigate this claim**
for evidence work. Display support and challenges together, with review state
and source availability visible. Provide source-linked export for writing.

The ingestion monitor should distinguish fetching, normalization, extraction,
discovery, and evidence assessment. Trace jobs through these stages; retain
durable progress even when the telemetry backend is unavailable. Retries use
idempotency keys based on stage, input revision, and processor configuration;
lease tokens prevent expired workers from committing over a newer attempt.

## Integration and implementation order

The current `paperflow_sources.py` provides paper identity normalization and
fetch dataclasses. `paperflow_refresh.py` dispatches arXiv and Hugging Face
adapters explicitly. `radar_db.py` retains paper-centric keys and reference
edges; `paperflow_publish.py` builds a paper-oriented corpus.

1. Add generic content identity, revisions, occurrences, and adapter registry.
   Link existing paper records to generic items without replacing current keys.
2. Add RSS/Atom and manual ingestion through the shared runner, preserving
   existing paper radar exports through an explicit compatibility projection.
3. Add buckets, memberships, annotations, and manual claims/assessments. This
   makes curation useful before automated paper discovery exists.
4. Add reference resolution and replayable discovery jobs with candidate review.
5. Add optional automated evidence proposals and synthesis. Export reviewed
   paper items to current publishing workflows; expand publication contracts
   explicitly before including arbitrary article content in paper manifests.

Acceptance scenarios include duplicate feed delivery, one item in two buckets,
an article linked to several papers, opposing assessments of one claim,
source edits after annotation, unavailable full text, failed searches, and a
worker crash followed by safe retry. Confirm existing paper export, publication,
and device workflows remain compatible throughout the migration.

Reader state and preferences now use the additive SQLite v4 migration. See
[local reader verification](../validation/reader-mvp.md) for revision anchors,
restart behavior, backup checks, and remaining accessibility evidence.
