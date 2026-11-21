import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import discover  # noqa: E402


def opencode(model_id, kind="chat", text_in=0, text_out=0, **extra_cost):
    cost = {
        "text": {
            "input": {"price": text_in},
            "output": {"price": text_out},
        }
    }
    cost.update(extra_cost)
    return {"id": model_id, "type": kind, "cost": cost}


class OpenCodeClassificationTests(unittest.TestCase):
    def test_zero_chat_is_verified_zero(self):
        model = discover.classify_opencode(opencode("free-chat"))
        self.assertEqual(model["classification"], "verified_zero")
        self.assertEqual(model["price"]["input"], 0)
        self.assertEqual(model["price"]["output"], 0)

    def test_positive_text_price_is_paid(self):
        model = discover.classify_opencode(opencode("paid-chat", text_in=1.5, text_out=4))
        self.assertEqual(model["classification"], "paid")

    def test_missing_price_is_unknown_not_free(self):
        model = discover.classify_opencode({"id": "mystery", "type": "chat", "cost": {}})
        self.assertEqual(model["classification"], "unknown")
        self.assertIsNone(model["price"])

    def test_non_chat_zero_price_is_unknown(self):
        model = discover.classify_opencode(opencode("image-model", kind="image"))
        self.assertEqual(model["classification"], "unknown")

    def test_positive_image_price_makes_chat_paid(self):
        raw = opencode(
            "chat-with-image-fee",
            image={"input": {"price": 0.02}, "output": {"price": 0}},
        )
        model = discover.classify_opencode(raw)
        self.assertEqual(model["classification"], "paid")
        self.assertIn("image.input", model["other_costs"])

    def test_positive_cache_price_makes_chat_paid(self):
        raw = opencode("cache-fee")
        raw["cost"]["text"]["input"]["cache"] = {"read": 0.01, "write": 0}
        model = discover.classify_opencode(raw)
        self.assertEqual(model["classification"], "paid")
        self.assertIn("text.input.cache.read", model["other_costs"])

    def test_missing_id_is_ignored(self):
        self.assertIsNone(discover.classify_opencode({"type": "chat"}))


class OpenRouterClassificationTests(unittest.TestCase):
    def openrouter(self, model_id="or/free", prompt="0", completion="0", **pricing):
        return {
            "id": model_id,
            "name": model_id,
            "pricing": {"prompt": prompt, "completion": completion, **pricing},
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
        }

    def test_zero_price_is_quota_free(self):
        model = discover.classify_openrouter(self.openrouter())
        self.assertEqual(model["classification"], "quota_free")
        self.assertTrue(model["auth_required"])

    def test_positive_price_is_paid(self):
        model = discover.classify_openrouter(
            self.openrouter(prompt="0.000001", completion="0.000002")
        )
        self.assertEqual(model["classification"], "paid")

    def test_free_suffix_does_not_override_positive_price(self):
        model = discover.classify_openrouter(
            self.openrouter(model_id="vendor/model:free", prompt="0.000001")
        )
        self.assertEqual(model["classification"], "paid")

    def test_missing_price_is_unknown(self):
        raw = self.openrouter()
        raw["pricing"] = {}
        model = discover.classify_openrouter(raw)
        self.assertEqual(model["classification"], "unknown")

    def test_negative_dynamic_price_is_unknown(self):
        model = discover.classify_openrouter(
            self.openrouter(prompt="-1", completion="0")
        )
        self.assertEqual(model["classification"], "unknown")

    def test_image_only_model_is_unknown(self):
        raw = self.openrouter()
        raw["architecture"] = {
            "input_modalities": ["text"],
            "output_modalities": ["image"],
        }
        model = discover.classify_openrouter(raw)
        self.assertEqual(model["classification"], "unknown")


class RuntimeMergeTests(unittest.TestCase):
    def test_runtime_only_model_becomes_unknown(self):
        models = [
            {
                "provider_id": "opencode",
                "model_id": "known-free",
                "classification": "verified_zero",
            }
        ]
        merged = discover.merge_runtime_models(
            models,
            ["opencode/known-free", "opencode/runtime-only"],
            "2026-11-21T00:00:00Z",
        )
        by_id = {m["model_id"]: m for m in merged}
        self.assertEqual(by_id["known-free"]["classification"], "verified_zero")
        self.assertEqual(by_id["runtime-only"]["classification"], "unknown")
        self.assertEqual(by_id["runtime-only"]["kind"], "other")

    def test_runtime_variants_are_normalized(self):
        merged = discover.merge_runtime_models(
            [], ["opencode/model#high", "opencode/model"], "2026-11-21T00:00:00Z"
        )
        self.assertEqual([m["model_id"] for m in merged], ["model"])

    def test_runtime_entries_without_provider_are_ignored(self):
        merged = discover.merge_runtime_models([], ["bare-model"], "2026-11-21T00:00:00Z")
        self.assertEqual(merged, [])


class BuildDiscoveryTests(unittest.TestCase):
    def test_dedupe_keeps_exact_ids(self):
        models = [
            {"provider_id": "opencode", "model_id": "ABC"},
            {"provider_id": "opencode", "model_id": "abc"},
            {"provider_id": "opencode", "model_id": "ABC"},
        ]
        result = discover.dedupe(models)
        self.assertEqual([m["model_id"] for m in result], ["ABC", "abc"])

    def test_summary_counts(self):
        models = [
            {"classification": "verified_zero"},
            {"classification": "paid"},
            {"classification": "paid"},
            {"classification": "unknown"},
        ]
        counts = discover.summarize(models)
        self.assertEqual(counts["verified_zero"], 1)
        self.assertEqual(counts["paid"], 2)
        self.assertEqual(counts["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
