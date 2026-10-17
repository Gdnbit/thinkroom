"""Social Media API — 社交媒体信息源管理与数据拉取"""
import os
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter()

ENV_PATH = Path(os.getcwd()) / ".env"


# ── 请求/响应模型 ──────────────────────────────────────────

class CookieUpdateRequest(BaseModel):
    twitter_cookie: Optional[str] = None
    weibo_cookie: Optional[str] = None
    xhs_cookie: Optional[str] = None
    douyin_cookie: Optional[str] = None


class AccountsUpdateRequest(BaseModel):
    twitter_accounts: Optional[str] = None
    weibo_uids: Optional[str] = None
    xhs_user_ids: Optional[str] = None
    douyin_user_ids: Optional[str] = None
    wechat_accounts: Optional[str] = None


class ServiceUrlsUpdateRequest(BaseModel):
    rsshub_base_url: Optional[str] = None
    mediacrawler_api_url: Optional[str] = None
    wemprss_base_url: Optional[str] = None
    wemprss_api_token: Optional[str] = None
    google_ai_api_key: Optional[str] = None


# ── 数据拉取 ──────────────────────────────────────────────

@router.get("/feed")
def get_social_feed(platform: str = "all", limit: int = 30):
    """拉取社交媒体内容（按平台或全部）"""
    from app.mcps.social_media_mcp import SocialMediaMCP
    mcp = SocialMediaMCP()
    if platform == "all":
        resp = mcp.fetch_all(limit=limit)
    else:
        resp = mcp.fetch_platform(platform, limit=limit)
    if not resp.success:
        raise HTTPException(status_code=502, detail=resp.errors)
    return {"success": True, "data": resp.data, "count": len(resp.data), "warnings": resp.warnings}


@router.get("/feed/{platform}")
def get_platform_feed(platform: str, limit: int = 20):
    """拉取指定平台的内容"""
    from app.mcps.social_media_mcp import SocialMediaMCP
    mcp = SocialMediaMCP()
    resp = mcp.fetch_platform(platform, limit=limit)
    if not resp.success:
        raise HTTPException(status_code=502, detail=resp.errors)
    return {"success": True, "data": resp.data, "count": len(resp.data)}


# ── Cookie 管理 ──────────────────────────────────────────

@router.get("/cookies")
async def get_cookie_status():
    """获取所有平台的 Cookie 状态和到期信息"""
    from app.mcps.social_media_mcp import get_cookie_status
    return {"success": True, "data": get_cookie_status()}


# ── G13: 聚合健康状态（轻量聚合给侧栏红点 + Dashboard banner 共用）──

@router.get("/health")
async def get_social_health():
    """G13 · 聚合社媒登录态：1 个轻量端点取代多端点合并。

    输出聚合摘要（需要前端拉一次拿 needs_attention / 各平台简短状态）：

    ```json
    {
      "needs_attention": true,           // 任一平台 needs_refresh OR is_expired
      "expired_count": 1,                // 已过期平台数
      "expiring_soon_count": 2,          // 3 天内将过期数（含 expired）
      "total_configured": 4,             // 已配置平台数（has_cookie=True）
      "total_platforms": 5,              // 全部平台数
      "platforms": [
        {"platform": "xhs", "has_cookie": true, "remaining_days": 1,
         "is_expired": false, "needs_refresh": true,
         "source": "mediacrawler_browser", "label": "小红书"},
        ...
      ]
    }
    ```

    侧栏红点：用 `needs_attention` 决定是否显示。
    Dashboard banner：用 `platforms` 列表（其中 `needs_refresh=True` 的项）展示。
    """
    from app.mcps.social_media_mcp import get_cookie_status

    PLATFORM_LABELS = {
        "twitter": "Twitter/X",
        "weibo": "微博",
        "xhs": "小红书",
        "douyin": "抖音",
        "wechat": "微信公众号",
    }

    cookies = get_cookie_status()
    expiring_soon = 0  # 3 天内（含已过期）
    expired = 0
    configured = 0
    needs_attention = False

    platforms_brief = []
    for c in cookies:
        platform = c.get("platform")
        has_cookie = bool(c.get("has_cookie"))
        is_expired = bool(c.get("is_expired"))
        remaining_days = c.get("remaining_days")
        needs_refresh = bool(c.get("needs_refresh"))
        if has_cookie:
            configured += 1
        if is_expired:
            expired += 1
            expiring_soon += 1
        elif remaining_days is not None and remaining_days <= 3 and has_cookie:
            expiring_soon += 1
        # 触发红点条件：has_cookie 但需要刷新（一般用户已配置才提醒）
        if has_cookie and needs_refresh:
            needs_attention = True
        platforms_brief.append({
            "platform": platform,
            "label": PLATFORM_LABELS.get(platform, platform),
            "has_cookie": has_cookie,
            "remaining_days": remaining_days,
            "is_expired": is_expired,
            "needs_refresh": needs_refresh,
            "source": c.get("source"),  # 可能为 None（旧 cookie 平台）
        })

    return {
        "success": True,
        "data": {
            "needs_attention": needs_attention,
            "expired_count": expired,
            "expiring_soon_count": expiring_soon,
            "total_configured": configured,
            "total_platforms": len(cookies),
            "platforms": platforms_brief,
        },
    }


# ── MediaCrawler 爬虫触发（headless 模式，无弹窗）──────────────

@router.post("/mediacrawler/trigger/{platform}")
async def trigger_mediacrawler(platform: str):
    """触发 MediaCrawler 爬取指定平台的已配置博主（headless 模式，不弹浏览器）。

    - platform: xhs | douyin
    - 依赖 MediaCrawler 的 SAVE_LOGIN_STATE（已登录态将自动复用）
    - 触发成功后，数据会写入 MediaCrawler 的 data/{platform}/jsonl/
    """
    import requests as req
    from app.config import settings

    if platform not in ("xhs", "douyin"):
        raise HTTPException(status_code=400, detail="platform 仅支持 xhs 或 douyin")

    # 取对应平台的博主 ID 列表
    if platform == "xhs":
        creator_ids = settings.SOCIAL_XHS_USER_IDS
        mc_platform = "xhs"
    else:
        creator_ids = settings.SOCIAL_DOUYIN_USER_IDS
        mc_platform = "dy"

    if not creator_ids.strip():
        raise HTTPException(
            status_code=400,
            detail=f"未配置 {platform} 博主列表（SOCIAL_{platform.upper()}_USER_IDS）",
        )

    api_base = settings.MEDIACRAWLER_API_URL.rstrip("/")
    payload = {
        "platform": mc_platform,
        "login_type": "qrcode",          # 已持久化登录时不会真要求扫码
        "crawler_type": "creator",       # 按博主 ID 抓取
        "creator_ids": creator_ids,
        "save_option": "jsonl",
        "enable_comments": False,        # 我们只需要笔记本体
        "headless": True,                # 关键：无头模式，不弹窗
    }
    try:
        r = req.post(f"{api_base}/api/crawler/start", json=payload, timeout=10)
        if r.status_code == 400 and "already running" in r.text.lower():
            return {
                "success": False,
                "status": "already_running",
                "message": "MediaCrawler 当前已有任务在跑，请稍后再试或先调用 /stop",
            }
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"MediaCrawler 拒绝任务: HTTP {r.status_code} {r.text[:200]}")
        return {
            "success": True,
            "status": "started",
            "platform": platform,
            "creator_count": len([x for x in creator_ids.split(",") if x.strip()]),
            "headless": True,
        }
    except req.exceptions.ConnectionError:
        raise HTTPException(
            status_code=502,
            detail=f"连接不上 MediaCrawler ({api_base})，请确认它在 http://localhost:8080 运行",
        )
    except req.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="MediaCrawler 响应超时")


@router.post("/mediacrawler/stop")
async def stop_mediacrawler():
    """停止当前 MediaCrawler 爬取任务"""
    import requests as req
    from app.config import settings
    api_base = settings.MEDIACRAWLER_API_URL.rstrip("/")
    try:
        r = req.post(f"{api_base}/api/crawler/stop", timeout=10)
        return {"success": r.status_code == 200, "status_code": r.status_code, "body": r.text[:300]}
    except req.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"无法连接 MediaCrawler: {e}")


@router.get("/mediacrawler/status")
async def get_mediacrawler_status():
    """查询 MediaCrawler 爬虫状态 + 最近 20 条日志 + 输出文件计数"""
    import requests as req
    from app.config import settings

    api_base = settings.MEDIACRAWLER_API_URL.rstrip("/")
    mc_root = Path(settings.MEDIACRAWLER_ROOT)

    # 1. 爬虫状态
    status = {"status": "unreachable", "platform": None, "started_at": None}
    logs: list = []
    try:
        r = req.get(f"{api_base}/api/crawler/status", timeout=5)
        if r.status_code == 200:
            status = r.json()
        log_resp = req.get(f"{api_base}/api/crawler/logs?limit=20", timeout=5)
        if log_resp.status_code == 200:
            logs = log_resp.json().get("logs", [])
    except req.exceptions.RequestException:
        pass

    # 2. 本地 JSONL 输出计数（判断是否已有可入库数据）
    file_stats = {}
    for platform in ("xhs", "douyin"):
        jsonl_dir = mc_root / "data" / platform / "jsonl"
        if jsonl_dir.exists():
            files = list(jsonl_dir.glob("*_contents_*.jsonl"))
            total_size = sum(f.stat().st_size for f in files)
            latest_mtime = max((f.stat().st_mtime for f in files), default=0)
            file_stats[platform] = {
                "file_count": len(files),
                "total_size_bytes": total_size,
                "latest_mtime": datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat() if latest_mtime else None,
            }
        else:
            file_stats[platform] = {"file_count": 0, "total_size_bytes": 0, "latest_mtime": None}

    return {
        "success": True,
        "crawler": status,
        "recent_logs": logs[-20:],
        "output_files": file_stats,
    }


@router.get("/login-status")
async def get_login_status():
    """检测 MediaCrawler 浏览器登录态（小红书 / 抖音）+ 微信 API Token 状态。

    通过读取 `browser_data/{platform}_user_data_dir/Default/Network/Cookies`
    (Chromium SQLite DB) 查询关键 session cookie 判断是否已登录。
    使用只读 URI + immutable=1，避免与运行中的浏览器争锁。
    """
    import sqlite3
    from urllib.parse import quote
    from app.config import settings

    mc_root = Path(settings.MEDIACRAWLER_ROOT)
    platforms = [
        ("xhs", "xhs_user_data_dir", "xiaohongshu.com", "web_session"),
        ("douyin", "dy_user_data_dir", "douyin.com", "sessionid_ss"),
    ]

    def _probe(data_dir: Path, domain: str, cookie_name: str):
        """返回 (logged_in, age_days, error)"""
        cookies_db = data_dir / "Default" / "Network" / "Cookies"
        if not cookies_db.exists():
            return False, None, "user_data_dir 不存在，尚未登录过"
        try:
            uri = f"file:{quote(str(cookies_db))}?mode=ro&immutable=1"
            conn = sqlite3.connect(uri, uri=True, timeout=1.0)
            row = conn.execute(
                "SELECT COUNT(*), MAX(last_access_utc) FROM cookies "
                "WHERE host_key LIKE ? AND name = ?",
                (f"%{domain}%", cookie_name),
            ).fetchone()
            conn.close()
            count = row[0] if row else 0
            if count == 0:
                return False, None, f"无 {cookie_name} cookie"
            # Chromium 时间戳：1601-01-01 微秒，转为 Unix
            last_access_chrome = row[1] or 0
            if last_access_chrome > 0:
                unix_ts = (last_access_chrome / 1_000_000) - 11644473600
                age_days = int((datetime.now(timezone.utc).timestamp() - unix_ts) / 86400)
            else:
                # fallback 到文件 mtime
                age_days = int(
                    (datetime.now(timezone.utc).timestamp() - cookies_db.stat().st_mtime) / 86400
                )
            return True, age_days, None
        except sqlite3.Error as e:
            # 文件被锁（MediaCrawler 正在运行）→ 仍算已登录，用 mtime 作为备用
            age_days = int(
                (datetime.now(timezone.utc).timestamp() - cookies_db.stat().st_mtime) / 86400
            )
            return True, age_days, f"SQLite 锁定中 ({e.__class__.__name__})"

    result = []
    for platform, dirname, domain, cookie_name in platforms:
        data_dir = mc_root / "browser_data" / dirname
        logged_in, age_days, err = _probe(data_dir, domain, cookie_name)
        # MediaCrawler 官方：xhs ~10 天、douyin ~10 天
        ttl = 10
        expires_in = max(0, ttl - age_days) if (logged_in and age_days is not None) else None
        result.append({
            "platform": platform,
            "logged_in": logged_in,
            "cookie_age_days": age_days,
            "estimated_ttl_days": ttl,
            "expires_in_days": expires_in,
            "needs_relogin": logged_in and expires_in is not None and expires_in <= 2,
            "user_data_dir": str(data_dir),
            "note": err,
        })

    # 微信：没有浏览器流程，靠 WEMPRSS_API_TOKEN
    result.append({
        "platform": "wechat",
        "logged_in": bool(settings.WEMPRSS_API_TOKEN),
        "cookie_age_days": None,
        "estimated_ttl_days": 30,
        "expires_in_days": None,
        "needs_relogin": not bool(settings.WEMPRSS_API_TOKEN),
        "user_data_dir": None,
        "note": "通过 We-MP-RSS API Token 认证，非浏览器 Cookie",
    })

    return {
        "success": True,
        "data": result,
        "mediacrawler_root": str(mc_root),
        "mediacrawler_exists": mc_root.exists(),
    }


@router.post("/cookies")
async def update_cookies(body: CookieUpdateRequest):
    """批量更新 Cookie（一键换 Cookie）"""
    from app.config import settings

    env = _read_env()
    updates = {}
    now = datetime.now(timezone.utc).isoformat()

    if body.twitter_cookie is not None:
        updates["TWITTER_COOKIE"] = body.twitter_cookie
        updates["TWITTER_COOKIE_UPDATED_AT"] = now
        settings.TWITTER_COOKIE = body.twitter_cookie
        settings.TWITTER_COOKIE_UPDATED_AT = now

    if body.weibo_cookie is not None:
        updates["WEIBO_COOKIE"] = body.weibo_cookie
        updates["WEIBO_COOKIE_UPDATED_AT"] = now
        settings.WEIBO_COOKIE = body.weibo_cookie
        settings.WEIBO_COOKIE_UPDATED_AT = now

    if body.xhs_cookie is not None:
        updates["XHS_COOKIE"] = body.xhs_cookie
        updates["XHS_COOKIE_UPDATED_AT"] = now
        settings.XHS_COOKIE = body.xhs_cookie
        settings.XHS_COOKIE_UPDATED_AT = now

    if body.douyin_cookie is not None:
        updates["DOUYIN_COOKIE"] = body.douyin_cookie
        updates["DOUYIN_COOKIE_UPDATED_AT"] = now
        settings.DOUYIN_COOKIE = body.douyin_cookie
        settings.DOUYIN_COOKIE_UPDATED_AT = now

    if updates:
        env.update(updates)
        _write_env(env)

    return {
        "success": True,
        "updated_platforms": [
            k.replace("_COOKIE_UPDATED_AT", "").replace("_COOKIE", "").lower()
            for k in updates if k.endswith("_COOKIE") and not k.endswith("_UPDATED_AT")
        ],
        "updated_at": now,
    }


# ── 博主账号管理 ──────────────────────────────────────────

@router.get("/accounts")
async def get_configured_accounts():
    """获取已配置的所有博主列表"""
    from app.mcps.social_media_mcp import SocialMediaMCP
    mcp = SocialMediaMCP()
    resp = mcp.get_configured_accounts()
    return {"success": True, "data": resp.data}


@router.post("/accounts")
async def update_accounts(body: AccountsUpdateRequest):
    """更新追踪的博主列表"""
    from app.config import settings

    env = _read_env()
    updates = {}

    if body.twitter_accounts is not None:
        updates["SOCIAL_TWITTER_ACCOUNTS"] = body.twitter_accounts
        settings.SOCIAL_TWITTER_ACCOUNTS = body.twitter_accounts
    if body.weibo_uids is not None:
        updates["SOCIAL_WEIBO_UIDS"] = body.weibo_uids
        settings.SOCIAL_WEIBO_UIDS = body.weibo_uids
    if body.xhs_user_ids is not None:
        updates["SOCIAL_XHS_USER_IDS"] = body.xhs_user_ids
        settings.SOCIAL_XHS_USER_IDS = body.xhs_user_ids
    if body.douyin_user_ids is not None:
        updates["SOCIAL_DOUYIN_USER_IDS"] = body.douyin_user_ids
        settings.SOCIAL_DOUYIN_USER_IDS = body.douyin_user_ids
    if body.wechat_accounts is not None:
        updates["SOCIAL_WECHAT_ACCOUNTS"] = body.wechat_accounts
        settings.SOCIAL_WECHAT_ACCOUNTS = body.wechat_accounts

    if updates:
        env.update(updates)
        _write_env(env)

    return {"success": True, "updated_keys": list(updates.keys())}


# ── 服务 URL 和 API Key 管理 ─────────────────────────────

@router.get("/services")
async def get_service_config():
    """获取外部服务配置状态"""
    from app.config import settings
    return {
        "success": True,
        "data": {
            "rsshub_base_url": settings.RSSHUB_BASE_URL,
            "mediacrawler_api_url": settings.MEDIACRAWLER_API_URL,
            "wemprss_base_url": settings.WEMPRSS_BASE_URL,
            "wemprss_api_token_set": bool(settings.WEMPRSS_API_TOKEN),
            "google_ai_api_key_set": bool(settings.GOOGLE_AI_API_KEY),
            "video_transcribe_model": settings.VIDEO_TRANSCRIBE_MODEL,
            "video_transcribe_fallback": settings.VIDEO_TRANSCRIBE_FALLBACK_MODEL,
        }
    }


@router.post("/services")
async def update_service_config(body: ServiceUrlsUpdateRequest):
    """更新外部服务 URL 和 API Key"""
    from app.config import settings

    env = _read_env()
    updates = {}
    ignored_secret_keys = []

    if body.rsshub_base_url is not None:
        updates["RSSHUB_BASE_URL"] = body.rsshub_base_url
        settings.RSSHUB_BASE_URL = body.rsshub_base_url
    if body.mediacrawler_api_url is not None:
        updates["MEDIACRAWLER_API_URL"] = body.mediacrawler_api_url
        settings.MEDIACRAWLER_API_URL = body.mediacrawler_api_url
    if body.wemprss_base_url is not None:
        updates["WEMPRSS_BASE_URL"] = body.wemprss_base_url
        settings.WEMPRSS_BASE_URL = body.wemprss_base_url
    if body.wemprss_api_token not in (None, ""):
        ignored_secret_keys.append("WEMPRSS_API_TOKEN")
    if body.google_ai_api_key not in (None, ""):
        ignored_secret_keys.append("GOOGLE_AI_API_KEY")

    if updates:
        env.update(updates)
        _write_env(env)

    return {
        "success": True,
        "updated_keys": list(updates.keys()),
        "ignored_secret_keys": ignored_secret_keys,
        "secret_policy": "API keys are loaded from SOPS/secrets env and are not written to .env.",
    }


# ── 订阅内容持久化同步 ─────────────────────────────────────

@router.post("/subscription/sync")
def sync_social_subscriptions(auto_ingest: bool = True):
    """手动触发社媒订阅内容同步（拉取→去重→入库→ingestion）"""
    from app.database import SessionLocal
    from app.services.social_subscription_service import SocialSubscriptionService

    db = SessionLocal()
    try:
        svc = SocialSubscriptionService(db)
        result = svc.sync_all(auto_ingest=auto_ingest)
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/subscription/sync/{platform}")
def sync_platform_subscription(platform: str, auto_ingest: bool = True, limit: int = 20):
    """手动触发指定平台的社媒同步"""
    from app.database import SessionLocal
    from app.services.social_subscription_service import SocialSubscriptionService

    valid_platforms = {"twitter", "weibo", "xiaohongshu", "douyin", "wechat_mp"}
    if platform not in valid_platforms:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid platform: {platform}. Valid: {sorted(valid_platforms)}"
        )

    db = SessionLocal()
    try:
        svc = SocialSubscriptionService(db)
        result = svc.sync_platform(platform, auto_ingest=auto_ingest, limit=limit)
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.get("/subscription/history")
def get_subscription_history(limit: int = 50):
    """查看社媒内容的入库历史"""
    from app.database import SessionLocal
    from app.services.social_subscription_service import SocialSubscriptionService

    db = SessionLocal()
    try:
        svc = SocialSubscriptionService(db)
        records = svc.get_sync_history(limit=limit)
        return {"success": True, "data": records, "count": len(records)}
    finally:
        db.close()


@router.get("/subscription/status")
def get_subscription_status():
    """查看社媒同步整体状态（监控用）"""
    from app.database import SessionLocal
    from app.services.social_subscription_service import SocialSubscriptionService

    db = SessionLocal()
    try:
        svc = SocialSubscriptionService(db)
        status = svc.get_sync_status()
        return {"success": True, **status}
    finally:
        db.close()


@router.post("/subscription/retry")
def retry_failed_ingestions(limit: int = 20):
    """手动重试之前 ingestion 失败的社媒内容"""
    from app.database import SessionLocal
    from app.services.social_subscription_service import SocialSubscriptionService

    db = SessionLocal()
    try:
        svc = SocialSubscriptionService(db)
        result = svc.retry_failed_ingestions(limit=limit)
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ── Cookie / 账号 导入导出 ────────────────────────────────

# 所有与社媒相关的 env 键（用于批量导出/导入）
ALL_SOCIAL_KEYS = [
    "TWITTER_COOKIE", "TWITTER_COOKIE_UPDATED_AT",
    "WEIBO_COOKIE",   "WEIBO_COOKIE_UPDATED_AT",
    "XHS_COOKIE",     "XHS_COOKIE_UPDATED_AT",
    "DOUYIN_COOKIE",  "DOUYIN_COOKIE_UPDATED_AT",
    "SOCIAL_TWITTER_ACCOUNTS", "SOCIAL_WEIBO_UIDS",
    "SOCIAL_XHS_USER_IDS", "SOCIAL_DOUYIN_USER_IDS",
    "SOCIAL_WECHAT_ACCOUNTS",
    "RSSHUB_BASE_URL", "MEDIACRAWLER_API_URL",
    "WEMPRSS_BASE_URL", "WEMPRSS_API_TOKEN",
]


@router.get("/export")
def export_social_config():
    """导出所有社媒相关配置（Cookie + 账号 + 服务 URL）为 JSON。

    前端可直接触发文件下载。敏感信息（Cookie / Token）会原样导出，
    请妥善保管导出文件（不要提交到 git）。
    """
    env = _read_env()
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "version": 1,
        "data": {k: env.get(k, "") for k in ALL_SOCIAL_KEYS},
    }
    return payload


class ImportRequest(BaseModel):
    data: dict
    overwrite: bool = True  # True=覆盖已有值，False=仅填充空值


@router.post("/import")
def import_social_config(body: ImportRequest):
    """从 JSON（之前 /export 的产物）批量恢复配置。

    - overwrite=True：导入文件中非空的字段会覆盖当前值
    - overwrite=False：仅当当前值为空时才写入
    """
    from app.config import settings

    env = _read_env()
    updates = {}
    incoming = body.data or {}
    for key in ALL_SOCIAL_KEYS:
        new_val = incoming.get(key)
        if new_val is None or new_val == "":
            continue
        cur_val = env.get(key, "")
        if body.overwrite or not cur_val:
            updates[key] = new_val
            # 同步到 runtime settings（若存在）
            if hasattr(settings, key):
                setattr(settings, key, new_val)

    if updates:
        env.update(updates)
        _write_env(env)

    return {"success": True, "imported_keys": list(updates.keys()), "count": len(updates)}


# ── Cookie 有效性测试 ────────────────────────────────────

@router.post("/cookies/test/{platform}")
def test_cookie(platform: str, limit: int = 1):
    """触发一次小规模拉取（limit 条）验证 Cookie / 订阅是否有效。

    返回：success=True 表示 Cookie 有效且至少拉到了 1 条内容；
          success=False 且 detail 会说明原因（Cookie 失效 / 未配置账号 / 中间件不可达）。
    """
    from app.mcps.social_media_mcp import SocialMediaMCP

    valid_platforms = {"twitter", "weibo", "xiaohongshu", "douyin", "wechat_mp"}
    if platform not in valid_platforms:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid platform: {platform}. Valid: {sorted(valid_platforms)}",
        )

    try:
        mcp = SocialMediaMCP()
        resp = mcp.fetch_platform(platform, limit=limit)
        if not resp.success:
            return {
                "success": False,
                "platform": platform,
                "reason": "fetch_failed",
                "detail": (resp.errors or resp.error or "unknown error"),
            }
        items = resp.data or []
        if not items:
            return {
                "success": False,
                "platform": platform,
                "reason": "empty_result",
                "detail": "拉取成功但返回 0 条，可能是 Cookie 失效 / 账号未配置 / 该平台无新内容",
                "warnings": resp.warnings,
            }
        sample = items[0]
        return {
            "success": True,
            "platform": platform,
            "fetched_count": len(items),
            "sample": {
                "title": (sample.get("title") or "")[:120],
                "author": sample.get("author"),
                "published": sample.get("event_timestamp"),
                "source_url": sample.get("source_url"),
            },
            "warnings": resp.warnings,
        }
    except Exception as e:
        return {
            "success": False,
            "platform": platform,
            "reason": "exception",
            "detail": str(e)[:300],
        }


# ── 最近入库内容预览 ─────────────────────────────────────

# 平台 → source_type 映射（与 social_subscription_service._infer_source_type 保持一致）
_PLATFORM_TO_SOURCE_TYPE = {
    "twitter":     "tweet",
    "weibo":       "tweet",
    "xiaohongshu": "note",
    "douyin":      "video",
    "wechat_mp":   "article",
}


@router.get("/recent")
def get_recent_ingested(platform: Optional[str] = None, limit: int = 20):
    """查看最近入库的社媒内容（从 RawSource 表读取）。

    - platform 可选: twitter/weibo/xiaohongshu/douyin/wechat_mp/all
    - 返回按 created_at DESC 的最新 N 条
    """
    from app.database import SessionLocal
    from app.schemas.raw_source import RawSource

    social_types = {"tweet", "note", "video", "article", "social"}
    db = SessionLocal()
    try:
        q = db.query(RawSource)

        if platform and platform != "all":
            src_type = _PLATFORM_TO_SOURCE_TYPE.get(platform)
            if src_type:
                q = q.filter(RawSource.source_type == src_type)
            # speaker_hint 常含 platform 字段（作者名或 platform），再二次过滤更精确
            # 但为简化，仅按 source_type 过滤足矣
        else:
            q = q.filter(RawSource.source_type.in_(social_types))

        rows = q.order_by(RawSource.created_at.desc()).limit(max(1, min(limit, 100))).all()

        data = []
        for r in rows:
            data.append({
                "id": r.id,
                "source_type": r.source_type,
                "title": (r.title or "")[:200],
                "author": r.speaker_hint,
                "publisher": r.publisher,
                "url": r.original_url,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "preview": (r.raw_text or "")[:300],
                "theme": r.theme_hint,
                "word_count": r.word_count,
            })

        return {"success": True, "platform": platform or "all", "count": len(data), "data": data}
    finally:
        db.close()


# ── 视频转写测试 ─────────────────────────────────────────

@router.post("/transcribe-test")
def test_video_transcribe(video_url: str, hint: str = ""):
    """测试视频转文字功能"""
    from app.services.video_transcriber import transcribe_video
    try:
        result = transcribe_video(video_url, hint=hint)
        success = not result.startswith("[视频转写失败")
        return {"success": success, "transcript": result, "length": len(result)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── 健康检查 ─────────────────────────────────────────────

@router.get("/health")
def social_media_health():
    """检查所有社媒服务的可达性"""
    import requests as req
    from app.config import settings

    checks = {}
    timeout = 5

    # RSSHub
    try:
        r = req.get(f"{settings.RSSHUB_BASE_URL}/", timeout=timeout)
        checks["rsshub"] = {"status": "ok" if r.status_code == 200 else "error", "http_code": r.status_code}
    except Exception as e:
        checks["rsshub"] = {"status": "unreachable", "error": str(e)[:100]}

    # MediaCrawler
    try:
        r = req.get(f"{settings.MEDIACRAWLER_API_URL}/", timeout=timeout)
        checks["mediacrawler"] = {"status": "ok" if r.status_code in (200, 404) else "error", "http_code": r.status_code}
    except Exception as e:
        checks["mediacrawler"] = {"status": "unreachable", "error": str(e)[:100]}

    # We-MP-RSS
    try:
        r = req.get(f"{settings.WEMPRSS_BASE_URL}/", timeout=timeout)
        checks["wemprss"] = {"status": "ok" if r.status_code == 200 else "error", "http_code": r.status_code}
    except Exception as e:
        checks["wemprss"] = {"status": "unreachable", "error": str(e)[:100]}

    # Google AI API Key
    checks["google_ai"] = {
        "status": "configured" if settings.GOOGLE_AI_API_KEY else "not_configured",
    }

    all_ok = all(c.get("status") in ("ok", "configured") for c in checks.values())
    return {"success": True, "all_healthy": all_ok, "services": checks}


# ── .env 文件读写（复用 settings.py 的模式）──────────────

def _read_env() -> dict:
    result = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    return result


def _write_env(data: dict):
    lines = []
    if ENV_PATH.exists():
        existing_lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        written_keys = set()
        for line in existing_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in data:
                    lines.append(f"{k}={data[k]}")
                    written_keys.add(k)
                else:
                    lines.append(line)
            else:
                lines.append(line)
        for k, v in data.items():
            if k not in written_keys:
                lines.append(f"{k}={v}")
    else:
        for k, v in data.items():
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
