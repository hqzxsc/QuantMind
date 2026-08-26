"""Market analysis SQLAlchemy models."""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import BigInteger
from sqlalchemy.dialects.postgresql import DATE
from sqlalchemy.sql import func

from backend.services.api.models.base import Base


class MarketSectorRecord(Base):
    __tablename__ = "qm_market_sectors"

    sector_id = Column(String(64), primary_key=True)
    sector_type = Column(String(16), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    code = Column(String(32), nullable=False)
    parent_sector_id = Column(String(64))
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("sector_type IN ('industry','concept','index')", name="ck_qm_sectors_type"),
        UniqueConstraint("sector_type", "code", name="uq_qm_sectors_code"),
    )


class SectorConstituentRecord(Base):
    __tablename__ = "qm_sector_constituents"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sector_id = Column(String(64), ForeignKey("qm_market_sectors.sector_id", ondelete="CASCADE"), nullable=False)
    instrument = Column(String(16), nullable=False)
    weight = Column(Float)
    added_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("sector_id", "instrument", name="uq_qm_constituents_sector_inst"),
        Index("idx_qm_constituents_sector", "sector_id"),
        Index("idx_qm_constituents_inst", "instrument"),
    )


class SectorDailyMetricsRecord(Base):
    __tablename__ = "qm_sector_daily_metrics"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trade_date = Column(DATE, nullable=False)
    sector_id = Column(String(64), ForeignKey("qm_market_sectors.sector_id", ondelete="CASCADE"), nullable=False)
    avg_pct_change = Column(Float)
    median_pct_change = Column(Float)
    total_market_cap = Column(Float)
    avg_turnover_rate = Column(Float)
    advance_count = Column(Integer)
    decline_count = Column(Integer)
    flat_count = Column(Integer)
    net_inflow = Column(Float)
    sentiment_score = Column(Float)
    sentiment_label = Column(String(16))
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("trade_date", "sector_id", name="uq_qm_daily_metrics_date_sector"),
        Index("idx_qm_daily_metrics_date", "trade_date"),
        Index("idx_qm_daily_metrics_sector", "sector_id", "trade_date"),
    )


class MarketAnomalyRecord(Base):
    __tablename__ = "qm_market_anomalies"

    anomaly_id = Column(String(36), primary_key=True)
    trade_date = Column(DATE, nullable=False)
    anomaly_type = Column(String(32), nullable=False)
    sector_id = Column(String(64))
    instrument = Column(String(16))
    severity = Column(String(16), nullable=False, default="info")
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=False, default="")
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "anomaly_type IN ('volume_surge','price_limit_up','price_limit_down',"
            "'sector_rotation','flow_reversal','breadth_divergence')",
            name="ck_qm_anomaly_type",
        ),
        CheckConstraint("severity IN ('info','warning','critical')", name="ck_qm_anomaly_severity"),
        Index("idx_qm_anomalies_date", "trade_date", "created_at"),
        Index("idx_qm_anomalies_type", "anomaly_type", "trade_date"),
        Index("idx_qm_anomalies_sector", "sector_id", "trade_date"),
    )
