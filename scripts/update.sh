#!/usr/bin/env bash
# Pull the latest version of the skill and re-apply it.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Updating free-models-only..."
git -C "$SKILL_DIR" pull --ff-only
bash "$SKILL_DIR/scripts/install.sh"
echo "Update complete."
