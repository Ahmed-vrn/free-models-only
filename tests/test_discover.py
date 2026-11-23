import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import discover  # noqa: E402


def opencode_row(model_id, kind="chat", text_in=0, text_out=0, **extra_cost):
    cost = {"text": {"input": {"price": text_in}, "output": {"price": text_out}}}
    cost.update(extra_cost)
    return {"id": model_id, "type": kind, "cost": cost}


def classify_opencode(row):
    result = discover.classify_opencode_catalog({"data": [row]})
    return next(iter(result.values()))


def openrouter_row(model_id="or/free", prompt="0", completion="0", **pricing):
    return {
        "id": model_id,
        "name": model_id,
        "pricing": {"prompt": prompt, "completion": completion, **pricing},
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    }


def classify_openrouter(row):
    result = discover.classify_openrouter_catalog({"data": [row]})
    return next(iter(result.values()))


class OpenCodeClassificationTests(unittest.TestCase):
    def test_zero_chat_is_verified_zero(self):
        model = classify_opencode(opencode_row("free-chat"))
        self.assertEqual(model["classification"], "verified_zero")
        self.assertEqual(model["price"]["input"], 0)
        self.assertEqual(model["other_costs"], {})

    def test_positive_text_price_is_paid(self):
        model = classify_opencode(opencode_row("paid-chat", text_in=1.5, text_out=4))
        self.assertEqual(model["classification"], "paid")
        self.assertIn("text.input.price", model["reason"])

    def test_missing_price_is_unknown_not_free(self):
        model = classify_opencode({"id": "mystery", "type": "chat", "cost": {}})
        self.assertEqual(model["classification"], "unknown")

    def test_non_chat_zero_price_is_unknown(self):
        model = classify_opencode(opencode_row("image-model", kind="image"))
        self.assertEqual(model["classification"], "unknown")

    def test_positive_image_price_makes_chat_paid(self):
        model = classify_opencode(opencode_row(
            "chat-with-image-fee",
            image={"input": {"price": 0.02}, "output": {"price": 0}},
        ))
        self.assertEqual(model["classification"], "paid")
        self.assertIn("image.input.price", model["other_costs"])

    def test_positive_cache_price_makes_chat_paid(self):
        row = opencode_row("cache-fee")
        row["cost"]["text"]["input"]["cache"] = {"read": 0.01, "write": 0}
        model = classify_opencode(row)
        self.assertEqual(model["classification"], "paid")
        self.assertIn("text.input.cache.read", model["other_costs"])

    def test_reasoning_request_and_search_costs_are_checked(self):
        for field in ("reasoning", "request", "web_search"):
            with self.subTest(field=field):
                row = opencode_row("fee-" + field, **{field: {"price": 0.5}})
                self.assertEqual(classify_opencode(row)["classification"], "paid")

    def test_tiered_pricing_is_paid(self):
        row = opencode_row("tiered")
        row["cost"]["text"]["input"]["tiers"] = [{"length": 1000, "price": 9}]
        model = classify_opencode(row)
        self.assertEqual(model["classification"], "paid")
        self.assertIn("tier", model["reason"])

    def test_dynamic_price_is_unknown(self):
        row = opencode_row("dynamic", text_in=-1)
        model = classify_opencode(row)
        self.assertEqual(model["classification"], "unknown")

    def test_free_suffix_does_not_override_price(self):
        model = classify_opencode(opencode_row("model:free", text_in=2))
        self.assertEqual(model["classification"], "paid")

    def test_funded_account_and_quota_flags(self):
        row = opencode_row("funded")
        row["requires_funded_account"] = True
        self.assertEqual(classify_opencode(row)["classification"], "funded_account_required")
        row2 = opencode_row("quota")
        row2["quota_limited"] = True
        self.assertEqual(classify_opencode(row2)["classification"], "quota_free")

    def test_privacy_retention_flag(self):
        row = opencode_row("trains-on-input")
        row["training_opt_in"] = True
        self.assertTrue(classify_opencode(row)["privacy_retention"])


class OpenRouterClassificationTests(unittest.TestCase):
    def test_zero_price_is_quota_free(self):
        model = classify_openrouter(openrouter_row())
        self.assertEqual(model["classification"], "quota_free")
        self.assertTrue(model["privacy_retention"])

    def test_positive_price_is_paid(self):
        model = classify_openrouter(openrouter_row(prompt="0.000001"))
        self.assertEqual(model["classification"], "paid")

    def test_internal_reasoning_fee_is_paid(self):
        model = classify_openrouter(openrouter_row(internal_reasoning="0.0001"))
        self.assertEqual(model["classification"], "paid")

    def test_tiered_pricing_is_paid(self):
        row = openrouter_row()
        row["pricing"]["tiers"] = [{"price": 1}]
        self.assertEqual(classify_openrouter(row)["classification"], "paid")

    def test_missing_price_is_unknown(self):
        row = openrouter_row()
        row["pricing"] = {}
        self.assertEqual(classify_openrouter(row)["classification"], "unknown")

    def test_negative_dynamic_price_is_unknown(self):
        model = classify_openrouter(openrouter_row(prompt="-1"))
        self.assertEqual(model["classification"], "unknown")

    def test_image_only_model_is_unknown(self):
        row = openrouter_row()
        row["architecture"] = {"output_modalities": ["image"]}
        self.assertEqual(classify_openrouter(row)["classification"], "unknown")


class ModelsDevVetoTests(unittest.TestCase):
    def test_positive_cost_becomes_veto(self):
        payload = {"anthropic": {"models": {"claude-x": {"cost": {"input": 3.0}}}}}
        vetoes = discover.extract_models_dev_vetoes(payload)
        self.assertIn(("anthropic", "claude-x"), vetoes)
        self.assertIn("models_dev_veto", vetoes[("anthropic", "claude-x")])

    def test_zero_cost_is_not_a_veto(self):
        payload = {"vendor": {"models": {"free": {"cost": {"input": 0, "output": 0}}}}}
        self.assertEqual(discover.extract_models_dev_vetoes(payload), {})

    def test_tiered_cost_is_a_veto(self):
        payload = {"vendor": {"models": {"tiered": {"cost": {"tiers": [1, 2]}}}}}
        self.assertIn(("vendor", "tiered"), discover.extract_models_dev_vetoes(payload))


class LocalVerificationTests(unittest.TestCase):
    def test_loopback_urls(self):
        for url in ("http://127.0.0.1:11434", "http://localhost:1234",
                    "tcp://[::1]:11434", "unix:///tmp/ollama.sock"):
            with self.subTest(url=url):
                self.assertTrue(discover.is_loopback_url(url))

    def test_cloud_url_is_not_local(self):
        self.assertFalse(discover.is_loopback_url("https://api.ollama.cloud/v1"))
        self.assertFalse(discover.is_loopback_url("http://192.168.1.10:11434"))

    def test_lan_only_with_flag(self):
        url = "http://192.168.1.10:11434"
        self.assertTrue(discover.is_loopback_url(url, allow_lan=True))

    def test_configured_local_provider_must_have_local_base_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "opencode.jsonc"
            config.write_text(
                '{\n  "providers": {\n'
                '    "ollama-local": { "baseURL": "http://127.0.0.1:11434/v1" },\n'
                '    "ollama-cloud": { "baseURL": "https://ollama.example.com/v1" }\n'
                '  }\n}\n',
                encoding="utf-8",
            )
            found = discover.load_configured_local_providers(config, allow_lan=False)
            self.assertEqual(found, {"ollama-local"})


class HelpersTests(unittest.TestCase):
    def test_runtime_variants_are_normalized(self):
        keys = discover.runtime_refs_to_keys(["opencode/model#high", "opencode/model"])
        self.assertEqual(keys, {("opencode", "model")})

    def test_summary_counts(self):
        counts = discover.summarize([
            {"classification": "verified_zero"},
            {"classification": "paid"},
            {"classification": "paid"},
            {"classification": "unknown"},
        ])
        self.assertEqual(counts["verified_zero"], 1)
        self.assertEqual(counts["paid"], 2)
        self.assertEqual(counts["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
