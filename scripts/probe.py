#!/usr/bin/env python3
"""Smoke-test a small number of free models with the OpenCode CLI.

The probe is intentionally conservative:

* only ``verified_zero`` chat models are tested by default;
* at most three models per run (``--limit``, capped at 10);
* no automatic retries;
* a model is tested with an explicit ``--model provider/model`` argument,
  never through the session default alone;
* results are written back into ``discovery.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from apply import parse_jsonc
except ImportError:  # pragma: no cover - only relevant for unusual import paths
    parse_jsonc = None  # type: ignore[assignment]

from opencode_cli import find_cli, strip_ansi

DEFAULT_DISCOVERY = Path.home() / ".cache" / "free-models-only" / "discovery.json"
DEFAULT_CONFIG = Path.home() / ".config/opencode/opencode.jsonc"
MARKER = "FREE_MODEL_TEST_OK"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_default_model(config_path: Path) -> Optional[str]:
    if parse_jsonc is None or not config_path.exists():
        return None
    try:
        config = parse_jsonc(config_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    value = config.get("model")
    return value if isinstance(value, str) else None


def load_discovery(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise SystemExit("error: unsupported discovery file: %s" % path)
    if not isinstance(payload.get("models"), list):
        raise SystemExit("error: discovery file has no models array: %s" % path)
    return payload


def write_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def model_ref(model: Dict[str, Any]) -> str:
    return "%s/%s" % (model.get("provider_id"), model.get("model_id"))


def select_models(
    payload: Dict[str, Any],
    requested: List[str],
    limit: int,
    include_quota_free: bool,
    allow_nonfree: bool,
    default_ref: Optional[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    models = [m for m in payload.get("models", []) if isinstance(m, dict)]
    by_ref = {model_ref(model): model for model in models}
    by_id = {str(model.get("model_id")): model for model in models}

    warnings: List[str] = []
    if requested:
        selected: List[Dict[str, Any]] = []
        for ref in requested:
            model = by_ref.get(ref) or by_id.get(ref)
            if model is None:
                warnings.append("model not found in discovery: %s" % ref)
                continue
            classification = str(model.get("classification") or "unknown")
            if classification not in ("verified_zero", "local", "quota_free") and not allow_nonfree:
                warnings.append(
                    "skipping %s (%s); pass --allow-nonfree to test it explicitly"
                    % (ref, classification)
                )
                continue
            if classification == "quota_free" and not include_quota_free and not allow_nonfree:
                warnings.append(
                    "skipping %s (quota_free); pass --include-quota-free to test it"
                    % ref
                )
                continue
            selected.append(model)
        return selected[:limit], warnings

    candidates = []
    for model in models:
        classification = str(model.get("classification") or "unknown")
        kind = str(model.get("kind") or "other")
        if kind != "chat":
            continue
        if classification == "verified_zero":
            candidates.append(model)
        elif classification == "quota_free" and include_quota_free:
            candidates.append(model)
        elif classification == "local":
            candidates.append(model)

    def order(model: Dict[str, Any]) -> Tuple[int, str]:
        return (0 if model_ref(model) == default_ref else 1, model_ref(model))

    candidates.sort(key=order)
    return candidates[:limit], warnings


def classify_output(output: str, returncode: int) -> Tuple[str, str]:
    lowered = output.lower()
    if MARKER in output and returncode == 0:
        return "verified", ""
    if re.search(r"\b402\b|insufficient balance|top[- ]?up|funded account|payment required", lowered):
        return "funded_account_required", "provider reported a billing or funding requirement"
    if re.search(r"\b429\b|rate[- ]?limit|too many requests|quota", lowered):
        return "rate_limited", "provider reported a rate limit or quota"
    if re.search(r"\b401\b|\b403\b|unauthori[sz]ed|forbidden|invalid api key", lowered):
        return "auth_required", "provider rejected authentication"
    return "failed", "probe did not return the expected marker"


def run_one(cli: str, model: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    ref = model_ref(model)
    command = [cli, "run", "--model", ref, "Reply with exactly: %s" % MARKER]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "NO_COLOR": "1"},
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        output = strip_ansi((completed.stdout or "") + "\n" + (completed.stderr or ""))
        status, error = classify_output(output, completed.returncode)
        return {
            "status": status,
            "tested_at_utc": utc_now(),
            "latency_ms": latency_ms,
            "error": error or None,
            "snippet": output.strip()[-600:],
            "returncode": completed.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "tested_at_utc": utc_now(),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": "probe timed out after %ds" % timeout,
            "snippet": "",
            "returncode": None,
        }
    except OSError as exc:
        return {
            "status": "failed",
            "tested_at_utc": utc_now(),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": str(exc),
            "snippet": "",
            "returncode": None,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test free OpenCode models")
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cli", help="explicit opencode binary")
    parser.add_argument("--model", action="append", default=[], help="provider/model (repeatable)")
    parser.add_argument("--limit", type=int, default=3, help="max models per run (default 3, max 10)")
    parser.add_argument("--timeout", type=int, default=90, help="per-model timeout in seconds")
    parser.add_argument("--include-quota-free", action="store_true")
    parser.add_argument("--allow-nonfree", action="store_true")
    parser.add_argument("--no-write", action="store_true", help="do not update discovery.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cli = find_cli(args.cli)
    if not cli:
        print(
            "error: opencode CLI not found. Set OPENCODE_BIN or pass --cli.",
            file=sys.stderr,
        )
        return 2

    discovery_path = args.discovery.expanduser()
    payload = load_discovery(discovery_path)
    default_ref = read_default_model(args.config.expanduser())
    limit = max(1, min(10, args.limit))
    selected, warnings = select_models(
        payload,
        args.model,
        limit,
        args.include_quota_free,
        args.allow_nonfree,
        default_ref,
    )

    for warning in warnings:
        print("warning: %s" % warning, file=sys.stderr)
    if not selected:
        print("no eligible models to probe")
        return 0

    results = []
    for model in selected:
        result = run_one(cli, model, args.timeout)
        results.append({"ref": model_ref(model), "classification": model.get("classification"), **result})
        model["probe"] = {
            "status": result["status"],
            "tested_at_utc": result["tested_at_utc"],
            "latency_ms": result["latency_ms"],
            "error": result["error"],
        }
        if not args.json:
            print(
                "%-45s %-24s %5sms %s"
                % (
                    model_ref(model),
                    result["status"],
                    result["latency_ms"],
                    result["error"] or "",
                )
            )

    if not args.no_write:
        write_atomic(discovery_path, payload)
        if not args.json:
            print("updated %s" % discovery_path)

    if args.json:
        print(json.dumps({"results": results, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
