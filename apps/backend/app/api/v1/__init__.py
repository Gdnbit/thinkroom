from fastapi import APIRouter

from app.api.v1 import (
    claims, events, strategies, briefings,
    watchlists, upload, health, signals, ingestion, lifecycle, verification, settings, knowledge,
    outcomes, speakers, committee, alerts, chat, models, market_history, costs, progress, datasources,
    fund_valuation, backtest, social_media, overrides, replay, radar, ingestion_audit,
    routing_feedback, snapshots, evaluate, quality_monitor, fund_reconciliation,
    fund_profiles, data_repair, decision_traces,
)

router = APIRouter()

router.include_router(health.router, prefix="/health", tags=["health"])
router.include_router(upload.router, prefix="/upload", tags=["upload"])
router.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
router.include_router(claims.router, prefix="/claims", tags=["claims"])
router.include_router(events.router, prefix="/events", tags=["events"])
router.include_router(signals.router, prefix="/signals", tags=["signals"])
router.include_router(market_history.router, prefix="/market-history", tags=["market-history"])
router.include_router(strategies.router, prefix="/strategies", tags=["strategies"])
router.include_router(briefings.router, prefix="/briefings", tags=["briefings"])
router.include_router(watchlists.router, prefix="/watchlists", tags=["watchlists"])
router.include_router(lifecycle.router, prefix="/lifecycle", tags=["lifecycle"])
router.include_router(verification.router, prefix="/verification", tags=["verification"])
router.include_router(settings.router, prefix="/settings", tags=["settings"])
router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
router.include_router(outcomes.router, prefix="/outcomes", tags=["outcomes"])
router.include_router(speakers.router, prefix="/speakers", tags=["speakers"])
router.include_router(committee.router, prefix="/committee", tags=["committee"])
router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
router.include_router(chat.router, prefix="/chat", tags=["chat"])
router.include_router(costs.router, prefix="/costs", tags=["costs"])
router.include_router(progress.router, prefix="/progress", tags=["progress"])
router.include_router(datasources.router, prefix="/datasources", tags=["datasources"])
router.include_router(fund_valuation.router, prefix="/fund", tags=["fund"])
router.include_router(backtest.router, prefix="/backtest", tags=["backtest"])
router.include_router(social_media.router, prefix="/social-media", tags=["social-media"])
router.include_router(overrides.router, prefix="/overrides", tags=["overrides"])
router.include_router(replay.router, prefix="/replay", tags=["replay"])
router.include_router(radar.router, prefix="/radar", tags=["radar"])
router.include_router(ingestion_audit.router, prefix="/ingestion-audit", tags=["ingestion-audit"])
router.include_router(routing_feedback.router, prefix="/routing-feedback", tags=["routing-feedback"])
router.include_router(snapshots.router, prefix="/snapshots", tags=["snapshots"])
router.include_router(evaluate.router, prefix="/evaluate", tags=["evaluate"])
router.include_router(quality_monitor.router, prefix="/quality-monitor", tags=["quality-monitor"])
router.include_router(fund_reconciliation.router, prefix="/fund-reconciliation", tags=["fund-reconciliation"])
router.include_router(fund_profiles.router, prefix="/fund-profiles", tags=["fund-profiles"])
router.include_router(data_repair.router, prefix="/data-repair", tags=["data-repair"])
router.include_router(decision_traces.router, prefix="/decision-traces", tags=["decision-traces"])
router.include_router(models.router, prefix="", tags=["models"])
