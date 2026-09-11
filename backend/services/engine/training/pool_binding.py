"""训练编排器 - 全局股票池解析（P3）。

训练容器**没有 DB 访问**，所以「pool_id → 成分代码」的解析必须在编排器侧
（有 DB 的一侧）完成，再随 `config.yaml` 传进容器。

本地 Docker 与远端 SSH 两个编排器共用本模块，避免各自实现一份。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def resolve_training_pool(payload: dict[str, Any] | None) -> dict[str, Any]:
    """把 payload 里的 `pool_id` 解析成 `DataCfg` 的池字段。

    返回可直接 `**` 展开进 `DataCfg(...)` 的 dict；未指定池时返回 `{}`
    （保持旧行为：全市场训练）。

    严格语义：池解析为空时抛 `RuntimeError`，拒绝提交训练 ——
    否则会训出一个「以为是池内、实际是全市场」的模型。
    """
    data = payload or {}
    pool_ref = str(data.get("pool_id") or "").strip()
    if not pool_ref:
        return {}

    from backend.shared.stock_pool.resolver import resolve_pool_sync

    tenant_id = str(data.get("tenant_id") or "").strip() or None
    user_id = str(data.get("user_id") or "").strip() or None

    snapshot = resolve_pool_sync(
        pool_ref, tenant_id=tenant_id, user_id=user_id, strict=True
    )

    base = {
        "pool_id": pool_ref,
        "pool_symbols": None,
        "pool_checksum": snapshot.checksum,
    }

    if snapshot.unfiltered:
        logger.info(
            "训练池解析为不过滤（等价全市场）: pool_id=%s", pool_ref
        )
        return base

    if snapshot.is_empty:
        reason = "; ".join(snapshot.warnings) or "池成分为空"
        raise RuntimeError(
            f"训练任务指定的股票池 {pool_ref} 解析为空池，拒绝提交：{reason}"
        )

    logger.info(
        "训练池解析完成: pool_id=%s code=%s symbols=%d checksum=%s",
        pool_ref,
        snapshot.code,
        len(snapshot.symbols),
        snapshot.checksum,
    )
    base["pool_symbols"] = list(snapshot.symbols)
    return base
