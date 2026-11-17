---
title: "Local reader MVP verification"
project_id: paperflow
doc_uuid: e2890969-c04e-4036-ad8c-bfd2507a9137
tags: [reader, validation]
---

# Local reader MVP verification

Implementation in progress against the [release slices](../plans/reader-release.md)
and [acceptance baseline](../plans/reader-acceptance.md). This is not a completed
release or an accessibility conformance claim.

## Implemented surface

- Today, Saved, Research, Sources and stable source/discussion URLs served by FastAPI.
- Finite channel/title-filtered feed, safe source text and explicit excerpt availability.
- Revision-aware saved reading, finish/unread, unsave/resave, and persisted typography/themes.
- Existing research buckets and brief links; full curator editing remains outside this MVP.
- Source freshness and monitor controls, with honest failure/empty states.

The SQLite schema advances additively from version 3 to 4. Reader state and
preferences are separate from immutable source/evidence records. Updates reject
stale conflicting versions and accept exact retries. The reader is a packaged
Python module with local static assets and a locked Tailwind build.

## Evidence collected

`tests/acceptance/test_reader_state.py` verifies restart persistence, changed-revision recovery,
invalid/cross-source anchor rejection, feed pagination under concurrent arrivals,
schema upgrade, SQLite backup restoration, and packaged route assets.

`tests/browser/reader.spec.js` exercises the real FastAPI service with disposable
SQLite data. The initial browser journeys pass in Chromium:

1. Browse, save, read, return with focus restoration, then resume in a new browser context.
2. Open settings by keyboard, persist them across reload, check Today/Saved/Research/Sources
   at 320px, and check source reflow with doubled base text size and spacing overrides.
   Axe reports no violations under its WCAG 2 A/AA, 2.1 AA and 2.2 AA rule tags.
3. Filter a channel, inspect an excerpt, and follow the separate discussion empty state.

These are automated keyboard and structural accessibility checks. Doubling base
text size is not a substitute for testing native browser zoom. No manual
screen-reader result has been recorded yet.

Additional browser checks now cover dismissal/undo, unsafe source markup, service
failure/retry, later-batch return through Discussion, and forced-colors/reduced-motion
keyboard focus. Changed-revision and conflicting-tab recovery are also tested in
the browser. Source pause/resume/fetch UI checks use intercepted monitor responses;
monitor execution itself is covered by the existing backend monitor tests.

The live database was also browsed at desktop and mobile sizes. A real Hacker News
item was opened, saved, inspected in Discussion (explicitly labeled as a sample),
and found in Saved. Its previous reader state was restored after the check.
Desktop and mobile screenshots were inspected under ignored `artifacts/reader/`.
The source shell was opened successfully in Safari as well as Chromium.
Native Safari zoom automation could not run because the browser tool lacks macOS
Accessibility permission; no native zoom result is claimed.

## Remaining verification and refinement

- Repeat the complete daily journey, including a service restart, with a screen reader.
- Verify native browser zoom manually; automated enlarged-text, forced-colors and reduced-motion checks pass.
- Manually test screen-reader navigation, announcements and focus restoration.
- Complete the manual accessibility criterion before marking the full acceptance baseline complete.

The acceptance coverage manifest now links saved/resume to backend and browser
execution. Accessibility remains unresolved pending manual verification. Use the [local reader guide](../guides/local-reader.md)
for startup and test commands.

## Completion review follow-up

A bookmarked source has no in-app list history. Its return action now opens
Today after Source/Discussion navigation, rather than jumping across unrelated
browser history. An executable browser regression covers this entry path.
Late feed-save responses also avoid rewriting the history state of a screen the
reader has already navigated to.

## Manual accessibility evidence to collect

The following remains a required human check, with the result currently **pending**.
Record operating system, browser/version, screen reader/version, date, and defects
alongside each outcome. Do not convert a pending result into a pass from axe results.

| Task | Expected outcome | Result |
| --- | --- | --- |
| Use skip navigation, then navigate Today, Saved and Research | Landmarks, headings and the current destination are understandable; focus is visible and follows the task | Pending |
| Open a source and switch to Discussion | Source availability and sampled/missing comments are announced clearly; source and discussion remain distinguishable | Pending |
| Change reading settings using the keyboard, then close with Escape | Every control has a useful name/value; focus returns to Reading settings; preferences survive reload | Pending |
| Save, read, return, restart browser/service, and resume through Saved | Save feedback is understandable; list context and reading position are retained | Pending |
| Repeat at native 200% browser zoom and narrow width | Ordinary prose and controls reflow without horizontal scrolling, clipping, or obscured focus | Pending |

Report any loss of focus, ambiguous control, unexpected announcement, or lost
position with the exact action that triggered it. A manual pass is evidence for
these journeys, not a blanket accessibility certification for unrelated workflows.

## Embedded views and design alignment

The current design follows Option D's compact header, bold serif item titles,
forest-green active controls, warm paper surface and divided feed rows. Introductory
copy has moved to About; the channel picker shows current-view counts and freshness.
Desktop and narrow-screen renders were inspected after these changes.

Additional tests cover local PDF validation, immutable/idempotent attachment and
restart persistence; PDF page rendering/navigation; Markdown lists and emphasis;
Mermaid rendering/error definitions; view-sensitive channel counts; and About
navigation. Schema v5 adds PDF bytes and metadata to the SQLite backup boundary.
Markdown and SVG are sanitized; Mermaid runs in strict mode with document-supplied
configuration disabled. Automated viewer checks do not close the existing manual
screen-reader/zoom acceptance gap.

## Local feedback review: 2026-11-15

The browser suite passes all 15 journeys, including embedded research briefs and
automatic-topic filtering, editorial renaming, sensitivity changes, and recuration.
The topic editor also passes the 320px overflow check and automated accessibility
scan. Its test now locates the sensitivity dropdown by accessible role and name;
an exact label-text lookup incorrectly included the dropdown option text.

A real research bucket opened in Safari with a rendered heading, four paragraphs,
and three lists, with no viewport overflow. Markdown download remains a separate
action. The local preview was restarted to expose the new topic API and research
brief route. Its automatic ingestion scheduler remains disabled for this review;
manual topic curation uses the real local database.

Live curation processed 40 incoming items. Review found generic headline words
forming weak themes. The baseline now excludes generic standalone words such as
Down and Story while retaining phrases; a regression test protects this behavior.
The [topic design](../designs/editorial-topics.md) documents the model, explicit
configuration defaults, editorial lifecycle, and keyword-baseline limitations.
This review does not close the pending manual screen-reader/native zoom checks.

## Topic CLI and larger batches

The local article-source caps are now 200 entries per run; the model permits up
to 1,000. RSS and Atom regression fixtures each persist 250 entries from a larger
feed and verify that retrying the same batch does not duplicate observations.
Upstream feeds can still return fewer entries than the configured cap.

A Terra subagent reviewed and exercised the progressive CLI with a real ephemeral
HTTP server and disposable SQLite storage. All five tests in
`tests/test_topic_cli_journey.py` pass: bounded briefs and synopsis, exact-title and
UUID-prefix lookup, include/exclude/reset with reader-feed agreement, rolling
expiry, and rejected ambiguous, nonexistent, or stale edits. Editorial summaries
remain separate from source excerpts and retain their full text in audit history.

On the live local database, a three-item Linux brief emits 1,765 bytes (441 tokens
estimated from character count). Lightweight name resolution reduced the same
command's aggregate response size from 11,137 to 2,436 bytes and database queries
from 82 to 9. These are measurements of this dataset, not fixed cost guarantees.

## Channel selection feedback

A held mouse click reproduced a WebKit failure: mouse-down blurred the focused
option without focusing the clicked button. The focus-out handler then hid the
menu before click could navigate, leaving the feed unchanged. Option mouse-down
now retains focus inside the list until selection commits. The regression checks
source switching, changed feed rows, keyboard return to Latest, and Escape focus
restoration in both Chromium and WebKit. CI installs both engines. This automated
WebKit coverage does not replace the pending manual accessibility review.
