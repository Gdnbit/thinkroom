"""所有 ORM Schema 模型入口 — 必须全部导入以确保 Alembic 能检测到所有表"""
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey  # noqa: F401
from app.schemas.raw_source import RawSource, SourceAttachment  # noqa: F401
from app.schemas.speaker_profile import SpeakerProfile  # noqa: F401
from app.schemas.evidence_span import EvidenceSpan  # noqa: F401
from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink, ClaimEvidenceLink  # noqa: F401
from app.schemas.lifecycle_log import LifecycleLog  # noqa: F401
from app.schemas.event_card import EventCard  # noqa: F401
from app.schemas.market_state_card import MarketStateCard  # noqa: F401
from app.schemas.market_snapshot import MarketSnapshot, FundProfile, FundEstimateSnapshot, SourceHealthLog  # noqa: F401
from app.schemas.flow_signal_card import FlowSignalCard  # noqa: F401
from app.schemas.strategy_card import StrategyCard, StrategySourceLink  # noqa: F401
from app.schemas.setup_card import SetupCard  # noqa: F401
from app.schemas.briefing_card import BriefingCard  # noqa: F401
from app.schemas.briefing_evidence_link import BriefingEvidenceLink  # noqa: F401
from app.schemas.outcome_record import OutcomeRecord  # noqa: F401
from app.schemas.debate import DebateRecord, DebateRound, FinalAdjudication  # noqa: F401
from app.schemas.watchlist import Watchlist, WatchlistItem  # noqa: F401
from app.schemas.job_run import JobRun  # noqa: F401
from app.schemas.processing_trace import ProcessingTrace  # noqa: F401
from app.schemas.decision_trace_event import DecisionTraceEvent, DecisionTraceSourceLink  # noqa: F401
from app.schemas.decision_trace_eval_review import DecisionTraceEvalReview  # noqa: F401
from app.schemas.decision_snapshot import DecisionSnapshot  # noqa: F401
from app.schemas.user_override import UserOverride  # noqa: F401
from app.schemas.chat import ChatSession, ChatMessage  # noqa: F401 — 之前漏注册，导致 Alembic autogenerate 看不到 chat_* 表（G1 修复）
from app.schemas.model_registry import KeyGroup, ModelRegistry  # noqa: F401 — 与 chat 同类漏注册（doctor pass 修复，0012）

__all__ = [
    "RawSource", "SourceAttachment",
    "SpeakerProfile",
    "EvidenceSpan",
    "ClaimCard", "ClaimRawSourceLink", "ClaimEvidenceLink",
    "LifecycleLog",
    "EventCard",
    "MarketStateCard",
    "MarketSnapshot", "FundProfile", "FundEstimateSnapshot", "SourceHealthLog",
    "FlowSignalCard",
    "StrategyCard", "StrategySourceLink",
    "SetupCard",
    "BriefingCard", "BriefingEvidenceLink",
    "OutcomeRecord",
    "DebateRecord", "DebateRound", "FinalAdjudication",
    "Watchlist", "WatchlistItem",
    "JobRun",
    "ProcessingTrace",
    "DecisionTraceEvent", "DecisionTraceSourceLink",
    "DecisionTraceEvalReview",
    "DecisionSnapshot",
    "UserOverride",
    "ChatSession", "ChatMessage",
    "KeyGroup", "ModelRegistry",
]
