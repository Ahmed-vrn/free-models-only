import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLY = ROOT / "scripts" / "apply.py"
sys.path.insert(0, str(ROOT / "scripts"))

from apply import parse_jsonc  # noqa: E402

LEGACY_BEGIN = "  // >>> free-models-only: START (managed — do not edit manually)"
LEGACY_END = "  // <<< free-models-only: END"
V3_BEGIN = "  // BEGIN free-models-only"
V3_END = "  // END free-models-only"

CONFIG_TEMPLATE = """{
  // keep this comment
  "$schema": "https://opencode.ai/config.json",
  "model": "%(default)s",
  "small_model": "%(small)s",
  "shell": "/bin/bash",
%(begin)s
  "providers": {
    "opencode": {
      "models": {
        "old-paid": { "disabled": true }
      }
    }
  },
%(end)s
  "permissions": []
}
"""


def config_text(default="opencode/free-model", small="opencode/free-model",
                begin=LEGACY_BEGIN, end=LEGACY_END, extra=""):
    text = CONFIG_TEMPLATE % {"default": default, "small": small, "begin": begin, "end": end}
    if extra:
        index = text.rstrip().rfind("}")
        text = text[:index] + extra + text[index:]
    return text


def discovery(models):
    return {"schema_version": "3.0", "mode": "free-only", "sources": [], "models": models}


BASE_MODELS = [
    {"provider_id": "opencode", "model_id": "free-model", "classification": "verified_zero",
     "kind": "chat", "privacy_retention": False},
    {"provider_id": "opencode", "model_id": "paid-model", "classification": "paid", "kind": "chat"},
    {"provider_id": "opencode", "model_id": "unknown-model", "classification": "unknown", "kind": "chat"},
    {"provider_id": "openrouter", "model_id": "or-paid", "classification": "paid", "kind": "chat"},
]


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.config = self.dir / "opencode.jsonc"
        self.config.write_text(config_text(), encoding="utf-8")
        self.disc = self.dir / "discovery.json"
        self.disc.write_text(json.dumps(discovery(BASE_MODELS)), encoding="utf-8")
        self.state = self.dir / "state.json"

    def tearDown(self):
        self.temp.cleanup()

    def run_apply(self, *args):
        return subprocess.run(
            [sys.executable, str(APPLY), "--config", str(self.config),
             "--state", str(self.state), *args],
            capture_output=True,
            text=True,
        )

    def parsed(self):
        return parse_jsonc(self.config.read_text(encoding="utf-8"))

    def test_apply_disables_paid_and_unknown_and_preserves_comments(self):
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.config.read_text(encoding="utf-8")
        models = self.parsed()["providers"]["opencode"]["models"]
        self.assertTrue(models["paid-model"]["disabled"])
        self.assertTrue(models["unknown-model"]["disabled"])
        self.assertNotIn("free-model", models)
        self.assertTrue(models["old-paid"]["disabled"])
        self.assertNotIn("openrouter", self.parsed()["providers"])
        self.assertEqual(self.parsed()["model"], "opencode/free-model")
        self.assertIn("// keep this comment", text)
        self.assertIn('"shell": "/bin/bash"', text)

    def test_v3_markers_are_preserved(self):
        self.config.write_text(config_text(begin=V3_BEGIN, end=V3_END), encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.config.read_text(encoding="utf-8")
        self.assertIn(V3_BEGIN, text)
        self.assertIn(V3_END, text)
        self.assertNotIn(LEGACY_BEGIN, text)

    def test_dry_run_does_not_write(self):
        before = self.config.read_text(encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.config.read_text(encoding="utf-8"))
        self.assertFalse(self.state.exists())

    def test_default_model_is_protected(self):
        self.config.write_text(config_text(default="opencode/paid-model"), encoding="utf-8")
        before = self.config.read_text(encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 4)
        self.assertIn("would be disabled", result.stderr)
        self.assertEqual(before, self.config.read_text(encoding="utf-8"))

    def test_small_model_is_protected(self):
        self.config.write_text(config_text(small="opencode/paid-model"), encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 4)
        self.assertIn("small_model", result.stderr)

    def test_agent_model_is_protected(self):
        extra = (
            ',\n  "agent": {\n'
            '    "build": { "model": "opencode/paid-model" }\n'
            '  }\n'
        )
        self.config.write_text(config_text(extra=extra), encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 4)
        self.assertIn("agent.build.model", result.stderr)

    def test_tombstone_from_state_survives_disappearance(self):
        self.state.write_text(json.dumps({
            "schema_version": 1,
            "disabled_models": [{"provider_id": "opencode", "model_id": "ghost-paid"}],
        }), encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.parsed()["providers"]["opencode"]["models"]["ghost-paid"]["disabled"])

    def test_allowed_model_is_removed_from_tombstones(self):
        self.state.write_text(json.dumps({
            "schema_version": 1,
            "disabled_models": [{"provider_id": "opencode", "model_id": "free-model"}],
        }), encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("free-model", self.parsed()["providers"]["opencode"]["models"])
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertNotIn("free-model", [x["model_id"] for x in saved["disabled_models"]])

    def test_require_zero_retention_disables_privacy_models(self):
        models = BASE_MODELS + [{
            "provider_id": "opencode", "model_id": "trainer", "classification": "verified_zero",
            "kind": "chat", "privacy_retention": True,
        }]
        self.disc.write_text(json.dumps(discovery(models)), encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply", "--require-zero-retention")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.parsed()["providers"]["opencode"]["models"]["trainer"]["disabled"])

    def test_include_unconfigured_providers(self):
        result = self.run_apply("--discovery", str(self.disc), "--apply",
                                "--include-unconfigured-providers")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.parsed()["providers"]["openrouter"]["models"]["or-paid"]["disabled"])

    def test_quota_free_is_kept_unless_strict(self):
        models = BASE_MODELS + [{
            "provider_id": "opencode", "model_id": "quota-model", "classification": "quota_free",
            "kind": "chat",
        }]
        self.disc.write_text(json.dumps(discovery(models)), encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("quota-model", self.parsed()["providers"]["opencode"]["models"])
        result = self.run_apply("--discovery", str(self.disc), "--apply", "--disable-quota-free")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.parsed()["providers"]["opencode"]["models"]["quota-model"]["disabled"])

    def test_legacy_paid_file_still_works(self):
        paid = self.dir / "paid.txt"
        paid.write_text("legacy-paid\n", encoding="utf-8")
        result = self.run_apply("--paid-file", str(paid), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.parsed()["providers"]["opencode"]["models"]["legacy-paid"]["disabled"])

    def test_symlink_is_refused_by_default(self):
        link = self.dir / "link.jsonc"
        link.symlink_to(self.config)
        result = subprocess.run(
            [sys.executable, str(APPLY), "--config", str(link),
             "--state", str(self.state), "--discovery", str(self.disc), "--apply"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("symlink", result.stderr.lower())

    def test_backup_is_created(self):
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(list(self.dir.glob("opencode.jsonc.bak.*")))

    def test_missing_markers_is_an_error(self):
        self.config.write_text('{"model": "opencode/free-model"}\n', encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 3)
        self.assertIn("managed block", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
