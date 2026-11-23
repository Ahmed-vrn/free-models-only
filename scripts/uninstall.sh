#!/usr/bin/env bash
# Remove the managed block from the OpenCode config (keeps the skill directory
# and the tombstone state so a later reinstall does not resurrect paid models).
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
styles = (
    ("  // BEGIN free-models-only\n", "  // END free-models-only\n"),
    ("  // >>> free-models-only: START (managed — do not edit manually)\n",
     "  // <<< free-models-only: END\n"),
)

def span(token):
    idx = text.find(token)
    if idx < 0:
        return None
    line_start = text.rfind("\n", 0, idx) + 1
    newline = text.find("\n", idx)
    line_end = len(text) if newline < 0 else newline + 1
    return line_start, line_end

found = []
for begin, end in styles:
    b, e = span(begin), span(end)
    if b and e and b[1] <= e[0]:
        found.append((b[0], e[1]))
if len(found) != 1:
    print("managed block not found or ambiguous; nothing to remove")
    raise SystemExit(0)

backup = path.with_name(path.name + ".bak.uninstall")
backup.write_text(text, encoding="utf-8")
start, end = found[0]
path.write_text(text[:start] + text[end:], encoding="utf-8")
print("managed block removed from %s (backup: %s)" % (path, backup))
PY

echo "Tombstone state kept at: ${HOME}/.cache/free-models-only/state.json"
