---
title: On-demand static publishing
project_id: paperflow
doc_uuid: 944f2705-8910-48f4-b557-a406a1ca365e
tags: [publishing, operations]
---

# On-demand static publishing

Paperflow can build a static release from its existing local research database
on request. Source ingestion and topic curation keep their own schedules; a
static build does not fetch sources or invoke curation.

## Configuration

The API is disabled by default. Enable it in an external runtime configuration:

```yaml
research:
  publishing:
    build_api_enabled: true
    build_token_file: /run/secrets/publishing-build-token
    releases_dir: artifacts/publish/releases
    build_reuse_seconds: 30
```

The same fields accept `PAPERFLOW_PUBLISHING__` environment overrides. The token
file must contain a random credential of at least 32 characters; keep it outside
the repository with access limited to the application and the authorized build
client. Missing or empty credentials prevent startup when the API is enabled.
Token rotation requires restarting the application. Endpoint addresses, transport
security, credentials, transfer accounts, and deployment paths belong to the
operator's external configuration. Use HTTPS or an encrypted private transport.

## Build contract

Both endpoints require `Authorization: Bearer <credential>`:

- `POST /api/publish/builds` returns HTTP 202 with a durable job receipt.
- `GET /api/publish/builds/{job_id}` returns that receipt's current status.

A receipt contains `id`, `status`, `created_at`, `release_id`, `build_id`, and
`error`. Status is `running`, `succeeded`, or `failed`. On success, `release_id`
is the job UUID without hyphens and names the immutable output directory under
`releases_dir`. `build_id` identifies the renderer's build timestamp. No filesystem
paths or exception details are returned. Missing or incorrect credentials get
HTTP 401. Unknown jobs get HTTP 404. Disabled endpoints are not registered.

Concurrent requests share the active job. Successful jobs can be reused for the
configured short interval, allowing immediate retries without another build.
A POSIX filesystem lock coordinates processes that share the release directory.
This is a local-filesystem service, not a distributed job queue.

The worker renders outside the release tree, adds `release_id` and the
`index_sha256` digest to `build.json`, then atomically renames the completed
directory into place. Only `index.html` and `build.json` are published. Job
receipts live in the sibling `.build-jobs` directory and survive restarts.
Interrupted jobs become failed on lookup or the next request; a release already
renamed before an interruption is recovered as successful. Abandoned staging
directories are never published and may be removed during operator maintenance.

## Consumer responsibilities

Poll the returned job until it succeeds or fails, with a bounded deadline.
Fetch exactly the returned release rather than selecting the newest directory.
Verify the manifest's release identity and HTML digest before promoting it with
an atomic local operation. Failed builds, downloads, or verification must leave
the previously published site in place. Do not forward credentials on redirects.

Job receipts and releases are retained for audit and rollback. Operators own
retention and must preserve releases still being transferred or served.

The command `paperflow publish site` remains available for local exports. Avoid
running a separate scheduled exporter into the service-managed releases tree.

## Expandable curation cards

Published links retain their compact default view. Open **Curation & details**
with a click or keyboard to inspect source metadata, observed score and comment
counts, discussion links, and model curation when available. Missing counts remain
absent; zero counts are displayed. Source excerpts are labeled separately from
model summaries. Curation of an older source revision is explicitly identified.

When the assistant is enabled and its database exists, both CLI and API builds
include its latest successful public analysis: summary, findings, uncertainty,
category, and evidence limits. Internal memory, tool activity, costs, and private
run diagnostics are excluded. Publishing does not run a model or initialize the
assistant database.

**Read curation Markdown** opens the full document inside the static page, including
citations, limitations, bias assessments, research gaps, and downstream hypotheses.
**Download Markdown** saves the same document as a `.md` file. Documents use stable
item fragment links and are embedded in `index.html`, preserving the two-file
release and digest verification contract; no private workspace access is needed.
