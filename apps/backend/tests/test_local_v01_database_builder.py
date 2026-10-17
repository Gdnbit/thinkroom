import importlib.util
import json
import sqlite3
from pathlib import Path


def _load_builder_module():
    script = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "build_local_v01_database.py"
    spec = importlib.util.spec_from_file_location("build_local_v01_database_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selection_keeps_user_graph_and_only_referenced_system_support():
    module = _load_builder_module()
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE raw_sources (
            id TEXT PRIMARY KEY,
            title TEXT,
            storage_path TEXT
        );
        CREATE TABLE chat_sessions (
            id TEXT PRIMARY KEY,
            title TEXT
        );
        CREATE TABLE chat_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            payload TEXT
        );
        CREATE TABLE claim_cards (
            id TEXT PRIMARY KEY,
            parent_claim_id TEXT,
            speaker_id TEXT
        );
        CREATE TABLE claim_raw_source_links (
            claim_id TEXT,
            source_id TEXT
        );
        """
    )
    connection.executemany(
        "INSERT INTO raw_sources(id,title,storage_path) VALUES (?,?,?)",
        [
            ("source-user", "WEF_Global_Risks_Report_2026.pdf", "uploads/user.pdf"),
            ("source-support", "local-v01-live-evidence.txt", None),
            ("source-system-only", "main-flow-upload-smoke.txt", None),
        ],
    )
    connection.executemany(
        "INSERT INTO chat_sessions(id,title) VALUES (?,?)",
        [
            ("session-user", "分析一下最近石油的动向"),
            ("session-system", "live smoke 01ABC"),
        ],
    )
    connection.executemany(
        "INSERT INTO claim_cards(id,parent_claim_id,speaker_id) VALUES (?,?,?)",
        [
            ("claim-user", None, None),
            ("claim-support", None, None),
            ("claim-system-only", None, None),
        ],
    )
    connection.executemany(
        "INSERT INTO claim_raw_source_links(claim_id,source_id) VALUES (?,?)",
        [
            ("claim-user", "source-user"),
            ("claim-support", "source-support"),
            ("claim-system-only", "source-system-only"),
        ],
    )
    connection.executemany(
        "INSERT INTO chat_messages(id,session_id,payload) VALUES (?,?,?)",
        [
            (
                "message-user",
                "session-user",
                json.dumps(
                    {
                        "citations": [
                            {"claim_id": "claim-support", "source_id": "source-support"}
                        ]
                    }
                ),
            ),
            ("message-system", "session-system", "{}"),
        ],
    )

    keep = module.Selection(connection).compute()

    assert keep["chat_sessions"] == {"session-user"}
    assert keep["chat_messages"] == {"message-user"}
    assert keep["raw_sources"] == {"source-user", "source-support"}
    assert keep["claim_cards"] == {"claim-user", "claim-support"}
    connection.close()
