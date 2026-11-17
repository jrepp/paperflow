---
title: "Editorial topics above ingestion sources"
project_id: paperflow
doc_uuid: 32b67822-e59b-4b74-8689-d139b19b0ed0
tags: [designs, reader]
---

# Editorial topics above ingestion sources

Stable source channels describe where information came from. Topics describe what
readers can explore now. Topic assignment does not change source observations,
article identity, saved state, research buckets, or evidence revisions.

## Window and lifecycle

The curator considers items first observed locally in the rolling 24-hour window.
Refreshing an old observation does not make an item new again. The window includes
its start and excludes its end. Reader queries apply the window even between runs,
so expired assignments cannot linger indefinitely when scheduling stops.

New themes publish automatically; editors can rename, suppress, or change their
sensitivity afterward. Those decisions survive later runs. Dynamic themes with no
current members disappear from the reader picker while their history remains.
Persistent editorial topics remain available when quiet and match configured terms
in titles or excerpts. Latest is a permanent index of current published topics,
ordered by their newest arrival; it is not an ingestion source or editable topic row.

Sensitivity is independent of persistence and window membership. Urgent, timely,
and lasting themes use relevance half-lives of 3, 12, and 72 hours respectively.
This affects topic relevance ranking, not evidence quality or source retention.

## Domain and storage

Pydantic models define topics, immutable run editions, assignments, editorial edits,
and settings. SQLite schema v6 stores these through the repository boundary. Each
assignment retains the item, observation, original arrival time, and assignment
reason. Editorial updates require an expected version and retain before/after state.
Runs commit atomically; exact run-ID retries return the prior result. Candidate
limits fail explicitly before publishing a partial selection. Old runs cannot
replace a newer edition. History is retained without a pruning policy in this MVP.

The injectable curator protocol currently uses deterministic title phrases. It is
a local baseline, not semantic clustering or an LLM quality assessment. It needs
at least two matching items to propose a dynamic theme; unmatched items remain
readable in Today. Persistent topics use editor-provided matching terms. An item
can belong to a persistent topic as well as an automatic theme.

## Operations and review

`research.topics` configures the 24-hour window, automatic publication, 15-minute
schedule, minimum membership, maximum 12 proposed themes, and 2,000 candidate cap.
The monitor executes curation alongside ingestion with bounded worker capacity.
OpenTelemetry records run counts, candidate counts, duration, and a curation span;
monitor health exposes failures. Failed attempts are not durable topic editions.

Use `/topics` to run curation, create persistent topics, inspect assignment reasons,
and edit published themes. The reader picker separates topics from source feeds.
Use `/api/topics/latest`, `/api/topics`, `/api/topics/runs`, and the individual topic
resource for API access. The same operations are available from the CLI:

```bash
paperflow topics --api-url http://127.0.0.1:8766 run
paperflow topics --api-url http://127.0.0.1:8766 list --editorial
paperflow topics --api-url http://127.0.0.1:8766 create "Local models" --term "local model" --editor "local reader"
```

`tests/test_topics.py` checks time boundaries, restart and concurrent retry,
editorial preservation, provenance rejection, overflow rollback, API filtering,
monitor telemetry, and CLI delegation. The browser suite exercises Latest,
filtering, editorial changes, and recuration. See the
[reader verification record](../validation/reader-mvp.md) for accessibility gaps.

## Compact CLI curation

`paperflow topics` prints a short workflow without contacting the service.
`list` shows ten topics by default; `brief TOPIC` returns five source excerpts of
at most 240 characters each. These excerpts are explicitly labeled source material,
not an AI synthesis. Use `--limit`, `--offset`, and `--excerpt-chars` for controlled
drill-down. A brief includes item IDs, source URLs, observation IDs in JSON,
assignment reasons, total count, and a continuation offset. `show` is compact by
default; global `--full show TOPIC` retrieves assignments and editorial history.

Use `edit TOPIC --summary "Synopsis" --editor NAME` to save a reviewed synopsis
in editorial history. Briefs label it as an editorial summary, separately from
source excerpts, and cap it at 500 characters with truncation metadata.
`edit TOPIC --term TERM` replaces matching terms (repeat for each term).

Topics resolve by case-insensitive exact title, UUID, or unique UUID prefix of at
least four characters. Ambiguous selectors fail before mutation. Global `--measure`
prints output bytes, characters, a rough characters/4 token estimate, and aggregate
API request/response/query measurements to stderr; `--json` retains structured
output on stdout. Limits still apply in JSON mode.

```bash
paperflow topics --api-url http://127.0.0.1:8766 --measure brief "Linux" --limit 3
paperflow curate --api-url http://127.0.0.1:8766 items --query "kernel"
paperflow topics --api-url http://127.0.0.1:8766 curate "Linux" --include ITEM_UUID --editor "local reader"
paperflow topics --api-url http://127.0.0.1:8766 curate "Linux" --exclude ITEM_UUID --note "Outside this theme"
paperflow topics --api-url http://127.0.0.1:8766 curate "Linux" --reset ITEM_UUID
```

Inclusions and exclusions are editorial overrides retained in the topic payload
and before/after edit history. They survive curator runs; reset removes both kinds
of override and returns that item to automatic matching. Inclusion does not revive
an item outside the rolling arrival window. Suppression still hides the topic.
These payload additions are backward compatible with existing v6 records.

The CLI reads the current version before writing; an explicit `--version` can
protect a previously reviewed snapshot. Conflicts require re-reading. Curation
first saves the editorial choices, then runs assignment refresh. If refresh fails,
the choices remain saved and the CLI advises retrying `topics run`. The operation
does not pretend these two requests are a single transaction.
