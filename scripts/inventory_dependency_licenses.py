"""Record installed Python and locked npm license metadata; never read user config."""
import importlib.metadata
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    python = []
    for package in importlib.metadata.distributions():
        meta = package.metadata
        license_id = meta.get("License-Expression")
        if not license_id:
            license_id = "; ".join(c.removeprefix("License :: ") for c in meta.get_all("Classifier", []) if c.startswith("License :: "))
        if not license_id:
            license_id = meta.get("License") or "UNSPECIFIED"
        if license_id == "UNSPECIFIED" and meta["Name"].lower() == "peewee":
            license_id = "MIT (reviewed installed LICENSE; upstream coleifer/peewee LICENSE)"
        if license_id == "OSI Approved" and meta["Name"].lower() == "prometheus-fastapi-instrumentator":
            license_id = "ISC (reviewed installed LICENSE)"
        python.append({"name": meta["Name"], "version": package.version, "license_metadata": license_id})
    lock = json.loads((ROOT / "apps/frontend/package-lock.json").read_text(encoding="utf-8"))
    npm = [{"package_path": name, "version": data.get("version"), "license_metadata": data.get("license", "UNSPECIFIED")}
           for name, data in lock["packages"].items() if name]
    target = ROOT / "licenses"
    target.mkdir(exist_ok=True)
    (target / "dependencies.json").write_text(json.dumps({
        "scope": "source-only; Python installed dev/runtime environment and npm lock (including platform-optional packages)",
        "limitations": "Package metadata is not a legal compatibility opinion. Installed binaries are not redistributed by this source snapshot. Preserve upstream licenses in binary/container distributions.",
        "python": sorted(python, key=lambda p: p['name'].lower()), "npm": npm,
    }, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({"python_packages": len(python), "npm_lock_entries": len(npm), "python_unspecified": [p['name'] for p in python if p['license_metadata']=='UNSPECIFIED'], "npm_unspecified": [p['package_path'] for p in npm if p['license_metadata']=='UNSPECIFIED']}))


if __name__ == "__main__":
    main()
