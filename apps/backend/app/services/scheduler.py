"""Scheduler Service

使用 asyncio 实现轻量定时任务，无需 Celery/Redis，内嵌到 FastAPI 进程。

定时任务：
1. auto_briefing_job       — 每 6 小时，对 Watchlist 中的资产自动生成 Briefing
2. lifecycle_check_job     — 每小时，检查并更新 Claim 生命周期状态
3. snapshot_capture_job    — 市场快照采集（启动时 + 每天 Asia/Shanghai 12:00 各一次）
4. indicator_snapshot_job  — Pizza / Fear&Greed / Polymarket 等衡生指标（与快照同步触发）
5. fund_focus_capture_job  — A股基金关注时段（14:00-15:00）增强采集
6. daily_radar_job         — 每 12 小时，主动发现 / 每日雷达（V2）
7. social_subscription_job — 每 N 分钟（默认 360=6h），社媒订阅内容持久化 + 自动入库
8. fund_eod_reconciliation_job — 每天 17:00 SH，标记当日 EOD 估值快照
9. auto_pnl_tracker_job    — 每天 03:00 SH，扫到期 DecisionSnapshot 自动算 PnL + 判方向
10. ingestion_stale_reaper_job — 每 N 分钟，恢复 lease 过期的入库任务
"""
import asyncio
from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo
from loguru import logger

from app.services.distributed_lock import with_distributed_lock

_scheduler_task: Optional[asyncio.Task] = None
# 用来防止同一天 12:00 多次触发（当进程长期在线时）
_last_daily_snapshot_date: Optional[date] = None
_last_eod_recon_date: Optional[date] = None  # G3: 防止同一天 17:00 EOD 多次触发
_last_auto_pnl_date: Optional[date] = None  # G7: 防止同一天 03:00 PnL 追踪多次触发
_last_snapshot_at: Optional[datetime] = None
_last_snapshot_result: dict = {"requested": 0, "saved": 0, "failed": 0, "triggered_by": None}


@with_distributed_lock("scheduler:auto_briefing", ttl=1800)  # 30 min, briefing 耗时较长
async def auto_briefing_job():
    """对 Watchlist 热门资产自动生成 Briefing（线程池执行，不阻塞事件循环）"""
    try:
        def _sync_work():
            from app.database import SessionLocal
            from app.schemas.watchlist import WatchlistItem
            from app.workflows.briefing_workflow import BriefingWorkflow

            db = SessionLocal()
            try:
                items = db.query(WatchlistItem).filter(
                    WatchlistItem.status == "active"
                ).limit(5).all()
                assets = list({i.item_key.upper() for i in items if i.item_key})

                if not assets:
                    logger.info("[Scheduler] auto_briefing: no watchlist assets, skip")
                    return

                workflow = BriefingWorkflow(db)
                for asset in assets[:3]:
                    try:
                        result = workflow.generate(
                            question=f"{asset} 当前市场状态综合研判",
                            asset_scope=[asset],
                            committee_level="single",
                            triggered_by="scheduler",
                        )
                        if result.get("success"):
                            logger.info(f"[Scheduler] auto_briefing: {asset} briefing generated ({result.get('briefing_id')})")
                        else:
                            logger.warning(f"[Scheduler] auto_briefing: {asset} failed — {result.get('error')}")
                    except Exception as e:
                        logger.warning(f"[Scheduler] auto_briefing: {asset} exception — {e}")
            finally:
                db.close()

        await asyncio.to_thread(_sync_work)
    except Exception as e:
        logger.error(f"[Scheduler] auto_briefing_job error: {e}")


@with_distributed_lock("scheduler:lifecycle_check", ttl=600)  # 10 min, batch_check 不超过
async def lifecycle_check_job():
    """检查并更新 Claim 生命周期状态（线程池执行，不阻塞事件循环）"""
    try:
        def _sync_work():
            from app.database import SessionLocal
            from app.workflows.lifecycle_workflow import LifecycleWorkflow

            db = SessionLocal()
            try:
                workflow = LifecycleWorkflow(db)
                result = workflow.run_batch_check(limit=50)
                if result.get("processed", 0) > 0:
                    logger.info(f"[Scheduler] lifecycle_check: {result}")
            finally:
                db.close()

        await asyncio.to_thread(_sync_work)
    except Exception as e:
        logger.error(f"[Scheduler] lifecycle_check_job error: {e}")


@with_distributed_lock("scheduler:snapshot_capture", ttl=900)  # 15 min, 多 symbol 串行下载
async def snapshot_capture_job(
    force_symbols: Optional[list[str]] = None,
    include_indicators: bool = True,
    triggered_by: str = "scheduler",
):
    """市场快照采集任务（免费源友好）

    注意：capture_market_snapshots 内部使用同步 requests 库调用外部 API，
    必须用 asyncio.to_thread 包装，否则会阻塞整个 FastAPI 事件循环。

    Args:
        force_symbols: 指定要采集的 symbol 子集，None 则用 get_default_symbols
        include_indicators: 是否同步触发 Pizza/Fear&Greed/Polymarket 等衡生指标
        triggered_by: 触发来源，写入最后一次结果（scheduler / manual / startup）
    """
    global _last_snapshot_at, _last_snapshot_result
    try:
        from app.database import SessionLocal
        from app.services.snapshot_service import (
            capture_market_snapshots, capture_indicator_snapshots, get_default_symbols,
        )

        def _sync_work():
            db = SessionLocal()
            try:
                symbols = force_symbols or get_default_symbols(db)
                res = capture_market_snapshots(db, symbols=symbols, timeframe="1d")
                ind_res = None
                if include_indicators:
                    try:
                        ind_res = capture_indicator_snapshots(db)
                    except Exception as e:
                        logger.warning(f"[Scheduler] indicator_snapshots failed (non-fatal): {e}")
                return res, ind_res
            finally:
                db.close()

        res, ind_res = await asyncio.to_thread(_sync_work)
        _last_snapshot_at = datetime.now(ZoneInfo("UTC"))
        _last_snapshot_result = {
            "requested": res.requested,
            "saved": res.saved,
            "failed": res.failed,
            "funds": res.estimated_funds,
            "indicators": ind_res or {},
            "triggered_by": triggered_by,
            "ts": _last_snapshot_at.isoformat(),
        }
        logger.info(
            f"[Scheduler] snapshot_capture[{triggered_by}]: "
            f"market(req={res.requested}, saved={res.saved}, failed={res.failed}, funds={res.estimated_funds}) "
            f"indicators={ind_res}"
        )
    except Exception as e:
        logger.error(f"[Scheduler] snapshot_capture_job error: {e}")


def get_last_snapshot_info() -> dict:
    """查询最后一次快照采集的结果（供手动触发 API 返回状态）。"""
    return dict(_last_snapshot_result)


@with_distributed_lock("scheduler:daily_radar", ttl=1800)  # 30 min, OpportunityScout LLM 耗时较长
async def daily_radar_job():
    """V2: 每日雷达 / 主动发现（线程池执行）"""
    try:
        def _sync_work():
            from app.database import SessionLocal
            from app.schemas.watchlist import WatchlistItem
            from app.services.opportunity_scout import OpportunityScout

            db = SessionLocal()
            try:
                items = db.query(WatchlistItem).filter(
                    WatchlistItem.status == "active"
                ).limit(10).all()
                assets = list({i.item_key.upper() for i in items if i.item_key})

                if not assets:
                    logger.info("[Scheduler] daily_radar: no watchlist assets, skip")
                    return

                scout = OpportunityScout(db)
                cards = scout.scan(watchlist_assets=assets)
                summary = scout.format_daily_radar(cards)
                logger.info(f"[Scheduler] daily_radar: {len(cards)} opportunities found")
                logger.info(f"[Scheduler] daily_radar summary:\n{summary}")
            finally:
                db.close()

        await asyncio.to_thread(_sync_work)
    except Exception as e:
        logger.error(f"[Scheduler] daily_radar_job error: {e}")


@with_distributed_lock("scheduler:mediacrawler_refresh", ttl=1800)  # 30 min, headless 爬取耗时最长
async def refresh_mediacrawler_data(
    staleness_hours: int = 5,
    max_wait_seconds: int = 600,
) -> dict:
    """如 MediaCrawler 输出数据已陈旧，触发 headless 爬取并等待完成。

    仅当有配置博主且 JSONL 文件 mtime > staleness_hours 时才触发。
    xhs 和 douyin 串行执行（MediaCrawler 只能同时跑一个任务）。

    返回：{platform: {triggered, skipped, reason, elapsed_s}}
    """
    import time
    from pathlib import Path
    import requests as req
    from app.config import settings

    api_base = settings.MEDIACRAWLER_API_URL.rstrip("/")
    mc_root = Path(settings.MEDIACRAWLER_ROOT)
    now = time.time()
    summary: dict = {}

    for platform, dirname, mc_platform, creator_ids in (
        ("xhs", "xhs", "xhs", settings.SOCIAL_XHS_USER_IDS),
        ("douyin", "douyin", "dy", settings.SOCIAL_DOUYIN_USER_IDS),
    ):
        # 0. 没有博主就跳过
        if not creator_ids.strip():
            summary[platform] = {"triggered": False, "skipped": True, "reason": "no creators configured"}
            continue

        # 1. 检查数据新鲜度
        jsonl_dir = mc_root / "data" / dirname / "jsonl"
        latest_mtime = 0.0
        if jsonl_dir.exists():
            files = list(jsonl_dir.glob("*_contents_*.jsonl"))
            latest_mtime = max((f.stat().st_mtime for f in files), default=0.0)
        age_hours = (now - latest_mtime) / 3600 if latest_mtime else float("inf")

        if age_hours < staleness_hours:
            summary[platform] = {
                "triggered": False,
                "skipped": True,
                "reason": f"fresh ({age_hours:.1f}h old)",
            }
            continue

        # 2. 触发 headless 爬取
        payload = {
            "platform": mc_platform,
            "login_type": "qrcode",
            "crawler_type": "creator",
            "creator_ids": creator_ids,
            "save_option": "jsonl",
            "enable_comments": False,
            "headless": True,
        }
        start_ts = time.time()
        try:
            r = await asyncio.to_thread(
                lambda: req.post(f"{api_base}/api/crawler/start", json=payload, timeout=10)
            )
            if r.status_code != 200:
                summary[platform] = {
                    "triggered": False,
                    "skipped": True,
                    "reason": f"start failed HTTP {r.status_code}: {r.text[:100]}",
                }
                continue
        except req.exceptions.RequestException as e:
            summary[platform] = {
                "triggered": False,
                "skipped": True,
                "reason": f"MediaCrawler unreachable: {type(e).__name__}",
            }
            continue

        logger.info(f"[Scheduler] MediaCrawler {platform} crawl triggered (age={age_hours:.1f}h)")

        # 3. 轮询状态直到 idle，或超时
        polled = 0
        final_status: Optional[str] = None
        while polled < max_wait_seconds:
            await asyncio.sleep(15)
            polled += 15
            try:
                s = await asyncio.to_thread(
                    lambda: req.get(f"{api_base}/api/crawler/status", timeout=5)
                )
                final_status = s.json().get("status") if s.status_code == 200 else None
                if final_status in ("idle", "error"):
                    break
            except req.exceptions.RequestException:
                break

        elapsed = time.time() - start_ts
        summary[platform] = {
            "triggered": True,
            "skipped": False,
            "elapsed_s": int(elapsed),
            "final_status": final_status or "unknown",
            "timed_out": polled >= max_wait_seconds,
        }
        logger.info(
            f"[Scheduler] MediaCrawler {platform} finished in {elapsed:.0f}s "
            f"(timed_out={summary[platform]['timed_out']})"
        )

    return summary


@with_distributed_lock("scheduler:auto_pnl_tracker", ttl=600)  # 10 min, 仅 DB 内查询
async def auto_pnl_tracker_job():
    """G7 · 扫所有到期的 pending DecisionSnapshot，自动算 PnL + 判方向.

    数据来源：完全靠系统已收集的 MarketSnapshot 表（启动 / 12:00 / 14:00-15:00 抓取）。
    不调外部数据源，避免后端进程在没网时 hang。找不到 exit price 的就跳过保留 pending。

    幂等：同一 snapshot 在 outcome_direction 已写入后不会被重复处理。
    不修改 replay_status，保留给用户/LLM 后续做 replay 分析。
    """
    try:
        from app.database import SessionLocal
        from app.services.auto_pnl_tracker import auto_track_pnl

        def _sync_work():
            db = SessionLocal()
            try:
                return auto_track_pnl(db)
            finally:
                db.close()

        summary = await asyncio.to_thread(_sync_work)
        if summary.get("evaluated", 0) > 0 or summary.get("errors", 0) > 0:
            logger.info(
                f"[Scheduler] auto_pnl_tracker: checked={summary['checked']}, "
                f"evaluated={summary['evaluated']}, too_early={summary['skipped_too_early']}, "
                f"no_entry={summary['skipped_no_entry']}, no_exit={summary['skipped_no_exit']}, "
                f"errors={summary['errors']}"
            )
        else:
            logger.debug(f"[Scheduler] auto_pnl_tracker: {summary['checked']} pending, none ready")
    except Exception as e:
        logger.error(f"[Scheduler] auto_pnl_tracker_job error: {e}")


@with_distributed_lock("scheduler:fund_eod_reconciliation", ttl=300)  # 5 min, EOD 标记很快
async def fund_eod_reconciliation_job():
    """G3 · 每天 EOD 标记当日最后一次 FundEstimateSnapshot 为 is_eod_final.

    用户后续可以在「基金对账」页面填入真实 NAV，系统自动算误差统计。
    幂等：当天已标记的不会被重复处理。
    """
    try:
        from app.database import SessionLocal
        from app.services.fund_reconciliation import mark_eod_finals

        def _sync_work():
            db = SessionLocal()
            try:
                return mark_eod_finals(db)
            finally:
                db.close()

        summary = await asyncio.to_thread(_sync_work)
        logger.info(f"[Scheduler] fund_eod_reconciliation_job: {summary}")
    except Exception as e:
        logger.error(f"[Scheduler] fund_eod_reconciliation_job error: {e}")


@with_distributed_lock("scheduler:social_subscription", ttl=1200)  # 20 min, ingest 多源 + RSS 延迟
async def social_subscription_job():
    """社交媒体订阅内容同步（线程池执行）

    拉取所有已订阅博主的最新内容，去重后持久化为 RawSource，
    自动触发 ingestion pipeline 提取观点。
    """
    try:
        def _sync_work():
            from app.database import SessionLocal
            from app.services.social_subscription_service import SocialSubscriptionService

            db = SessionLocal()
            try:
                svc = SocialSubscriptionService(db)
                result = svc.sync_all(auto_ingest=True)
                new = result.get("new_saved", 0)
                ingested = result.get("ingested", 0)
                if new > 0:
                    logger.info(
                        f"[Scheduler] social_subscription: "
                        f"fetched={result['fetched']}, new={new}, "
                        f"ingested={ingested}, dup={result['duplicates_skipped']}"
                    )
                else:
                    logger.debug("[Scheduler] social_subscription: no new items")

                # 自动重试之前失败的 ingestion
                retry = svc.retry_failed_ingestions(limit=10)
                if retry.get("recovered", 0) > 0:
                    logger.info(f"[Scheduler] social_subscription retry: {retry}")
            finally:
                db.close()

        await asyncio.to_thread(_sync_work)
    except Exception as e:
        logger.error(f"[Scheduler] social_subscription_job error: {e}")


@with_distributed_lock("scheduler:ingestion_stale_reaper", ttl=300)
async def ingestion_stale_reaper_job(triggered_by: str = "scheduler") -> dict:
    """Recover ingestion jobs whose persisted worker lease has expired."""
    try:
        def _sync_work():
            from app.config import settings as _cfg
            from app.database import SessionLocal
            from app.services.ingestion_job_recovery import reap_stale_ingestion_jobs

            db = SessionLocal()
            try:
                return reap_stale_ingestion_jobs(
                    db,
                    limit=_cfg.INGESTION_RECOVERY_MAX_JOBS,
                    resume=True,
                    max_retries=_cfg.INGESTION_RECOVERY_MAX_RETRIES,
                )
            finally:
                db.close()

        stats = await asyncio.to_thread(_sync_work)
        result = {**stats, "triggered_by": triggered_by}
        if any(result.get(key, 0) for key in ("stale", "resumed", "failed", "dead_lettered")):
            logger.info(f"[Scheduler] ingestion_stale_reaper: {result}")
        return result
    except Exception as e:
        logger.error(f"[Scheduler] ingestion_stale_reaper_job error: {e}")
        return {"triggered_by": triggered_by, "error": str(e)}


async def _scheduler_loop():
    """主调度循环（按分钟 tick）

    市场快照规则（用户要求：频率不高）：
    - 服务启动时抓一次
    - 每天 Asia/Shanghai 12:00 自动抓一次（如当天还未抓过）
    - A股 14:00-14:59 小范围增强采样（基金净值估值场景）
    """
    global _last_daily_snapshot_date, _last_eod_recon_date, _last_auto_pnl_date
    logger.info("[Scheduler] started")
    tick_minute = 0

    # 启动时延迟 5 秒后做第一次快照，让服务器先就绪
    await asyncio.sleep(5)
    try:
        await snapshot_capture_job(triggered_by="startup")
        _last_daily_snapshot_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    except Exception as e:
        logger.warning(f"[Scheduler] initial snapshot failed (non-fatal): {e}")

    while True:
        try:
            await asyncio.sleep(60)
            tick_minute += 1

            now_sh = datetime.now(ZoneInfo("Asia/Shanghai"))
            today_sh = now_sh.date()
            from app.config import settings as _cfg

            # 每 N 分钟：恢复 lease 过期的入库任务，避免后台 worker 消失后进度永久卡住
            _reaper_interval = max(int(_cfg.INGESTION_STALE_REAPER_INTERVAL_MINUTES or 5), 1)
            if _cfg.INGESTION_RECOVERY_ENABLED and tick_minute % _reaper_interval == 0:
                await ingestion_stale_reaper_job()

            # 每天 Asia/Shanghai 12:00 自动快照（如当天还未抓过）
            if (
                now_sh.hour == 12
                and now_sh.minute < 5          # 给 5 分钟的窗口防止 tick 错过
                and _last_daily_snapshot_date != today_sh
            ):
                logger.info("[Scheduler] daily noon snapshot window reached")
                await snapshot_capture_job(triggered_by="daily_noon")
                _last_daily_snapshot_date = today_sh

            # 每小时：生命周期检查
            if tick_minute % 60 == 0:
                await lifecycle_check_job()

            # 每 6 小时：自动 briefing
            if tick_minute % (60 * 6) == 0:
                await auto_briefing_job()

            # 每 12 小时：每日雷达
            if tick_minute % (60 * 12) == 0:
                await daily_radar_job()

            # 社媒订阅同步（频率由 SOCIAL_FETCH_INTERVAL_MINUTES 配置，默认每 6 小时）
            _social_interval = max(_cfg.SOCIAL_FETCH_INTERVAL_MINUTES, 30)
            if tick_minute % _social_interval == 0:
                # 先 headless 刷新 MediaCrawler 数据（小红书 / 抖音）
                try:
                    mc_summary = await refresh_mediacrawler_data(
                        staleness_hours=max(_cfg.SOCIAL_FETCH_INTERVAL_MINUTES / 60 - 1, 1)
                    )
                    if any(v.get("triggered") for v in mc_summary.values()):
                        logger.info(f"[Scheduler] MediaCrawler refresh: {mc_summary}")
                except Exception as e:
                    logger.warning(f"[Scheduler] MediaCrawler refresh failed (non-fatal): {e}")
                # 然后再跑订阅同步（读取刚刷新的 JSONL）
                await social_subscription_job()

            # A股基金关注时段：14:00-14:59 每 10 分钟小范围采样（原每 2 分钟降低频率）
            if now_sh.hour == 14 and now_sh.minute % 10 == 0:
                await snapshot_capture_job(
                    force_symbols=["CSI300", "ZZ500", "CYB", "HSI", "HSTECH"],
                    include_indicators=False,  # 盘中增强采样不重复抓指标
                    triggered_by="a_share_intraday",
                )

            # G3: A 股 15:00 收盘 + 2h 后（17:00）跑 EOD 对账标记
            if (
                now_sh.hour == 17
                and now_sh.minute < 5
                and _last_eod_recon_date != today_sh
            ):
                logger.info("[Scheduler] daily EOD reconciliation window reached")
                await fund_eod_reconciliation_job()
                _last_eod_recon_date = today_sh

            # G7: 每天 03:00 SH 跑 auto PnL 追踪（避开 backup 02:00 + 1h 缓冲）
            if (
                now_sh.hour == 3
                and now_sh.minute < 5
                and _last_auto_pnl_date != today_sh
            ):
                logger.info("[Scheduler] daily auto PnL tracker window reached")
                await auto_pnl_tracker_job()
                _last_auto_pnl_date = today_sh

        except asyncio.CancelledError:
            logger.info("[Scheduler] cancelled, shutting down")
            break
        except Exception as e:
            logger.error(f"[Scheduler] loop error: {e}")
            await asyncio.sleep(30)


def start_scheduler():
    """在 FastAPI startup 时调用。

    满足以下任一条件则跳过：
    - `APP_ENV == 'test'`（避免测试期间触发 LLM/外网调用 + 数据库写入）
    - `APP_SCHEDULER_ENABLED == false`（多实例部署时 backend-only pod 用，
      让独立的 scheduler pod 接管）
    """
    from app.config import settings as _cfg
    if _cfg.APP_ENV == "test":
        logger.info("[Scheduler] APP_ENV=test, scheduler disabled")
        return
    if not _cfg.APP_SCHEDULER_ENABLED:
        logger.info("[Scheduler] APP_SCHEDULER_ENABLED=false, scheduler disabled")
        return
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("[Scheduler] task created")


def stop_scheduler():
    """在 FastAPI shutdown 时调用：取消调度循环 task。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        _scheduler_task.cancel()
        logger.info("[Scheduler] task cancelled")
