"""Verify privacy scanner output contracts without touching real credentials."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "audit_open_source", Path(__file__).resolve().parents[1] / "audit_open_source.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PrivacyScanTests(unittest.TestCase):
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
