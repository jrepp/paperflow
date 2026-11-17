# Paperflow

A local research reader, ingestion service, curation workflow, publishing pipeline,
and BOOX device CLI. The selected reader direction is Today → Saved → Research;
the first web MVP runs without identity.

- [Agent instructions](AGENTS.md)
- [Release changelog](CHANGELOG.md)
- [Documentation index](docs/README.md)
- [Reader release plan](docs/plans/reader-release.md)
- [Selected reader design](docs/designs/core-reader.md)
- [CLI and service runbook](docs/guides/runbook.md)
- [Development, hooks, CI and releases](docs/guides/development.md)

## Development

The shared tool versions live in `.toolchain.env`. Install the listed uv version,
then run:

```bash
direnv allow
./scripts/bootstrap
./scripts/check
```

Direnv loads the pinned versions and adds `.venv/bin` to PATH. Bootstrap installs
locked dependencies and local pre-commit hooks. Neither command configures an
identity provider or deploys the application.
