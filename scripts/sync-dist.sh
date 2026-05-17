#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "→ Building controller…"
(cd "$REPO_ROOT/controller" && npm run build)

echo "→ Syncing dist → td/unicorner_controller_dist/"
rsync -a --delete "$REPO_ROOT/controller/dist/" "$REPO_ROOT/td/unicorner_controller_dist/"

echo "✓ Done. In TouchDesigner, pulse the 'Refreshurl' parameter on unicorner_controller to reload."
