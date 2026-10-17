from __future__ import annotations

from app.services.progress_tracker import WORKFLOW_STAGES


def test_committee_progress_tracks_all_six_seats_for_two_rounds():
    keys = {stage["key"] for stage in WORKFLOW_STAGES["committee_debate"]}

    for role in ("baseline", "probability", "risk", "structure", "audit", "pnl"):
        assert f"round_1_{role}" in keys
        assert f"round_2_{role}" in keys

    assert "adjudication" in keys
    assert "persist" in keys
