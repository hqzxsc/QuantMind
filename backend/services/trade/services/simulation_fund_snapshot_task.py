"""模拟盘资金快照周期采集任务。

SimulationFundSnapshotWorker 此前只有定义没有接入点，导致快照表
只在账户重置/手动触发时写入，智能图表「每日收益率」长期无数据。
本任务与调度器解耦，按固定间隔（默认 300s）把全部模拟账户的
资金状态 upsert 到 simulation_fund_snapshots（按天）。

环境变量：
- SIM_FUND_SNAPSHOT_ENABLED=false 关闭任务
- SIM_FUND_SNAPSHOT_INTERVAL_SECONDS 采集间隔（最小 60s）
"""

import asyncio
import logging
import os

from backend.services.trade.redis_client import redis_client
from backend.services.trade.simulation.services.fund_snapshot_service import (
    SimulationFundSnapshotService,
)

logger = logging.getLogger(__name__)


async def run_simulation_fund_snapshot_task() -> None:
    enabled = os.getenv("SIM_FUND_SNAPSHOT_ENABLED", "true").strip().lower() != "false"
    if not enabled:
        logger.info("Simulation fund snapshot task disabled (SIM_FUND_SNAPSHOT_ENABLED=false)")
        return

    try:
        interval_seconds = int(os.getenv("SIM_FUND_SNAPSHOT_INTERVAL_SECONDS", "300"))
    except Exception:
        interval_seconds = 300
    interval_seconds = max(60, interval_seconds)

    logger.info("Simulation fund snapshot task started (interval=%ss)", interval_seconds)
    while True:
        try:
            if redis_client.client:
                result = await SimulationFundSnapshotService.capture_all(redis_client)
                if result.scanned_accounts > 0:
                    logger.info(
                        "Simulation fund snapshot upserted: %s/%s",
                        result.upserted_rows,
                        result.scanned_accounts,
                    )
        except Exception as exc:
            logger.error("Simulation fund snapshot task failed: %s", exc, exc_info=True)
        await asyncio.sleep(interval_seconds)
