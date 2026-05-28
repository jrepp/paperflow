#!/usr/bin/env bash
set -euo pipefail

cd /path/to/paperflow
export PATH="/path/to/paperflow/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

./arxiv-radar refresh --update-only --config arxiv-radar.yaml
