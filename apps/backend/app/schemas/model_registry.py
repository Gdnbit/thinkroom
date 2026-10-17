from sqlalchemy import Column, String, Float, Boolean, Text, UniqueConstraint
from app.database import Base


class KeyGroup(Base):
    __tablename__ = 'key_groups'
    id = Column(String(26), primary_key=True)
    group_name = Column(String(100), nullable=False)
    api_key = Column(String(200), nullable=False)
    base_url = Column(String(300), nullable=True)
    label = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    last_tested_at = Column(String(50), nullable=True)
    last_test_ok = Column(Boolean, nullable=True)
    created_at = Column(String(50), nullable=False)
    updated_at = Column(String(50), nullable=False)
    __table_args__ = (UniqueConstraint('group_name', 'api_key', name='uq_key_group'),)


class ModelRegistry(Base):
    __tablename__ = 'model_registry'
    id = Column(String(26), primary_key=True)
    group_name = Column(String(100), nullable=False)
    model = Column(String(200), nullable=False)
    pricing_type = Column(String(20), nullable=False)
    input_price_per_1m = Column(Float, nullable=True)
    output_price_per_1m = Column(Float, nullable=True)
    cache_read_price_per_1m = Column(Float, nullable=True)
    cache_write_price_per_1m = Column(Float, nullable=True)
    price_per_call = Column(Float, nullable=True)
    is_available = Column(Boolean, default=True, nullable=False)
    unavailable_reason = Column(Text, nullable=True)
    last_checked_at = Column(String(50), nullable=True)
    consecutive_failures = Column(String(10), default='0')
    created_at = Column(String(50), nullable=False)
    updated_at = Column(String(50), nullable=False)
    __table_args__ = (UniqueConstraint('group_name', 'model', name='uq_model_registry'),)
