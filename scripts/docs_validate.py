"""Close Docuchango 1.20.0's landing-page scan gaps and enforce repo coverage.

Use the pinned validator rather than reimplementing Markdown/link rules. Index
paths in docs-project.yaml are the explicit opt-in for conventional root files.
The small adapter is regression-tested because its scanner hook is internal.
"""
from pathlib import Path
import subprocess
import sys

from docuchango.validator import DocValidator


class RepositoryDocValidator(DocValidator):
    def scan_documents(self):
        super().scan_documents()
        for context in self.project_configs:
            for index in context.config.indexes:
                path = (context.base_dir / index.path).resolve()
                if not path.is_relative_to(self.repo_root):
                    self.errors.append(f"Index leaves repository: {index.path}")
                    continue
                if path in self.file_to_doc or not path.is_file():
                    continue  # Native index validation reports missing files.
                # Changelogs and landing pages use their native, tool-owned format.
                document = self._parse_document(
                    path, "generic", require_frontmatter=path.name == "AGENTS.md"
                )
                if document:
                    self.documents.append(document)
                    self.file_to_doc[path] = document


def repository_documents(root: Path) -> set[Path]:
    """Include new docs before staging; omit only Git-ignored runtime material."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, check=True, capture_output=True,
    )
    return {
        (root / name.decode()).resolve()
        for name in result.stdout.split(b"\0") if name
        if Path(name.decode()).suffix.lower() in {".md", ".mdx", ".rst"}
        and (root / name.decode()).is_file()
    }


def uncovered_documents(validator: RepositoryDocValidator) -> set[Path]:
    return repository_documents(validator.repo_root) - set(validator.file_to_doc)


def main():
    root = Path(__file__).resolve().parents[1]
    validator = RepositoryDocValidator(root)
    passed = validator.validate(skip_build=True)
    missing = uncovered_documents(validator)
    for path in sorted(missing):
        print(f"Uncovered documentation: {path.relative_to(root)}", file=sys.stderr)
    if missing:
        print("Add a Docuchango lane or an explicit index; do not silently exclude source docs.", file=sys.stderr)
    print(f"Documentation coverage: {len(repository_documents(root))} files; {len(missing)} uncovered")
    return 0 if passed and not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
