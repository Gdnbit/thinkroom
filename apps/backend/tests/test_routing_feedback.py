"""B6 · routing_feedback endpoint 测试"""
import pytest


API_PREFIX = "/api/v1/routing-feedback"


class TestSubmitFeedback:
    def test_submit_thumbs_up(self, client):
        r = client.post(API_PREFIX, json={
            "message_id": "msg_001",
            "auto_route": "chat",
            "is_helpful": True,
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["message_id"] == "msg_001"
        assert data["auto_route"] == "chat"
        assert data["chosen_route"] == "chat"  # 未切换 → 等于 auto
        assert data["is_helpful"] is True
        assert data["id"]

    def test_submit_thumbs_down_with_switch(self, client):
        r = client.post(API_PREFIX, json={
            "message_id": "msg_002",
            "auto_route": "chat",
            "chosen_route": "briefing",
            "is_helpful": False,
            "message": "需要更深度的研究",
            "intent_mode": "asset_analysis",
            "intent_confidence": 0.6,
            "assets": ["BTC"],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["chosen_route"] == "briefing"
        assert data["is_helpful"] is False

    def test_submit_without_feedback_just_log_switch(self, client):
        # 用户切换但没表态 — 仍可记录
        r = client.post(API_PREFIX, json={
            "message_id": "msg_003",
            "auto_route": "briefing",
            "chosen_route": "chat",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["chosen_route"] == "chat"
        assert data["is_helpful"] is None

    def test_invalid_auto_route_rejected(self, client):
        r = client.post(API_PREFIX, json={
            "message_id": "msg_x", "auto_route": "invalid",
        })
        assert r.status_code == 400

    def test_invalid_chosen_route_rejected(self, client):
        r = client.post(API_PREFIX, json={
            "message_id": "msg_x", "auto_route": "chat",
            "chosen_route": "typo_mode",
        })
        assert r.status_code == 400

    def test_message_truncated_to_500(self, client):
        long_msg = "x" * 1000
        r = client.post(API_PREFIX, json={
            "message_id": "msg_004",
            "auto_route": "chat",
            "is_helpful": False,
            "message": long_msg,
        })
        assert r.status_code == 200
        # 查询反馈，验证 message 被截断
        q = client.get(f"{API_PREFIX}/by-message/msg_004")
        assert q.status_code == 200
        payload = q.json()["feedback"][0]["payload"]
        assert len(payload["message"]) == 500


class TestStats:
    def test_empty_stats(self, client):
        r = client.get(f"{API_PREFIX}/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_feedback"] == 0
        assert data["buckets"] == []
        assert data["top_switches"] == []

    def test_aggregated_stats(self, client):
        # 构造一组反馈：5 次 chat (3👍 1👎 1切换到briefing)，2 次 briefing (2👍)
        samples = [
            ("m1", "chat", "chat", True),
            ("m2", "chat", "chat", True),
            ("m3", "chat", "chat", True),
            ("m4", "chat", "chat", False),
            ("m5", "chat", "briefing", False),   # 切换
            ("m6", "briefing", "briefing", True),
            ("m7", "briefing", "briefing", True),
        ]
        for mid, auto, chosen, helpful in samples:
            client.post(API_PREFIX, json={
                "message_id": mid, "auto_route": auto,
                "chosen_route": chosen, "is_helpful": helpful,
            })

        r = client.get(f"{API_PREFIX}/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_feedback"] == 7

        buckets_by_route = {b["auto_route"]: b for b in data["buckets"]}
        # chat: 5 total, 3 helpful, 2 unhelpful, 1 switched
        chat = buckets_by_route["chat"]
        assert chat["total"] == 5
        assert chat["helpful"] == 3
        assert chat["unhelpful"] == 2
        assert chat["switched"] == 1
        assert chat["helpful_rate"] == round(3 / 5, 3)
        assert chat["accept_rate"] == round(4 / 5, 3)  # 4/5 未切换

        briefing = buckets_by_route["briefing"]
        assert briefing["total"] == 2
        assert briefing["helpful"] == 2
        assert briefing["switched"] == 0
        assert briefing["accept_rate"] == 1.0

        # top_switches
        assert {"from": "chat", "to": "briefing", "count": 1} in data["top_switches"]


class TestByMessage:
    def test_no_feedback_returns_empty(self, client):
        r = client.get(f"{API_PREFIX}/by-message/never_submitted")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_multiple_feedback_on_same_message(self, client):
        # 用户可能第一次 👎 然后又改主意 👍 — 两条都保留
        for helpful in (False, True):
            client.post(API_PREFIX, json={
                "message_id": "msg_revise",
                "auto_route": "chat",
                "is_helpful": helpful,
            })
        r = client.get(f"{API_PREFIX}/by-message/msg_revise")
        assert r.status_code == 200
        assert r.json()["count"] == 2
