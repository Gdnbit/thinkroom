"""Exercise the publication boundary with synthetic files, never real secrets."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("public_check", ROOT / "scripts/check_public_source.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class PublicSourceBoundaryTests(unittest.TestCase):
    def make_source(self, root):
        files = {name: "synthetic\n" for name in ["README.md", "LICENSE", "SECURITY.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md", "examples/demo-evidence.txt"]}
        files['apps/backend/requirements.txt'] = 'pypdfium2==5.10.1\n'
        files['scripts/update_public_manifest.py'] = (ROOT / 'scripts/update_public_manifest.py').read_text(encoding='utf-8')
        for name, content in files.items():
            p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content, encoding='utf-8')
        return files

    def test_source_tampering_and_unreviewed_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); files = self.make_source(root)
            hashes = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}
            (root/'PUBLIC_SOURCE_MANIFEST.json').write_text(json.dumps({'files':hashes}), encoding='utf-8')
            self.assertEqual(CHECK.audit_public(root)['status'], 'pass')
            (root/'README.md').write_text('unexpected change', encoding='utf-8')
            (root/'extra.py').write_text('print(1)', encoding='utf-8')
            codes = {x['code'] for x in CHECK.audit_public(root)['issues']}
            self.assertIn('source_changed', codes)
            self.assertIn('unreviewed_source', codes)

    def test_private_credentials_cannot_be_added_to_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); files=self.make_source(root)
            (root/'credentials.key').write_text('synthetic non-secret', encoding='utf-8')
            files['credentials.key']=''
            hashes={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}
            (root/'PUBLIC_SOURCE_MANIFEST.json').write_text(json.dumps({'files':hashes}), encoding='utf-8')
            self.assertIn('private_file', {x['code'] for x in CHECK.audit_public(root)['issues']})


if __name__ == '__main__':
    unittest.main()
