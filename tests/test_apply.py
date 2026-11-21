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


CONFIG_TEMPLATE = """{
  // keep this comment
  "$schema": "https://opencode.ai/config.json",
  "model": "%(default)s",
  "shell": "/bin/bash",
  // >>> free-models-only: START (managed — do not edit manually)
  "providers": {
    "opencode": {
      "models": {
        "old-paid": { "disabled": true }
      }
    }
  },
  // <<< free-models-only: END
  "permissions": []
}
"""


def discovery(models):
    return {
        "schema_version": 1,
        "mode": "free-only",
        "sources": [],
        "models": models,
    }


BASE_MODELS = [
    {
        "provider_id": "opencode",
        "model_id": "free-model",
        "classification": "verified_zero",
        "kind": "chat",
    },
    {
        "provider_id": "opencode",
        "model_id": "paid-model",
        "classification": "paid",
        "kind": "chat",
    },
    {
        "provider_id": "opencode",
        "model_id": "unknown-model",
        "classification": "unknown",
        "kind": "chat",
    },
    {
        "provider_id": "openrouter",
        "model_id": "or-paid",
        "classification": "paid",
        "kind": "chat",
    },
]


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.config = self.dir / "opencode.jsonc"
        self.config.write_text(CONFIG_TEMPLATE % {"default": "opencode/free-model"}, encoding="utf-8")
        self.disc = self.dir / "discovery.json"
        self.disc.write_text(json.dumps(discovery(BASE_MODELS)), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_apply(self, *args):
        return subprocess.run(
            [sys.executable, str(APPLY), "--config", str(self.config), *args],
            capture_output=True,
            text=True,
        )

    def test_apply_disables_paid_and_unknown_and_preserves_comments(self):
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.config.read_text(encoding="utf-8")
        parsed = parse_jsonc(text)
        models = parsed["providers"]["opencode"]["models"]
        self.assertTrue(models["paid-model"]["disabled"])
        self.assertTrue(models["unknown-model"]["disabled"])
        self.assertNotIn("free-model", models)
        self.assertTrue(models["old-paid"]["disabled"])
        self.assertNotIn("openrouter", parsed["providers"])
        self.assertEqual(parsed["model"], "opencode/free-model")
        self.assertIn("// keep this comment", text)
        self.assertIn('"shell": "/bin/bash"', text)
        self.assertEqual(parsed["permissions"], [])

    def test_previous_disabled_free_model_is_reenabled(self):
        text = CONFIG_TEMPLATE % {"default": "opencode/free-model"}
        text = text.replace(
            '"old-paid": { "disabled": true }',
            '"old-paid": { "disabled": true },\n        "free-model": { "disabled": true }',
        )
        self.config.write_text(text, encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = parse_jsonc(self.config.read_text(encoding="utf-8"))
        models = parsed["providers"]["opencode"]["models"]
        self.assertNotIn("free-model", models)
        self.assertTrue(models["old-paid"]["disabled"])

    def test_dry_run_does_not_write(self):
        before = self.config.read_text(encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.config.read_text(encoding="utf-8"))

    def test_default_model_is_protected(self):
        self.config.write_text(
            CONFIG_TEMPLATE % {"default": "opencode/paid-model"}, encoding="utf-8"
        )
        before = self.config.read_text(encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 4)
        self.assertIn("refusing to disable", result.stderr.lower())
        self.assertEqual(before, self.config.read_text(encoding="utf-8"))

    def test_include_unconfigured_providers(self):
        result = self.run_apply(
            "--discovery",
            str(self.disc),
            "--apply",
            "--include-unconfigured-providers",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = parse_jsonc(self.config.read_text(encoding="utf-8"))
        self.assertIn("openrouter", parsed["providers"])
        self.assertTrue(parsed["providers"]["openrouter"]["models"]["or-paid"]["disabled"])

    def test_quota_free_is_kept_unless_strict(self):
        models = BASE_MODELS + [
            {
                "provider_id": "opencode",
                "model_id": "quota-model",
                "classification": "quota_free",
                "kind": "chat",
            }
        ]
        self.disc.write_text(json.dumps(discovery(models)), encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = parse_jsonc(self.config.read_text(encoding="utf-8"))
        self.assertNotIn("quota-model", parsed["providers"]["opencode"]["models"])

    def test_strict_mode_disables_quota_free(self):
        models = BASE_MODELS + [
            {
                "provider_id": "opencode",
                "model_id": "quota-model",
                "classification": "quota_free",
                "kind": "chat",
            }
        ]
        self.disc.write_text(json.dumps(discovery(models)), encoding="utf-8")
        result = self.run_apply(
            "--discovery", str(self.disc), "--apply", "--disable-quota-free"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = parse_jsonc(self.config.read_text(encoding="utf-8"))
        self.assertTrue(parsed["providers"]["opencode"]["models"]["quota-model"]["disabled"])

    def test_legacy_paid_file_still_works(self):
        paid = self.dir / "paid.txt"
        paid.write_text("legacy-paid\n", encoding="utf-8")
        result = self.run_apply("--paid-file", str(paid), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = parse_jsonc(self.config.read_text(encoding="utf-8"))
        self.assertTrue(parsed["providers"]["opencode"]["models"]["legacy-paid"]["disabled"])

    def test_symlink_is_refused_by_default(self):
        link = self.dir / "link.jsonc"
        link.symlink_to(self.config)
        result = subprocess.run(
            [sys.executable, str(APPLY), "--config", str(link), "--discovery", str(self.disc), "--apply"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("symlink", result.stderr.lower())

    def test_backup_is_created(self):
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list(self.dir.glob("opencode.jsonc.bak.*"))
        self.assertTrue(backups)

    def test_missing_markers_is_an_error(self):
        self.config.write_text('{"model": "opencode/free-model"}\n', encoding="utf-8")
        result = self.run_apply("--discovery", str(self.disc), "--apply")
        self.assertEqual(result.returncode, 3)
        self.assertIn("managed block", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
