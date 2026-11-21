#!/usr/bin/env bash
# Remove the managed block from the OpenCode config (keeps the skill directory).
set -euo pipefail

CONFIG="${OPENCODE_CONFIG:-$HOME/.config/opencode/opencode.jsonc}"
[ -f "$CONFIG" ] || { echo "no config at $CONFIG"; exit 0; }

python3 - "$CONFIG" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")
START = "  // >>> free-models-only: START (managed — do not edit manually)\n"
END = "  // <<< free-models-only: END\n"
a, b = s.find(START), s.find(END)
if a < 0 or b < 0:
    print("managed block not found; nothing to remove")
else:
    p.write_text(s[:a] + s[b + len(END):], encoding="utf-8")
    print("managed block removed from", p)
PY
