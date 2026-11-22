---
title: Development, validation, and releases
project_id: paperflow
doc_uuid: 41aec3f9-e464-405f-943e-9a8ac32e3a56
tags: [development, releases]
---

# Development, validation, and releases

## One toolchain, locally and in CI

[.toolchain.env](../../.toolchain.env) pins uv and Python and selects the project
virtual environment. It is the single source read by direnv, bootstrap/check
scripts and GitHub Actions. Do not add independent version literals to workflows
or hooks. Install the listed uv version before starting; scripts reject a mismatch.
Python is provisioned by uv when needed. Install the Gitleaks version pinned in
`.toolchain.env` as well (for example with Homebrew). Bootstrap enables hooks for this
repository even if global Git configuration sets `core.hooksPath=/dev/null`; it
leaves the global setting untouched and refuses to overwrite a custom hook directory.

```bash
direnv allow
./scripts/bootstrap
./scripts/check
```

`.envrc` loads this file, watches `pyproject.toml` and `uv.lock`, and exposes
`.venv/bin`. It does not load device credentials, install dependencies or run
network operations on directory entry. Without direnv the scripts still load
the same pins. Re-run `direnv allow` after reviewing changes to `.envrc`.

Dependency groups and extras are declared in `pyproject.toml`; `uv.lock` locks
the resolved versions. Development tools are pinned exactly, including
Docuchango, Ruff, mypy, pre-commit, actionlint-py and Twine. The build backend is pinned
in `build-system.requires`. Change versions deliberately, run `uv lock`, and
validate the resulting lockfile and package. CI never repairs the lockfile.

## Shared checks

| Command | Local hooks / CI behavior |
| --- | --- |
| `scripts/check security` | Gitleaks credentials, personal home paths, private addresses, and personal email checks on publishable files |
| `scripts/check lint` | Ruff checks Python source and tests for unused names, common bugs, and Pylint errors |
| `scripts/check types` | Strict mypy checks the research, ingestion, monitor, story, topic, and assistant model modules |
| `scripts/check tests` | Lock consistency, the non-Docker test suite, and linked backend acceptance journeys |
| `scripts/check code` | Runs lint, types, then tests |
| `scripts/check docs` | Docuchango dry-run validation of config, frontmatter, names, links and index coverage |
| `scripts/check workflows` | actionlint syntax and expression checks; external shellcheck/pyflakes integrations disabled consistently |
| `scripts/check release` | VERSION, package version, lockfile and release manifest agreement; updater and module checks |
| `scripts/check` | Security, code, docs, reader, workflows, and release checks |
| `scripts/check integration` | Docker-backed tests, requires a working daemon and fails if tests skip |
| `scripts/package` | Build wheel/sdist, Twine metadata validation, isolated installed-wheel smoke test with exported locked constraints, SHA256SUMS |
| `scripts/release-stage vX.Y.Z` | All shared checks, matching-tag validation and package staging; no deployment |

All pre-commit hooks invoke the same `scripts/check` subcommands and inspect the
whole relevant tree. Run them explicitly with:

```bash
uv run --locked pre-commit run --all-files
```

For a short feedback loop, run `scripts/check lint` and `scripts/check types`
while editing. Both are read-only. The commit hooks and CI run each layer
independently, including when the change is to tool configuration.

To expand coverage, enable a Ruff rule in `[tool.ruff.lint]` or add a module to
`[tool.mypy].files` in `pyproject.toml`. Fix the findings and rerun that layer.
Mypy checks the minimum supported Python version and uses the Pydantic plugin
for model fields and constructors. Docker tests and package smoke tests also run
in CI and can be run locally with the commands above. CI uses GitHub-hosted Linux runners, not
host-specific deployment runners.

## Documentation layout

[The index](../README.md) lists every governed document. Plans, designs, guides,
reference, validation notes and periodicals use lowercase kebab-case filenames
and generic frontmatter: title, project_id, doc_uuid, tags. Keep UUIDs stable
across moves. Preserve explicit proposal/decision/implementation status in the
body; adding frontmatter must not imply approval.

Docuchango 1.20.0 is initialized from its bootstrap scaffold with a small
[docs-project.yaml](../docs-project.yaml) and its editor schema. Nested designs
are scanned, including the mock overview; image assets remain alongside them.
An index rule ensures new documents are linked from the documentation index.

```bash
# Report only; the same command runs in hooks and CI.
./scripts/check docs
# Apply Docuchango's atomic repairs, inspect changes, then validate again.
uv run --locked docuchango validate --skip-build
```

There is no Docusaurus application in this repository, so validation explicitly
skips a site build. Markdown structure, frontmatter and internal links are still
checked. This documentation refactor does not publish a documentation site.
Root README, AGENTS and CHANGELOG retain their repository roles and are
explicitly registered in the same Docuchango project. Both READMEs and CHANGELOG
use plain Markdown; AGENTS has validated frontmatter. Because Docuchango 1.20.0
skips landing-page bodies in its normal scanner, `scripts/docs_validate.py`
adds configured index files to the native validator and runs its checks on them.
It does not implement a second Markdown validator. The pinned scanner adapter
has regression coverage and must be checked when upgrading Docuchango.

The docs check also compares validated paths with all tracked and non-ignored
untracked Markdown, MDX and reStructuredText files. A new doc, folder, nested
README or unsupported markup format fails until it is governed; deleted and
Git-ignored runtime artifacts are excluded. Configured index paths may reach
root entrypoints but must remain within this repository. References to working artifacts are not portable
release evidence; validation receipts stay in git-ignored `artifacts/`.

## Release workflow

Releases use Conventional Commits, Release Please, shared validation, and separate
staging and publication steps for Python distribution artifacts.

1. Normal and release PRs run Validate. Use Conventional Commit titles such as
   `fix(reader): restore position` or `feat(ingestion): add a source`.
2. Changes on main run Release Please, maintaining a release PR with VERSION,
   pyproject.toml, the local-package entry in uv.lock, changelog and manifest.
3. Merging the release PR creates a `vX.Y.Z` tag and GitHub release.
4. Release checks out that exact tag, reruns validation and Docker integration,
   builds and smoke-tests artifacts, and passes immutable CI artifacts onward.
5. The publish job rechecks the tag's commit and attaches the wheel, sdist and
   runtime constraints and checksums to the existing GitHub release. Re-runs replace the same asset names.

The simple version strategy uses a shared version; TOML extra-file updaters keep the
Python package and lockfile synchronized. See the upstream
[Release Please configuration guide](https://github.com/googleapis/release-please/blob/main/docs/customizing.md).
The 0.1.0 manifest is a starting baseline, not evidence of an already published tag.

### Repository setup before enabling automated releases

- Enable Actions and permission for the release bot to create PRs.
- Configure `RELEASE_PLEASE_TOKEN` as a repository-scoped bot token with contents
  and pull-request write access, plus Actions workflow access if the provider
  requires it for PRs containing workflow changes. Keep the token in GitHub
  secrets, never in `.toolchain.env` or source files.
- Set the repository variable `RELEASE_AUTOMATION_ENABLED=true` after configuring
  the bot token and permissions. Push-triggered release automation stays disabled
  until then; manual dispatch validates the token explicitly.
- Require the aggregate **PR Status Check** through a GitHub ruleset. It fails
  unless both local checks/package and Docker integration succeed. The Mergify
  queue reruns these checks on speculative combinations before merging.
- A bot token is intentional: events created using the default GITHUB_TOKEN do
  not trigger the follow-on PR/tag workflows. See the
  [Release Please action documentation](https://github.com/googleapis/release-please-action).

Those remote settings are not established by this local configuration change.
Manual Release dispatch accepts an existing version tag, not an arbitrary branch
or SHA, and still requires a pre-existing GitHub release. A failed stage prevents
artifact attachment; the Release Please-created release entry may already exist.
Investigate and rerun a failed release rather than moving a published version tag.

GitHub assets are the initial publication target. PyPI upload and hosted-service
deployment are not configured. Hosting and browser identity still follow the
identity-free MVP as recorded in the [reader release plan](../plans/reader-release.md).

## Acceptance criteria and executable references

Docuchango 1.20.0 enables requirement traceability for the governed documentation
lane. Define stable requirement and acceptance IDs in the
[reader acceptance baseline](../plans/reader-acceptance.md); use qualified references
and `implements` frontmatter in the [coverage document](../validation/reader-acceptance.md).
Live references must remain outside inline code and fenced examples.

Run `scripts/check acceptance` for the focused FastAPI/SQLite journeys. The existing
`code` check runs the backend acceptance gate with the full unit suite. CI also
runs `scripts/check acceptance` with browser execution; `scripts/check browser`
is available locally for the same browser suite. Pre-commit checks frontend syntax
and reproducible CSS without requiring an installed browser. The JSON coverage
manifest and its Markdown table must agree. A covered test must collect and pass;
skips, expected failures, missing tests, and teardown failures fail the gate.
Unresolved browser criteria are reported explicitly. Use the coverage document's
`--require-complete` command when checking readiness of this acceptance baseline.

Reader development now uses the shared Node pin and locked npm dependencies. See
[Using the local reader](local-reader.md) for asset builds and browser checks.

## Publication hygiene

The shared checks scan tracked and non-ignored new files with pinned Gitleaks
and the repository rules in `.gitleaks.toml`. CI also scans reachable Git history.
Findings are redacted. Review commit author metadata separately before publishing;
content scanners do not establish that author emails or images are private-data free.
Use a GitHub noreply email for new commits. Keep local credentials, private keys,
databases, reports, and machine-specific overrides in ignored files. Never use
ignore rules as a substitute for removing secrets already committed to history.

## Speculative merge queue and automatic deployment

Paperflow follows the Prism merge-queue pattern: squash merges, an approving
review, successful CI, no requested changes, and no draft or blocking labels.
The repository owner's PRs receive Mergify approval after validation; other
contributors require a review. The `do-not-merge` and `work-in-progress` labels
hold a change out of the queue.

The root `.mergify.yml` uses a cumulative serial queue with two speculative
checks running concurrently, batches of up to five PRs, and at most one minute
of batch collection. Serial mode preserves merge ordering while checking later
combinations ahead of time. Explicit queue actions avoid depending on the
separate Merge Protections auto-merge product. See the
[Mergify queue modes documentation](https://docs.mergify.com/merge-queue/queue-modes/).

The **PR Status Check** job always runs and fails if either validation job fails,
is cancelled, or is skipped. CI also runs on Mergify's temporary draft PRs.
Keep validation free of path filters and draft exclusions that could leave queue
checks missing. Each pull request has its own concurrency group, so speculative
runs do not cancel one another.

Every merge to main starts the Deployable release workflow automatically. It
validates the exact commit again before publishing the immutable image bundle.
Non-release builds are limited to main; a build superseded by a newer main
commit does not publish. The existing nightly and version-release triggers
remain available. This deployment path does not require the optional Release
Please token or variable: versioned releases remain separately configured.

GitHub settings are separate from repository files. Install Mergify for this
repository, enable squash merges and deletion of merged branches, and restrict
main updates to Mergify through an active ruleset. Require **PR Status Check**
from the GitHub Actions app and an approving review. Mergify is the ruleset's
exempt integration actor and repeats the checks and review requirements in its
queue configuration. Block force pushes and deletion. Leave GitHub's native
merge queue and strict up-to-date checks disabled; Mergify owns speculative
validation. Never bypass a failed queue check to deploy a feature.

Host-side consumers verify bundle checksums, preflight the image, check running
identity/readiness, and roll back failed activation. Static publication uses an
atomic switch after build and digest verification. These controls and host
credentials remain in the external deployment repository.

### Auditing repository settings

The desired main ruleset is versioned in `.github/main-ruleset.json`. Compare it
with `gh api repos/jrepp/paperflow/rulesets`, then inspect the returned ruleset ID.
After reviewing changes, apply that file with the GitHub rulesets PUT endpoint.
The Mergify and GitHub Actions integration IDs in it identify the specific apps;
queue check conditions likewise name `@github-actions` to prevent a similarly
named check from another integration satisfying the gate.

Verify that Mergify has repository access and that both Merge Queue and Workflow
Automation are enabled in its dashboard. GitHub Actions should remain enabled,
require SHA-pinned actions, and use read-only default workflow permissions.
GitHub's native auto-merge is disabled: Mergify is responsible for merging.
The merge-queue configuration's source of truth is `.mergify.yml`; do not edit
queue settings independently in the dashboard without reconciling the file.

To hold a ready PR, add `do-not-merge`; remove it to let the queue reconsider it.
For failed speculative CI, fix the underlying failure and push the correction
instead of bypassing the rule. If deployment fails after merge, preserve the
host's rollback state and inspect its health and identity checks before retrying.
