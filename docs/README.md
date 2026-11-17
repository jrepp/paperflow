# Paperflow documentation

Start with the [reader release plan](plans/reader-release.md), the selected
[Option D design](designs/core-reader.md), or the [runbook](guides/runbook.md).
The local MVP has no identity dependency; browser identity follows it.

Configuration: [docs-project.yaml](docs-project.yaml). Validate with `scripts/check docs`
from the repository root. See the development guide for hooks, CI and release setup.

## Plans

- [New Paper Sources and Radar Refresh Plan](plans/new-paper-sources.md)
- [Research Radar Periodical Roadmap](plans/periodical-roadmap.md)
- [Paperflow reader: selected direction and release slices](plans/reader-release.md)

- [Reader acceptance criteria](plans/reader-acceptance.md)

## Designs

- [Editorial topics above ingestion sources](designs/editorial-topics.md)

- [Composable source integrations](designs/composable-ingestion.md)
- [Paperflow: Core Reader Design](designs/core-reader.md)
- [Ingestion and Research Curation Model](designs/ingestion-curation-model.md)
- [Paperflow reader concepts, v1](designs/reader-v1/concepts.md)

## Guides

- [API Quick Start](guides/api-quickstart.md)
- [Curator workflow: domain operations, API, and CLI](guides/curator-workflow.md)
- [Development, validation, and releases](guides/development.md)
- [FastAPI integration monitor](guides/integration-monitor.md)
- [Runbook](guides/runbook.md)

- [Using the local reader](guides/local-reader.md)

## Reference

- [Organization Taxonomy Strategy](reference/organization-taxonomy.md)

## Validation

- [Reddit ingestion validation](validation/reddit-ingestion.md)

- [Reader acceptance test coverage](validation/reader-acceptance.md)

- [Local reader MVP verification](validation/reader-mvp.md)

## Periodicals

- [Research Radar Issue 1 Draft](periodicals/issue-001-house-style-draft.md)

## Repository entrypoints

These files are part of this Docuchango project too. README landing pages and
the release changelog retain plain Markdown; agent instructions have frontmatter.
Their bodies and links are included by the pinned validation wrapper.

- [Repository overview](../README.md)
- [Agent instructions](../AGENTS.md)
- [Release changelog](../CHANGELOG.md)
- [Deployment guide](../deploy/README.md)

`./scripts/check docs` fails if any tracked or non-ignored new source document
is not covered. Keep generated validation receipts under ignored `artifacts/`.
