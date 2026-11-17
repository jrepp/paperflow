---
title: "Composable source integrations"
project_id: paperflow
doc_uuid: ae0626fd-8ceb-4848-b1be-da089af841d0
tags: [designs]
---

# Composable source integrations

Updated: 2026-11-15. Status: implemented feed pipeline, HN enrichment, and Hugging Face papers discovery.

## Pattern

```text
Channel configuration
  → discovery component
  → zero or more enrichment components
  → validated source-neutral batch
  → optional storage transaction
```

The same RSS/Atom discovery component serves Lemmy, Mbin, Tildes, Hacker News,
and ordinary publication feeds. Platform names are not switches in the runner.
Use API components only where they add information beyond the feed.

| Component | Responsibility | Contract |
| --- | --- | --- |
| Channel configuration | Endpoint, item kind, limits, composition | Pydantic `ChannelConfig` |
| Transport | Bounded HTTP(S) response, timeout, explicit failure | Injected `Callable[[str], bytes]` |
| Discovery | Parse a feed into normalized entries | `Discovery.fetch(channel, get)` |
| Enricher | Add signals or bounded discussion samples | `Enricher.enrich(entry, channel, get)` |
| Runner | Validate component names, compose stages, report partial failures | `IntegrationRegistry.collect(...)` |
| Storage service | Resolve identity, append observations and excerpt revisions atomically | `persist_batch(storage, batch)` |

The Pydantic contracts live in `paperflow_ingest_models.py`. The runner lives
in `paperflow_ingestion.py`; source-specific code stays in
`paperflow_sources_*.py`. Network injection lets fixtures exercise the exact
production composition without credentials or network access.

New integrations register a discovery or enrichment object once in
`default_registry()`. A new RSS/Atom source requires only configuration, not
another Python adapter. External package/plugin loading is not needed yet.

## Source investigation and live validation

All four examples below were fetched successfully from this environment on
2026-11-15, then parsed through the shared pipeline. These checks establish
endpoint availability at that time, not universal availability of all instances
or a guarantee of future endpoint stability.

| Source | Validated discovery endpoint | Enrichment in this version | Live result |
| --- | --- | --- | --- |
| Lemmy | [Technology feed on lemmy.world](https://lemmy.world/feeds/c/technology.xml) | None yet | 2 entries, no warnings |
| Mbin | [Fedia thread feed](https://fedia.io/feed?content=threads) | None yet | 2 entries, no warnings |
| Tildes | [Computing Atom feed](https://tildes.net/~comp/topics.atom) | None yet | 2 entries, no warnings |
| Hacker News | [Native RSS](https://news.ycombinator.com/rss) | Official API score, comment count, bounded top-level comments | 2 entries and 2 comment samples, no warnings |

Additional observations:

- Lemmy [documents feed support](https://join-lemmy.org/) for communities and
  other views. Treat each instance endpoint as configuration. A federation-wide
  identity merge must use verified origin identities, not a bare numeric ID.
- Mbin's `/rss` URL on Fedia returned 404. Its home page advertises
  `/feed?content=threads`, which worked. Its
  [API documentation](https://docs.joinmbin.org/api/) is a future enrichment
  reference; API access and version assumptions must be checked per instance.
- Tildes exposes both [RSS](https://tildes.net/topics.rss) and
  [Atom](https://tildes.net/topics.atom), also scoped to groups such as `~comp`.
  The verified model includes groups, not only tags. This integration does not
  require joining Tildes. No undocumented discussion-scraping API is assumed.
- The [official HN API](https://github.com/HackerNews/API) exposes story score,
  total comment count, and child IDs in ranked display order. Public comment
  scores are not supplied by this adapter; they remain unknown. Ranking order
  is not a comment-quality judgment.

The user-selected Reddit channel remains a separate approval-dependent probe;
see [Reddit validation](../validation/reddit-ingestion.md). It is not silently routed through
an unauthenticated substitute or persisted by this feed pipeline.

## Run it

The small, explicit presets are in `ingestion.example.yaml`:

```bash
uv run paperflow sources collect hackernews --config ingestion.example.yaml
uv run paperflow sources collect lemmy-technology --config ingestion.example.yaml
uv run paperflow sources collect mbin --config ingestion.example.yaml
uv run paperflow sources collect tildes-computing --config ingestion.example.yaml
uv run paperflow sources collect huggingface-papers --config ingestion.example.yaml
```

These commands print a JSON batch. Add `--persist` to store observations and
source excerpts through the research storage interface:

```bash
uv run paperflow sources collect hackernews --config ingestion.example.yaml --persist
```

The example selects `artifacts/source-validation.db`, separate from the normal
library. Live validation snapshots are in `artifacts/source-validation/` and
are git-ignored. Selected channels can be copied into the stable
`arxiv-radar.yaml` under `research.ingestion.channels`:

```yaml
research:
  ingestion:
    channels:
      hackernews:
        url: https://news.ycombinator.com/rss
        discovery: feed
        enrichers: [hackernews]
        kind: discussion
        limit: 10
        comment_limit: 3
      publication:
        url: https://YOUR-PUBLISHER.example/feed
        discovery: feed
        kind: article
```

Each channel is explicitly collected; the example does not install background
subscriptions. Nested Pydantic Settings environment/explicit overrides retain
their established precedence over YAML.

## Hugging Face papers

The `huggingface-papers` discovery component uses the public
[Daily Papers API](https://huggingface.co/docs/hub/en/api#get-apidailypapers),
registered in the same runner as RSS/Atom. Configure
`url: https://huggingface.co/api/daily_papers`, `discovery: huggingface-papers`,
and `kind: paper`. The source module also retains the separate legacy radar
command; `sources collect` emits the modern `IngestBatch` contract.

One bounded request supplies at most the configured `limit`. Optional API query
parameters such as `date=YYYY-MM-DD` are preserved, while the configured limit
wins. Keep the configured URL stable for ongoing monitoring: changing the date
in that URL also changes the existing feed-scoped identity namespace.

Entries use the API's arXiv paper ID, the arXiv abstract URL as their publication
and canonical URL, and the Hugging Face paper page as their discussion URL.
Publication time comes from the paper, not its later Daily Papers submission.
The source abstract becomes an excerpt revision; API-generated AI summaries are
not substituted. Upvotes and comment counts are observation signals, with
missing counts remaining unknown. This component does not fetch comments or
assign evidence-quality scores. Observation channel, endpoint, batch, and fetch
time preserve provenance for curation and rolling topic selection.

Malformed entries or optional metadata produce warnings; invalid response
containers and HTTP failures fail discovery, preserving HTTP status and retry
headers for the monitor. The shared transport supplies response-size and timeout
bounds. Invalid endpoint or item-kind configuration fails before network access.

On 2026-11-15 a live collection persisted two paper entries without warnings to
`artifacts/source-validation/huggingface-plugin.db`. Deterministic fixture tests
in `tests/test_huggingface_ingestion.py` cover parsing, limits, failures, replay,
canonical paper identity, and refreshed observations without duplicate revisions.
This establishes ingestion/storage behavior, not browser acceptance.

## Identity, observations, and evidence

`SourceEntry` separates the outbound URL from the discussion URL. A discussion
that links to a paper is not itself that paper. Feed GUIDs/Atom IDs are preferred
over entry links for identity. A link is a fallback only when no ID is present.

Identity is scoped to the exact configured feed URL to avoid collapsing unrelated
posts or instance-local identifiers. Shared origin IDs and cross-feed identity
reconciliation are future work. Changing a feed URL can therefore create a new
identity scope; it is not a transparent channel migration.

Persistence resolves an alias to an existing `Item`, creates it only when absent,
and records a typed `SourceObservation` containing the latest title, excerpt,
URLs, signals, sampled comments, channel, batch ID, and observation timestamp.
Item creation metadata remains unchanged; observations carry source updates.
Do not read the original item title as the current source title after an update.

The same batch can be replayed without duplicates. A new collection produces a
new observation even when unchanged, preserving the time of signal collection.
Unchanged excerpt revisions are reused. The complete batch transaction commits
or rolls back together, and fetch/enrichment happen before opening it.

Feed descriptions are stored as source-provided excerpts after HTML-to-text
conversion. They are not generated summaries or full-text extraction. Scores
and comment counts are optional: unavailable is not zero. Discussion samples
are limited and are not evidence assessments. Curators can place imported item
IDs into buckets using the existing domain/storage interfaces.

The additive research schema is now version 2, introducing source observations.
Initialization upgrades version 1 without modifying legacy radar tables.

## Failure and scope boundaries

- Unsupported component names fail before fetching.
- Discovery HTTP/parsing errors fail the collection; HTML login/error pages are
  not accepted as feeds.
- Optional enrichment failures preserve discovery records with batch warnings.
  HN comment failures mark the sample unavailable; available samples may remain.
- HN items reported deleted/dead during enrichment are omitted. Previously
  stored deletions are not reconciled in this initial implementation.
- Fetches are byte- and time-bounded. HN enrichment caps comment requests per
  story and does not traverse replies. No automatic rate-limit retry loop runs.
- Collection polls a bounded current feed window. It does not claim complete
  historical ingestion, pagination, ETag checkpoints, or deletion reconciliation.
- The CLI is a local trusted configuration entry point. Remote service exposure,
  ranking, and full-text extraction remain later layers. Local FastAPI service,
  background scheduling, and OTel instrumentation are now implemented in the
  [integration monitor](../guides/integration-monitor.md).
- This content pipeline emits `IngestBatch`; it does not push articles into the
  legacy paper-only manifest or fabricate arXiv identifiers. Existing radar and
  publishing contracts remain unchanged.

## Next UX work

Use the validated records to prototype four connected tasks in a neutral style:
discover a useful discussion, read its context, follow a paper/article link,
and collect it into a research bucket. Make unavailable signals and partial
comment samples visible. Keep source popularity distinct from reviewed evidence
when the curator investigates supporting and challenging papers.
