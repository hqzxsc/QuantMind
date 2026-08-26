"""Market analysis repository."""

from datetime import date
from typing import Any, cast

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    MarketAnomalyRecord,
    MarketSectorRecord,
    SectorConstituentRecord,
    SectorDailyMetricsRecord,
)


class MarketAnalysisRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---- Sectors ----

    async def get_sector(self, sector_id: str) -> MarketSectorRecord | None:
        stmt = select(MarketSectorRecord).where(MarketSectorRecord.sector_id == sector_id)
        return cast(MarketSectorRecord | None, (await self.session.execute(stmt)).scalar_one_or_none())

    async def list_sectors(
        self, *, sector_type: str | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[list[MarketSectorRecord], int]:
        filters = []
        if sector_type:
            filters.append(MarketSectorRecord.sector_type == sector_type)
        total = int((await self.session.execute(
            select(func.count(MarketSectorRecord.sector_id)).where(*filters)
        )).scalar_one())
        stmt = (
            select(MarketSectorRecord)
            .where(*filters)
            .order_by(MarketSectorRecord.sector_type, MarketSectorRecord.name)
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    # ---- Constituents ----

    async def list_constituents(self, sector_id: str) -> list[SectorConstituentRecord]:
        stmt = (
            select(SectorConstituentRecord)
            .where(SectorConstituentRecord.sector_id == sector_id)
            .order_by(SectorConstituentRecord.weight.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_sectors_by_instrument(self, instrument: str) -> list[MarketSectorRecord]:
        stmt = (
            select(MarketSectorRecord)
            .join(
                SectorConstituentRecord,
                SectorConstituentRecord.sector_id == MarketSectorRecord.sector_id,
            )
            .where(SectorConstituentRecord.instrument == instrument)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    # ---- Daily Metrics ----

    async def get_latest_metrics(self, sector_id: str) -> SectorDailyMetricsRecord | None:
        stmt = (
            select(SectorDailyMetricsRecord)
            .where(SectorDailyMetricsRecord.sector_id == sector_id)
            .order_by(SectorDailyMetricsRecord.trade_date.desc())
            .limit(1)
        )
        return cast(SectorDailyMetricsRecord | None, (await self.session.execute(stmt)).scalar_one_or_none())

    async def get_metrics_history(
        self,
        sector_id: str,
        *,
        start_date: date,
        end_date: date,
    ) -> list[SectorDailyMetricsRecord]:
        stmt = (
            select(SectorDailyMetricsRecord)
            .where(
                SectorDailyMetricsRecord.sector_id == sector_id,
                SectorDailyMetricsRecord.trade_date >= start_date,
                SectorDailyMetricsRecord.trade_date <= end_date,
            )
            .order_by(SectorDailyMetricsRecord.trade_date)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_all_latest_metrics(self, trade_date: date) -> list[SectorDailyMetricsRecord]:
        stmt = (
            select(SectorDailyMetricsRecord)
            .where(SectorDailyMetricsRecord.trade_date == trade_date)
            .order_by(SectorDailyMetricsRecord.sector_id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    # ---- Anomalies ----

    async def list_anomalies(
        self,
        *,
        trade_date: date | None = None,
        anomaly_type: str | None = None,
        sector_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MarketAnomalyRecord], int]:
        filters = []
        if trade_date:
            filters.append(MarketAnomalyRecord.trade_date == trade_date)
        if anomaly_type:
            filters.append(MarketAnomalyRecord.anomaly_type == anomaly_type)
        if sector_id:
            filters.append(MarketAnomalyRecord.sector_id == sector_id)
        total = int((await self.session.execute(
            select(func.count(MarketAnomalyRecord.anomaly_id)).where(*filters)
        )).scalar_one())
        stmt = (
            select(MarketAnomalyRecord)
            .where(*filters)
            .order_by(MarketAnomalyRecord.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    # ---- Write helpers ----

    def add(self, record: Any) -> None:
        self.session.add(record)

    async def flush(self) -> None:
        await self.session.flush()
