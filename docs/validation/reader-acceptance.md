---
title: "Reader acceptance test coverage"
project_id: paperflow
doc_uuid: 0947b2c0-edd5-4ea2-9736-6eccf9b467a6
id: reader-acceptance-tests
tags: [acceptance]
implements: [reader-acceptance]
---

# Reader acceptance test coverage

See the [criteria](../plans/reader-acceptance.md). Run `scripts/check acceptance`.
The runner checks the table against the [coverage manifest](../../tests/acceptance/coverage.json),
resolves definitions with Docuchango, and requires every covered test to collect and pass
without skips or expected failures. `scripts/check code` runs the backend gate with the full unit suite;
`scripts/check acceptance` additionally executes and verifies the linked browser test.

<!-- acceptance-coverage:start -->
| Criterion | Requirement | Disposition | Test | Browser test | Evidence or gap |
| --- | --- | --- | --- | --- | --- |
| reader-acceptance::AC-1 | reader-acceptance::FR-1 | covered | tests/acceptance/test_journeys.py::test_browse_channels | — | FastAPI and on-disk SQLite journey. |
| reader-acceptance::AC-2 | reader-acceptance::FR-2 | covered | tests/acceptance/test_journeys.py::test_read_source_context | — | FastAPI and on-disk SQLite journey. |
| reader-acceptance::AC-3 | reader-acceptance::FR-3 | covered | tests/acceptance/test_journeys.py::test_collect_and_restart | — | FastAPI and on-disk SQLite journey. |
| reader-acceptance::AC-4 | reader-acceptance::FR-4 | covered | tests/acceptance/test_journeys.py::test_review_evidence | — | FastAPI and on-disk SQLite journey. |
| reader-acceptance::AC-5 | reader-acceptance::FR-5 | covered | tests/acceptance/test_reader_state.py::test_saved_position_preferences_and_old_revision_survive_restart | reader.spec.js::browse, save, read, return, and resume in a new browser context | SQLite restart and backup tests plus the real browser save and resume journey. |
| reader-acceptance::AC-6 | reader-acceptance::FR-6 | unresolved | — | — | Browser surface and automated accessibility checks exist; manual screen-reader and native zoom verification remain open. |
<!-- acceptance-coverage:end -->

Covered means an executable test exists; only a successful run establishes a passing result.
Unresolved criteria remain release gaps, not skipped tests. Run
`uv run --locked --group dev --extra test --extra telemetry python scripts/acceptance.py --require-complete`
to enforce all criteria; it intentionally fails until the browser criteria are implemented.
This baseline is necessary but does not replace the full release checklist.
