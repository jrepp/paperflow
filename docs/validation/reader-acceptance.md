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
| reader-acceptance::AC-7 | reader-acceptance::FR-7 | covered | tests/test_assistant.py::test_monitor_runs_curator_and_keeps_ingestion_pool_separate | — | Monitor lifecycle with an injected offline model runner and a dedicated worker pool. |
| reader-acceptance::AC-8 | reader-acceptance::FR-8 | covered | tests/test_assistant.py::test_memory_replay_versions_search_and_exclusion | — | Versioned private storage, conflict handling, retrieval, and public API exclusion. |
| reader-acceptance::AC-9 | reader-acceptance::FR-9 | covered | tests/test_assistant.py::test_request_ledger_preserves_failures_retries_and_spend | — | Mixed request outcomes, retry costs, unknown usage, and incomplete projections. |
| reader-acceptance::AC-10 | reader-acceptance::FR-10 | covered | tests/test_assistant.py::test_quality_intervals_and_empty_samples | curation-monitor.spec.js::curation monitor distinguishes volume from sampled estimated quality | Empty estimates and small-sample intervals; browser checks model-estimate labels and sample coverage. |
| reader-acceptance::AC-11 | reader-acceptance::FR-11 | covered | tests/test_assistant.py::test_bad_citations_and_evidence_basis_do_not_complete_jobs | — | Fabricated quotes and incorrect evidence basis cannot complete a job. |
| reader-acceptance::AC-12 | reader-acceptance::FR-12 | covered | tests/test_story_worker.py::test_worker_routes_repeat_without_curation | — | Two observed sources share one story and a repeat does not run curation. |
| reader-acceptance::AC-13 | reader-acceptance::FR-13 | covered | tests/test_story_worker.py::test_material_updates_coalesce_and_include_prior_evidence | — | Worker combines pending contributions with both source revisions. |
| reader-acceptance::AC-14 | reader-acceptance::FR-14 | covered | tests/test_publish_site.py::test_story_eligibility_window_privacy_counts_and_links | — | SQLite publication checks source eligibility, privacy, counts, limits and stable links. |
| reader-acceptance::AC-15 | reader-acceptance::FR-15 | covered | tests/test_reader_ratings.py::test_gateway_ratings_are_versioned_idempotent_and_pseudonymous | published-site.spec.js::anonymous rating is retryable, deduplicates concurrent clicks, and supports changes | Gateway-authenticated SQLite writes, stable pseudonyms, keyboard feedback, retry and update. |
<!-- acceptance-coverage:end -->

Covered means an executable test exists; only a successful run establishes a passing result.
Unresolved criteria remain release gaps, not skipped tests. Run
`uv run --locked --group dev --extra test --extra telemetry python scripts/acceptance.py --require-complete`
to enforce all criteria; it intentionally fails until the browser criteria are implemented.
This baseline is necessary but does not replace the full release checklist.
