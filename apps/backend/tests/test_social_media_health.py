"""G13 单元测试 · /api/v1/social-media/health 聚合端点

聚合逻辑：把 `get_cookie_status()` 5 个平台的明细折叠成
needs_attention / expired_count / expiring_soon_count / total_* + 简短列表。
不依赖真实 MediaCrawler 浏览器目录，所有测试 monkeypatch `get_cookie_status`。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
HEALTH_URL = "/api/v1/social-media/health"


def _stub_status(*, has_cookie=True, remaining_days=10, is_expired=False,
                 needs_refresh=False, source="manual_cookie", platform="twitter"):
    return {
        "platform": platform,
        "has_cookie": has_cookie,
        "updated_at": "2026-04-20T00:00:00+00:00",
        "estimated_ttl_days": 30,
        "expires_at": "2026-04-30T00:00:00+00:00",
        "remaining_days": remaining_days,
        "is_expired": is_expired,
        "needs_refresh": needs_refresh,
        "source": source,
    }


# ── 基础场景 ───────────────────────────────────────────────


def test_health_all_fresh_no_attention():
    fake = [
        _stub_status(platform="twitter", remaining_days=15),
        _stub_status(platform="weibo", remaining_days=20),
        _stub_status(platform="xhs", remaining_days=8, source="mediacrawler_browser"),
        _stub_status(platform="douyin", remaining_days=8, source="mediacrawler_browser"),
        _stub_status(platform="wechat", remaining_days=20, source="wemprss_api_token"),
    ]
    with patch("app.mcps.social_media_mcp.get_cookie_status", return_value=fake):
        r = client.get(HEALTH_URL)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["needs_attention"] is False
    assert body["expired_count"] == 0
    assert body["expiring_soon_count"] == 0
    assert body["total_configured"] == 5
    assert body["total_platforms"] == 5


def test_health_one_expired_triggers_attention():
    fake = [
        _stub_status(platform="twitter", is_expired=True, needs_refresh=True,
                     remaining_days=-2),
        _stub_status(platform="weibo", remaining_days=15),
        _stub_status(platform="xhs", remaining_days=8, source="mediacrawler_browser"),
        _stub_status(platform="douyin", remaining_days=8, source="mediacrawler_browser"),
        _stub_status(platform="wechat", has_cookie=False, source="none",
                     is_expired=True, needs_refresh=True, remaining_days=None),
    ]
    with patch("app.mcps.social_media_mcp.get_cookie_status", return_value=fake):
        body = client.get(HEALTH_URL).json()["data"]
    assert body["needs_attention"] is True  # twitter has_cookie + needs_refresh
    assert body["expired_count"] == 2  # twitter + wechat（has_cookie=False 也算 is_expired）
    assert body["expiring_soon_count"] == 2
    assert body["total_configured"] == 4  # twitter/weibo/xhs/douyin


def test_health_expiring_soon_within_3_days():
    """3 天内（不含已过期）也算 expiring_soon."""
    fake = [
        _stub_status(platform="twitter", remaining_days=2, needs_refresh=True),
        _stub_status(platform="weibo", remaining_days=4),  # 不算
        _stub_status(platform="xhs", remaining_days=0, needs_refresh=True,
                     source="mediacrawler_browser"),  # 0 天 = 即将过期
        _stub_status(platform="douyin", remaining_days=8, source="mediacrawler_browser"),
        _stub_status(platform="wechat", remaining_days=20, source="wemprss_api_token"),
    ]
    with patch("app.mcps.social_media_mcp.get_cookie_status", return_value=fake):
        body = client.get(HEALTH_URL).json()["data"]
    assert body["expiring_soon_count"] == 2  # twitter (2d) + xhs (0d)
    assert body["expired_count"] == 0
    assert body["needs_attention"] is True


def test_health_no_cookie_no_attention_unless_refresh_needed():
    """has_cookie=False 的平台不应单纯触发 needs_attention（用户没配置）."""
    fake = [
        _stub_status(platform="twitter", has_cookie=False, source="none",
                     is_expired=True, needs_refresh=True, remaining_days=None),
        _stub_status(platform="weibo", remaining_days=15),
    ]
    with patch("app.mcps.social_media_mcp.get_cookie_status", return_value=fake):
        body = client.get(HEALTH_URL).json()["data"]
    # twitter has_cookie=False → 不触发 needs_attention（虽 needs_refresh=True）
    # weibo 健康 → 也不触发
    assert body["needs_attention"] is False
    assert body["total_configured"] == 1  # 只 weibo
    # is_expired=True 仍然计入 expired_count（即使没配置）
    assert body["expired_count"] == 1


# ── 数据完整性 ─────────────────────────────────────────────


def test_health_returns_per_platform_label():
    fake = [
        _stub_status(platform="xhs", source="mediacrawler_browser"),
        _stub_status(platform="douyin", source="mediacrawler_browser"),
        _stub_status(platform="wechat", source="wemprss_api_token"),
    ]
    with patch("app.mcps.social_media_mcp.get_cookie_status", return_value=fake):
        body = client.get(HEALTH_URL).json()["data"]
    label_map = {p["platform"]: p["label"] for p in body["platforms"]}
    assert label_map["xhs"] == "小红书"
    assert label_map["douyin"] == "抖音"
    assert label_map["wechat"] == "微信公众号"


def test_health_remaining_days_passthrough():
    fake = [
        _stub_status(platform="twitter", remaining_days=5),
        _stub_status(platform="xhs", remaining_days=None,
                     source="mediacrawler_browser"),
    ]
    with patch("app.mcps.social_media_mcp.get_cookie_status", return_value=fake):
        body = client.get(HEALTH_URL).json()["data"]
    twitter = next(p for p in body["platforms"] if p["platform"] == "twitter")
    xhs = next(p for p in body["platforms"] if p["platform"] == "xhs")
    assert twitter["remaining_days"] == 5
    assert xhs["remaining_days"] is None


def test_health_empty_status_no_crash():
    with patch("app.mcps.social_media_mcp.get_cookie_status", return_value=[]):
        body = client.get(HEALTH_URL).json()["data"]
    assert body["needs_attention"] is False
    assert body["expired_count"] == 0
    assert body["expiring_soon_count"] == 0
    assert body["total_configured"] == 0
    assert body["total_platforms"] == 0
    assert body["platforms"] == []


# ── 真实路径回归（不 mock get_cookie_status）─────────────────
# 04-26 端到端冒烟发现 social_media_mcp.py 缺 `from pathlib import Path`，
# 全文件其他测试都 monkeypatch get_cookie_status，从未跑过 _probe_mediacrawler_login，
# 所以测试通过但生产 500。现在加一个不 mock 的 e2e 测试覆盖这条路径。


def test_get_cookie_status_real_path_no_nameerror():
    """直接调真实 get_cookie_status() 不应抛 NameError / ImportError.

    哪怕 MEDIACRAWLER_ROOT 不存在或 cookie 文件缺失，也只该返回降级 status，
    不应崩溃。覆盖 _probe_mediacrawler_login 整个 import / Path() 链路。
    """
    from app.mcps.social_media_mcp import get_cookie_status

    result = get_cookie_status()
    assert isinstance(result, list)
    # 5 平台：twitter / weibo / xhs / douyin / wechat
    platforms = {item["platform"] for item in result}
    assert {"twitter", "weibo", "xhs", "douyin", "wechat"}.issubset(platforms)
    # 每条 status 必有的字段
    for item in result:
        assert "has_cookie" in item
        assert "platform" in item


def test_health_endpoint_real_path_no_500():
    """e2e: GET /api/v1/social-media/health 不 mock，验证整链路 200."""
    r = client.get(HEALTH_URL)
    assert r.status_code == 200, f"500 indicates code path bug, got body: {r.text[:200]}"
    body = r.json()
    assert body["success"] is True
    assert "platforms" in body["data"]
