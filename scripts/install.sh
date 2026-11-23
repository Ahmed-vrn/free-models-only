#!/usr/bin/env bash
# Install / refresh the free-models-only skill and apply the managed block.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${OPENCODE_CONFIG:-$HOME/.config/opencode/opencode.jsonc}"
CONFIG_EXPLICIT=false
SCOPE="global"
DRY_RUN=false
OFFLINE=false
DISCOVERY="${HOME}/.cache/free-models-only/discovery.json"
EXTRA_APPLY_ARGS=()
EXTRA_DISCOVER_ARGS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --no-discovery|--offline) OFFLINE=true ;;
    --config) CONFIG="$2"; CONFIG_EXPLICIT=true; shift ;;
    --scope) SCOPE="$2"; shift ;;
    --include-unconfigured-providers) EXTRA_APPLY_ARGS+=("--include-unconfigured-providers") ;;
    --disable-quota-free) EXTRA_APPLY_ARGS+=("--disable-quota-free") ;;
    --require-zero-retention) EXTRA_APPLY_ARGS+=("--require-zero-retention") ;;
    --allow-lan-local) EXTRA_DISCOVER_ARGS+=("--allow-lan-local") ;;
    --no-runtime) EXTRA_DISCOVER_ARGS+=("--no-runtime") ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

# Resolve the project config without touching a global file by mistake.
if [ "$SCOPE" = "project" ] && [ "$CONFIG_EXPLICIT" = false ]; then
  found=""
  search="$PWD"
  while :; do
    for relative in .opencode/opencode.jsonc .opencode/opencode.json opencode.jsonc opencode.json; do
      if [ -f "$search/$relative" ]; then
        found="$search/$relative"
        break 2
      fi
    done
    [ "$search" = "/" ] && break
    search="$(dirname "$search")"
  done
  if [ -z "$found" ]; then
    echo "error: no project config found (.opencode/opencode.jsonc or .opencode/opencode.json)" >&2
    exit 2
  fi
  CONFIG="$found"
fi

mkdir -p "$(dirname "$CONFIG")" "$(dirname "$DISCOVERY")"

# Add managed markers only if neither the v3 nor the legacy v2 style exists.
if ! grep -qF 'BEGIN free-models-only' "$CONFIG" 2>/dev/null &&
   ! grep -qF 'free-models-only: START' "$CONFIG" 2>/dev/null; then
  python3 - "$CONFIG" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8") if path.exists() else '{\n  "$schema": "https://opencode.ai/config.json"\n}\n'
if '"providers"' in text or '"provider"' in text:
    raise SystemExit(
        "error: this config already has a providers/provider key but no free-models-only markers.\n"
        "Wrap that block with:\n"
        "  // BEGIN free-models-only\n  ...\n  // END free-models-only\n"
        "then rerun install.sh."
    )
marker = (
    "\n  // BEGIN free-models-only\n"
    "  // END free-models-only\n"
)
index = text.rstrip().rfind("}")
text = text[:index].rstrip() + marker + text[index:]
path.write_text(text, encoding="utf-8")
PY
fi

APPLY_ARGS=(--config "$CONFIG")
if [ "$DRY_RUN" = true ]; then
  APPLY_ARGS+=(--dry-run)
else
  APPLY_ARGS+=(--apply)
fi
if [ ${#EXTRA_APPLY_ARGS[@]} -gt 0 ]; then
  APPLY_ARGS+=("${EXTRA_APPLY_ARGS[@]}")
fi

apply_fallback() {
  echo "==> Applying curated offline fallback list" >&2
  python3 "$SKILL_DIR/scripts/apply.py" "${APPLY_ARGS[@]}" \
    --paid-file "$SKILL_DIR/references/paid-model-ids.txt"
}

if [ "$OFFLINE" = true ]; then
  echo "==> Offline mode: reusing cached discovery + fallback list"
  if python3 "$SKILL_DIR/scripts/discover.py" --offline --output "$DISCOVERY" --config "$CONFIG" \
      "${EXTRA_DISCOVER_ARGS[@]}"; then
    python3 "$SKILL_DIR/scripts/apply.py" "${APPLY_ARGS[@]}" --discovery "$DISCOVERY"
  else
    apply_fallback
  fi
else
  echo "==> Discovering models from official catalogs (zero-trust)"
  if python3 "$SKILL_DIR/scripts/discover.py" --output "$DISCOVERY" --config "$CONFIG" \
      "${EXTRA_DISCOVER_ARGS[@]}"; then
    python3 "$SKILL_DIR/scripts/apply.py" "${APPLY_ARGS[@]}" --discovery "$DISCOVERY"
  else
    echo "warning: discovery failed; falling back to the curated paid list" >&2
    apply_fallback
  fi
fi

echo
echo "Skill:  $SKILL_DIR"
echo "Config: $CONFIG"
echo "Cache:  $DISCOVERY"
if [ "$DRY_RUN" = true ]; then
  echo "Dry-run only. Re-run without --dry-run to write the config."
else
  echo "OpenCode reloads the config automatically; new sessions use free models only."
fi
