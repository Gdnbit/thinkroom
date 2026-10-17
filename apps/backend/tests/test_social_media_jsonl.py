"""
回归测试：MediaCrawler JSONL 文件读取器（fetch_xiaohongshu / fetch_douyin）

覆盖要点：
- JSONL 解析与字段映射（title/desc/nickname/url 等）
- 发布时间戳正确转换（XHS 毫秒、抖音秒）
- 博主 ID 过滤（user_id / sec_uid 双路径）
- 每人 N 条配额限制
- 跨文件去重（note_id / aweme_id）
- 数据目录缺失时安全返回空列表
- 忽略 *_comments_*.jsonl（只读 *_contents_*.jsonl）
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.mcps.social_media_mcp import fetch_xiaohongshu, fetch_douyin


# ── Fixtures ───────────────────────────────────────────────────

def _write_jsonl(path: Path, rows: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


@pytest.fixture
def fake_mediacrawler(tmp_path, monkeypatch):
    """在临时目录构造 data/xhs/jsonl 和 data/douyin/jsonl 结构。"""
    root = tmp_path / "MediaCrawler"
    xhs_dir = root / "data" / "xhs" / "jsonl"
    dy_dir = root / "data" / "douyin" / "jsonl"

    # ---- XHS 数据 ----
    _write_jsonl(xhs_dir / "search_contents_2026-04-17.jsonl", [
        {
            "note_id": "xhs_note_001",
            "title": "测试笔记 A",
            "desc": "这是笔记 A 的正文内容",
            "user_id": "user_alice",
            "nickname": "Alice",
            "time": 1775759528000,  # 毫秒，约 2026-04-09
            "note_url": "https://www.xiaohongshu.com/explore/xhs_note_001",
        },
        {
            "note_id": "xhs_note_002",
            "title": "测试笔记 B",
            "desc": "另一篇笔记",
            "user_id": "user_bob",
            "nickname": "Bob",
            "time": 1775000000000,
            "note_url": "https://www.xiaohongshu.com/explore/xhs_note_002",
        },
        {
            "note_id": "xhs_note_003",
            "title": "Alice 的第二条",
            "desc": "Alice 又发了一条",
            "user_id": "user_alice",
            "nickname": "Alice",
            "time": 1776000000000,
            "note_url": "https://www.xiaohongshu.com/explore/xhs_note_003",
        },
    ])
    # 评论文件应被忽略
    _write_jsonl(xhs_dir / "search_comments_2026-04-17.jsonl", [
        {"comment_id": "c1", "content": "这应该被忽略"}
    ])

    # ---- Douyin 数据 ----
    _write_jsonl(dy_dir / "creator_contents_2026-04-17.jsonl", [
        {
            "aweme_id": "dy_001",
            "title": "抖音视频 A",
            "desc": "视频 A 描述",
            "user_id": "dy_user_1",
            "sec_uid": "MS4wLjABAAAA_sec1",
            "nickname": "DyAlice",
            "create_time": 1775000000,  # 秒
            "aweme_url": "https://www.douyin.com/video/dy_001",
        },
        {
            "aweme_id": "dy_002",
            "title": "抖音视频 B",
            "desc": "视频 B 描述",
            "user_id": "dy_user_2",
            "sec_uid": "MS4wLjABAAAA_sec2",
            "nickname": "DyBob",
            "create_time": 1775100000,
            "aweme_url": "https://www.douyin.com/video/dy_002",
        },
        # 重复的 aweme_id（测去重）
        {
            "aweme_id": "dy_001",
            "title": "重复的视频 A",
            "desc": "应该被去重",
            "user_id": "dy_user_1",
            "sec_uid": "MS4wLjABAAAA_sec1",
            "nickname": "DyAlice",
            "create_time": 1775000000,
        },
    ])

    # 指向临时 MediaCrawler 根
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIACRAWLER_ROOT", str(root))
    # 清空 env 中的博主列表，测试无过滤模式
    monkeypatch.setattr(settings, "SOCIAL_XHS_USER_IDS", "")
    monkeypatch.setattr(settings, "SOCIAL_DOUYIN_USER_IDS", "")

    return root


# ── XHS 测试 ───────────────────────────────────────────────────

def test_xhs_reads_all_when_no_filter(fake_mediacrawler):
    items = fetch_xiaohongshu(user_ids=None, limit_per_user=10)
    assert len(items) == 3, "无过滤时应返回全部 3 条笔记"
    assert all(it["platform"] == "xiaohongshu" for it in items)
    # 评论文件必须被忽略
    assert all("应该被忽略" not in (it.get("summary") or "") for it in items)


def test_xhs_user_filter(fake_mediacrawler):
    items = fetch_xiaohongshu(user_ids=["user_alice"], limit_per_user=10)
    assert len(items) == 2, "user_alice 有 2 条笔记"
    assert all("Alice" in it["author"] for it in items)


def test_xhs_per_user_quota(fake_mediacrawler):
    items = fetch_xiaohongshu(user_ids=["user_alice"], limit_per_user=1)
    assert len(items) == 1, "配额应限制为每人 1 条"


def test_xhs_timestamp_conversion(fake_mediacrawler):
    items = fetch_xiaohongshu(user_ids=["user_alice"], limit_per_user=10)
    # 1775759528000 ms → 2026-04-09
    for it in items:
        ts = it["event_timestamp"]
        assert ts and "2026" in ts, f"应解析为 2026 年时间: {ts}"
        # 能被 ISO 解析
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert dt.tzinfo is not None


def test_xhs_fields_mapping(fake_mediacrawler):
    items = fetch_xiaohongshu(user_ids=["user_alice"], limit_per_user=1)
    assert len(items) == 1
    it = items[0]
    assert it["title"]
    assert it["summary"]
    assert it["author"] == "Alice"
    assert "xiaohongshu.com" in it["source_url"]
    assert it["data_source"] == "mediacrawler_xhs_jsonl"


def test_xhs_missing_dir_returns_empty(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIACRAWLER_ROOT", str(tmp_path / "nonexistent"))
    monkeypatch.setattr(settings, "SOCIAL_XHS_USER_IDS", "")
    assert fetch_xiaohongshu() == []


# ── Douyin 测试 ───────────────────────────────────────────────────

def test_douyin_reads_and_dedupes(fake_mediacrawler):
    items = fetch_douyin(user_ids=None, limit_per_user=10, transcribe=False)
    assert len(items) == 2, "重复 aweme_id 应被去重"
    assert all(it["platform"] == "douyin" for it in items)


def test_douyin_filter_by_user_id(fake_mediacrawler):
    items = fetch_douyin(user_ids=["dy_user_1"], limit_per_user=10, transcribe=False)
    assert len(items) == 1
    assert items[0]["author"] == "DyAlice"


def test_douyin_filter_by_sec_uid(fake_mediacrawler):
    """MediaCrawler creator mode 用的是 sec_uid，必须也匹配。"""
    items = fetch_douyin(
        user_ids=["MS4wLjABAAAA_sec2"], limit_per_user=10, transcribe=False
    )
    assert len(items) == 1
    assert items[0]["author"] == "DyBob"


def test_douyin_timestamp_is_seconds(fake_mediacrawler):
    items = fetch_douyin(user_ids=["dy_user_1"], limit_per_user=1, transcribe=False)
    assert len(items) == 1
    ts = items[0]["event_timestamp"]
    # 1775000000 s → 2026
    assert "2026" in ts


def test_douyin_missing_dir_returns_empty(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIACRAWLER_ROOT", str(tmp_path / "nowhere"))
    monkeypatch.setattr(settings, "SOCIAL_DOUYIN_USER_IDS", "")
    assert fetch_douyin(transcribe=False) == []


def test_douyin_event_type_is_video(fake_mediacrawler):
    items = fetch_douyin(user_ids=None, limit_per_user=10, transcribe=False)
    assert all(it["event_type"] == "video" for it in items)
