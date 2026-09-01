"""
TDX Account Sync Task - 定期把通达信桥账户/持仓落库到 real_account_snapshots

供前端 REAL 模式 /account 接口读取通达信实盘持仓。
"""

import asyncio
import logging
import os

from backend.services.live_trading.services.tdx_push_service import tdx_pusher

logger = logging.getLogger(__name__)


async def run_tdx_account_sync_task(interval_seconds: int = 30):
    """定期拉取通达信桥账户并落库 PG。默认每 30 秒同步一次。"""
    if not tdx_pusher.enabled:
        logger.info("[TdxSync] TDX_BRIDGE_URL/TOKEN 未配置，通达信账户同步任务跳过")
        return

    logger.info(
        "[TdxSync] 通达信账户同步任务启动, interval=%ss, bridge=%s",
        interval_seconds,
        tdx_pusher.bridge_url,
    )
    while True:
        try:
            await tdx_pusher.sync_account_to_pg(
                tenant_id="default",
                user_id=os.getenv("TDX_ACCOUNT_USER_ID", "00000001"),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[TdxSync] 通达信账户同步失败: %s", exc)
        await asyncio.sleep(max(10, float(interval_seconds)))
