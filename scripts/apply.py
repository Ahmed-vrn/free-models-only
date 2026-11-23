#!/usr/bin/env python3
"""Free Models Only v3.0 - safe atomic JSONC enforcer.

Owns only the block between the ``free-models-only`` markers. Everything else
in the config is preserved byte-for-byte, including comments. New in v3:

* protects ``model``, ``small_model``, ``default_model`` and ``agent.*.model``;
* keeps tombstones in ``~/.cache/free-models-only/state.json`` so a model
  hidden by the skill never reappears just because it vanished from runtime;
* honours ``--require-zero-retention``;
* accepts both v3 (``// BEGIN ...``) and legacy v2 markers;
* dry-run by default; timestamped backup + fsync + atomic replace on write.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

MARKER_STYLES: Tuple[Tuple[str, str], ...] = (
    ("// BEGIN free-models-only", "// END free-models-only"),
    ("// >>> free-models-only: START (managed — do not edit manually)", "// <<< free-models-only: END"),
)
DEFAULT_CONFIG = Path.home() / ".config" / "opencode" / "opencode.jsonc"
DEFAULT_STATE = Path.home() / ".cache" / "free-models-only" / "state.json"
ALLOWED_CLASSIFICATIONS = {"verified_zero", "quota_free", "local"}
KNOWN_CLASSIFICATIONS = {
    "verified_zero", "quota_free", "funded_account_required", "local", "paid", "unknown"
}


# ---------------------------------------------------------------------------
# JSONC helpers (string-aware, standard library only)
# ---------------------------------------------------------------------------
def strip_comments(text: str) -> str:
    out: List[str] = []
    i, n = 0, len(text)
    in_string, quote = False, ""
    while i < n:
        char = text[i]
        if in_string:
            out.append(char)
            if char == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if char == quote:
                in_string = False
            i += 1
            continue
        if char in ('"', "'"):
            in_string, quote = True, char
            out.append(char)
            i += 1
            continue
        if char == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if char == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def remove_trailing_commas(text: str) -> str:
    out: List[str] = []
    i, n = 0, len(text)
    in_string, quote = False, ""
    while i < n:
        char = text[i]
        if in_string:
            out.append(char)
            if char == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if char == quote:
                in_string = False
            i += 1
            continue
        if char in ('"', "'"):
            in_string, quote = True, char
            out.append(char)
            i += 1
            continue
        if char == ",":
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(char)
        i += 1
    return "".join(out)


def parse_jsonc(text: str) -> Dict[str, Any]:
    cleaned = remove_trailing_commas(strip_comments(text)).strip()
    if not cleaned:
        return {}
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("JSONC parse error: %s" % exc) from exc
    if not isinstance(value, dict):
        raise ValueError("JSONC root must be an object")
    return value


# ---------------------------------------------------------------------------
# Markers / config paths
# ---------------------------------------------------------------------------
def marker_span(raw: str, token: str) -> Optional[Tuple[int, int, str]]:
    """Return (line start, line end including newline, indentation)."""
    index = raw.find(token)
    if index < 0:
        return None
    line_start = raw.rfind("\n", 0, index) + 1
    newline = raw.find("\n", index)
    line_end = len(raw) if newline < 0 else newline + 1
    indent = raw[line_start:index]
    return line_start, line_end, indent


def find_managed_block(raw: str) -> Tuple[int, int, str, str, str]:
    matches = []
    for begin, end in MARKER_STYLES:
        b, e = marker_span(raw, begin), marker_span(raw, end)
        if b is None and e is None:
            continue
        if b is None or e is None:
            raise ValueError("only one of the managed markers was found")
        if b[1] > e[0]:
            raise ValueError("managed block end appears before its start")
        matches.append((b[0], e[1], b[2], begin, end))
    if len(matches) != 1:
        raise ValueError("expected exactly one managed block in the config")
    return matches[0]


def resolve_config(config: Optional[Path], scope: str) -> Path:
    if config is not None:
        return config.expanduser()
    if scope == "project":
        current = Path.cwd().resolve()
        for base in (current, *current.parents):
            for relative in (".opencode/opencode.jsonc", ".opencode/opencode.json",
                             "opencode.jsonc", "opencode.json"):
                candidate = base / relative
                if candidate.is_file():
                    return candidate
            if (base / ".git").exists():
                break
        raise ValueError("no project config found in current or parent directories")
    return DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def load_discovery(path: Path) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], Dict[str, Any]]:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("discovery root must be an object")
    version = str(payload.get("schema_version", ""))
    if version not in ("1", "3", "3.0"):
        raise ValueError("unsupported discovery schema_version: %r" % version)
    if not isinstance(payload.get("models"), list):
        raise ValueError("discovery file has no models array")

    result: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for model in payload["models"]:
        if not isinstance(model, dict):
            continue
        provider = str(model.get("provider_id") or "").strip()
        model_id = str(model.get("model_id") or "").strip()
        if not provider or not model_id:
            continue
        entry = dict(model)
        classification = str(entry.get("classification") or "unknown")
        entry["classification"] = classification if classification in KNOWN_CLASSIFICATIONS else "unknown"
        result[(provider, model_id)] = entry
    return result, payload


def legacy_discovery(ids: Iterable[str]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    result: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for token in ids:
        token = token.strip()
        if not token:
            continue
        if "/" in token:
            provider, model_id = token.split("/", 1)
        else:
            provider, model_id = "opencode", token
        result[(provider, model_id)] = {
            "provider_id": provider,
            "model_id": model_id,
            "classification": "paid",
            "source": "fallback_list",
        }
    return result


def read_paid_file(path: Path) -> List[str]:
    ids: List[str] = []
    for line in path.expanduser().read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ids.extend(part for part in re.split(r"[\s,]+", line) if part)
    return ids


def load_state(path: Path) -> Set[Tuple[str, str]]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    result: Set[Tuple[str, str]] = set()
    entries = data.get("disabled_models", []) if isinstance(data, dict) else []
    for item in entries:
        if isinstance(item, dict):
            provider, model_id = item.get("provider_id"), item.get("model_id")
            if provider and model_id:
                result.add((str(provider), str(model_id)))
        elif isinstance(item, str) and "/" in item:
            provider, model_id = item.split("/", 1)
            result.add((provider.strip(), model_id.strip()))
    return result


def save_state(path: Path, disabled: Set[Tuple[str, str]]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "disabled_models": [
            {"provider_id": p, "model_id": m} for p, m in sorted(disabled)
        ],
    }
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def disabled_in_managed_block(raw: str, block_start: int, block_end: int) -> Set[Tuple[str, str]]:
    """Read disabled ids that the previous run wrote inside the managed block."""
    body = raw[block_start:block_end]
    try:
        parsed = parse_jsonc("{\n" + body + "\n}")
    except ValueError:
        return set()
    providers = parsed.get("providers") or parsed.get("provider") or {}
    found: Set[Tuple[str, str]] = set()
    if not isinstance(providers, dict):
        return found
    for provider, conf in providers.items():
        if not isinstance(conf, dict):
            continue
        models = conf.get("models") or {}
        if not isinstance(models, dict):
            continue
        for model_id, model_conf in models.items():
            if isinstance(model_conf, dict) and model_conf.get("disabled") is True:
                found.add((str(provider), str(model_id)))
    return found


def collect_protected_defaults(config: Dict[str, Any]) -> Dict[str, str]:
    """Collect every active model selector across the whole config."""
    protected: Dict[str, str] = {}
    for key in ("model", "small_model", "default_model"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            protected[key] = value.strip()
        elif isinstance(value, dict) and isinstance(value.get("model"), str):
            protected[key] = value["model"].strip()

    for parent in ("agent", "agents"):
        agents = config.get(parent)
        if not isinstance(agents, dict):
            continue
        for name, conf in agents.items():
            if isinstance(conf, str) and conf.strip():
                protected["%s.%s" % (parent, name)] = conf.strip()
            elif isinstance(conf, dict):
                model = conf.get("model")
                if isinstance(model, str) and model.strip():
                    protected["%s.%s.model" % (parent, name)] = model.strip()
                elif isinstance(model, dict) and isinstance(model.get("model"), str):
                    protected["%s.%s.model" % (parent, name)] = model["model"].strip()
    return protected


def ref_to_key(ref: str) -> Optional[Tuple[str, str]]:
    if "/" not in ref:
        return None
    provider, model_id = ref.split("/", 1)
    return provider.strip(), model_id.strip()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_block(disabled: Dict[str, List[str]], indent: str, begin: str, end: str) -> str:
    lines: List[str] = [
        indent + begin,
        indent + "//  مُدار بواسطة free-models-only v3.0 — لا تُعدّل يدوياً",
        indent + "//  السياسة: مجاني مُثبت فقط؛ المصدر discovery.json + state.json",
    ]
    if not disabled:
        lines.append(indent + '"providers": {},')
    else:
        lines.append(indent + '"providers": {')
        providers = sorted(disabled)
        for p_index, provider in enumerate(providers):
            lines.append(indent + "  " + json.dumps(provider, ensure_ascii=False) + ": {")
            lines.append(indent + '    "models": {')
            model_ids = sorted(set(disabled[provider]))
            for m_index, model_id in enumerate(model_ids):
                comma = "," if m_index < len(model_ids) - 1 else ""
                lines.append(
                    indent + "      " + json.dumps(model_id, ensure_ascii=False)
                    + ': { "disabled": true }' + comma
                )
            lines.append(indent + "    }")
            lines.append(indent + "  }" + ("," if p_index < len(providers) - 1 else ""))
        lines.append(indent + "},")
    lines.append(indent + end)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Apply free-model rules to opencode.jsonc")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--discovery", type=Path, help="discovery.json from discover.py")
    source.add_argument("--paid-file", type=Path, help="legacy fallback list (offline)")
    parser.add_argument("--config", type=Path, help="explicit config path")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="tombstone state path")
    parser.add_argument("--scope", choices=("global", "project"), default="global")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="explicit dry-run")
    parser.add_argument("--disable-quota-free", action="store_true")
    parser.add_argument("--require-zero-retention", action="store_true")
    parser.add_argument("--include-unconfigured-providers", action="store_true")
    parser.add_argument("--allow-symlink", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        print("error: --apply and --dry-run are mutually exclusive", file=sys.stderr)
        return 2
    dry_run = not args.apply

    try:
        config_path = resolve_config(args.config, args.scope)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    if not config_path.exists():
        print("error: config not found: %s" % config_path, file=sys.stderr)
        return 2
    if config_path.is_symlink() and not args.allow_symlink:
        print("error: config is a symlink; pass --allow-symlink if intentional", file=sys.stderr)
        return 2

    raw = config_path.read_text(encoding="utf-8")
    try:
        block_start, block_end, indent, begin_marker, end_marker = find_managed_block(raw)
        old_config = parse_jsonc(raw)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 3

    try:
        if args.discovery:
            discovered, discovery_doc = load_discovery(args.discovery)
            source_label = str(args.discovery)
        else:
            discovered = legacy_discovery(read_paid_file(args.paid_file))
            discovery_doc = {"schema_version": "1", "mode": "fallback-list"}
            source_label = str(args.paid_file)
    except (OSError, ValueError) as exc:
        print("error: cannot read discovery source: %s" % exc, file=sys.stderr)
        return 2

    allowed_classes = set(ALLOWED_CLASSIFICATIONS)
    if args.disable_quota_free:
        allowed_classes.discard("quota_free")

    allowed_keys: Set[Tuple[str, str]] = set()
    for key, entry in discovered.items():
        if entry.get("classification") not in allowed_classes:
            continue
        if args.require_zero_retention and entry.get("privacy_retention"):
            continue
        allowed_keys.add(key)

    state_path = args.state.expanduser()
    tombstones = load_state(state_path) | disabled_in_managed_block(raw, block_start, block_end)
    to_disable: Set[Tuple[str, str]] = set()
    for key in discovered:
        if key not in allowed_keys:
            to_disable.add(key)
    for key in tombstones:
        if key not in allowed_keys:
            to_disable.add(key)

    providers_conf = old_config.get("providers") or old_config.get("provider") or {}
    configured = {str(p) for p in providers_conf} if isinstance(providers_conf, dict) else set()
    skipped: Set[str] = set()
    disabled: Dict[str, List[str]] = {}
    for provider, model_id in sorted(to_disable):
        if provider != "opencode" and provider not in configured and not args.include_unconfigured_providers:
            skipped.add(provider)
            continue
        disabled.setdefault(provider, []).append(model_id)

    warnings: List[str] = []
    if skipped:
        names = sorted(skipped)
        preview = ", ".join(names[:10]) + ("..." if len(names) > 10 else "")
        warnings.append("unconfigured providers skipped (%d): %s" % (len(names), preview))

    # Protect every model selector, not just the primary one.
    protected = collect_protected_defaults(old_config)
    for cfg_key, ref in sorted(protected.items()):
        key = ref_to_key(ref)
        if key is None:
            warnings.append("%s=%r is not in provider/model form; kept unchanged" % (cfg_key, ref))
            continue
        if key in to_disable:
            suggestions = ["%s/%s" % item for item in sorted(allowed_keys)][:5]
            print(
                "[ABORT] %s selects '%s' which would be disabled.\n"
                "Change it to a verified free model first. Suggestions: %s"
                % (cfg_key, ref, ", ".join(suggestions) or "(none available)"),
                file=sys.stderr,
            )
            return 4

    block = render_block(disabled, indent, begin_marker, end_marker)
    new_raw = raw[:block_start] + block + raw[block_end:]

    try:
        new_config = parse_jsonc(new_raw)
    except ValueError as exc:
        print("error: generated config is invalid; nothing written: %s" % exc, file=sys.stderr)
        return 5
    if set(new_config.keys()) != set(old_config.keys()):
        print("error: generated config changed top-level keys; nothing written", file=sys.stderr)
        return 5
    for selector in ("model", "small_model", "default_model"):
        if new_config.get(selector) != old_config.get(selector):
            print("error: generated config changed %s; nothing written" % selector, file=sys.stderr)
            return 5

    disabled_count = sum(len(ids) for ids in disabled.values())
    summary: Dict[str, Any] = {
        "status": "dry-run" if dry_run else "applied",
        "config": str(config_path),
        "source": source_label,
        "allowed_count": len(allowed_keys),
        "disabled_count": disabled_count,
        "disabled_by_provider": {p: sorted(set(v)) for p, v in sorted(disabled.items())},
        "tombstones": len(tombstones),
        "skipped_providers": sorted(skipped),
        "warnings": warnings,
    }
    if dry_run:
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print("dry-run OK: %d model(s) would be disabled, %d allowed" % (disabled_count, len(allowed_keys)))
            for warning in warnings:
                print("  warning: %s" % warning)
            print("  config: %s" % config_path)
        return 0

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = config_path.with_name("%s.bak.%s" % (config_path.name, timestamp))
    counter = 1
    while backup.exists():
        backup = config_path.with_name("%s.bak.%s-%d" % (config_path.name, timestamp, counter))
        counter += 1
    shutil.copy2(config_path, backup)

    fd, tmp_name = tempfile.mkstemp(prefix=config_path.name + ".tmp-", dir=str(config_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, stat.S_IMODE(config_path.stat().st_mode))
        os.replace(tmp_name, config_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    try:
        parse_jsonc(config_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print("error: written config failed validation (%s); backup: %s" % (exc, backup), file=sys.stderr)
        return 6

    save_state(state_path, {(p, m) for p, ids in disabled.items() for m in ids})
    summary["backup"] = str(backup)
    summary["state"] = str(state_path)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("applied: %d model(s) disabled (backup: %s)" % (disabled_count, backup))
        for warning in warnings:
            print("  warning: %s" % warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
