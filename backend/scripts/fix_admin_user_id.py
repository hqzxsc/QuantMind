"""纠正 admin 的 user_id 为 00000001（幂等，可重复执行）。

用法（服务器上）：
    docker exec quantmind python3 /app/backend/scripts/fix_admin_user_id.py --dry-run
    docker exec quantmind python3 /app/backend/scripts/fix_admin_user_id.py

纠正后 admin 需重新登录一次（旧 JWT 的 sub='admin' 将查无此人）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def _rename_redis_keys(dry_run: bool) -> list[str]:
    """把各库中以 ':admin' 结尾的用户键改名 ':00000001'（尽力而为）。"""
    renamed: list[str] = []
    try:
        import redis
    except ImportError:
        print("redis 包不可用，跳过 Redis 键迁移")
        return renamed
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD") or None
    for db in range(6):
        try:
            client = redis.Redis(
                host=host, port=port, db=db, password=password,
                socket_connect_timeout=5, decode_responses=True,
            )
            for key in client.scan_iter(match="*:admin", count=1000):
                new_key = key[: -len("admin")] + "00000001"
                renamed.append(f"db{db}:{key} -> {new_key}")
                if not dry_run:
                    client.rename(key, new_key)
            client.close()
        except Exception as exc:
            print(f"Redis db{db} 跳过: {exc}")
    return renamed


async def main() -> int:
    ap = argparse.ArgumentParser(description="纠正 admin user_id 为 00000001")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写入")
    ap.add_argument("--skip-redis", action="store_true", help="跳过 Redis 键迁移")
    args = ap.parse_args()

    from backend.shared.admin_identity import fix_admin_user_id

    report = await fix_admin_user_id(dry_run=args.dry_run)
    total = sum(report["updated"].values())
    print(f"DB: {len(report['updated'])} 张表，共 {total} 行 admin→00000001")
    for table, n in sorted(report["updated"].items()):
        print(f"  - {table}: {n}")
    print(f"users 主行已纠正: {report['users_fixed']}")

    if not args.skip_redis:
        renamed = _rename_redis_keys(args.dry_run)
        print(f"Redis: {len(renamed)} 个键")
        for r in renamed:
            print(f"  - {r}")

    if args.dry_run:
        print("[dry-run] 未写入任何数据")
    else:
        print("完成，admin 请重新登录一次")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
