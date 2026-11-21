#!/usr/bin/env bash
# Install / refresh the free-models-only skill and re-apply the config block.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${OPENCODE_CONFIG:-$HOME/.config/opencode/opencode.jsonc}"

mkdir -p "$(dirname "$CONFIG")"

# Add the managed markers if this config predates the skill.
if ! grep -qF '>>> free-models-only: START' "$CONFIG" 2>/dev/null; then
  python3 - "$CONFIG" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8") if p.exists() else '{\n  "$schema": "https://opencode.ai/config.json"\n}\n'
marker = "\n  // >>> free-models-only: START (managed — do not edit manually)\n  // <<< free-models-only: END\n"
# Insert before the final closing brace of the root object.
i = s.rstrip().rfind('}')
s = s[:i].rstrip() + marker + s[i:]
p.write_text(s, encoding="utf-8")
PY
fi

python3 "$SKILL_DIR/scripts/apply.py" \
  --config "$CONFIG" \
  --paid-file "$SKILL_DIR/references/paid-model-ids.txt"

echo "Skill installed at: $SKILL_DIR"
echo "Config updated:     $CONFIG"
echo "OpenCode reloads the config automatically; new sessions use free models only."
