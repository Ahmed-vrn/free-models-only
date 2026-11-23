#!/usr/bin/env python3
"""Free Models Only v3.0 - multi-provider zero-trust discovery engine.

Standard library only. The output ``discovery.json`` is consumed by
``apply.py`` and ``probe.py``.

Zero-trust rules implemented here:

* a missing, non-numeric, negative or dynamic (``-1``) price is NEVER zero;
* ``models.dev`` is a ONE-WAY VETO source: a positive cost there disables a
  model, but absence of a price there never enables it;
* every official cost dimension is scanned (text, reasoning, cache, request,
  web search, image/audio/video);
* ``tiers`` / dynamic pricing are treated as paid / unknown respectively;
* a provider is ``local`` only after its ``baseURL`` is proven to be loopback;
* models seen only at runtime (``opencode models``) are ``unknown``;
* cloud quota tiers without explicit zero pricing are ``unknown``, not free.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from opencode_cli import find_cli, list_runtime_models

OPENCODE_URL = "https://opencode.ai/inference/v1/models"
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
MODELS_DEV_URL = "https://models.dev/api.json"
FALLBACK_LIST = Path(__file__).resolve().parents[1] / "references" / "paid-model-ids.txt"

DEFAULT_OUTPUT = Path.home() / ".cache" / "free-models-only" / "discovery.json"
DEFAULT_CONFIG = Path.home() / ".config" / "opencode" / "opencode.jsonc"
USER_AGENT = "free-models-only/3.0 (+https://github.com/Ahmed-vrn/free-models-only)"

CLASSIFICATIONS = (
    "verified_zero",
    "quota_free",
    "funded_account_required",
    "local",
    "paid",
    "unknown",
)

LOCAL_PROVIDER_HINTS = {"ollama", "lmstudio", "vllm", "llamacpp", "llama.cpp", "localai", "jan", "llama"}
NON_CHAT_KEYWORDS = (
    "embedding", "embed", "rerank", "tts", "whisper", "moderation",
    "audio-only", "imagen", "dall-e", "image-only", "transcription",
)
CHAT_TYPES = {"chat", "language", "completion"}
COST_SCALAR_KEYS = {
    "price", "prompt", "completion", "request", "image", "audio", "video",
    "web_search", "web_search_requests", "internal_reasoning", "reasoning",
    "input", "output", "cache", "cache_read", "cache_write", "read", "write",
    "input_cache_read", "input_cache_write", "input_cost", "output_cost",
}
TIER_KEYS = {"tiers", "tiered", "pricing_tiers", "context_tiers"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_price(value: Any) -> Tuple[bool, Decimal]:
    """Strict Decimal parse. Negative / null / bool / non-numeric -> invalid."""
    if value is None or isinstance(value, bool):
        return False, Decimal("-1")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return False, Decimal("-1")
    if number.is_nan() or number.is_infinite() or number < Decimal("0"):
        return False, Decimal("-1")
    return True, number


def scan_cost(obj: Any) -> Dict[str, Any]:
    """Walk any pricing tree and collect every cost fact.

    Returns ``positive`` (path -> value), ``dynamic`` (path -> raw value),
    ``tiered`` (list of paths), ``zero`` (set of zero-valued paths) and
    ``fields`` (all discovered scalar paths). This is deliberately recursive so
    that a malformed field cannot be overwritten by a later loop iteration.
    """
    findings: Dict[str, Any] = {
        "positive": {},
        "dynamic": {},
        "tiered": [],
        "zero": set(),
        "fields": set(),
    }

    def walk(node: Any, path: List[str]) -> None:
        if isinstance(node, dict):
            for raw_key, value in node.items():
                key = str(raw_key)
                lowered = key.lower()
                next_path = path + [key]
                if lowered in TIER_KEYS and value:
                    findings["tiered"].append(".".join(next_path))
                    continue
                scalar = isinstance(value, (int, float, str, Decimal)) and not isinstance(value, bool)
                if lowered in COST_SCALAR_KEYS and scalar:
                    dotted = ".".join(next_path)
                    ok, number = parse_price(value)
                    findings["fields"].add(dotted)
                    if not ok:
                        findings["dynamic"][dotted] = str(value)
                    elif number > Decimal("0"):
                        findings["positive"][dotted] = str(number)
                    else:
                        findings["zero"].add(dotted)
                    continue
                if isinstance(value, (dict, list)):
                    walk(value, next_path)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, path + [str(index)])

    walk(obj, [])
    return findings


def is_loopback_url(url_str: str, allow_lan: bool = False) -> bool:
    """Prove that a baseURL is a loopback / unix socket, not a cloud proxy."""
    if not url_str:
        return False
    url_str = str(url_str).strip()
    if url_str.startswith("unix://"):
        return True
    try:
        parsed = urllib.parse.urlparse(url_str)
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return False
        if host == "localhost" or host.endswith(".localhost"):
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False  # a hostname other than localhost is not proven local
        if ip.is_loopback:
            return True
        return bool(allow_lan and ip.is_private)
    except ValueError:
        return False


def strip_jsonc_comments(text: str) -> str:
    """String-aware removal of // and /* */ comments and trailing commas."""
    pattern = re.compile(r'("(?:\\.|[^"\\])*")|//[^\r\n]*|/\*[\s\S]*?\*/')
    without = pattern.sub(lambda m: m.group(1) if m.group(1) else "", text)
    return re.sub(r",(\s*[}\]])", r"\1", without)


def load_configured_local_providers(config_path: Path, allow_lan: bool) -> Set[str]:
    """Return provider ids whose configured baseURL is proven local."""
    verified: Set[str] = set()
    if not config_path or not config_path.exists():
        return verified
    try:
        data = json.loads(strip_jsonc_comments(config_path.read_text(encoding="utf-8")))
        providers = data.get("providers") or data.get("provider") or {}
        if not isinstance(providers, dict):
            return verified
        for pid, conf in providers.items():
            if not isinstance(conf, dict):
                continue
            base_url = (
                conf.get("baseURL") or conf.get("base_url") or conf.get("endpoint") or ""
            )
            if is_loopback_url(str(base_url), allow_lan=allow_lan):
                verified.add(str(pid))
    except Exception as exc:  # pragma: no cover - defensive
        print("warning: could not inspect local endpoints: %s" % exc, file=sys.stderr)
    return verified


def fetch_json(url: str, timeout: int = 25) -> Tuple[Any, str]:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def _source(source_id: str, url: str, ok: bool, count: int, sha256: Optional[str],
            error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": source_id,
        "url": url,
        "fetched_at_utc": utc_now(),
        "ok": ok,
        "model_count": count,
        "sha256": sha256,
        "error": error,
    }


def _base_model(
    provider_id: str,
    model_id: str,
    display_name: str,
    kind: str,
    classification: str,
    source_id: str,
    source_url: str,
    reason: str,
    fetched_at: str,
) -> Dict[str, Any]:
    return {
        "provider_id": provider_id,
        "model_id": model_id,
        "display_name": display_name or model_id,
        "kind": kind,
        "classification": classification,
        "reason": reason,
        "source": source_id,
        "price": None,
        "other_costs": {},
        "quota": {"notes": None, "rpm": None, "rpd": None},
        "auth_required": False,
        "privacy_retention": False,
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


def summarize(models: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {name: 0 for name in CLASSIFICATIONS}
    for model in models:
        name = model.get("classification")
        if name in counts:
            counts[name] += 1
    return counts


# ---------------------------------------------------------------------------
# Official adapters
# ---------------------------------------------------------------------------
def classify_opencode_catalog(
    payload: Any,
    fetched_at: Optional[str] = None,
    source_url: str = OPENCODE_URL,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Classify every row of the official OpenCode inference catalog."""
    results: Dict[Tuple[str, str], Dict[str, Any]] = {}
    rows = payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return results
    now = fetched_at or utc_now()

    for raw in rows:
        if not isinstance(raw, dict):
            continue
        model_id = str(raw.get("id", "")).strip()
        if not model_id:
            continue
        provider_id, bare_id = model_id.split("/", 1) if "/" in model_id else ("opencode", model_id)
        model_type = str(raw.get("type", "chat")).lower()
        is_chat = model_type in CHAT_TYPES and not any(
            word in bare_id.lower() for word in NON_CHAT_KEYWORDS
        )

        cost = raw.get("cost")
        findings = scan_cost(cost) if isinstance(cost, dict) else {"positive": {}, "dynamic": {},
                                                                   "tiered": [], "zero": set(), "fields": set()}
        has_text_io = (
            "text.input.price" in findings["zero"] and "text.output.price" in findings["zero"]
        )

        if findings["tiered"]:
            classification, reason = "paid", "tiered_pricing:" + ",".join(findings["tiered"][:3])
        elif findings["positive"]:
            first = sorted(findings["positive"])[0]
            classification, reason = "paid", "positive_price_%s=%s" % (first, findings["positive"][first])
        elif findings["dynamic"]:
            first = sorted(findings["dynamic"])[0]
            classification, reason = "unknown", "dynamic_or_invalid_price_%s" % first
        elif not isinstance(cost, dict) or not findings["fields"]:
            classification, reason = "unknown", "missing_official_cost_object"
        elif not has_text_io:
            classification, reason = "unknown", "missing_text_input_or_output_price"
        elif not is_chat:
            classification, reason = "unknown", "non_chat_or_unverified_modality"
        else:
            classification, reason = "verified_zero", "all_official_cost_fields_zero"

        model = _base_model(
            provider_id, bare_id,
            str(raw.get("name") or bare_id),
            model_type if is_chat else "other",
            classification, "opencode_api", source_url, reason, now,
        )
        model["other_costs"] = dict(findings["positive"])
        if has_text_io:
            model["price"] = {"input": 0.0, "output": 0.0, "currency": "USD", "unit": "per_1m_tokens"}
        model["privacy_retention"] = bool(
            raw.get("training_opt_in") or raw.get("data_retention") or raw.get("data_collection") == "allow"
        )
        if classification == "verified_zero":
            if raw.get("requires_funded_account") is True:
                classification = "funded_account_required"
                model["reason"] = "requires_funded_account_flag"
            elif raw.get("requires_api_key") is True or raw.get("quota_limited") is True:
                classification = "quota_free"
                model["reason"] = "requires_key_or_quota_limited"
                model["auth_required"] = True
                model["quota"]["notes"] = "Zero catalog price with account/key and usage limits."
        model["classification"] = classification
        results[(provider_id, bare_id)] = model
    return results


def classify_openrouter_catalog(
    payload: Any,
    fetched_at: Optional[str] = None,
    source_url: str = OPENROUTER_URL,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Classify every OpenRouter model. Zero price => quota_free, never verified_zero."""
    results: Dict[Tuple[str, str], Dict[str, Any]] = {}
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return results
    now = fetched_at or utc_now()

    for raw in rows:
        if not isinstance(raw, dict):
            continue
        model_id = str(raw.get("id", "")).strip()
        if not model_id:
            continue
        pricing = raw.get("pricing")
        findings = scan_cost(pricing) if isinstance(pricing, dict) else {"positive": {}, "dynamic": {},
                                                                          "tiered": [], "zero": set(), "fields": set()}
        architecture = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
        outputs = architecture.get("output_modalities")
        is_chat = (not isinstance(outputs, list)) or ("text" in outputs)
        if any(word in model_id.lower() for word in NON_CHAT_KEYWORDS):
            is_chat = False

        if raw.get("tiers") or findings["tiered"]:
            classification, reason = "paid", "tiered_context_pricing"
        elif findings["positive"]:
            first = sorted(findings["positive"])[0]
            classification, reason = "paid", "positive_price_%s=%s" % (first, findings["positive"][first])
        elif findings["dynamic"]:
            first = sorted(findings["dynamic"])[0]
            classification, reason = "unknown", "dynamic_or_invalid_price_%s" % first
        elif not isinstance(pricing, dict) or "prompt" not in findings["fields"] or "completion" not in findings["fields"]:
            classification, reason = "unknown", "missing_openrouter_pricing_fields"
        elif not is_chat:
            classification, reason = "unknown", "non_chat_or_unverified_modality"
        else:
            classification, reason = "quota_free", "zero_price_all_fields_with_account_and_quota"

        model = _base_model(
            "openrouter", model_id, str(raw.get("name") or model_id),
            "chat" if is_chat else "other",
            classification, "openrouter_api", source_url, reason, now,
        )
        model["other_costs"] = dict(findings["positive"])
        if "prompt" in findings["zero"] and "completion" in findings["zero"]:
            model["price"] = {"input": 0.0, "output": 0.0, "currency": "USD", "unit": "per_1m_tokens"}
        model["auth_required"] = True
        model["quota"]["notes"] = "OpenRouter free tier: API key required; rate limits apply."
        # OpenRouter free variants may train on inputs; strict mode drops them.
        model["privacy_retention"] = True
        results[("openrouter", model_id)] = model
    return results


def extract_models_dev_vetoes(payload: Any) -> Dict[Tuple[str, str], str]:
    """One-way veto: positive cost anywhere in models.dev disables the model."""
    vetoes: Dict[Tuple[str, str], str] = {}
    if not isinstance(payload, dict):
        return vetoes
    for provider_id, pdata in payload.items():
        if not isinstance(pdata, dict):
            continue
        models = pdata.get("models", {})
        if not isinstance(models, dict):
            continue
        for model_id, mdata in models.items():
            if not isinstance(mdata, dict):
                continue
            findings = scan_cost(mdata.get("cost", {}))
            if findings["positive"]:
                first = sorted(findings["positive"])[0]
                vetoes[(str(provider_id), str(model_id))] = "models_dev_veto_%s=%s" % (
                    first, findings["positive"][first]
                )
            elif findings["tiered"]:
                vetoes[(str(provider_id), str(model_id))] = "models_dev_veto_tiered:" + findings["tiered"][0]
    return vetoes


def read_fallback_paid_ids(path: Path = FALLBACK_LIST) -> Set[Tuple[str, str]]:
    ids: Set[Tuple[str, str]] = set()
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for token in re.split(r"[\s,]+", line):
            if not token:
                continue
            if "/" in token:
                provider, model_id = token.split("/", 1)
                ids.add((provider.strip(), model_id.strip()))
            else:
                ids.add(("opencode", token))
    return ids


def runtime_refs_to_keys(refs: Iterable[str]) -> Set[Tuple[str, str]]:
    keys: Set[Tuple[str, str]] = set()
    for ref in refs:
        token = str(ref).strip().split()[0] if str(ref).strip() else ""
        if "/" not in token:
            continue
        provider, model_id = token.split("/", 1)
        model_id = model_id.split("#", 1)[0].strip()
        provider = provider.strip()
        if provider and model_id:
            keys.add((provider, model_id))
    return keys


# ---------------------------------------------------------------------------
# Discovery assembly / IO
# ---------------------------------------------------------------------------
def build_document(models: List[Dict[str, Any]], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    at = utc_now()
    return {
        "schema_version": "3.0",
        "generated_at": at,
        "generated_at_utc": at,
        "mode": "free-only",
        "sources": sources,
        "summary": summarize(models),
        "models": sorted(models, key=lambda m: (m["provider_id"], m["model_id"])),
    }


def read_cached_document(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            return None
        cached_version = str(payload.get("schema_version", ""))
        if cached_version not in ("1", "3", "3.0"):
            return None
        return payload
    except (OSError, ValueError):
        return None


def write_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Zero-trust multi-provider model discovery")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="discovery.json path")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="opencode.jsonc to inspect for local endpoints")
    parser.add_argument("--timeout", type=int, default=25, help="per-source timeout in seconds")
    parser.add_argument("--no-runtime", action="store_true", help="skip `opencode models`")
    parser.add_argument("--offline", action="store_true", help="reuse cached discovery + fallback paid list, no network")
    parser.add_argument("--allow-lan-local", action="store_true", help="allow private LAN IPs for local classification")
    parser.add_argument("--cli", help="explicit opencode binary")
    parser.add_argument("--json", action="store_true", help="print the full document")
    args = parser.parse_args()

    output = args.output.expanduser()

    if args.offline:
        cached = read_cached_document(output)
        if cached is None:
            print("error: --offline needs an existing discovery.json and none was found", file=sys.stderr)
            return 10
        models = [m for m in cached["models"] if isinstance(m, dict)]
        seen = {(m.get("provider_id"), m.get("model_id")) for m in models}
        for provider, model_id in sorted(read_fallback_paid_ids()):
            if (provider, model_id) in seen:
                continue
            model = _base_model(provider, model_id, model_id, "other", "paid",
                                "offline_fallback", "file:" + str(FALLBACK_LIST),
                                "offline_fallback_paid_list", utc_now())
            models.append(model)
            seen.add((provider, model_id))
        document = build_document(models, [
            {"id": "cache", "url": str(output), "ok": True, "model_count": len(cached["models"]),
             "sha256": None, "error": None, "fetched_at_utc": utc_now()},
            {"id": "fallback-list", "url": str(FALLBACK_LIST), "ok": FALLBACK_LIST.exists(),
             "model_count": len(read_fallback_paid_ids()), "sha256": None,
             "error": None if FALLBACK_LIST.exists() else "fallback list missing",
             "fetched_at_utc": utc_now()},
        ])
        write_atomic(output, document)
        print(json.dumps(document["summary"], indent=2, ensure_ascii=False))
        if args.json:
            print(json.dumps(document, ensure_ascii=False, indent=2))
        return 0

    sources: List[Dict[str, Any]] = []
    catalogs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    vetoes: Dict[Tuple[str, str], str] = {}

    for source_id, url, classifier in (
        ("opencode-api", OPENCODE_URL, classify_opencode_catalog),
        ("openrouter-api", OPENROUTER_URL, classify_openrouter_catalog),
    ):
        try:
            payload, digest = fetch_json(url, args.timeout)
            rows = payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else []
            sources.append(_source(source_id, url, True, len(rows or []), digest))
            catalogs.update(classifier(payload))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            sources.append(_source(source_id, url, False, 0, None, str(exc)))

    try:
        payload, digest = fetch_json(MODELS_DEV_URL, args.timeout)
        sources.append(_source("models-dev-veto", MODELS_DEV_URL, True, len(payload or {}), digest))
        vetoes = extract_models_dev_vetoes(payload)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        sources.append(_source("models-dev-veto", MODELS_DEV_URL, False, 0, None, str(exc)))

    if not any(s["ok"] for s in sources):
        if read_cached_document(output) is not None:
            print("warning: all remote sources failed; keeping existing discovery.json", file=sys.stderr)
            for s in sources:
                print("  - %s: %s" % (s["id"], s["error"]), file=sys.stderr)
            return 0
        print("error: no discovery source available", file=sys.stderr)
        return 10

    runtime_keys: Set[Tuple[str, str]] = set()
    if not args.no_runtime:
        cli = find_cli(args.cli)
        if cli:
            refs, _out, error = list_runtime_models(cli)
            runtime_keys = runtime_refs_to_keys(refs)
            sources.append(_source("opencode-runtime", "cli:opencode models", error is None,
                                   len(runtime_keys), None, error))
        else:
            sources.append(_source("opencode-runtime", "cli:opencode models", False, 0, None,
                                   "opencode CLI not found"))

    local_providers = load_configured_local_providers(args.config, args.allow_lan_local)
    all_keys = set(catalogs) | set(vetoes) | runtime_keys
    models: List[Dict[str, Any]] = []

    for provider, model_id in all_keys:
        key = (provider, model_id)
        entry = catalogs.get(key)
        if key in vetoes:
            model = entry or _base_model(provider, model_id, model_id, "other", "paid",
                                         "models_dev_veto", MODELS_DEV_URL, vetoes[key], utc_now())
            model["classification"] = "paid"
            model["reason"] = vetoes[key]
            model["source"] = "models_dev_veto"
            model["other_costs"]["veto"] = vetoes[key]
        elif provider in local_providers:
            model = _base_model(provider, model_id, model_id, "chat", "local",
                                "local_config", "cli:opencode config", "verified_loopback_endpoint", utc_now())
        elif entry is not None:
            model = entry
        else:
            if provider.lower() in LOCAL_PROVIDER_HINTS:
                reason = "local_provider_without_verified_loopback_url"
            elif key in runtime_keys:
                reason = "runtime_model_missing_from_official_pricing_catalogs"
            else:
                reason = "unverified_model_cost"
            model = _base_model(provider, model_id, model_id, "other", "unknown",
                                "runtime_or_fallback", "cli:opencode models", reason, utc_now())
        models.append(model)

    document = build_document(models, sources)
    write_atomic(output, document)
    print("wrote %s" % output)
    print(json.dumps(document["summary"], indent=2, ensure_ascii=False))
    for s in sources:
        print("source %-16s %-6s models=%d %s" % (
            s["id"], "ok" if s["ok"] else "FAILED", s["model_count"], s["error"] or ""
        ))
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
