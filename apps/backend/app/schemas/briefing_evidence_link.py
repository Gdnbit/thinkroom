"""BriefingEvidenceLink ORM 模型 — G4"""
from sqlalchemy import Column, String, Float, Text, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.database import Base


class BriefingEvidenceLink(Base):
    __tablename__ = "briefing_evidence_links"

    id            = Column(String(26), primary_key=True)
    briefing_id   = Column(String(26), ForeignKey("briefing_cards.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_type = Column(String(20), nullable=False, comment="claim | event | market | flow")
    evidence_id   = Column(String(26), nullable=True)
    role          = Column(String(20), nullable=False, default="supporting", comment="supporting | contrary | background")
    relevance_score = Column(Float, nullable=True)
    notes         = Column(Text, nullable=True)
    created_at    = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_briefing_evidence_links_type_id", "evidence_type", "evidence_id"),
    )
