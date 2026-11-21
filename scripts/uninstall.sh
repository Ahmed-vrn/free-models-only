#!/usr/bin/env bash
# Remove the managed block from the OpenCode config (keeps the skill directory).
set -euo pipefail

CONFIG="${OPENCODE_CONFIG:-$HOME/.config/opencode/opencode.jsonc}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --config) CONFIG="$2"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

[ -f "$CONFIG" ] || { echo "no config at $CONFIG"; exit 0; }

python3 - "$CONFIG" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
start = "  // >>> free-models-only: START (managed — do not edit manually)\n"
end = "  // <<< free-models-only: END\n"
first = text.find(start)
last = text.find(end)
if first < 0 or last < 0 or last <= first:
    print("managed block not found; nothing to remove")
    raise SystemExit(0)

backup = path.with_name(path.name + ".bak.uninstall")
backup.write_text(text, encoding="utf-8")
path.write_text(text[:first] + text[last + len(end):], encoding="utf-8")
print("managed block removed from %s (backup: %s)" % (path, backup))
PY
