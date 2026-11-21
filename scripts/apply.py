#!/usr/bin/env python3
"""Apply a discovery document to an OpenCode JSONC config safely.

The script owns only the block between the two ``free-models-only`` markers.
Everything outside that block is preserved byte-for-byte, including comments.

Safety rules:
* dry-run is the default; pass ``--apply`` to write;
* the configured default model is never disabled;
* a timestamped backup is created before any write;
* the new file is parsed before it replaces the old one;
* symlinked configs are refused unless ``--allow-symlink`` is given.
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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

START = "  // >>> free-models-only: START (managed — do not edit manually)\n"
END = "  // <<< free-models-only: END\n"
DEFAULT_CONFIG = Path.home() / ".config/opencode/opencode.jsonc"
DISABLE_CLASSIFICATIONS = {"paid", "unknown", "funded_account_required"}
ALL_CLASSIFICATIONS = {
    "verified_zero",
    "quota_free",
    "funded_account_required",
    "local",
    "paid",
    "unknown",
}


# ---------------------------------------------------------------------------
# JSONC helpers (string-aware, no third-party dependency)
# ---------------------------------------------------------------------------
def strip_comments(text: str) -> str:
    out: List[str] = []
    i = 0
    n = len(text)
    in_string = False
    quote = ""
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
            in_string = True
            quote = char
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
    i = 0
    n = len(text)
    in_string = False
    quote = ""
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
            in_string = True
            quote = char
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
    cleaned = remove_trailing_commas(strip_comments(text))
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("JSONC parse error: %s" % exc) from exc
    if not isinstance(value, dict):
        raise ValueError("JSONC root must be an object")
    return value


# ---------------------------------------------------------------------------
# Path / input helpers
# ---------------------------------------------------------------------------
def resolve_config(config: Optional[Path], scope: str) -> Path:
    if config is not None:
        return config.expanduser()

    if scope == "project":
        current = Path.cwd().resolve()
        for base in (current, *current.parents):
            for relative in (".opencode/opencode.jsonc", ".opencode/opencode.json"):
                candidate = base / relative
                if candidate.exists():
                    return candidate
            if (base / ".git").exists():
                break
        raise SystemExit(
            "error: no project config found. Expected .opencode/opencode.jsonc "
            "or .opencode/opencode.json in the current repository."
        )

    return DEFAULT_CONFIG


def read_paid_file(path: Path) -> List[str]:
    ids: List[str] = []
    for line in path.expanduser().read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ids.extend(part for part in re.split(r"[\s,]+", line) if part)
    return ids


def load_discovery(path: Path) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("error: discovery file root must be an object")
    if payload.get("schema_version") != 1:
        raise SystemExit("error: unsupported discovery schema_version")
    models = payload.get("models")
    if not isinstance(models, list):
        raise SystemExit("error: discovery file has no models array")

    by_provider: Dict[str, List[Dict[str, Any]]] = {}
    for model in models:
        if not isinstance(model, dict):
            continue
        provider = str(model.get("provider_id") or "").strip()
        model_id = str(model.get("model_id") or "").strip()
        if not provider or not model_id:
            continue
        classification = str(model.get("classification") or "unknown")
        if classification not in ALL_CLASSIFICATIONS:
            classification = "unknown"
        entry = dict(model)
        entry["classification"] = classification
        by_provider.setdefault(provider, []).append(entry)
    return by_provider, payload


def legacy_discovery(ids: Iterable[str]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    unique = sorted({item.strip() for item in ids if item.strip()})
    return (
        {
            "opencode": [
                {
                    "provider_id": "opencode",
                    "model_id": model_id,
                    "classification": "paid",
                }
                for model_id in unique
            ]
        },
        {"schema_version": 1, "mode": "legacy-paid-list", "models": unique},
    )


# ---------------------------------------------------------------------------
# Block rendering
# ---------------------------------------------------------------------------
def render_block(disabled: Dict[str, List[str]]) -> str:
    lines: List[str] = [
        START.rstrip("\n"),
        "  // ═══════════════════════════════════════════════════════════════",
        "  //  إخفاء النماذج المدفوعة وغير المعروفة — يبقى المجاني فقط",
        "  //  مُدار بواسطة مهارة free-models-only — لا تُعدّل يدوياً",
        "  //  المصدر: discovery.json المولّد من واجهات المزوّدين الرسمية",
        "  // ═══════════════════════════════════════════════════════════════",
    ]

    providers = [name for name in disabled if disabled[name]]
    providers.sort(key=lambda name: (name != "opencode", name))
    if not providers:
        lines.append('  "providers": {},')
    else:
        lines.append('  "providers": {')
        for provider_index, provider in enumerate(providers):
            lines.append('    %s: {' % json.dumps(provider, ensure_ascii=False))
            lines.append('      "models": {')
            model_ids = sorted(set(disabled[provider]))
            for model_index, model_id in enumerate(model_ids):
                comma = "," if model_index < len(model_ids) - 1 else ""
                lines.append(
                    '        %s: { "disabled": true }%s'
                    % (json.dumps(model_id, ensure_ascii=False), comma)
                )
            lines.append("      }")
            comma = "," if provider_index < len(providers) - 1 else ""
            lines.append("    }%s" % comma)
        lines.append("  },")
    lines.append(END.rstrip("\n"))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply a free-model discovery document to opencode.jsonc"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--discovery", type=Path, help="discovery.json from discover.py")
    source.add_argument("--paid-file", type=Path, help="legacy: one paid model id per line")
    source.add_argument("--paid", help="legacy: comma/space separated paid model ids")
    parser.add_argument("--config", type=Path, help="explicit config path")
    parser.add_argument(
        "--scope",
        choices=("global", "project"),
        default="global",
        help="global config (default) or the nearest project config",
    )
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="explicit dry-run (default)")
    parser.add_argument(
        "--disable-quota-free",
        action="store_true",
        help="also disable quota_free models (strict zero-cost-only mode)",
    )
    parser.add_argument(
        "--include-unconfigured-providers",
        action="store_true",
        help="write disabled entries even for providers absent from the config",
    )
    parser.add_argument("--allow-symlink", action="store_true", help="allow writing through a symlink")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        print("error: --apply and --dry-run are mutually exclusive", file=sys.stderr)
        return 2
    dry_run = not args.apply

    config_path = resolve_config(args.config, args.scope)
    if not config_path.exists():
        print("error: config not found: %s" % config_path, file=sys.stderr)
        return 2
    if config_path.is_symlink() and not args.allow_symlink:
        print(
            "error: config is a symlink; pass --allow-symlink if this is intentional",
            file=sys.stderr,
        )
        return 2

    raw_text = config_path.read_text(encoding="utf-8")
    if raw_text.count(START) != 1 or raw_text.count(END) != 1:
        print(
            "error: expected exactly one managed block in %s. "
            "Run scripts/install.sh first to add the markers." % config_path,
            file=sys.stderr,
        )
        return 3
    start_index = raw_text.find(START)
    end_index = raw_text.find(END, start_index)
    if end_index < 0:
        print("error: managed block is not closed", file=sys.stderr)
        return 3

    try:
        old_config = parse_jsonc(raw_text)
    except ValueError as exc:
        print("error: existing config is invalid: %s" % exc, file=sys.stderr)
        return 3

    if args.discovery:
        by_provider, discovery = load_discovery(args.discovery)
        source_label = str(args.discovery)
    elif args.paid_file:
        by_provider, discovery = legacy_discovery(read_paid_file(args.paid_file))
        source_label = str(args.paid_file)
    else:
        by_provider, discovery = legacy_discovery(re.split(r"[\s,]+", args.paid or ""))
        source_label = "inline paid list"

    disable_set = set(DISABLE_CLASSIFICATIONS)
    if args.disable_quota_free:
        disable_set.add("quota_free")

    configured_providers: Set[str] = set()
    providers_config = old_config.get("providers")
    if isinstance(providers_config, dict):
        configured_providers = {str(name) for name in providers_config.keys()}

    disabled: Dict[str, List[str]] = {}
    kept: Dict[str, List[str]] = {}
    skipped_providers: Set[str] = set()

    for provider, models in by_provider.items():
        include = (
            provider == "opencode"
            or provider in configured_providers
            or args.include_unconfigured_providers
        )
        if not include:
            skipped_providers.add(provider)
            continue
        for model in models:
            classification = str(model.get("classification") or "unknown")
            model_id = str(model.get("model_id") or "").strip()
            if not model_id:
                continue
            if classification in disable_set:
                disabled.setdefault(provider, []).append(model_id)
            else:
                kept.setdefault(provider, []).append(model_id)

    # A runtime-only model can disappear from every catalog once it is disabled.
    # Keep such previously managed ids disabled unless the new discovery
    # explicitly classifies them as free. This prevents a re-enable loop.
    discovered = {
        (str(model.get("provider_id") or ""), str(model.get("model_id") or ""))
        for models in by_provider.values()
        for model in models
    }
    carried_over: List[Tuple[str, str]] = []
    for provider_name, provider_conf in providers_config.items():
        if not isinstance(provider_conf, dict):
            continue
        models_conf = provider_conf.get("models")
        if not isinstance(models_conf, dict):
            continue
        for model_id, model_conf in models_conf.items():
            if not isinstance(model_conf, dict) or model_conf.get("disabled") is not True:
                continue
            key = (str(provider_name), str(model_id))
            if key in discovered:
                continue
            if model_id in disabled.get(str(provider_name), []):
                continue
            disabled.setdefault(str(provider_name), []).append(str(model_id))
            carried_over.append(key)

    # Protect the configured default model before writing anything.
    default_ref = old_config.get("model")
    default_provider = None
    default_model = None
    if isinstance(default_ref, str) and "/" in default_ref:
        default_provider, default_model = default_ref.split("/", 1)

    warnings: List[str] = []
    if default_provider and default_model:
        if default_model in disabled.get(default_provider, []):
            print(
                "error: refusing to disable the configured default model %s. "
                "Change the default first or fix the discovery classification." % default_ref,
                file=sys.stderr,
            )
            return 4
        for model in by_provider.get(default_provider, []):
            if model.get("model_id") == default_model:
                classification = str(model.get("classification") or "unknown")
                if classification in disable_set:
                    print(
                        "error: default model %s is classified as %s. Refusing to apply."
                        % (default_ref, classification),
                        file=sys.stderr,
                    )
                    return 4
                break
        else:
            warnings.append(
                "default model %s is not present in the discovery document; kept unchanged"
                % default_ref
            )
    elif default_ref:
        warnings.append("default model %r is not in provider/model form; kept unchanged" % default_ref)

    if skipped_providers:
        warnings.append(
            "providers not present in config were skipped: %s"
            % ", ".join(sorted(skipped_providers))
        )

    block = render_block(disabled)
    new_text = raw_text[:start_index] + block + raw_text[end_index + len(END):]

    try:
        new_config = parse_jsonc(new_text)
    except ValueError as exc:
        print("error: generated config is invalid; nothing written: %s" % exc, file=sys.stderr)
        return 5

    if set(new_config.keys()) != set(old_config.keys()):
        print(
            "error: generated config changed top-level keys; nothing written",
            file=sys.stderr,
        )
        return 5
    if new_config.get("model") != old_config.get("model"):
        print("error: generated config changed the default model; nothing written", file=sys.stderr)
        return 5

    total_disabled = sum(len(ids) for ids in disabled.values())
    total_kept = sum(len(ids) for ids in kept.values())
    summary: Dict[str, Any] = {
        "status": "dry-run" if dry_run else "applied",
        "config": str(config_path),
        "source": source_label,
        "default_model": default_ref,
        "disabled_count": total_disabled,
        "disabled_by_provider": {name: sorted(set(ids)) for name, ids in sorted(disabled.items())},
        "kept_count": total_kept,
        "carried_over_count": len(carried_over),
        "carried_over": ["%s/%s" % item for item in sorted(carried_over)],
        "skipped_providers": sorted(skipped_providers),
        "warnings": warnings,
    }

    if dry_run:
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print("dry-run OK: %d model(s) would be disabled" % total_disabled)
            for provider in sorted(disabled):
                print("  %-12s %d" % (provider, len(set(disabled[provider]))))
            for warning in warnings:
                print("  warning: %s" % warning)
            if carried_over:
                print("  carried over %d previously disabled id(s)" % len(carried_over))
            print("  config: %s" % config_path)
        return 0

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = config_path.with_name("%s.bak.%s" % (config_path.name, timestamp))
    counter = 1
    while backup_path.exists():
        backup_path = config_path.with_name(
            "%s.bak.%s-%d" % (config_path.name, timestamp, counter)
        )
        counter += 1

    shutil.copy2(config_path, backup_path)
    fd, tmp_name = tempfile.mkstemp(prefix=config_path.name + ".tmp-", dir=str(config_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_text)
        os.chmod(tmp_name, stat.S_IMODE(config_path.stat().st_mode))
        os.replace(tmp_name, config_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    # Re-read and validate the file that actually landed on disk.
    try:
        parse_jsonc(config_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(
            "error: written config failed validation (%s); backup at %s"
            % (exc, backup_path),
            file=sys.stderr,
        )
        return 6

    summary["status"] = "applied"
    summary["backup"] = str(backup_path)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("applied: %d model(s) disabled" % total_disabled)
        for provider in sorted(disabled):
            print("  %-12s %d" % (provider, len(set(disabled[provider]))))
        print("  backup: %s" % backup_path)
        for warning in warnings:
            print("  warning: %s" % warning)
        if carried_over:
            print("  carried over %d previously disabled id(s)" % len(carried_over))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
