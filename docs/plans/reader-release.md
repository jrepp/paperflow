---
title: "Paperflow reader: selected direction and release slices"
project_id: paperflow
doc_uuid: e4fecd1f-d4c0-4636-a2f2-a8c8aa18c46a
tags: [plans]
---

# Paperflow reader: selected direction and release slices

Recorded: 2026-11-15.
Design direction: **Option D — Everyday reader, selected by the user.**
Delivery status: local MVP implementation in progress. See the [verification record](../validation/reader-mvp.md) for implemented surfaces, automated evidence and remaining gates.

## Product decision

Paperflow is a highly accessible daily reading surface intended to replace the
user's Hacker News and Reddit reading habit. Primary navigation is **Today →
Saved → Research**. The default journey is **browse → read → save → resume →
return**. Article and discussion are distinct views. Research tools become
available when the reader wants to investigate; they do not dominate the reader.

Use [Option D](../designs/reader-v1/d-everyday-reader.png) for the primary composition.
A informs optional desktop preview, B the research workspace, and C focused
claim review. They are supporting flows, not competing product directions.
The [core design](../designs/core-reader.md) owns detailed accessibility, persona,
continuity, and source/provenance requirements.

FastAPI remains the service tier, Pydantic defines contracts/settings, local
storage remains supported, and the frontend uses Tailwind with semantic HTML.
Decorative themes are pluggable; accessibility preferences override theme defaults.
Static image mocks are not implemented functionality or accessibility evidence.

## Explicit sequencing decisions

1. **Run the MVP without identity.** No sign-in, identity provider, account setup,
   authentication middleware, or identity infrastructure is an MVP dependency.
2. MVP is a single-user, local installation/workspace. Browser preferences and
   durable reader state are useful without authenticated browser identity.
   An installation-local state key is not a user credential or proof of identity.
3. **After the MVP runs**, define a provider-neutral browser identity contract.
4. Add that integration before publishing a hosted application that exposes
   private reader/research state. A distributable local MVP and a hosted release
   are separate milestones; no public unauthenticated private workspace is implied.
5. This plan records the path to publication. It does not select a production
   hostname, configure hosting, or authorize a deployment during this documentation task.

## Release map

| Milestone | Required slices | Outcome / exit gate |
| --- | --- | --- |
| M — Local reading MVP | UX-01 through UX-06 | A real feed can be read, saved, resumed and revisited accessibly across browser restarts, with no identity integration |
| C — Capable reader and curator | UX-07 through UX-11 | Richer source reading and discussion, full research journey, personal control and dependable daily operation |
| I — Browser identity | ID-01 through ID-03, starts only after M | Hosted browser sessions, ownership and isolation are integrated and migration from local state is tested |
| P — Published release | REL-01 through REL-03, after C and I | Reproducible hosted release with accessible end-to-end journeys, recoverable data and operational ownership |

C and I may proceed in parallel once M is demonstrated. Identity must not delay
or silently expand the local MVP. Every slice includes desktop and narrow-screen
behavior, keyboard access, explicit loading/error/empty states, and testing.

## M — Identity-free MVP

| Slice | User journey and UX | Backend / delivery needs | Done when |
| --- | --- | --- | --- |
| UX-01: Accessible application shell | Open Today; navigate Saved/Research; find search, Sources and reading settings | Local frontend served with FastAPI; stable item/view URLs; shared theme tokens and semantic layout | Works on desktop and mobile, with skip navigation, visible focus, useful page titles and no login |
| UX-02: Daily reading feed | Scan a bounded batch; filter by channel/latest; open a source or its discussion; return to the same row | Bounded feed projection joining source observations and reader state; pagination; freshness/availability metadata | Real ingested items appear, visible rows do not reorder under the reader, back navigation restores filters/position, and stale differs from caught-up |
| UX-03: Focused source reader | Read available local text; adjust typography/width/contrast; open original when needed | Safe semantic rendering; explicit excerpt/full-text/summary availability; persisted reading preferences | Comfortable prose with working zoom, spacing overrides and keyboard navigation; unavailable full text is never implied to exist |
| UX-04: Save and resume | Save without choosing a bucket; return through Saved; resume; finish, un-save or undo | Durable local reader-state API for saved/read/in-progress/dismissed and revision-aware reading anchors; retry-safe writes | State survives refresh and service/browser restart; repeated saves do not duplicate items; undo and changed-revision recovery work |
| UX-05: Discussion reading | Open Discussion separately; inspect available sampled comments; return to the source/feed | Read existing bounded comment observations with source attribution and explicit completeness metadata | A sample is labeled sampled, missing comments have an honest empty state, and Open original discussion is useful; no posting/voting required |
| UX-06: Operate and verify the MVP | Start locally, inspect source health, pause/retry when appropriate; recover after interruption | Existing monitor APIs; a minimal Sources screen; startup instructions; failure handling; initial reader-state migration/backup checks | The complete daily journey passes on live ingested data and after restart, with recorded accessibility and persistence checks |

MVP scope intentionally excludes full thread mirroring, automatic paper discovery,
generated summaries, multi-user sharing, cross-installation sync and hosted
identity. Plain source excerpts and Open original are acceptable at M. Full-text
reading and richer discussion remain required later slices, not silently dropped.
Research may initially offer an honest entry/empty state; no nonfunctional
buttons should imply that the full curator UI already exists.

### MVP demonstration gate

Use real local feed data, then complete the following with mouse/touch and
keyboard. Repeat the main path with a screen reader:

1. Open Today and inspect a channel's freshness.
2. Open a source, change reading settings, and inspect its discussion sample.
3. Save it, return to the same feed position, and reach an end-of-batch state.
4. Restart browser/service, reopen Saved, and resume with settings intact.
5. Handle an excerpt-only item and a stale/failed source without losing context.
6. At narrow width and enlarged text, complete the same actions without clipped
   controls, obscured focus or horizontal scrolling for ordinary prose.

Record task completion, lost-position incidents, provenance comprehension and
accessibility defects. There is no account creation or IdP dependency in this demo.

## C — Fully capable reading and research

| Slice | User outcome and scope | Done when |
| --- | --- | --- |
| UX-07: Rich source reading | Read permitted linked full articles locally; follow references; view optional labeled summaries with input provenance | Extraction has pending/failed/available states, cached text remains usable when upstream is unavailable, and summaries never replace or impersonate source text |
| UX-08: Useful conversations and connections | Read supported threaded discussions with parent context, collapse branches, move between article/discussion/paper without losing place | Available coverage is explicit, threads remain accessible on mobile, source aliases/relationships reduce duplicate browsing, and unknown popularity/quality signals remain unknown |
| UX-09: Collect around a question | Add from the reader, select/create a bucket, return immediately; later organize sources and membership notes | B's workspace supports collection, edits, reversible removal and archive with conflict recovery; an item can belong to multiple questions without copying |
| UX-10: Investigate and publish a research brief | Annotate a passage, create/supersede a claim, inspect supporting/challenging evidence, review, preview and export | C's review flow preserves scope, provenance and history; links are not treated as proof; source-linked briefs contain unresolved states accurately |
| UX-11: Personal control and sustained use | Tune channels, search/filter saved material, follow explicit connections, choose accessible themes and manage local data | Preferences remain independent of themes, large queues paginate, source failures are diagnosable, and backup/restore preserves reader and research state |

Automated paper discovery and generated summaries can be added within these
flows without replacing manual import/identifier resolution. Do not make an
unavailable source silently mandatory: Reddit access is still pending approval,
and HN currently supplies bounded samples. Track provider coverage as a release
capability matrix, separating product completeness from unavailable upstream access.
If live Reddit coverage remains unavailable at publication, explicitly describe
the supported sources rather than claiming complete Reddit replacement.

## I — Browser identity after the MVP

Paperflow owns its application data, workspace ownership, and session-facing UX.
Deployment operators supply provider, gateway, and network configuration outside
this repository. No provider or existing client integration is assumed.

| Slice | Scope | Done when |
| --- | --- | --- |
| ID-01: Establish the integration contract | Define the supported browser integration; agree issuer, claims, identity key, callback/origin, groups/policy needs and environments | A concrete reviewed contract identifies application and operator responsibilities and keeps infrastructure policy outside Paperflow; no secret values in documentation |
| ID-02: Browser session and ownership UX | Sign in, return to intended page, restore the correct workspace, handle expiry/logout and denied access | Server-verified session maps to stable identity/workspace ownership; access checks cover reads and writes; two identities cannot see each other's Saved, notes or research unless sharing is explicitly designed |
| ID-03: Adopt local MVP state | Explicitly import/associate the existing local workspace with the intended signed-in owner | Saved items, reading anchors, preferences, buckets and audit history survive; migration is repeat-safe and recoverable; local data is never assigned automatically to an arbitrary first visitor |

Choose browser/session storage mechanics during integration, not in the MVP.
Browser identity, source-provider credentials and authored-record attribution
are separate concepts. Preserve historical creator labels during migration;
login must not retroactively turn an unverified label into verified authorship.
Global idempotency receipt keys must become scoped to the authenticated
workspace/principal before multi-user operation. New ownership checks also apply
to exports, relationship lookup and lifecycle history, not just list pages.

Cross-device continuity becomes a server-owned reader-state capability for the
same authenticated workspace. It is not promised for separate anonymous local
installations. Session expiry should preserve reading context and unsent drafts,
then return the reader to the intended task after successful authentication.

## P — Published application

| Slice | Scope | Done when |
| --- | --- | --- |
| REL-01: Package and stage | Reproducible frontend/backend artifact, release configuration, schema migration, static assets, service/gateway integration in hosting | A staging deployment runs the exact candidate artifact; client/API versions are compatible; identity and data persistence work on the intended origin |
| REL-02: Release rehearsal | Complete daily-reader and curator journeys, accessibility evaluation, source failures, session expiry, stale edits, backup/restore and rollback | Evidence is captured against the staged build; data survives restore; operational telemetry is useful without logging private source text or credentials |
| REL-03: Publish and operate | Final hostname/routing/TLS, deployment, health verification, operator runbook, feedback and upgrade path | The published URL works with verified identity and isolation; an owner can diagnose failed ingestion and reverse a bad release; supported source coverage and known limits are stated |

Research-brief export and application publication are different outcomes.
Bridging research exports into the existing TeX/periodical pipeline remains a
separate publishing integration; a working Markdown brief must not be described
as an implemented TeX bridge.

## Shared completion requirements

- Apply the core design's WCAG 2.2 AA target and stronger reading defaults to
  every slice. Test working HTML with keyboard, screen readers, zoom/reflow,
  text-spacing overrides, reduced motion and forced colors; screenshots alone
  establish none of these.
- Keep list position, reading position and source availability predictable.
- All new persistent state has a typed API/domain contract, storage migration,
  conflict/retry behavior and restart verification. Keep reader state separate
  from immutable source revisions and evidence assessments.
- Show loading, empty, unavailable, stale, failed and recovered states where
  relevant; do not disguise absence as a successful zero-result operation.
- Use representative real sources and opposing evidence in functional checks.
  Track task completion and context restoration, not time spent scrolling.

## Immediate next work

Implement UX-01 through UX-04 as the first vertical slice, then UX-05/UX-06 to
complete the local MVP gate. Reuse the existing FastAPI/domain/storage boundary.
Start identity integration only once that MVP journey runs. Revisit this plan
as implementation evidence arrives, preserving the selected D direction and
identity-free MVP constraint unless the user changes them.

## Acceptance traceability

The [acceptance baseline](reader-acceptance.md) defines stable requirement and criterion IDs.
The [test coverage](../validation/reader-acceptance.md) distinguishes executable backend
journeys from unresolved browser work. Passing backend tests does not complete the UX slices.
