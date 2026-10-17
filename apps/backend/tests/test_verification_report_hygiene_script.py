from __future__ import annotations

import importlib.util
from pathlib import Path


DUMMY_OPENROUTER_TOKEN = "sk-or-v1-" + "should-not-be-read"


def _load_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "check_verification_reports.py"
    spec = importlib.util.spec_from_file_location("check_verification_reports", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verification_report_hygiene_flags_bom_and_invalid_json(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)

    (report_dir / "good-latest.json").write_text('{"status":"pass"}', encoding="utf-8")
    (report_dir / "bom-latest.json").write_bytes(b"\xef\xbb\xbf{\"status\":\"pass\"}")
    (report_dir / "bad-latest.json").write_text("{not-json", encoding="utf-8")

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 3
    assert {issue["code"] for issue in report["issues"]} == {
        "utf8_bom",
        "invalid_json",
    }


def test_verification_report_hygiene_scans_underscore_latest_json(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)

    (report_dir / "main_workflow_eval_runner_latest.json").write_text(
        f'{{"accidental":"{DUMMY_OPENROUTER_TOKEN}"}}',
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 1
    assert report["json_checked_count"] == 1
    assert report["json_patterns"] == [
        "*-latest.json",
        "*_latest.json",
        "main_flow_runtime_smoke*.json",
    ]
    assert {issue["path"] for issue in report["issues"]} == {
        "main_workflow_eval_runner_latest.json"
    }
    assert {issue["code"] for issue in report["issues"]} == {
        "blocked_marker",
        "secret_shape_marker",
    }


def test_verification_report_hygiene_flags_sensitive_markers_but_allows_local_context(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)

    (report_dir / "local-latest.json").write_text(
        '{"endpoint":"http://127.0.0.1:8000","path":"C:/example/demo"}',
        encoding="utf-8",
    )
    (report_dir / "leak-latest.json").write_text(
        f'{{"accidental":"{DUMMY_OPENROUTER_TOKEN}","bad":"dev-secret-key"}}',
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 2
    assert [issue["code"] for issue in report["issues"]] == [
        "blocked_marker",
        "secret_shape_marker",
    ]
    assert set(report["issues"][0]["markers"]) == {
        "dev-secret-key",
        DUMMY_OPENROUTER_TOKEN,
    }
    assert report["issues"][1] == {
        "path": "leak-latest.json",
        "code": "secret_shape_marker",
        "message": "Verification artifact contains a secret-like token shape.",
        "markers": ["openrouter_api_key_shape"],
    }


def test_verification_report_hygiene_scans_historical_main_flow_private_excerpts(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)
    (report_dir / "main_flow_runtime_smoke_20260711T010203Z.json").write_text(
        '{"steps":{"chat":{"content_head":"private answer fragment","content_length":23}}}',
        encoding="utf-8",
    )
    (report_dir / "main_flow_runtime_smoke_latest.json").write_text(
        '{"steps":{"chat":{"content_hash":"sha256:abc","content_length":23}}}',
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["json_checked_count"] == 2
    assert report["issues"] == [{
        "path": "main_flow_runtime_smoke_20260711T010203Z.json",
        "code": "private_answer_excerpt",
        "message": "Main-flow smoke report stores a private answer excerpt; retain only a hash and length.",
        "field_paths": ["$.steps.chat.content_head"],
    }]


def test_verification_report_hygiene_flags_local_backend_url_in_public_release_artifacts(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)

    (report_dir / "main_flow_runtime_smoke_latest.json").write_text(
        '{"base_url":"http://127.0.0.1:8000"}',
        encoding="utf-8",
    )
    (report_dir / "release-gate-latest.json").write_text(
        '{"verification_commands":["python scripts/run_release_gate.py --backend-url http://127.0.0.1:8000"]}',
        encoding="utf-8",
    )
    (report_dir / "production-readiness-action-plan-latest.md").write_text(
        "# Production Readiness Action Plan\n\n"
        "Verify: `python scripts/run_release_gate.py --backend-url http://localhost:8000`\n",
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 3
    assert [issue["code"] for issue in report["issues"]] == [
        "local_backend_url",
        "local_backend_url",
    ]
    assert {issue["path"] for issue in report["issues"]} == {
        "release-gate-latest.json",
        "production-readiness-action-plan-latest.md",
    }
    assert "127.0.0.1" not in str(report)
    assert "localhost" not in str(report)


def test_verification_report_hygiene_flags_openrouter_key_shapes_without_echoing_token(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)
    token = "sk-or-v1-" + "differenttokenvaluethatmustnotbereported"

    (report_dir / "shape-latest.json").write_text(
        f'{{"accidental":"Bearer {token}"}}',
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 1
    assert report["issues"] == [
        {
            "path": "shape-latest.json",
            "code": "secret_shape_marker",
            "message": "Verification artifact contains a secret-like token shape.",
            "markers": ["openrouter_api_key_shape"],
        }
    ]
    assert token not in str(report)


def test_verification_report_hygiene_flags_credential_uris_without_echoing_secret(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)
    credential_uri = "postgresql://reportuser:" + "super-secret-password@postgres:5432/agent_db"

    (report_dir / "uri-latest.json").write_text(
        f'{{"accidental_database_url":"{credential_uri}"}}',
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 1
    assert report["issues"] == [
        {
            "path": "uri-latest.json",
            "code": "secret_shape_marker",
            "message": "Verification artifact contains a secret-like token shape.",
            "markers": ["uri_embedded_credentials"],
        }
    ]
    assert "super-secret-password" not in str(report)


def test_verification_report_hygiene_flags_sops_blobs_without_echoing_payload(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)
    blob = "ENC[" + "AES256_GCM,data:sensitiveblobpayload,iv:abc,tag:def,type:str]"

    (report_dir / "sops-latest.json").write_text(
        f'{{"encrypted":"{blob}"}}',
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 1
    assert report["issues"] == [
        {
            "path": "sops-latest.json",
            "code": "secret_shape_marker",
            "message": "Verification artifact contains a secret-like token shape.",
            "markers": ["sops_encrypted_blob"],
        }
    ]
    assert "sensitiveblobpayload" not in str(report)


def test_verification_report_hygiene_scans_release_action_plan_markdown(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    runbook = tmp_path / "docs" / "operations" / "PRODUCTION_SECRET_SOURCE_PROVENANCE.md"
    report_dir.mkdir(parents=True)
    runbook.parent.mkdir(parents=True)
    runbook.write_text("# Production Secret Source Provenance Runbook\n", encoding="utf-8")

    (report_dir / "release-gate-latest.json").write_text('{"status":"blocked"}', encoding="utf-8")
    (report_dir / "release-gate-action-plan-latest.md").write_bytes(
        b"\xef\xbb\xbf# Release Gate Action Plan\n\n"
        b"## Source Provenance Runbook\n\n"
        b"- Runbook: `docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md`\n\n"
        + DUMMY_OPENROUTER_TOKEN.encode("utf-8")
        + b"\n"
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 2
    assert report["json_checked_count"] == 1
    assert report["text_checked_count"] == 1
    assert {issue["code"] for issue in report["issues"]} == {
        "blocked_marker",
        "secret_shape_marker",
        "utf8_bom",
    }
    assert {issue["path"] for issue in report["issues"]} == {
        "release-gate-action-plan-latest.md",
    }


def test_verification_report_hygiene_scans_release_secret_inventory_markdown(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    runbook = tmp_path / "docs" / "operations" / "PRODUCTION_SECRET_SOURCE_PROVENANCE.md"
    report_dir.mkdir(parents=True)
    runbook.parent.mkdir(parents=True)
    runbook.write_text("# Production Secret Source Provenance Runbook\n", encoding="utf-8")

    (report_dir / "release-gate-latest.json").write_text('{"status":"blocked"}', encoding="utf-8")
    (report_dir / "release-gate-action-plan-latest.md").write_text(
        "# Release Gate Action Plan\n\n"
        "## Source Provenance Runbook\n\n"
        "- Runbook: `docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md`\n",
        encoding="utf-8",
    )
    (report_dir / "release-gate-secret-inventory-latest.md").write_text(
        "# Release Gate Secret Inventory\n\n"
        "## Source Provenance Runbook\n\n"
        "- Runbook: `docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md`\n\n"
        f"{DUMMY_OPENROUTER_TOKEN}\n",
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 3
    assert report["json_checked_count"] == 1
    assert report["text_checked_count"] == 2
    assert [issue["code"] for issue in report["issues"]] == [
        "blocked_marker",
        "secret_shape_marker",
    ]
    assert {issue["path"] for issue in report["issues"]} == {
        "release-gate-secret-inventory-latest.md",
    }


def test_verification_report_hygiene_scans_production_readiness_action_plan_markdown(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)

    (report_dir / "production-readiness-summary-latest.json").write_text('{"status":"blocked"}', encoding="utf-8")
    (report_dir / "production-readiness-action-plan-latest.md").write_text(
        "# Production Readiness Action Plan\n\n" f"{DUMMY_OPENROUTER_TOKEN}\n",
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 2
    assert report["json_checked_count"] == 1
    assert report["text_checked_count"] == 1
    assert [issue["code"] for issue in report["issues"]] == [
        "blocked_marker",
        "secret_shape_marker",
    ]
    assert {issue["path"] for issue in report["issues"]} == {
        "production-readiness-action-plan-latest.md",
    }


def test_verification_report_hygiene_flags_missing_release_action_plan_runbook(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)

    (report_dir / "release-gate-latest.json").write_text('{"status":"blocked"}', encoding="utf-8")
    (report_dir / "release-gate-action-plan-latest.md").write_text(
        "# Release Gate Action Plan\n\n"
        "## Source Provenance Runbook\n\n"
        "- Runbook: `docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md`\n",
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 2
    assert report["issues"] == [
        {
            "path": "release-gate-action-plan-latest.md",
            "code": "missing_runbook_link",
            "message": "Release action plan references an operational runbook that does not exist.",
            "runbook_path": "docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md",
        }
    ]


def test_verification_report_hygiene_flags_missing_secret_inventory_runbook(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    runbook = tmp_path / "docs" / "operations" / "PRODUCTION_SECRET_SOURCE_PROVENANCE.md"
    report_dir.mkdir(parents=True)
    runbook.parent.mkdir(parents=True)
    runbook.write_text("# Production Secret Source Provenance Runbook\n", encoding="utf-8")

    (report_dir / "release-gate-latest.json").write_text('{"status":"blocked"}', encoding="utf-8")
    (report_dir / "release-gate-action-plan-latest.md").write_text(
        "# Release Gate Action Plan\n\n"
        "## Source Provenance Runbook\n\n"
        "- Runbook: `docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md`\n",
        encoding="utf-8",
    )
    (report_dir / "release-gate-secret-inventory-latest.md").write_text(
        "# Release Gate Secret Inventory\n\n"
        "## Source Provenance Runbook\n\n"
        "- Runbook: `docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md`\n\n"
        "- Operator runbook: `docs/operations/MISSING_SECRET_ROTATION.md`\n",
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 3
    assert report["issues"] == [
        {
            "path": "release-gate-secret-inventory-latest.md",
            "code": "missing_runbook_link",
            "message": "Verification text artifact references an operational runbook that does not exist.",
            "runbook_path": "docs/operations/MISSING_SECRET_ROTATION.md",
        }
    ]


def test_verification_report_hygiene_requires_secret_inventory_source_provenance_runbook(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    runbook = tmp_path / "docs" / "operations" / "PRODUCTION_SECRET_SOURCE_PROVENANCE.md"
    report_dir.mkdir(parents=True)
    runbook.parent.mkdir(parents=True)
    runbook.write_text("# Production Secret Source Provenance Runbook\n", encoding="utf-8")

    (report_dir / "release-gate-latest.json").write_text('{"status":"blocked"}', encoding="utf-8")
    (report_dir / "release-gate-action-plan-latest.md").write_text(
        "# Release Gate Action Plan\n\n"
        "## Source Provenance Runbook\n\n"
        "- Runbook: `docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md`\n",
        encoding="utf-8",
    )
    (report_dir / "release-gate-secret-inventory-latest.md").write_text(
        "# Release Gate Secret Inventory\n\n"
        "## Handling Rules\n"
        "- Store real values only in SOPS or the deployment secret store.\n",
        encoding="utf-8",
    )

    report = module.check_verification_reports(report_dir)

    assert report["status"] == "fail"
    assert report["checked_count"] == 3
    assert report["issues"] == [
        {
            "path": "release-gate-secret-inventory-latest.md",
            "code": "missing_required_runbook_link",
            "message": "Verification text artifact is missing a required operational runbook link.",
            "runbook_path": "docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md",
        }
    ]


def test_verification_report_hygiene_cli_result_hides_absolute_paths(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)
    (report_dir / "good-latest.json").write_text('{"status":"pass"}', encoding="utf-8")

    report = module.check_verification_reports(report_dir)
    cli = module.public_cli_result(report)

    assert cli["status"] == "pass"
    assert cli["checked_count"] == 1
    assert str(tmp_path) not in str(cli)
