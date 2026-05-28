#!/usr/bin/env bash
set -euo pipefail

cd /Users/jrepp/dev/boox-org
export PATH="/Users/jrepp/dev/boox-org/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

./arxiv-radar refresh --update-only --config arxiv-radar.yaml
