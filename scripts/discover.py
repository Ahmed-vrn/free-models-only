#!/usr/bin/env python3
"""Discover models from official catalogs and classify their cost.

The output is a machine-readable ``discovery.json`` consumed by ``apply.py``.
Only classifications backed by an official price field are trusted:

* ``verified_zero``          -- official price is zero for every cost field.
* ``quota_free``             -- zero price but a quota / account is required.
* ``funded_account_required``-- zero price but a top-up or funded account is needed.
* ``local``                  -- local runtime, no API invoice.
* ``paid``                   -- at least one official price is positive.
* ``unknown``                -- price is missing or cannot be interpreted.

Missing price is NEVER treated as zero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from opencode_cli import find_cli, list_runtime_models

OPENCODE_URL = "https://opencode.ai/inference/v1/models"
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_OUTPUT = Path.home() / ".cache" / "free-models-only" / "discovery.json"
USER_AGENT = "free-models-only/2.0 (+https://github.com/Ahmed-vrn/free-models-only)"

CLASSIFICATIONS = (
    "verified_zero",
    "quota_free",
    "funded_account_required",
    "local",
    "paid",
    "unknown",
)

OPENCODE_KINDS = {"chat", "decision", "image", "realtime", "embedding", "video", "other"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> Optional[float]:
    """Return a finite float, or None for missing / invalid / boolean values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def fetch_json(url: str, timeout: int = 25) -> Tuple[Any, str]:
    """Fetch JSON and return ``(payload, sha256)``."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def price_of(cost: Dict[str, Any], modality: str, direction: str) -> Optional[float]:
    """Read ``cost.<modality>.<direction>.price`` without inventing a value."""
    block = cost.get(modality)
    if not isinstance(block, dict):
        return None
    side = block.get(direction)
    if not isinstance(side, dict):
        return None
    return _number(side.get("price"))


def positive_costs(cost: Dict[str, Any]) -> Dict[str, float]:
    """Collect every positive price under the OpenCode cost tree."""
    found: Dict[str, float] = {}
    for modality in ("text", "image", "audio", "video"):
        block = cost.get(modality)
        if not isinstance(block, dict):
            continue
        for direction in ("input", "output"):
            side = block.get(direction)
            if not isinstance(side, dict):
                continue
            price = _number(side.get("price"))
            if price is not None and price > 0:
                found["%s.%s" % (modality, direction)] = price
            cache = side.get("cache")
            if isinstance(cache, dict):
                for cache_key, cache_value in cache.items():
                    cache_price = _number(cache_value)
                    if cache_price is not None and cache_price > 0:
                        found["%s.%s.cache.%s" % (modality, direction, cache_key)] = cache_price
    return found


def _base_model(
    provider_id: str,
    model_id: str,
    display_name: str,
    kind: str,
    classification: str,
    source_id: str,
    source_url: str,
    fetched_at: str,
) -> Dict[str, Any]:
    return {
        "provider_id": provider_id,
        "model_id": model_id,
        "display_name": display_name or model_id,
        "kind": kind,
        "classification": classification,
        "price": None,
        "other_costs": {},
        "quota": {"notes": None, "rpm": None, "rpd": None},
        "auth_required": False,
        "evidence": [
            {
                "source_id": source_id,
                "url": source_url,
                "fetched_at_utc": fetched_at,
            }
        ],
        "probe": {
            "status": "not_tested",
            "tested_at_utc": None,
            "latency_ms": None,
            "error": None,
        },
        "notes": [],
    }


def classify_opencode(
    raw: Dict[str, Any],
    fetched_at: Optional[str] = None,
    source_url: str = OPENCODE_URL,
) -> Optional[Dict[str, Any]]:
    """Classify one OpenCode catalog row. Returns None for rows without an id."""
    model_id = str(raw.get("id") or "").strip()
    if not model_id:
        return None

    raw_kind = str(raw.get("type") or "chat")
    kind = raw_kind if raw_kind in OPENCODE_KINDS else "other"
    cost = raw.get("cost") if isinstance(raw.get("cost"), dict) else {}

    text_input = price_of(cost, "text", "input")
    text_output = price_of(cost, "text", "output")
    positive = positive_costs(cost)

    notes: List[str] = []
    if positive:
        classification = "paid"
    elif text_input is None or text_output is None:
        classification = "unknown"
        notes.append("Official text price is missing; not treated as free.")
    elif kind != "chat":
        classification = "unknown"
        notes.append("Non-chat model; disabled in free-only chat mode.")
    elif text_input == 0 and text_output == 0:
        classification = "verified_zero"
    else:
        classification = "paid"

    model = _base_model(
        provider_id="opencode",
        model_id=model_id,
        display_name=str(raw.get("name") or model_id),
        kind=kind,
        classification=classification,
        source_id="opencode-api",
        source_url=source_url,
        fetched_at=fetched_at or utc_now(),
    )
    if text_input is not None and text_output is not None:
        model["price"] = {
            "input": text_input,
            "output": text_output,
            "currency": "USD",
            "unit": "per_1m_tokens",
        }
    model["other_costs"] = positive
    model["notes"] = notes
    model["auth_required"] = False
    if classification == "verified_zero":
        model["quota"]["notes"] = "Zero catalog price; per-model usage limits may still apply."
    return model


def _openrouter_price(pricing: Dict[str, Any], key: str) -> Optional[float]:
    raw = pricing.get(key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None  # OpenRouter uses -1 for dynamic/unknown pricing.
    return value * 1_000_000  # OpenRouter prices are per token.


def classify_openrouter(
    raw: Dict[str, Any],
    fetched_at: Optional[str] = None,
    source_url: str = OPENROUTER_URL,
) -> Optional[Dict[str, Any]]:
    """Classify one OpenRouter catalog row.

    OpenRouter free variants always require an account/key and are subject to
    rate limits, so a zero price maps to ``quota_free``, never ``verified_zero``.
    """
    model_id = str(raw.get("id") or "").strip()
    if not model_id:
        return None

    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    prompt = _openrouter_price(pricing, "prompt")
    completion = _openrouter_price(pricing, "completion")
    request = _openrouter_price(pricing, "request")
    image = _openrouter_price(pricing, "image")

    architecture = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
    output_modalities = architecture.get("output_modalities") or []
    input_modalities = architecture.get("input_modalities") or []
    if not isinstance(output_modalities, list):
        output_modalities = []
    if not isinstance(input_modalities, list):
        input_modalities = []

    if "image" in output_modalities and "text" not in output_modalities:
        kind = "image"
    elif "text" in output_modalities:
        kind = "chat"
    else:
        kind = "other"

    values = [v for v in (prompt, completion, request, image) if v is not None]
    notes: List[str] = []
    if prompt is None or completion is None:
        classification = "unknown"
        notes.append("OpenRouter price is missing or dynamic; not treated as free.")
    elif any(v > 0 for v in values):
        classification = "paid"
    elif kind != "chat":
        classification = "unknown"
        notes.append("Non-chat model; disabled in free-only chat mode.")
    else:
        classification = "quota_free"
        notes.append("Free tier requires an OpenRouter account and is rate-limited.")

    model = _base_model(
        provider_id="openrouter",
        model_id=model_id,
        display_name=str(raw.get("name") or model_id),
        kind=kind,
        classification=classification,
        source_id="openrouter-api",
        source_url=source_url,
        fetched_at=fetched_at or utc_now(),
    )
    if prompt is not None and completion is not None:
        model["price"] = {
            "input": prompt,
            "output": completion,
            "currency": "USD",
            "unit": "per_1m_tokens",
        }
    other: Dict[str, float] = {}
    if request is not None and request > 0:
        other["request"] = request
    if image is not None and image > 0:
        other["image"] = image
    model["other_costs"] = other
    model["auth_required"] = True
    model["quota"] = {
        "notes": "OpenRouter free tier: account/API key required; rate limits apply.",
        "rpm": None,
        "rpd": None,
    }
    model["notes"] = notes
    return model


def dedupe(models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the first row per exact (provider, model id); never lowercase ids."""
    seen = set()
    result = []
    for model in models:
        key = (model.get("provider_id"), model.get("model_id"))
        if key in seen:
            continue
        seen.add(key)
        result.append(model)
    return result


def merge_runtime_models(
    models: List[Dict[str, Any]],
    runtime_refs: List[str],
    fetched_at: str,
    source_url: str = "cli:opencode models",
) -> List[Dict[str, Any]]:
    """Add runtime-only models as ``unknown`` so they are disabled.

    ``opencode models`` can expose ids that are absent from the public
    inference catalog (for example a still-selectable legacy model). Such a
    model has no verified price, so free-only mode must treat it as unknown.
    """
    existing = {(m.get("provider_id"), m.get("model_id")) for m in models}
    added: List[Dict[str, Any]] = []
    for ref in runtime_refs:
        if "/" not in ref:
            continue
        provider, model_id = ref.split("/", 1)
        model_id = model_id.split("#", 1)[0].strip()
        provider = provider.strip()
        if not provider or not model_id or (provider, model_id) in existing:
            continue
        model = _base_model(
            provider_id=provider,
            model_id=model_id,
            display_name=model_id,
            kind="other",
            classification="unknown",
            source_id="opencode-runtime",
            source_url=source_url,
            fetched_at=fetched_at,
        )
        model["notes"] = [
            "Runtime model not present in the official catalog; price unverified."
        ]
        added.append(model)
        existing.add((provider, model_id))
    return models + added


def summarize(models: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {name: 0 for name in CLASSIFICATIONS}
    for model in models:
        classification = model.get("classification")
        if classification in counts:
            counts[classification] += 1
    return counts


def build_discovery(
    opencode_payload: Any,
    openrouter_payload: Any,
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:
    fetched_at = utc_now()
    models: List[Dict[str, Any]] = []
    if isinstance(opencode_payload, dict):
        for raw in opencode_payload.get("data") or []:
            if isinstance(raw, dict):
                model = classify_opencode(raw, fetched_at=fetched_at)
                if model:
                    models.append(model)
    if isinstance(openrouter_payload, dict):
        for raw in openrouter_payload.get("data") or []:
            if isinstance(raw, dict):
                model = classify_openrouter(raw, fetched_at=fetched_at)
                if model:
                    models.append(model)
    models = dedupe(models)
    return {
        "schema_version": 1,
        "generated_at_utc": fetched_at,
        "mode": "free-only",
        "sources": sources,
        "summary": summarize(models),
        "models": models,
    }


def _source(
    source_id: str,
    url: str,
    ok: bool,
    model_count: int,
    sha256: Optional[str],
    error: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": source_id,
        "url": url,
        "fetched_at_utc": utc_now(),
        "ok": ok,
        "model_count": model_count,
        "sha256": sha256,
        "error": error,
    }


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover and classify OpenCode / OpenRouter models")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="discovery.json path")
    parser.add_argument("--timeout", type=int, default=25, help="per-source timeout in seconds")
    parser.add_argument(
        "--source",
        choices=("all", "opencode", "openrouter"),
        default="all",
        help="which catalog(s) to fetch",
    )
    parser.add_argument("--json", action="store_true", help="print the full discovery document")
    parser.add_argument(
        "--no-runtime",
        action="store_true",
        help="do not query 'opencode models' for runtime-only model ids",
    )
    parser.add_argument("--cli", help="explicit opencode binary used for runtime discovery")
    args = parser.parse_args()

    sources: List[Dict[str, Any]] = []
    opencode_payload: Any = None
    openrouter_payload: Any = None

    if args.source in ("all", "opencode"):
        try:
            opencode_payload, digest = fetch_json(OPENCODE_URL, args.timeout)
            rows = opencode_payload.get("data") if isinstance(opencode_payload, dict) else []
            sources.append(_source("opencode-api", OPENCODE_URL, True, len(rows or []), digest))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            sources.append(_source("opencode-api", OPENCODE_URL, False, 0, None, str(exc)))

    if args.source in ("all", "openrouter"):
        try:
            openrouter_payload, digest = fetch_json(OPENROUTER_URL, args.timeout)
            rows = openrouter_payload.get("data") if isinstance(openrouter_payload, dict) else []
            sources.append(_source("openrouter-api", OPENROUTER_URL, True, len(rows or []), digest))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            sources.append(_source("openrouter-api", OPENROUTER_URL, False, 0, None, str(exc)))

    runtime_refs: List[str] = []
    if not args.no_runtime:
        cli = find_cli(args.cli)
        if not cli:
            sources.append(
                _source(
                    "opencode-runtime",
                    "cli:opencode models",
                    False,
                    0,
                    None,
                    "opencode CLI not found; runtime-only ids were not checked",
                )
            )
        else:
            runtime_refs, _output, error = list_runtime_models(cli)
            sources.append(
                _source(
                    "opencode-runtime",
                    "cli:opencode models",
                    error is None,
                    len(runtime_refs),
                    None,
                    error,
                )
            )

    if not any(source["ok"] for source in sources):
        print("error: every source failed; keeping the previous discovery file", file=sys.stderr)
        for source in sources:
            print("  - %s: %s" % (source["id"], source["error"]), file=sys.stderr)
        return 1

    discovery = build_discovery(opencode_payload, openrouter_payload, sources)
    if runtime_refs:
        discovery["models"] = merge_runtime_models(
            discovery["models"], runtime_refs, discovery["generated_at_utc"]
        )
        discovery["summary"] = summarize(discovery["models"])
    write_atomic(args.output, discovery)

    counts = discovery["summary"]
    print("wrote %s" % args.output.expanduser())
    print(
        "models: %d | verified_zero: %d | quota_free: %d | funded: %d | paid: %d | unknown: %d"
        % (
            len(discovery["models"]),
            counts["verified_zero"],
            counts["quota_free"],
            counts["funded_account_required"],
            counts["paid"],
            counts["unknown"],
        )
    )
    for source in sources:
        state = "ok" if source["ok"] else "FAILED"
        print("source %-16s %-6s models=%d %s" % (
            source["id"], state, source["model_count"], source["error"] or ""
        ))

    if args.json:
        print(json.dumps(discovery, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
