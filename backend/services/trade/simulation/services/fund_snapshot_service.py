"""
Persist simulation account fund overview snapshots into PostgreSQL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.services.trade.redis_client import RedisClient
from backend.services.trade.simulation.models.fund_snapshot import (
    SimulationFundSnapshot,
)
from backend.shared.database_manager_v2 import get_session

logger = logging.getLogger(__name__)


def _to_decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value is None:
            return default
        return Decimal(str(value))
    except Exception:
        return default


def _local_today() -> datetime.date:
    # Keep simple and deterministic; can be overridden by TZ env var.
    tz_name = os.getenv("SIM_FUND_SNAPSHOT_TZ", "Asia/Shanghai")
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return datetime.now().date()


def _parse_account_key(key: str) -> tuple[str, str] | None:
    # simulation:account:{tenant_id}:{user_id}
    parts = key.split(":")
    if len(parts) != 4:
        return None
    if parts[0] != "simulation" or parts[1] != "account":
        return None
    tenant_id = parts[2].strip() or "default"
    user_id = parts[3].strip()
    if not user_id:
        return None
    return tenant_id, user_id


@dataclass
class SnapshotUpsertResult:
    upserted_rows: int
    scanned_accounts: int


class SimulationFundSnapshotService:
    @staticmethod
    def _read_settings_initial_cash(redis: RedisClient, tenant_id: str, user_id: str) -> Decimal:
        if not redis.client:
            return Decimal("0")
        settings_key = f"simulation:settings:{tenant_id}:{user_id}"
        raw = redis.client.get(settings_key)
        if not raw:
            return Decimal("0")
        try:
            data = json.loads(raw)
        except Exception:
            return Decimal("0")
        return _to_decimal(data.get("initial_cash"), Decimal("0"))

    @classmethod
    def _build_row(cls, tenant_id: str, user_id: str, account: dict[str, object]) -> dict[str, object]:
        total_asset = _to_decimal(account.get("total_asset"))
        available_balance = _to_decimal(account.get("cash") or account.get("available_balance"))
        frozen_balance = _to_decimal(account.get("frozen_balance"))
        market_value = _to_decimal(account.get("market_value"))
        initial_capital = _to_decimal(account.get("initial_capital"))
        today_pnl = _to_decimal(account.get("today_pnl"))
        total_pnl = _to_decimal(account.get("total_pnl"))
        if initial_capital == 0:
            initial_capital = total_asset
        if total_pnl == 0:
            total_pnl = total_asset - initial_capital

        return {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "snapshot_date": _local_today(),
            "total_asset": total_asset,
            "available_balance": available_balance,
            "frozen_balance": frozen_balance,
            "market_value": market_value,
            "initial_capital": initial_capital,
            "total_pnl": total_pnl,
            "today_pnl": today_pnl,
            "source": "redis_simulation_account",
        }

    @classmethod
    async def get_baselines(
        cls,
        tenant_id: str,
        user_id: str,
        initial_capital: Decimal,
    ) -> dict[str, Decimal]:
        """计算日初/月初权益基线。

        取「今日之前」「本月初之前」最近一条日快照的总资产作为基线；
        无历史快照（新账户/刚重置）时回退初始资金，保证开盘口径盈亏为 0。
        """
        today = _local_today()
        month_start = today.replace(day=1)
        day_open = initial_capital
        month_open = initial_capital
        try:
            async with get_session(read_only=True) as session:
                day_row = (
                    await session.execute(
                        select(SimulationFundSnapshot.total_asset)
                        .where(
                            SimulationFundSnapshot.tenant_id == tenant_id,
                            SimulationFundSnapshot.user_id == user_id,
                            SimulationFundSnapshot.snapshot_date < today,
                        )
                        .order_by(SimulationFundSnapshot.snapshot_date.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if day_row is not None:
                    day_open = _to_decimal(day_row, initial_capital)
                month_row = (
                    await session.execute(
                        select(SimulationFundSnapshot.total_asset)
                        .where(
                            SimulationFundSnapshot.tenant_id == tenant_id,
                            SimulationFundSnapshot.user_id == user_id,
                            SimulationFundSnapshot.snapshot_date < month_start,
                        )
                        .order_by(SimulationFundSnapshot.snapshot_date.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if month_row is not None:
                    month_open = _to_decimal(month_row, initial_capital)
                elif month_start == today:
                    # 每月 1 号：无上月快照时用日初权益当月初基线，避免当月盈亏重复计入今日变动
                    month_open = day_open
        except Exception as exc:
            logger.warning(
                "get_baselines failed tenant=%s user=%s: %s", tenant_id, user_id, exc
            )
        return {"day_open_equity": day_open, "month_open_equity": month_open}

    @classmethod
    async def capture_all(cls, redis: RedisClient) -> SnapshotUpsertResult:
        if not redis.client:
            return SnapshotUpsertResult(upserted_rows=0, scanned_accounts=0)

        keys = list(redis.client.scan_iter(match="simulation:account:*", count=500))
        rows: list[dict[str, object]] = []
        for key in keys:
            parsed = _parse_account_key(str(key))
            if not parsed:
                continue
            tenant_id, user_id = parsed
            raw = redis.client.get(key)
            if not raw:
                continue
            try:
                account = json.loads(raw)
            except Exception:
                continue

            row = cls._build_row(tenant_id, user_id, account)
            if row["initial_capital"] == 0:
                row["initial_capital"] = cls._read_settings_initial_cash(redis, tenant_id, user_id)
                if row["initial_capital"] == 0:
                    row["initial_capital"] = row["total_asset"]
            # 总盈亏 = 总资产 - 初始资金（手续费已从现金扣减，天然计入）
            row["total_pnl"] = row["total_asset"] - row["initial_capital"]
            # 当日盈亏 = 总资产 - 日初权益（上一交易日快照基线）
            baselines = await cls.get_baselines(tenant_id, user_id, row["initial_capital"])
            row["today_pnl"] = row["total_asset"] - baselines["day_open_equity"]
            rows.append(row)

        if not rows:
            return SnapshotUpsertResult(upserted_rows=0, scanned_accounts=len(keys))

        async with get_session(read_only=False) as session:
            for row in rows:
                stmt = (
                    pg_insert(SimulationFundSnapshot)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["tenant_id", "user_id", "snapshot_date"],
                        set_={
                            "total_asset": row["total_asset"],
                            "available_balance": row["available_balance"],
                            "frozen_balance": row["frozen_balance"],
                            "market_value": row["market_value"],
                            "initial_capital": row["initial_capital"],
                            "total_pnl": row["total_pnl"],
                            "today_pnl": row["today_pnl"],
                            "source": row["source"],
                            "updated_at": datetime.now(),
                        },
                    )
                )
                await session.execute(stmt)

        return SnapshotUpsertResult(upserted_rows=len(rows), scanned_accounts=len(keys))

    @staticmethod
    async def list_user_daily(
        tenant_id: str,
        user_id: str,
        days: int = 30,
    ) -> list[SimulationFundSnapshot]:
        async with get_session(read_only=True) as session:
            stmt = (
                select(SimulationFundSnapshot)
                .where(
                    SimulationFundSnapshot.tenant_id == tenant_id,
                    SimulationFundSnapshot.user_id == user_id,
                )
                .order_by(SimulationFundSnapshot.snapshot_date.desc())
                .limit(max(1, min(days, 3650)))
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())


class SimulationFundSnapshotWorker:
    def __init__(self, redis: RedisClient, interval_seconds: int):
        self.redis = redis
        self.interval_seconds = max(60, int(interval_seconds))
        self._stopped = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._run(), name="sim-fund-snapshot-worker")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                result = await SimulationFundSnapshotService.capture_all(self.redis)
                if result.scanned_accounts > 0:
                    logger.info(
                        "Simulation fund snapshot upserted: %s/%s",
                        result.upserted_rows,
                        result.scanned_accounts,
                    )
            except Exception as exc:
                logger.error("Simulation fund snapshot worker failed: %s", exc, exc_info=True)

            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue
