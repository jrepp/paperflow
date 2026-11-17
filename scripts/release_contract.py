"""Check the portable release contract without writing files or contacting GitHub."""
import json
import re
import sys
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = (root / "VERSION").read_text().strip()
if not re.fullmatch(r"\d+\.\d+\.\d+", version):
    raise SystemExit("VERSION must be a stable X.Y.Z release version")
project = tomllib.loads((root / "pyproject.toml").read_text())
lock = tomllib.loads((root / "uv.lock").read_text())
manifest = json.loads((root / ".release-please-manifest.json").read_text())
locked = next(p["version"] for p in lock["package"] if p["name"] == "paperflow")
if not (project["project"]["version"] == manifest["."] == locked == version):
    raise SystemExit("VERSION, pyproject.toml, uv.lock and release manifest must agree")
config = json.loads((root / "release-please-config.json").read_text())["packages"]["."]
if config["version-file"] != "VERSION" or config["release-type"] != "simple":
    raise SystemExit("Release Please must maintain the simple VERSION contract")
for path, selector in (("pyproject.toml", "$.project.version"),
                       ("uv.lock", "$.package[?(@.name == 'paperflow')].version")):
    if {"type": "toml", "path": path, "jsonpath": selector} not in config["extra-files"]:
        raise SystemExit(f"Missing release updater for {path}")
for module in project["tool"]["setuptools"]["py-modules"]:
    if not (root / f"{module}.py").is_file():
        raise SystemExit(f"Packaged module is missing: {module}")
if len(sys.argv) > 1 and sys.argv[1] != f"v{version}":
    raise SystemExit(f"Release tag must be v{version}")
print(f"Release contract valid: {version}")
