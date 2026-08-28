"""海外券商（老虎/富途/IB）接入配置管理。

配置存 Trade Redis（键 broker:config:{broker}），overseas_brokers 运行时
优先读取该配置，缺失时回退环境变量。敏感字段（私钥/密码）只写不回读，
查询接口仅返回 *_configured 布尔状态。

供前端「模拟交易设置 → 券商接入」卡片使用。
"""
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.trade.deps import AuthContext, get_auth_context, get_redis
from backend.services.trade.redis_client import RedisClient

logger = logging.getLogger(__name__)
router = APIRouter()

_CONFIG_KEY = "broker:config:{broker}"

# 各券商的可配置字段定义：字段名 → 是否敏感（敏感字段只写不读）
BROKER_FIELDS: dict[str, dict[str, bool]] = {
    "tiger": {
        "tiger_id": False,
        "rsa_private_key": True,
        "account": False,
    },
    "futu": {
        "opend_host": False,
        "opend_port": False,
        "trade_pwd_md5": True,
        "trade_env": False,  # REAL / SIMULATE
    },
    "ib": {
        "gateway_host": False,
        "gateway_port": False,
        "client_id": False,
    },
}

BROKER_LABELS = {"tiger": "老虎证券", "futu": "富途证券", "ib": "盈透证券(IB)"}


class BrokerConfigUpdate(BaseModel):
    values: dict[str, str] = Field(..., description="字段名 → 值（敏感字段原文）")


def _normalize_broker(broker: str) -> str:
    broker = str(broker or "").lower().strip()
    if broker not in BROKER_FIELDS:
        raise HTTPException(status_code=404, detail=f"未知券商: {broker}")
    return broker


def _read_config(redis: RedisClient, broker: str) -> dict[str, str]:
    if not redis.client:
        return {}
    try:
        raw = redis.client.get(_CONFIG_KEY.format(broker=broker))
        return json.loads(raw) if raw else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取券商配置失败 broker=%s: %s", broker, exc)
        return {}


def _write_config(redis: RedisClient, broker: str, values: dict[str, str]) -> None:
    if not redis.client:
        raise HTTPException(status_code=503, detail="Redis 不可用")
    redis.client.set(_CONFIG_KEY.format(broker=broker), json.dumps(values, ensure_ascii=False))


def get_broker_setting(broker: str, field: str, default: str = "") -> str:
    """供 overseas_brokers 运行时读取（Redis 优先，回退环境变量由调用方处理）。"""
    from backend.services.trade.redis_client import RedisClient

    try:
        rc = RedisClient()
        values = _read_config(rc, broker)
        return str(values.get(field, "") or "")
    except Exception:  # noqa: BLE001
        return ""


_SELECTED_KEY = "broker:selected:{market}"


@router.get("/broker-config-status")
async def get_broker_config_status(
    market: str = "CN",
    auth: AuthContext = Depends(get_auth_context),
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    """按市场汇总：可选券商、各自配置状态、当前选中的券商。"""
    _ = auth
    market = str(market or "CN").upper()
    brokers = {"HK": ["futu", "tiger", "ib"], "US": ["tiger", "ib", "futu"], "FUTURES": ["ib"], "CN": ["qmt", "tdx"], "CRYPTO": []}.get(market, [])
    items: list[dict[str, Any]] = []
    for broker in brokers:
        stored = _read_config(redis, broker)
        required = BROKER_FIELDS[broker]
        configured = bool(stored) and all(
            str(stored.get(name, "") or "").strip() for name in required
        )
        items.append({
            "broker": broker,
            "label": BROKER_LABELS.get(broker, broker),
            "configured": configured,
        })
    selected_raw = redis.client.get(_SELECTED_KEY.format(market=market)) if redis.client else None
    selected = (selected_raw or b"").decode() if isinstance(selected_raw, (bytes, bytearray)) else (selected_raw or "")
    return {
        "success": True,
        "market": market,
        "brokers": items,
        "selected": selected or None,
    }


class BrokerSelectUpdate(BaseModel):
    broker: str = Field(..., description="该市场使用的券商（tiger/futu/ib/qmt/tdx）")


@router.put("/broker-config/selected/{market}")
async def select_market_broker(
    market: str,
    payload: BrokerSelectUpdate,
    auth: AuthContext = Depends(get_auth_context),
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    """设置某市场使用的实盘券商。"""
    _ = auth
    market = str(market or "CN").upper()
    broker = str(payload.broker or "").lower().strip()
    if not redis.client:
        raise HTTPException(status_code=503, detail="Redis 不可用")
    redis.client.set(_SELECTED_KEY.format(market=market), broker)
    return {"success": True, "market": market, "selected": broker}


@router.get("/broker-config/{broker}")
async def get_broker_config(
    broker: str,
    auth: AuthContext = Depends(get_auth_context),
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    """读取券商接入配置（敏感字段脱敏为 *_configured）。"""
    _ = auth
    broker = _normalize_broker(broker)
    stored = _read_config(redis, broker)
    fields: dict[str, Any] = {}
    for name, sensitive in BROKER_FIELDS[broker].items():
        value = str(stored.get(name, "") or "")
        if sensitive:
            fields[f"{name}_configured"] = bool(value)
        else:
            fields[name] = value
    return {
        "success": True,
        "broker": broker,
        "label": BROKER_LABELS[broker],
        "fields": fields,
    }


@router.put("/broker-config/{broker}")
async def update_broker_config(
    broker: str,
    payload: BrokerConfigUpdate,
    auth: AuthContext = Depends(get_auth_context),
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    """更新券商接入配置。未提供的敏感字段保持原值。"""
    _ = auth
    broker = _normalize_broker(broker)
    allowed = set(BROKER_FIELDS[broker])
    unknown = set(payload.values) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail=f"无效字段: {', '.join(sorted(unknown))}")

    stored = _read_config(redis, broker)
    for name, value in payload.values.items():
        text = str(value or "").strip()
        if text:
            stored[name] = text
        else:
            stored.pop(name, None)  # 空值清除
    _write_config(redis, broker, stored)

    return await get_broker_config(broker, auth, redis)
