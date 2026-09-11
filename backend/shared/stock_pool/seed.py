"""全局股票池 - 内置池 seed（幂等）。

把 `builtins.BUILTIN_POOLS` 写入 `qm_stock_pool`（scope=global, is_system=true）。
幂等策略：以 `pool_id` 为主键做 upsert，只刷新「系统拥有」的字段
（name / description / market / definition / refresh_policy / source_*），
不覆盖 `status` / `current_version` 等人工与运行态字段。
"""

from __future__ import annotations

import json
import logging
import os

from sqlalchemy import text

from .builtins import seed_rows
from .repository import ensure_tables

logger = logging.getLogger(__name__)

_UPSERT_SQL = """
INSERT INTO qm_stock_pool (
    pool_id, code, name, description, market, pool_type, scope,
    tenant_id, owner_user_id, status, visibility, definition, refresh_policy,
    source_kind, source_ref, is_system, created_by, updated_by
) VALUES (
    :pool_id, :code, :name, :description, :market, :pool_type, :scope,
    :tenant_id, :owner_user_id, :status, :visibility,
    CAST(:definition AS JSONB), CAST(:refresh_policy AS JSONB),
    :source_kind, :source_ref, :is_system, :created_by, :updated_by
)
ON CONFLICT (pool_id) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    market = EXCLUDED.market,
    pool_type = EXCLUDED.pool_type,
    definition = EXCLUDED.definition,
    refresh_policy = EXCLUDED.refresh_policy,
    source_kind = EXCLUDED.source_kind,
    source_ref = EXCLUDED.source_ref,
    is_system = EXCLUDED.is_system,
    updated_at = NOW(),
    updated_by = EXCLUDED.updated_by
"""


def _params(row: dict) -> dict:
    params = dict(row)
    params["definition"] = json.dumps(row.get("definition") or {}, ensure_ascii=False)
    params["refresh_policy"] = json.dumps(
        row.get("refresh_policy") or {}, ensure_ascii=False
    )
    return params


async def seed_builtin_pools(session) -> int:
    """异步 seed（admin / engine 启动期使用）。返回成功处理的池数量。"""
    await ensure_tables(session)
    rows = seed_rows()
    ok = 0
    for row in rows:
        try:
            # 逐行 savepoint：单行失败（如 code 已被非系统池占用）不拖垮整批
            async with session.begin_nested():
                await session.execute(text(_UPSERT_SQL), _params(row))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("内置池 seed 跳过 %s: %s", row.get("pool_id"), exc)
    await session.commit()
    logger.info("内置股票池 seed 完成: %d/%d", ok, len(rows))
    return ok


def seed_builtin_pools_sync() -> int:
    """同步 seed（`main_oss.py` 启动期使用）。

    失败仅告警，不影响主流程启动（与 `_ensure_seed_admin` 同策略）。
    """
    try:
        from backend.shared.database_pool import get_db
    except ImportError:  # pragma: no cover
        from shared.database_pool import get_db  # type: ignore

    rows = seed_rows()
    ok = 0
    try:
        with get_db() as session:
            _ensure_tables_sync(session)
            for row in rows:
                try:
                    with session.begin_nested():
                        session.execute(text(_UPSERT_SQL), _params(row))
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("内置池 seed 跳过 %s: %s", row.get("pool_id"), exc)
            session.commit()
        logger.info("内置股票池 seed 完成(同步): %d/%d", ok, len(rows))
        return ok
    except Exception as exc:  # noqa: BLE001
        logger.warning("内置股票池 seed 失败（不影响启动）: %s", exc)
        return 0


def _ensure_tables_sync(session) -> None:
    from pathlib import Path

    sql_path = (
        Path(__file__).resolve().parent / "migrations" / "001_create_stock_pool.sql"
    )
    raw = sql_path.read_text(encoding="utf-8")
    lines = [
        line
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    for statement in "\n".join(lines).split(";"):
        if statement.strip():
            session.execute(text(statement.strip()))
    session.commit()


def snapshot_dir_ready() -> str | None:
    """确保快照目录存在（启动期调用），返回路径。"""
    from pathlib import Path

    target = Path(os.getenv("QM_STOCK_POOL_SNAPSHOT_DIR", "/data/stock_pool"))
    try:
        target.mkdir(parents=True, exist_ok=True)
        return str(target)
    except OSError as exc:
        logger.warning("股票池快照目录创建失败 %s: %s", target, exc)
        return None
