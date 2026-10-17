"""API 调用边界控制服务 (API Quota Guard)

设计原则：
1. 「不主动调用」— 数据不会在后台自动轮询，只有用户提问时才触发
2. 「每日上限」 — 每日更新频率有限的数据源（FRED/Alpha Vantage）每天只调
               用一次，结果缓存到次日 00:00 UTC
3. 「冷却窗口」 — 高频数据源（Binance/Yahoo）在冷却期内直接返回 DB 存量，
               不重复调用外部 API
4. 「请求合并」 — 同一数据在冷却期内多次被问到时，只调用一次外部 API

数据源更新频率分类：
  DAILY   — 每天最多调用 1 次（FRED 宏观数据、Alpha Vantage 新闻情感）
  HOURLY  — 每小时最多调用 1 次（RSS 新闻源）
  FAST    — 5 分钟冷却（Binance 行情、Yahoo Finance 价格）
  MEDIUM  — 3 分钟冷却（Binance Futures 资金费率/OI/多空比）

TTL 存储后端：
  优先使用 Redis；Redis 不可用时降级到内存字典（进程重启后失效，但不影响功能）。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from loguru import logger

# ── TTL 配置（单位：秒）──────────────────────────────────────
COOLDOWN = {
    "DAILY":  86400,   # 24 h — FRED / Alpha Vantage
    "HOURLY":  3600,   # 1 h  — RSS 新闻源
    "FAST":     300,   # 5 min — Binance 现货 / Yahoo Finance
    "MEDIUM":   180,   # 3 min — Binance Futures 资金流
}

# key 前缀
_PREFIX = "adms:quota:"

# 内存降级字典：{key: expire_timestamp}
_mem_store: Dict[str, float] = {}


# ── 底层存储操作 ──────────────────────────────────────────────

def _redis_client():
    """获取 Redis 客户端（失败返回 None）"""
    try:
        from app.services.cache import _get_client
        return _get_client()
    except Exception:
        return None


def _is_locked(key: str) -> bool:
    """检查某个 key 是否还在冷却期（True = 还在冷却，禁止调用）"""
    full_key = _PREFIX + key

    # Redis 优先
    client = _redis_client()
    if client:
        try:
            return client.exists(full_key) == 1
        except Exception:
            pass

    # 内存降级
    expire_ts = _mem_store.get(full_key)
    if expire_ts and time.time() < expire_ts:
        return True
    # 过期了，清理
    _mem_store.pop(full_key, None)
    return False


def _lock(key: str, ttl: int) -> None:
    """设置冷却锁，ttl 秒后自动解锁"""
    full_key = _PREFIX + key

    client = _redis_client()
    if client:
        try:
            client.setex(full_key, ttl, "1")
            return
        except Exception:
            pass

    # 内存降级
    _mem_store[full_key] = time.time() + ttl


def _ttl_remaining(key: str) -> int:
    """返回剩余冷却秒数（0 表示已解锁）"""
    full_key = _PREFIX + key

    client = _redis_client()
    if client:
        try:
            ttl = client.ttl(full_key)
            return max(0, ttl)
        except Exception:
            pass

    expire_ts = _mem_store.get(full_key)
    if expire_ts:
        remaining = int(expire_ts - time.time())
        return max(0, remaining)
    return 0


# ── 每日 TTL 计算 ─────────────────────────────────────────────

def _seconds_until_midnight_utc() -> int:
    """计算距离今日 UTC 00:00:00 的剩余秒数（用于 DAILY 类型）"""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(60, int((tomorrow - now).total_seconds()))


# ── 公开 API ──────────────────────────────────────────────────

class QuotaGuard:
    """API 调用边界守卫

    用法：
        guard = QuotaGuard()

        # 检查是否允许调用
        if guard.allow("fred_macro", "DAILY"):
            data = _call_fred_api()
            guard.mark_called("fred_macro", "DAILY")  # 标记已调用
        else:
            # 冷却期内，直接使用 DB / 缓存存量
            data = _read_from_db()
    """

    def allow(self, resource: str, freq: str = "FAST") -> bool:
        """判断是否允许调用外部 API。

        Args:
            resource: 资源标识，如 "fred_macro" / "binance_BTC_1d" / "alpha_vantage_BTC"
            freq: 更新频率类别，DAILY / HOURLY / FAST / MEDIUM

        Returns:
            True = 允许调用（冷却期已结束或首次调用）
            False = 禁止调用（仍在冷却期）
        """
        key = f"{resource}:{freq}"
        locked = _is_locked(key)
        if locked:
            remaining = _ttl_remaining(key)
            logger.debug(f"[QuotaGuard] BLOCKED {resource} ({freq}), {remaining}s remaining")
        return not locked

    def mark_called(self, resource: str, freq: str = "FAST") -> None:
        """标记资源已被调用，开始冷却计时。

        DAILY 类型的冷却时间对齐到当天 UTC 午夜，
        其他类型使用固定冷却窗口。
        """
        key = f"{resource}:{freq}"
        if freq == "DAILY":
            ttl = _seconds_until_midnight_utc()
        else:
            ttl = COOLDOWN.get(freq, COOLDOWN["FAST"])
        _lock(key, ttl)
        logger.debug(f"[QuotaGuard] LOCKED {resource} ({freq}) for {ttl}s")

    def status(self, resource: str, freq: str = "FAST") -> dict:
        """返回某资源的当前配额状态（用于 debug / 监控）"""
        key = f"{resource}:{freq}"
        remaining = _ttl_remaining(key)
        return {
            "resource": resource,
            "freq": freq,
            "allowed": remaining == 0,
            "cooldown_remaining_sec": remaining,
            "cooldown_total_sec": COOLDOWN.get(freq, 0) if freq != "DAILY" else 86400,
        }

    def reset(self, resource: str, freq: str = "FAST") -> None:
        """强制解除某资源的冷却锁（用于调试/手动刷新）"""
        from app.services.cache import delete
        key = f"{resource}:{freq}"
        full_key = _PREFIX + key
        delete(full_key)
        _mem_store.pop(full_key, None)
        logger.info(f"[QuotaGuard] RESET {resource} ({freq})")


# 单例
guard = QuotaGuard()


# ── 便捷函数（各 MCP 直接导入使用）─────────────────────────────

def allow_market(asset: str, timeframe: str = "1d") -> bool:
    """行情数据（Binance / Yahoo）：5 分钟冷却"""
    return guard.allow(f"market_{asset.upper()}_{timeframe}", "FAST")


def mark_market_called(asset: str, timeframe: str = "1d") -> None:
    guard.mark_called(f"market_{asset.upper()}_{timeframe}", "FAST")


def allow_flow(asset: str) -> bool:
    """资金流数据（Binance Futures）：3 分钟冷却"""
    return guard.allow(f"flow_{asset.upper()}", "MEDIUM")


def mark_flow_called(asset: str) -> None:
    guard.mark_called(f"flow_{asset.upper()}", "MEDIUM")


def allow_events(asset: Optional[str] = None) -> bool:
    """事件/新闻数据（RSS / Alpha Vantage）：1 小时冷却"""
    key = f"events_{asset.upper()}" if asset else "events_global"
    return guard.allow(key, "HOURLY")


def mark_events_called(asset: Optional[str] = None) -> None:
    key = f"events_{asset.upper()}" if asset else "events_global"
    guard.mark_called(key, "HOURLY")


def allow_fred() -> bool:
    """FRED 宏观数据：每天只调用一次，对齐 UTC 午夜"""
    return guard.allow("fred_macro", "DAILY")


def mark_fred_called() -> None:
    guard.mark_called("fred_macro", "DAILY")


def allow_alpha_vantage(asset: str) -> bool:
    """Alpha Vantage 新闻情感：每天最多 25 次，此处按资产每天一次"""
    return guard.allow(f"alpha_vantage_{asset.upper()}", "DAILY")


def mark_alpha_vantage_called(asset: str) -> None:
    guard.mark_called(f"alpha_vantage_{asset.upper()}", "DAILY")


def get_all_status() -> list:
    """返回所有常见资源的配额状态（用于 /settings 或 /health 端点）"""
    common = [
        ("fred_macro", "DAILY"),
        ("events_global", "HOURLY"),
        ("events_BTC", "HOURLY"),
        ("alpha_vantage_BTC", "DAILY"),
        ("alpha_vantage_ETH", "DAILY"),
        ("market_BTC_1d", "FAST"),
        ("market_ETH_1d", "FAST"),
        ("market_SPX_1d", "FAST"),
        ("flow_BTC", "MEDIUM"),
        ("flow_ETH", "MEDIUM"),
    ]
    return [guard.status(r, f) for r, f in common]
