"""模拟账户 Redis 键唯一规范（纯标准库，沙箱子进程也可 import）。

键格式（CN 无后缀是历史存量，不迁移）：
    simulation:account:{tenant}:{user}            # CN / A 股
    simulation:account:{tenant}:{user}:{MARKET}   # HK / US / ...
    simulation:settings:{tenant}:{user}

所有读写必须经本模块，禁止各处手写 f-string，避免跨链路 key 漂移
（T+1 解锁扫错账户、资金快照漏市场账户、沙箱读到空账户）。
"""

from __future__ import annotations

ACCOUNT_KEY_PREFIX = "simulation:account:"
SETTINGS_KEY_PREFIX = "simulation:settings:"


def normalize_tenant(tenant_id: str | None) -> str:
    return (tenant_id or "").strip() or "default"


def normalize_market(market: str | None) -> str:
    """CN（含 A/A_SHARE/空）归一为 CN，其余大写原样返回。"""
    market_upper = str(market or "CN").upper().strip()
    if market_upper in {"", "CN", "A", "A_SHARE"}:
        return "CN"
    return market_upper


def account_key(tenant_id: str | None, user_id: object, market: str | None = "CN") -> str:
    """构造模拟账户 Redis 键。user_id 保持调用方原样（不做 zfill/int 改写）。"""
    tenant = normalize_tenant(tenant_id)
    user = str(user_id).strip()
    if normalize_market(market) == "CN":
        return f"{ACCOUNT_KEY_PREFIX}{tenant}:{user}"
    return f"{ACCOUNT_KEY_PREFIX}{tenant}:{user}:{normalize_market(market)}"


def settings_key(tenant_id: str | None, user_id: object) -> str:
    return f"{SETTINGS_KEY_PREFIX}{normalize_tenant(tenant_id)}:{str(user_id).strip()}"


def parse_account_key(key: str) -> tuple[str, str, str] | None:
    """解析账户键 -> (tenant, user原文, market)，CN 无后缀时 market='CN'。

    5 段（带市场后缀）与 4 段（CN）都接受；其余返回 None。
    """
    parts = str(key or "").split(":")
    if len(parts) not in (4, 5):
        return None
    if parts[0] != "simulation" or parts[1] != "account":
        return None
    tenant = parts[2].strip() or "default"
    user = parts[3].strip()
    if not tenant or not user:
        return None
    market = normalize_market(parts[4]) if len(parts) == 5 else "CN"
    return tenant, user, market
