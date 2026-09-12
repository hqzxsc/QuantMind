"""管理员身份口径：username='admin'，user_id='00000001'。

历史原因（db_init.sql 曾 seed user_id='admin' 的坏行，且 seed_data 按 username
判存在后跳过），线上 users.user_id 可能为 'admin'，导致 JWT sub、信号表、
池目录等全链路拿的是 'admin' 而非 8 位规范 ID。

本模块提供幂等纠正：把所有字符型 user_id 列中的 'admin' 改为 '00000001'，
整数列（strategies.user_id 等存 users.id）不受影响，无需处理。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

ADMIN_USERNAME = "admin"
ADMIN_USER_ID = "00000001"
LEGACY_ADMIN_USER_ID = "admin"

# 指向 users(user_id) 的 FK 约束名（live 库实测）。约束为即时检查：
# 子表先改则子侧校验失败，父表先改则父侧校验失败，故事务内先 drop、
# 改完再原名建回。子表当前均无 admin 行，但约束本身会拦父表更新。
_USER_ID_FKS: tuple[tuple[str, str], ...] = (
    ("user_roles", "user_roles_user_id_fkey"),
    ("identity_verifications", "identity_verifications_user_id_fkey"),
    ("notifications", "notifications_user_id_fkey"),
    ("password_reset_tokens", "password_reset_tokens_user_id_fkey"),
)


async def fix_admin_user_id(dry_run: bool = False) -> dict[str, Any]:
    """纠正 admin 的 user_id 为 00000001（幂等，可重复执行）。

    返回 {"updated": {table: rows}, "users_fixed": bool}。
    子表先改、users 表最后改（FK 无 ON UPDATE CASCADE，必须保证中间态一致，
    且整个 sweep 跑在 get_session 的单事务里）。
    """
    from backend.shared.database_manager_v2 import get_session

    report: dict[str, Any] = {"updated": {}, "users_fixed": False, "dry_run": dry_run}
    async with get_session() as session:
        if not dry_run:
            # 先卸掉指向 users(user_id) 的 FK（即时检查，任一顺序直接改都会违约束）
            for table, conname in _USER_ID_FKS:
                try:
                    await session.execute(
                        text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {conname}")
                    )
                except Exception as exc:
                    logger.warning("卸 FK %s 跳过: %s", conname, str(exc)[:120])
        tables = (
            (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND column_name='user_id' "
                        "AND data_type IN ('character varying', 'character', 'text')"
                    )
                )
            )
            .scalars()
            .all()
        )
        ordered = [t for t in sorted(set(tables)) if t != "users"] + ["users"]
        for table in ordered:
            try:
                async with session.begin_nested():
                    n = (
                        await session.execute(
                            text(
                                f"UPDATE {table} SET user_id=:new "
                                "WHERE user_id=:old"
                            ),
                            {"new": ADMIN_USER_ID, "old": LEGACY_ADMIN_USER_ID},
                        )
                    ).rowcount or 0
            except Exception as exc:
                logger.warning("纠正 %s 跳过: %s", table, str(exc)[:120])
                continue
            if n:
                report["updated"][table] = n
                if table == "users":
                    report["users_fixed"] = True
                logger.info("纠正 %s: %d 行 admin→00000001", table, n)
        if not dry_run:
            # 原名建回 FK（与 db_init.sql 一致：plain REFERENCES，NO ACTION）
            for table, conname in _USER_ID_FKS:
                try:
                    await session.execute(
                        text(
                            f"ALTER TABLE {table} ADD CONSTRAINT {conname} "
                            "FOREIGN KEY (user_id) REFERENCES users(user_id)"
                        )
                    )
                except Exception as exc:
                    logger.warning("建回 FK %s 跳过: %s", conname, str(exc)[:120])
        if dry_run:
            await session.rollback()
    return report


def needs_fix_sync() -> bool:
    """同步快检：users 表是否存在 user_id='admin' 的行（脚本预检用）。"""
    import os
    from urllib.parse import quote_plus

    from sqlalchemy import create_engine

    url = os.getenv("DATABASE_URL", "").strip()
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "+psycopg2")
    if not url.startswith("postgresql"):
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "5432")
        user = os.getenv("DB_USER", "quantmind")
        password = os.getenv("DB_PASSWORD", "")
        dbname = os.getenv("DB_NAME", "quantmind")
        url = (
            f"postgresql+psycopg2://{user}:{quote_plus(password)}"
            f"@{host}:{port}/{dbname}"
        )
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            n = conn.execute(
                text("SELECT count(*) FROM users WHERE user_id='admin'")
            ).scalar()
            return bool(n)
    finally:
        engine.dispose()
