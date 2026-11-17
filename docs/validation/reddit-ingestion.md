---
title: "Reddit ingestion validation"
project_id: paperflow
doc_uuid: 93689665-485f-4edb-ab85-96a98d52c2a6
tags: [validation]
---

# Reddit ingestion validation

Updated: 2026-11-14.

## Current result

Target channel: **r/LocalLLaMA**, selected by the user. The user does not yet have
approved Reddit Data API access. No live requests have been made by the adapter.
Live feasibility is therefore unverified; synthetic fixture tests validate only
our normalization, bounded fetch behavior, error handling, and CLI wiring.

`paperflow sources reddit-probe` is a read-only validation command. It prints a
typed JSON result and does not write Reddit content into the research database.
It is deliberately a probe rather than a production ingestion worker.

## Access path

Reddit's [developer FAQ](https://developers.reddit.com/docs/guides/faq) directs
external scripts and integrations to the Data API approval process. A Devvit
login does not provide credentials for an external Python application.

Start with the [Data API guidance](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)
and its linked [access request form](https://support.reddithelp.com/hc/en-us/requests/new?ticket_form_id=14868593862164).
Approval is required under the [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy).
Approval is not assured. RSS or anonymous JSON is not treated as a substitute
for approved access to scores and comment content.

Draft scope for the access request, to review before submission:

> Paperflow is a local research reader and curation application. The initial
> validation would read public posts from r/LocalLLaMA and a bounded sample of
> top-level comments to help a reader discover research discussions and links
> to papers. It would inspect observed scores, comment counts, and publication
> times, preserve source links, and distinguish popularity from evidence
> quality. It would not post, vote, message users, or access private communities.
> The first probe is limited to ten posts and at most twenty comments per post.
> The application runs locally in Python rather than inside Reddit. Persistent
> storage and any later summarization would need to follow the approved scope,
> including deletion handling and retention requirements.

Include the actual intended distribution and commercial/non-commercial status
in the request. Do not describe the project as an approved academic research
program or promise production lifecycle controls that are not implemented.
No request has been submitted on the user's behalf.

## Run the offline validation

```bash
uv run paperflow sources reddit-probe \
  --subreddit LocalLLaMA \
  --fixture tests/fixtures/reddit-synthetic.json
```

The fixture is synthetic, including its discussion text and illustrative paper
links. It is not a captured Reddit response and does not verify any research
finding. Fixture mode never initializes the live network transport and labels
the result `mode: fixture`.

## Run after approval

Provide a valid OAuth bearer token for the approved application with access to
the read endpoints. Token acquisition and refresh are not implemented in this
probe. Configure the token locally as `PAPERFLOW_REDDIT__ACCESS_TOKEN`, without
pasting it into chat or committing it to YAML. Configure
`PAPERFLOW_REDDIT__USER_AGENT` with an honest app/version/contact identifier,
following Reddit's documented format. Settings use Pydantic `SecretStr`, and
the token is excluded from serialized configuration.

```bash
uv run paperflow sources reddit-probe \
  --subreddit LocalLLaMA --sort top --window week --limit 5 --comments 5
```

Optional non-secret settings in `arxiv-radar.yaml`:

```yaml
research:
  reddit:
    user_agent: "desktop:paperflow:v0.1.0 (by /u/YOUR_ACTUAL_USERNAME)"
    timeout_seconds: 20
```

The probe makes one listing request and at most one comment request per returned
post. It does not paginate, expand `more` comments, follow external URLs, retry
denied requests, or follow redirects with the bearer token. It stops issuing
network requests when the reported rate budget is exhausted. Expired tokens,
denied access, and rate limiting produce actionable errors. A failed comment
sample preserves the post and marks comments unavailable rather than silently
reporting an empty successful sample.

## Signal and model contract

- Subreddit maps to a channel such as `reddit:localllama`.
- Post fullnames (`t3_*`) and comment fullnames (`t1_*`) are source identifiers.
- Preserve the observed post score and upvote ratio separately; do not label
  score as an exact count of upvotes.
- Hidden comment scores remain unknown. Top-ranked comments are a limited
  sample, not a quality judgment or complete representation of the discussion.
- Observations carry a collection timestamp. This is not the publication time.
- Explicit arXiv and DOI links are discovery candidates, not verified identities
  of the post and not evidence assessments. A post and a linked paper remain
  separate domain items.
- The probe does not generate summaries or infer support/refutation. Those
  belong in later, provenance-aware processors and curator review.

## Persistence prerequisite

Reddit's Data API guidance requires removal of deleted content and deletion of
author-identifying information when an account is deleted. It recommends
routine deletion within 48 hours; that recommendation is not a blanket guarantee
that a fixed retention period satisfies every obligation.

Before persisting Reddit records, add source occurrence tracking, refresh and
deletion reconciliation, and a removal path covering raw content, revisions,
quotes, derived summaries, indexes, exports, and backups. Immutable evidence
history must not prevent source-content removal. An independently retrieved
paper has its own provenance and should not be conflated with a deleted Reddit
discussion that linked to it. Exact retention and downstream processing must
fit the granted access scope.

## Validation checklist and UX handoff

Live validation, once access is available:

1. Retrieve five r/LocalLLaMA posts and bounded top-level comment samples.
2. Verify stable IDs, source links, timestamps, scores, and hidden-score handling.
3. Verify explicit paper links and incomplete comment samples are represented.
4. Record access failures and rate-limit behavior without leaking credentials.
5. Decide whether this approved access path supports the planned local reader.

Then prototype these journeys using actual permitted data, or clearly marked
synthetic examples while approval is pending:

- **Discover:** scan a discussion card, understand its signal, and inspect why
  it surfaced without confusing popularity with evidence.
- **Understand:** open the discussion and selected comments while preserving
  the original list position and source links.
- **Investigate:** follow a paper link, select a claim, and distinguish a
  discovered paper from reviewed supporting or challenging evidence.
- **Curate:** save the discussion and paper into a research bucket, annotate
  the relevant passage, and return to the reading context.

Compare interaction flows in one neutral visual style before theme variants.
Include empty, unavailable-comments, expired-access, and removed-content states.
The next UX work should follow these tasks rather than another palette sheet.
