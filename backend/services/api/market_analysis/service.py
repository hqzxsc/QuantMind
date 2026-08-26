"""Market analysis service."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from .domain import SectorConflictError, SectorNotFoundError, SectorType, classify_sentiment
from .models import (
    MarketAnomalyRecord,
    MarketSectorRecord,
    SectorConstituentRecord,
    SectorDailyMetricsRecord,
)
from .repository import MarketAnalysisRepository
from .schemas import CreateSectorRequest


class MarketAnalysisService:
    def __init__(self, repository: MarketAnalysisRepository):
        self.repository = repository

    # ---- Sectors ----

    async def create_sector(self, request: CreateSectorRequest) -> MarketSectorRecord:
        sector = MarketSectorRecord(
            sector_id=request.sector_id,
            sector_type=request.sector_type,
            name=request.name.strip(),
            code=request.code.strip(),
            parent_sector_id=request.parent_sector_id,
            metadata_json=request.metadata_json,
        )
        self.repository.add(sector)
        try:
            await self.repository.flush()
        except IntegrityError as exc:
            raise SectorConflictError(f"sector {request.sector_id} already exists") from exc
        return sector

    async def get_sector(self, sector_id: str) -> MarketSectorRecord:
        sector = await self.repository.get_sector(sector_id)
        if sector is None:
            raise SectorNotFoundError(f"sector {sector_id} not found")
        return sector

    async def list_sectors(
        self, *, sector_type: str | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[list[MarketSectorRecord], int]:
        return await self.repository.list_sectors(
            sector_type=sector_type, limit=limit, offset=offset
        )

    async def list_constituents(self, sector_id: str) -> list[SectorConstituentRecord]:
        await self.get_sector(sector_id)
        return await self.repository.list_constituents(sector_id)

    async def add_constituent(
        self, sector_id: str, instrument: str, weight: float | None = None
    ) -> SectorConstituentRecord:
        await self.get_sector(sector_id)
        constituent = SectorConstituentRecord(
            sector_id=sector_id,
            instrument=instrument,
            weight=weight,
        )
        self.repository.add(constituent)
        try:
            await self.repository.flush()
        except IntegrityError:
            existing = await self.repository.list_constituents(sector_id)
            for c in existing:
                if c.instrument == instrument:
                    return c
            raise
        return constituent

    # ---- Metrics ----

    async def record_daily_metrics(
        self,
        *,
        trade_date: date,
        sector_id: str,
        avg_pct_change: float | None = None,
        median_pct_change: float | None = None,
        total_market_cap: float | None = None,
        avg_turnover_rate: float | None = None,
        advance_count: int | None = None,
        decline_count: int | None = None,
        flat_count: int | None = None,
        net_inflow: float | None = None,
        sentiment_score: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> SectorDailyMetricsRecord:
        await self.get_sector(sector_id)

        sentiment_label = classify_sentiment(sentiment_score) if sentiment_score is not None else None

        record = SectorDailyMetricsRecord(
            trade_date=trade_date,
            sector_id=sector_id,
            avg_pct_change=avg_pct_change,
            median_pct_change=median_pct_change,
            total_market_cap=total_market_cap,
            avg_turnover_rate=avg_turnover_rate,
            advance_count=advance_count,
            decline_count=decline_count,
            flat_count=flat_count,
            net_inflow=net_inflow,
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            details=details or {},
        )
        self.repository.add(record)
        await self.repository.flush()
        return record

    async def get_latest_metrics(self, sector_id: str) -> SectorDailyMetricsRecord:
        await self.get_sector(sector_id)
        metrics = await self.repository.get_latest_metrics(sector_id)
        if metrics is None:
            raise SectorNotFoundError(f"no metrics found for sector {sector_id}")
        return metrics

    async def get_metrics_history(
        self, sector_id: str, *, start_date: date, end_date: date
    ) -> list[SectorDailyMetricsRecord]:
        await self.get_sector(sector_id)
        return await self.repository.get_metrics_history(
            sector_id, start_date=start_date, end_date=end_date
        )

    async def get_heatmap(self, trade_date: date) -> list[dict[str, Any]]:
        """获取指定日期的热力图数据。"""
        all_metrics = await self.repository.get_all_latest_metrics(trade_date)
        result = []
        for m in all_metrics:
            sector = await self.repository.get_sector(m.sector_id)
            if sector is None:
                continue
            result.append({
                "sector_id": str(m.sector_id),
                "name": str(sector.name),
                "sector_type": str(sector.sector_type),
                "avg_pct_change": float(m.avg_pct_change) if m.avg_pct_change is not None else None,
                "sentiment_score": float(m.sentiment_score) if m.sentiment_score is not None else None,
                "sentiment_label": str(m.sentiment_label) if m.sentiment_label is not None else None,
                "advance_count": int(m.advance_count) if m.advance_count is not None else None,
                "decline_count": int(m.decline_count) if m.decline_count is not None else None,
                "net_inflow": float(m.net_inflow) if m.net_inflow is not None else None,
            })
        return result

    # ---- Anomalies ----

    async def create_anomaly(
        self,
        *,
        trade_date: date,
        anomaly_type: str,
        title: str,
        description: str = "",
        sector_id: str | None = None,
        instrument: str | None = None,
        severity: str = "info",
        details: dict[str, Any] | None = None,
    ) -> MarketAnomalyRecord:
        if sector_id:
            await self.get_sector(sector_id)

        anomaly = MarketAnomalyRecord(
            anomaly_id=str(uuid4()),
            trade_date=trade_date,
            anomaly_type=anomaly_type,
            sector_id=sector_id,
            instrument=instrument,
            severity=severity,
            title=title.strip(),
            description=description.strip(),
            details=details or {},
        )
        self.repository.add(anomaly)
        await self.repository.flush()
        return anomaly

    async def list_anomalies(
        self,
        *,
        trade_date: date | None = None,
        anomaly_type: str | None = None,
        sector_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MarketAnomalyRecord], int]:
        return await self.repository.list_anomalies(
            trade_date=trade_date,
            anomaly_type=anomaly_type,
            sector_id=sector_id,
            limit=limit,
            offset=offset,
        )
