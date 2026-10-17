"""The portable starter must not forward unrelated application secrets."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("starter", Path(__file__).resolve().parents[1] / "start_backend.py")
STARTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STARTER)


class BackendEnvironmentTests(unittest.TestCase):
    def test_only_backend_credentials_and_os_essentials_are_forwarded(self):
        with patch.dict(os.environ, {"PATH":"synthetic-path", "OPENROUTER_API_KEY":"synthetic-test-value", "OTHER_APPLICATION_TOKEN":"synthetic-unrelated-value"}, clear=True):
            env = STARTER.backend_environment()
        self.assertIn("PATH", env)
        self.assertIn("OPENROUTER_API_KEY", env)
        self.assertNotIn("OTHER_APPLICATION_TOKEN", env)

    def test_caller_provenance_is_preserved_only_for_backend_settings(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY_SOURCE":"vault:synthetic", "OTHER_APPLICATION_TOKEN_SOURCE":"vault:other"}, clear=True):
            env = STARTER.backend_environment()
        self.assertEqual(env["OPENROUTER_API_KEY_SOURCE"], "vault:synthetic")
        self.assertNotIn("OTHER_APPLICATION_TOKEN_SOURCE", env)


if __name__ == '__main__':
    unittest.main()
