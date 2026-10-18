"""Verify privacy scanner output contracts without touching real credentials."""
import importlib.util
from pathlib import Path
import unittest
import json
import subprocess
import tempfile
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "audit_open_source", Path(__file__).resolve().parents[1] / "audit_open_source.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PrivacyScanTests(unittest.TestCase):
    def test_manifest_does_not_hide_git_history_or_other_tracked_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            git("init")
            git("config", "user.name", "Synthetic Test")
            git("config", "user.email", "test@example.invalid")
            (root / "PUBLIC_SOURCE_MANIFEST.json").write_text(json.dumps({"files": {}}), encoding="utf-8")
            (root / "note.txt").write_text("sk-" + "a" * 32, encoding="utf-8")
            git("add", ".")
            git("commit", "-m", "Synthetic scanner regression")
            git("rm", "note.txt")
            git("commit", "-m", "Remove fixture")
            with patch.object(MODULE, "ROOT", root):
                result = MODULE.audit(history=True)
            self.assertEqual(result["tracked_files"], 1)
            self.assertEqual(result["history"]["commits"], 2)
            self.assertTrue(any(f["path"] == "note.txt" for f in result["history"]["findings"]))

    def test_results_contain_locations_only(self):
        synthetic = b"sk-" + b"a" * 32
        result = MODULE.scan(b"first line\nTOKEN=" + synthetic + b"\n")
        self.assertEqual(result["credential_format"], [2])
        self.assertNotIn(synthetic.decode(), repr(result))

    def test_binary_content_is_not_treated_as_text(self):
        self.assertEqual(MODULE.scan(b"\x00user@example.com"), {})

    def test_templates_are_distinguished_from_secret_files(self):
        self.assertTrue(MODULE.SENSITIVE_PATH.search("apps/backend/tests/.env.test"))
        self.assertTrue(MODULE.SENSITIVE_PATH.search("credentials/key.pem"))
        self.assertFalse(MODULE.SENSITIVE_PATH.search("source/config.py"))

    def test_private_network_boundaries(self):
        self.assertIn("private_network", MODULE.scan(b"http://172.16.1.2"))
        self.assertNotIn("private_network", MODULE.scan(b"http://172.32.1.2"))
        self.assertNotIn("private_network", MODULE.scan(b"http://127.0.0.1"))


if __name__ == "__main__":
    unittest.main()
