"""Social Media MCP — 社交媒体信息源统一接入层

数据源：
1. Twitter/X via RSSHub  — 指定博主推文
2. 微博 via RSSHub        — 指定博主微博
3. 小红书 via MediaCrawler — 指定博主笔记
4. 抖音 via MediaCrawler   — 指定博主视频（含视频→文字转写）
5. 微信公众号 via We-MP-RSS — 指定公众号文章

输出统一格式 → 接入 event_feed / ingestion pipeline
"""
from typing import Optional, List, Dict
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
from app.config import settings
from app.mcps.base import MCPResponse

SOURCE_PACKAGE = "social_media_mcp"

# ── Cookie 有效期预估（天）──────────────────────────────────────
COOKIE_TTL_DAYS = {
    "twitter": 60,
    "weibo": 120,
    "xhs": 10,
    "douyin": 10,
    "wechat": 30,
}


def _get_proxies() -> Optional[dict]:
    hp = settings.HTTP_PROXY
    hs = settings.HTTPS_PROXY
    if hp or hs:
        return {"http": hp or hs, "https": hs or hp}
    return None


def _parse_csv(s: str) -> List[str]:
    """解析逗号分隔的配置字符串"""
    return [x.strip() for x in s.split(",") if x.strip()] if s else []


def _make_item(
    *,
    title: str,
    summary: str = "",
    source_name: str,
    source_url: str = "",
    platform: str,
    author: str = "",
    published: str = "",
    asset_scope: List[str] = None,
    event_type: str = "social",
    data_source: str = "",
) -> dict:
    """构建统一格式的社媒数据项"""
    return {
        "id": None,
        "event_type": event_type,
        "title": title[:500],
        "summary": summary if summary else None,
        "source_name": source_name,
        "source_url": source_url,
        "event_timestamp": published or datetime.now(timezone.utc).isoformat(),
        "asset_scope": asset_scope or [],
        "theme_scope": ["social_media"],
        "importance": 0.4,
        "impact_direction": "neutral",
        "time_horizon": "immediate",
        "is_surprise": False,
        "status": "active",
        "data_source": data_source or f"social_{platform}",
        "sentiment_score": None,
        # 社媒专属字段
        "platform": platform,
        "author": author,
    }


# ═══════════════════════════════════════════════════════════════
# 1. Twitter/X via RSSHub
# ═══════════════════════════════════════════════════════════════

def fetch_twitter_rsshub(accounts: Optional[List[str]] = None, limit_per_account: int = 5) -> List[dict]:
    """通过 RSSHub 获取 Twitter/X 博主推文"""
    try:
        import feedparser
        import requests as req
    except ImportError:
        logger.warning("[SocialMedia] feedparser or requests not installed")
        return []

    if not accounts:
        accounts = _parse_csv(settings.SOCIAL_TWITTER_ACCOUNTS)
        if not accounts:
            accounts = _parse_csv(settings.TWITTER_ACCOUNTS)
    if not accounts:
        return []

    base_url = settings.RSSHUB_BASE_URL.rstrip("/")
    proxies = _get_proxies()
    results = []

    for account in accounts:
        try:
            url = f"{base_url}/twitter/user/{account}"
            r = req.get(url, timeout=15, proxies=proxies)
            if r.status_code != 200:
                logger.debug(f"[SocialMedia] Twitter RSSHub failed for @{account}: HTTP {r.status_code}")
                continue
            feed = feedparser.parse(r.text)
            for entry in feed.get("entries", [])[:limit_per_account]:
                title = entry.get("title", "")[:300]
                summary = entry.get("summary", "") or entry.get("description", "")
                link = entry.get("link", "")
                pub = _parse_published(entry)
                results.append(_make_item(
                    title=f"@{account}: {title}",
                    summary=summary[:1000],
                    source_name=f"Twitter/@{account}",
                    source_url=link,
                    platform="twitter",
                    author=account,
                    published=pub,
                    data_source="rsshub_twitter",
                ))
        except Exception as e:
            logger.debug(f"[SocialMedia] Twitter fetch error for @{account}: {e}")
            continue

    return results


# ═══════════════════════════════════════════════════════════════
# 2. 微博 via RSSHub
# ═══════════════════════════════════════════════════════════════

def fetch_weibo_rsshub(uids: Optional[List[str]] = None, limit_per_user: int = 5) -> List[dict]:
    """通过 RSSHub 获取微博博主内容"""
    try:
        import feedparser
        import requests as req
    except ImportError:
        return []

    if not uids:
        uids = _parse_csv(settings.SOCIAL_WEIBO_UIDS)
    if not uids:
        return []

    base_url = settings.RSSHUB_BASE_URL.rstrip("/")
    proxies = _get_proxies()
    results = []

    for uid in uids:
        try:
            url = f"{base_url}/weibo/user/{uid}"
            r = req.get(url, timeout=15, proxies=proxies)
            if r.status_code != 200:
                logger.debug(f"[SocialMedia] Weibo RSSHub failed for uid={uid}: HTTP {r.status_code}")
                continue
            feed = feedparser.parse(r.text)
            author_name = feed.get("feed", {}).get("title", f"微博用户{uid}")
            for entry in feed.get("entries", [])[:limit_per_user]:
                title = entry.get("title", "")[:300]
                summary = entry.get("summary", "") or entry.get("description", "")
                link = entry.get("link", "")
                pub = _parse_published(entry)
                results.append(_make_item(
                    title=title or summary[:100],
                    summary=summary[:1000],
                    source_name=f"微博/{author_name}",
                    source_url=link,
                    platform="weibo",
                    author=author_name,
                    published=pub,
                    data_source="rsshub_weibo",
                ))
        except Exception as e:
            logger.debug(f"[SocialMedia] Weibo fetch error for uid={uid}: {e}")
            continue

    return results


# ═══════════════════════════════════════════════════════════════
# 3. 小红书 via MediaCrawler API
# ═══════════════════════════════════════════════════════════════

def fetch_xiaohongshu(user_ids: Optional[List[str]] = None, limit_per_user: int = 5) -> List[dict]:
    """从 MediaCrawler 的 JSONL 输出文件读取小红书笔记。

    MediaCrawler 会把爬取到的笔记写入 `data/xhs/jsonl/*_contents_YYYY-MM-DD.jsonl`
    本函数直接扫描这些文件（按 mtime 倒序取最新），不调用 HTTP API。
    使用 `mediacrawler/trigger/xhs` 接口来触发新的爬取。
    """
    import json
    from pathlib import Path

    if not user_ids:
        user_ids = _parse_csv(settings.SOCIAL_XHS_USER_IDS)

    data_dir = Path(settings.MEDIACRAWLER_ROOT) / "data" / "xhs" / "jsonl"
    if not data_dir.exists():
        logger.debug(f"[SocialMedia] XHS data dir not found: {data_dir}")
        return []

    # 只关心 *_contents_*.jsonl（笔记本体），忽略 *_comments_*.jsonl
    files = sorted(
        [p for p in data_dir.glob("*_contents_*.jsonl") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        logger.debug(f"[SocialMedia] XHS no JSONL files in {data_dir}")
        return []

    # 取最近 5 个文件（时间窗口内的数据）
    results: List[dict] = []
    per_user_counter: dict = {}
    seen_note_ids: set = set()
    user_ids_set = set(user_ids) if user_ids else None

    for f in files[:5]:
        try:
            with f.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        note = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    note_id = note.get("note_id") or note.get("id")
                    if not note_id or note_id in seen_note_ids:
                        continue

                    note_user_id = str(note.get("user_id") or "")
                    # 用户筛选（如果配置了博主名单）
                    if user_ids_set and note_user_id not in user_ids_set:
                        continue

                    # 每人 N 条配额
                    if user_ids_set:
                        if per_user_counter.get(note_user_id, 0) >= limit_per_user:
                            continue
                        per_user_counter[note_user_id] = per_user_counter.get(note_user_id, 0) + 1

                    seen_note_ids.add(note_id)

                    title = note.get("title", "") or (note.get("desc", "") or "")[:80]
                    desc = note.get("desc", "") or note.get("content", "")
                    author = note.get("nickname", "") or note_user_id
                    link = (note.get("note_url") or
                            f"https://www.xiaohongshu.com/explore/{note_id}")

                    # 发布时间：XHS 是毫秒时间戳
                    ts_ms = note.get("time") or note.get("last_update_time") or 0
                    published = ""
                    if ts_ms:
                        try:
                            published = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).isoformat()
                        except Exception:
                            pass

                    results.append(_make_item(
                        title=title,
                        summary=desc[:1500],
                        source_name=f"小红书/{author}",
                        source_url=link,
                        platform="xiaohongshu",
                        author=author,
                        published=published,
                        data_source="mediacrawler_xhs_jsonl",
                    ))
        except Exception as e:
            logger.debug(f"[SocialMedia] XHS parse error on {f.name}: {e}")
            continue

    # 无博主筛选时全局截断到 30 条
    if not user_ids_set and len(results) > 30:
        results = results[:30]

    logger.info(
        f"[SocialMedia] XHS: read {len(results)} notes from "
        f"{len(files[:5])} JSONL files (filter_users={len(user_ids) if user_ids else 'none'})"
    )
    return results


# ═══════════════════════════════════════════════════════════════
# 4. 抖音 via MediaCrawler API (+ 视频转文字)
# ═══════════════════════════════════════════════════════════════

def fetch_douyin(user_ids: Optional[List[str]] = None, limit_per_user: int = 3, transcribe: bool = True) -> List[dict]:
    """从 MediaCrawler 的 JSONL 输出文件读取抖音视频信息。

    MediaCrawler 会把抓取到的视频元数据（不含视频内容）写入
    `data/douyin/jsonl/*_contents_YYYY-MM-DD.jsonl`，本函数按 mtime 倒序扫描。
    视频转文字在有 video_download_url 时可选触发。
    """
    import json
    from pathlib import Path

    if not user_ids:
        user_ids = _parse_csv(settings.SOCIAL_DOUYIN_USER_IDS)

    data_dir = Path(settings.MEDIACRAWLER_ROOT) / "data" / "douyin" / "jsonl"
    if not data_dir.exists():
        logger.debug(f"[SocialMedia] Douyin data dir not found: {data_dir}")
        return []

    files = sorted(
        [p for p in data_dir.glob("*_contents_*.jsonl") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        logger.debug(f"[SocialMedia] Douyin no JSONL files in {data_dir}")
        return []

    results: List[dict] = []
    per_user_counter: dict = {}
    seen_ids: set = set()
    # 抖音可以按 user_id 或 sec_uid 过滤（MediaCrawler 默认用 sec_uid 作为 creator_id）
    user_ids_set = set(user_ids) if user_ids else None

    for f in files[:5]:
        try:
            with f.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        video = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    aweme_id = video.get("aweme_id") or video.get("id")
                    if not aweme_id or aweme_id in seen_ids:
                        continue

                    video_user_id = str(video.get("user_id") or "")
                    video_sec_uid = str(video.get("sec_uid") or "")
                    # 匹配 user_id 或 sec_uid（MediaCrawler creator mode 用的是 sec_uid）
                    if user_ids_set and not (
                        video_user_id in user_ids_set or video_sec_uid in user_ids_set
                    ):
                        continue

                    key = video_sec_uid or video_user_id
                    if user_ids_set:
                        if per_user_counter.get(key, 0) >= limit_per_user:
                            continue
                        per_user_counter[key] = per_user_counter.get(key, 0) + 1

                    seen_ids.add(aweme_id)

                    title = video.get("title", "") or (video.get("desc", "") or "")[:100]
                    desc = video.get("desc", "") or ""
                    author = video.get("nickname", "") or video_user_id
                    link = (video.get("aweme_url") or
                            f"https://www.douyin.com/video/{aweme_id}")

                    # 视频转文字（可选，昂贵，默认开启但失败降级）
                    transcript = ""
                    video_url = video.get("video_download_url", "")
                    if transcribe and video_url:
                        try:
                            from app.services.video_transcriber import transcribe_video
                            transcript = transcribe_video(video_url, hint=title)
                        except Exception as te:
                            logger.debug(f"[SocialMedia] Douyin transcribe failed: {te}")

                    summary = (transcript or desc or title)[:2000]

                    # 抖音 create_time 是秒时间戳
                    ts = video.get("create_time") or 0
                    published = ""
                    if ts:
                        try:
                            published = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
                        except Exception:
                            pass

                    results.append(_make_item(
                        title=title,
                        summary=summary,
                        source_name=f"抖音/{author}",
                        source_url=link,
                        platform="douyin",
                        author=author,
                        published=published,
                        event_type="video",
                        data_source="mediacrawler_douyin_jsonl",
                    ))
        except Exception as e:
            logger.debug(f"[SocialMedia] Douyin parse error on {f.name}: {e}")
            continue

    if not user_ids_set and len(results) > 20:
        results = results[:20]

    logger.info(
        f"[SocialMedia] Douyin: read {len(results)} videos from "
        f"{len(files[:5])} JSONL files (filter_users={len(user_ids) if user_ids else 'none'})"
    )
    return results


# ═══════════════════════════════════════════════════════════════
# 5. 微信公众号 via We-MP-RSS
# ═══════════════════════════════════════════════════════════════

def fetch_wechat_mp(accounts: Optional[List[str]] = None, limit: int = 10) -> List[dict]:
    """通过 We-MP-RSS 获取微信公众号文章"""
    try:
        import feedparser
        import requests as req
    except ImportError:
        return []

    if not accounts:
        accounts = _parse_csv(settings.SOCIAL_WECHAT_ACCOUNTS)
    if not accounts:
        return []

    base_url = settings.WEMPRSS_BASE_URL.rstrip("/")
    proxies = _get_proxies()
    results = []

    # We-MP-RSS 提供 RSS feed 列表接口
    try:
        headers = {}
        if settings.WEMPRSS_API_TOKEN:
            headers["Authorization"] = f"Bearer {settings.WEMPRSS_API_TOKEN}"

        # 先获取所有可用的 RSS feed
        feeds_resp = req.get(f"{base_url}/api/feeds", headers=headers, timeout=10, proxies=proxies)
        if feeds_resp.status_code != 200:
            logger.debug(f"[SocialMedia] We-MP-RSS feeds list failed: HTTP {feeds_resp.status_code}")
            # 回退：直接按账号名拼 RSS URL
            for account in accounts:
                _fetch_wechat_rss(base_url, account, limit, proxies, results)
            return results

        feeds = feeds_resp.json()
        feed_list = feeds if isinstance(feeds, list) else feeds.get("data", feeds.get("feeds", []))

        # 过滤出用户关注的公众号
        target_names = set(a.lower() for a in accounts)
        matched_feeds = []
        for f in feed_list:
            name = (f.get("mp_name", "") or f.get("name", "")).lower()
            if name in target_names or f.get("mp_id", "") in target_names:
                matched_feeds.append(f)

        if not matched_feeds:
            # 未匹配到，尝试直接按名称拼 RSS
            for account in accounts:
                _fetch_wechat_rss(base_url, account, limit, proxies, results)
        else:
            for f in matched_feeds:
                rss_url = f.get("rss_url", "") or f"{base_url}/rss/{f.get('mp_id', '')}"
                _fetch_wechat_rss_url(rss_url, f.get("mp_name", "公众号"), limit, proxies, results)

    except Exception as e:
        logger.debug(f"[SocialMedia] WeChat MP fetch error: {e}")
        for account in accounts:
            _fetch_wechat_rss(base_url, account, limit, proxies, results)

    return results


def _fetch_wechat_rss(base_url: str, account: str, limit: int, proxies, results: list):
    """按公众号名称直接请求 RSS"""
    _fetch_wechat_rss_url(f"{base_url}/rss/{account}", account, limit, proxies, results)


def _fetch_wechat_rss_url(rss_url: str, mp_name: str, limit: int, proxies, results: list):
    """从 RSS URL 获取公众号文章"""
    try:
        import feedparser
        import requests as req
        r = req.get(rss_url, timeout=15, proxies=proxies)
        if r.status_code != 200:
            return
        feed = feedparser.parse(r.text)
        for entry in feed.get("entries", [])[:limit]:
            title = entry.get("title", "")[:300]
            summary = entry.get("summary", "") or entry.get("description", "")
            link = entry.get("link", "")
            pub = _parse_published(entry)
            results.append(_make_item(
                title=title,
                summary=summary,
                source_name=f"公众号/{mp_name}",
                source_url=link,
                platform="wechat_mp",
                author=mp_name,
                published=pub,
                event_type="article",
                data_source="wemprss",
            ))
    except Exception as e:
        logger.debug(f"[SocialMedia] WeChat RSS error for {mp_name}: {e}")


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _parse_published(entry) -> str:
    """从 feedparser entry 解析发布时间"""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            import calendar
            ts = calendar.timegm(entry.published_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            pass
    return entry.get("published", "") or datetime.now(timezone.utc).isoformat()


def _probe_mediacrawler_login(dirname: str, domain: str, cookie_name: str) -> tuple:
    """探测 MediaCrawler 浏览器 SQLite Cookie DB，返回 (logged_in, age_days)。

    与 /login-status 端点共享同一探测逻辑，保证 UI 两处显示一致。
    """
    import sqlite3
    from urllib.parse import quote

    db = Path(settings.MEDIACRAWLER_ROOT) / "browser_data" / dirname / "Default" / "Network" / "Cookies"
    if not db.exists():
        return False, None
    try:
        uri = f"file:{quote(str(db))}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        row = conn.execute(
            "SELECT COUNT(*), MAX(last_access_utc) FROM cookies WHERE host_key LIKE ? AND name = ?",
            (f"%{domain}%", cookie_name),
        ).fetchone()
        conn.close()
        if not row or row[0] == 0:
            return False, None
        last_access_chrome = row[1] or 0
        if last_access_chrome > 0:
            unix_ts = (last_access_chrome / 1_000_000) - 11644473600
            age_days = int((datetime.now(timezone.utc).timestamp() - unix_ts) / 86400)
        else:
            age_days = int((datetime.now(timezone.utc).timestamp() - db.stat().st_mtime) / 86400)
        return True, age_days
    except sqlite3.Error:
        # SQLite 被 MediaCrawler 锁定 → 仍视为已登录，用 mtime 兜底
        try:
            age_days = int((datetime.now(timezone.utc).timestamp() - db.stat().st_mtime) / 86400)
            return True, age_days
        except Exception:
            return True, None


def get_cookie_status() -> List[dict]:
    """获取所有平台登录 / Cookie 状态。

    数据来源：
    - twitter / weibo: 传统 settings.XXX_COOKIE 字段
    - xhs / douyin:    MediaCrawler 浏览器 SQLite Cookie DB（首选），回退到 settings.XHS_COOKIE
    - wechat:          settings.WEMPRSS_API_TOKEN
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    result = []

    # ── twitter / weibo: 纯 cookie 字段 ──
    for name, cookie, updated_at in [
        ("twitter", settings.TWITTER_COOKIE, settings.TWITTER_COOKIE_UPDATED_AT),
        ("weibo", settings.WEIBO_COOKIE, settings.WEIBO_COOKIE_UPDATED_AT),
    ]:
        ttl = COOKIE_TTL_DAYS.get(name, 30)
        has_cookie = bool(cookie)
        expires_at, remaining_days = None, None
        is_expired = not has_cookie
        if updated_at:
            try:
                updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                expires_at = (updated + timedelta(days=ttl)).isoformat()
                remaining_days = (updated + timedelta(days=ttl) - now).days
                is_expired = remaining_days <= 0
            except Exception:
                pass
        result.append({
            "platform": name,
            "has_cookie": has_cookie,
            "updated_at": updated_at or None,
            "estimated_ttl_days": ttl,
            "expires_at": expires_at,
            "remaining_days": remaining_days,
            "is_expired": is_expired,
            "needs_refresh": is_expired or (remaining_days is not None and remaining_days <= 3),
        })

    # ── xhs / douyin: 首选 MediaCrawler 浏览器登录态 ──
    for name, dirname, domain, cookie_name, fallback_cookie, fallback_updated_at in [
        ("xhs", "xhs_user_data_dir", "xiaohongshu.com", "web_session",
         settings.XHS_COOKIE, settings.XHS_COOKIE_UPDATED_AT),
        ("douyin", "dy_user_data_dir", "douyin.com", "sessionid_ss",
         settings.DOUYIN_COOKIE, settings.DOUYIN_COOKIE_UPDATED_AT),
    ]:
        logged_in, age_days = _probe_mediacrawler_login(dirname, domain, cookie_name)
        ttl = COOKIE_TTL_DAYS.get(name, 10)

        if logged_in:
            # MediaCrawler 登录态为准
            remaining_days = max(0, ttl - age_days) if age_days is not None else None
            is_expired = remaining_days is not None and remaining_days <= 0
            result.append({
                "platform": name,
                "has_cookie": True,
                "updated_at": None,
                "estimated_ttl_days": ttl,
                "expires_at": None,
                "remaining_days": remaining_days,
                "is_expired": is_expired,
                "needs_refresh": is_expired or (remaining_days is not None and remaining_days <= 2),
                "source": "mediacrawler_browser",
            })
        else:
            # 回退到手填 cookie
            has_cookie = bool(fallback_cookie)
            expires_at, remaining_days = None, None
            is_expired = not has_cookie
            if fallback_updated_at:
                try:
                    updated = datetime.fromisoformat(fallback_updated_at.replace("Z", "+00:00"))
                    expires_at = (updated + timedelta(days=ttl)).isoformat()
                    remaining_days = (updated + timedelta(days=ttl) - now).days
                    is_expired = remaining_days <= 0
                except Exception:
                    pass
            result.append({
                "platform": name,
                "has_cookie": has_cookie,
                "updated_at": fallback_updated_at or None,
                "estimated_ttl_days": ttl,
                "expires_at": expires_at,
                "remaining_days": remaining_days,
                "is_expired": is_expired,
                "needs_refresh": is_expired or (remaining_days is not None and remaining_days <= 3),
                "source": "manual_cookie" if has_cookie else "none",
            })

    # ── wechat: WEMPRSS_API_TOKEN 即算已配置 ──
    wechat_configured = bool(settings.WEMPRSS_API_TOKEN)
    wechat_ttl = COOKIE_TTL_DAYS.get("wechat", 30)
    wechat_remaining = None
    wechat_expires_at = None
    wechat_expired = not wechat_configured
    if settings.WECHAT_AUTH_UPDATED_AT:
        try:
            updated = datetime.fromisoformat(settings.WECHAT_AUTH_UPDATED_AT.replace("Z", "+00:00"))
            wechat_expires_at = (updated + timedelta(days=wechat_ttl)).isoformat()
            wechat_remaining = (updated + timedelta(days=wechat_ttl) - now).days
            wechat_expired = wechat_remaining <= 0
        except Exception:
            pass
    result.append({
        "platform": "wechat",
        "has_cookie": wechat_configured,  # 语义：已配置认证（API Token）
        "updated_at": settings.WECHAT_AUTH_UPDATED_AT or None,
        "estimated_ttl_days": wechat_ttl,
        "expires_at": wechat_expires_at,
        "remaining_days": wechat_remaining,
        "is_expired": wechat_expired,
        "needs_refresh": wechat_expired or (wechat_remaining is not None and wechat_remaining <= 3),
        "source": "wemprss_api_token" if wechat_configured else "none",
    })

    return result


# ═══════════════════════════════════════════════════════════════
# 主类
# ═══════════════════════════════════════════════════════════════

class SocialMediaMCP:
    """社交媒体信息源统一 MCP"""

    def fetch_all(self, limit: int = 50) -> MCPResponse[List[dict]]:
        """拉取所有平台的最新内容"""
        all_items: List[dict] = []
        seen_titles: set = set()
        errors: List[str] = []

        fetchers = [
            ("twitter", lambda: fetch_twitter_rsshub()),
            ("weibo", lambda: fetch_weibo_rsshub()),
            ("xiaohongshu", lambda: fetch_xiaohongshu()),
            ("douyin", lambda: fetch_douyin()),
            ("wechat_mp", lambda: fetch_wechat_mp()),
        ]

        for name, fetcher in fetchers:
            try:
                items = fetcher()
                for item in items:
                    t = item.get("title", "")
                    if t and t not in seen_titles:
                        seen_titles.add(t)
                        all_items.append(item)
                logger.info(f"[SocialMedia] {name}: fetched {len(items)} items")
            except Exception as e:
                errors.append(f"{name}: {str(e)[:100]}")
                logger.warning(f"[SocialMedia] {name} failed: {e}")

        all_items.sort(key=lambda x: x.get("event_timestamp", ""), reverse=True)
        resp = MCPResponse.ok(data=all_items[:limit], source_package=SOURCE_PACKAGE)
        if errors:
            resp.warnings = errors
        return resp

    def fetch_platform(self, platform: str, limit: int = 20) -> MCPResponse[List[dict]]:
        """拉取指定平台的内容"""
        fetcher_map = {
            "twitter": lambda: fetch_twitter_rsshub(),
            "weibo": lambda: fetch_weibo_rsshub(),
            "xiaohongshu": lambda: fetch_xiaohongshu(),
            "douyin": lambda: fetch_douyin(),
            "wechat_mp": lambda: fetch_wechat_mp(limit=limit),
        }
        fetcher = fetcher_map.get(platform)
        if not fetcher:
            return MCPResponse.fail(f"Unknown platform: {platform}", SOURCE_PACKAGE)
        try:
            items = fetcher()
            return MCPResponse.ok(data=items[:limit], source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)

    def get_cookie_status(self) -> MCPResponse[List[dict]]:
        """获取所有 Cookie 状态"""
        return MCPResponse.ok(data=get_cookie_status(), source_package=SOURCE_PACKAGE)

    def get_configured_accounts(self) -> MCPResponse[dict]:
        """获取已配置的所有博主列表"""
        data = {
            "twitter": _parse_csv(settings.SOCIAL_TWITTER_ACCOUNTS) or _parse_csv(settings.TWITTER_ACCOUNTS),
            "weibo": _parse_csv(settings.SOCIAL_WEIBO_UIDS),
            "xiaohongshu": _parse_csv(settings.SOCIAL_XHS_USER_IDS),
            "douyin": _parse_csv(settings.SOCIAL_DOUYIN_USER_IDS),
            "wechat_mp": _parse_csv(settings.SOCIAL_WECHAT_ACCOUNTS),
        }
        return MCPResponse.ok(data=data, source_package=SOURCE_PACKAGE)
