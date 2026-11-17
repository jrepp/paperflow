#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
export PATH="$root/.venv/bin:$PATH"

./arxiv-radar refresh --update-only --config arxiv-radar.yaml
