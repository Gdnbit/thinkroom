"""Briefing Workflow

职责：封装 Briefing 生成的完整链路，供 scheduler.py 等非 HTTP 上下文调用。
与 /api/v1/briefings POST 端点逻辑一致，但无 FastAPI 依赖。
"""
import time
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy.orm import Session
from ulid import ULID
from app.database import safe_commit

from app.mcps.research_memory_mcp import ResearchMemoryMCP
from app.mcps.event_feed_mcp import EventFeedMCP
from app.mcps.market_data_mcp import MarketDataMCP
from app.mcps.flow_continuity_mcp import FlowContinuityMCP
from app.skills.fusion_skill import FusionSkill, FusionInput
from app.skills.briefing_skill import BriefingSkill, BriefingInput
from app.schemas.briefing_card import BriefingCard
from app.schemas.briefing_evidence_link import BriefingEvidenceLink
from app.schemas.job_run import JobRun


class BriefingWorkflow:
    def __init__(self, db: Session):
        self.db = db

    def generate(
        self,
        question: str,
        asset_scope: Optional[List[str]] = None,
        time_horizon: Optional[str] = None,
        committee_level: str = "single",
        triggered_by: str = "api",
    ) -> dict:
        """生成简报，写入 BriefingCard + BriefingEvidenceLink + JobRun"""
        asset = asset_scope[0] if asset_scope else "BTC"
        now = datetime.now(timezone.utc)
        start_ts = time.time()

        job = JobRun(
            id=str(ULID()),
            job_type="briefing_gen",
            status="running",
            triggered_by=triggered_by,
            input_params={"question": question, "asset": asset, "committee_level": committee_level},
            result_summary={},
            object_scope={
                "asset": asset,
                "question": question,
                "source_ids": [],
            },
            started_at=now,
            retry_count=0,
            related_object_type="briefing",
            created_at=now,
            updated_at=now,
        )
        self.db.add(job)
        self.db.flush()

        try:
            # 1. MCP 聚合
            memory_mcp = ResearchMemoryMCP(self.db)
            event_mcp  = EventFeedMCP(self.db)
            market_mcp = MarketDataMCP(self.db)
            flow_mcp   = FlowContinuityMCP(self.db)

            # 混合召回：语义相似 + 资产全量 + 高信任保底，三路合并去重
            claims_resp = memory_mcp.query_for_briefing(
                query=question, asset=asset, limit=12
            )
            # 数据覆盖摘要：告知 LLM 本地有哪些私域数据，避免幻觉式补充
            data_coverage = memory_mcp.get_data_coverage_summary(asset=asset)
            events_resp = event_mcp.fetch_recent_events(asset=asset, limit=5)
            market_resp = market_mcp.get_market_state(asset=asset)
            flow_resp   = flow_mcp.get_continuity_summary(asset=asset)

            # query_for_briefing 已返回序列化 dict，直接使用
            claims = claims_resp.data or []
            events       = events_resp.data or []
            market_state = market_resp.data
            flow_signals = (flow_resp.data or {}).get("signals", [])

            all_mock  = events_resp.is_mock and market_resp.is_mock and flow_resp.is_mock
            any_mock  = events_resp.is_mock or  market_resp.is_mock or  flow_resp.is_mock
            source_mode = "mock" if all_mock else ("mixed" if any_mock else "live")

            # 2. Fusion
            fusion_out = FusionSkill().execute(FusionInput(
                asset=asset, question=question,
                claims=claims, events=events,
                market_state=market_state, flow_signals=flow_signals,
                data_coverage=data_coverage,
            ))

            # 3. Briefing
            briefing_out = BriefingSkill().execute(BriefingInput(
                question=question, asset=asset,
                fusion_result=fusion_out, time_horizon=time_horizon,
            ))

            # 4. 持久化 BriefingCard
            briefing = BriefingCard(
                id=str(ULID()),
                title=briefing_out.title,
                topic=briefing_out.topic,
                question=question,
                direction=briefing_out.direction,
                setup_quality=briefing_out.setup_quality,
                time_horizon=briefing_out.time_horizon,
                asset_scope=asset_scope or [asset],
                executive_summary=briefing_out.executive_summary,
                supporting_points=briefing_out.supporting_points,
                contrary_points=briefing_out.contrary_points,
                key_risks=briefing_out.key_risks,
                strategy_fit_summary=briefing_out.strategy_fit_summary,
                recommended_action=briefing_out.recommended_action,
                invalidation_conditions=briefing_out.invalidation_conditions,
                source_claim_ids=[c.get("id", "") for c in claims if c.get("id")],
                source_event_ids=[e.get("id", "") for e in events if e.get("id")],
                model_used=briefing_out.model_used,
                committee_level=committee_level,
                status="active",
                generated_at=now,
                created_at=now,
                updated_at=now,
            )
            self.db.add(briefing)
            self.db.flush()

            # 5. 写入 briefing_evidence_links
            self._write_evidence_links(briefing.id, claims, events, market_state, now)

            # 6. 更新 JobRun
            duration_ms = int((time.time() - start_ts) * 1000)
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.duration_ms = duration_ms
            job.related_object_id = briefing.id
            job.result_summary = {
                "briefing_id": briefing.id,
                "direction": briefing_out.direction,
                "is_mock": briefing_out.is_mock,
                "source_mode": source_mode,
            }
            job.updated_at = datetime.now(timezone.utc)
            safe_commit(self.db)

            return {
                "success": True,
                "briefing_id": briefing.id,
                "direction": briefing_out.direction,
                "setup_quality": briefing_out.setup_quality,
                "is_mock": briefing_out.is_mock,
                "source_mode": source_mode,
                "job_id": job.id,
                "duration_ms": duration_ms,
            }

        except Exception as e:
            duration_ms = int((time.time() - start_ts) * 1000)
            job.status = "failed"
            job.completed_at = datetime.now(timezone.utc)
            job.duration_ms = duration_ms
            job.error_message = str(e)
            job.updated_at = datetime.now(timezone.utc)
            safe_commit(self.db)
            return {"success": False, "error": str(e)}

    def _write_evidence_links(
        self,
        briefing_id: str,
        claims: list,
        events: list,
        market_state: Optional[dict],
        now: datetime,
    ) -> None:
        """将关联的 Claim / Event / Market 证据写入 briefing_evidence_links"""
        try:
            for claim in claims:
                cid = claim.get("id")
                if not cid:
                    continue
                link = BriefingEvidenceLink(
                    id=str(ULID()),
                    briefing_id=briefing_id,
                    evidence_type="claim",
                    evidence_id=cid,
                    role="supporting",
                    relevance_score=claim.get("confidence"),
                    notes=claim.get("summary", "")[:100],
                    created_at=now,
                )
                self.db.add(link)

            for event in events:
                eid = event.get("id")
                if not eid:
                    continue
                link = BriefingEvidenceLink(
                    id=str(ULID()),
                    briefing_id=briefing_id,
                    evidence_type="event",
                    evidence_id=eid,
                    role="supporting",
                    relevance_score=event.get("importance"),
                    notes=event.get("title", "")[:100],
                    created_at=now,
                )
                self.db.add(link)

            if market_state:
                link = BriefingEvidenceLink(
                    id=str(ULID()),
                    briefing_id=briefing_id,
                    evidence_type="market",
                    evidence_id=None,
                    role="background",
                    relevance_score=None,
                    notes=f"{market_state.get('asset','')} {market_state.get('trend_direction','')} @ {market_state.get('price','')}",
                    created_at=now,
                )
                self.db.add(link)

            self.db.flush()
        except Exception:
            pass  # evidence links 写入失败不阻塞主流程
