"""
Simulation Account Manager - Manage paper trading accounts in Redis
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from backend.services.trade.redis_client import RedisClient
from backend.services.trade.trade_config import settings
from backend.shared.trade_account_cache import read_json_cache, write_json_cache

logger = logging.getLogger(__name__)


class SimulationAccountManager:
    """
    Manage simulation accounts state in Redis.
    Key: simulation:account:{tenant_id}:{user_id}
    """

    def __init__(self, redis: RedisClient):
        self.redis = redis
        # A股 T+1: 当日买入不可卖出。持仓用 volume(总量) 与 available_volume(可卖量) 双字段表达，
        # 买入只增 volume，卖出扣减 available_volume，次日开盘由 unlock_t1 把可卖量补齐。
        self._update_balance_lua = """
local key = KEYS[1]
local symbol = ARGV[1]
local delta_cash = tonumber(ARGV[2])
local delta_volume = tonumber(ARGV[3])
local price = tonumber(ARGV[4])

local raw = redis.call("GET", key)
if not raw then
    return cjson.encode({success=false, reason="ACCOUNT_NOT_FOUND"})
end

local account = cjson.decode(raw)
local cash = tonumber(account.cash or 0)
local positions = account.positions or {}

local pos = positions[symbol]
if not pos then
    pos = {volume=0, available_volume=0, cost=0, market_value=0, price=0}
end

-- ARGV[5]: 买入时新增的可卖量。CN(T+1) 传 0（锁定至次日），
-- T+0 市场传 delta_volume（买入即可卖）。卖出路径不受影响。
local avail_delta = tonumber(ARGV[5] or "0")

local old_volume = tonumber(pos.volume or 0)
-- 兼容 T+1 上线前写入的存量持仓: 它们没有 available_volume 字段，
-- 视为全部可卖。缺少这个回退会让存量持仓永久无法卖出且不报错。
local old_available
if pos.available_volume == nil then
    old_available = old_volume
else
    old_available = tonumber(pos.available_volume)
end

local new_cash = cash + delta_cash
local new_volume = old_volume + delta_volume

if new_cash < -0.000001 then
    return cjson.encode({success=false, reason="INSUFFICIENT_CASH"})
end

if new_volume < -0.000001 then
    return cjson.encode({success=false, reason="INSUFFICIENT_HOLDINGS"})
end

local new_available = old_available
if delta_volume < 0 then
    -- 卖出必须动用可卖量（T+1 约束）
    if old_available + delta_volume < -0.000001 then
        return cjson.encode({
            success=false,
            reason="INSUFFICIENT_AVAILABLE_VOLUME",
            available_volume=old_available,
            requested=-delta_volume
        })
    end
    new_available = old_available + delta_volume
elseif delta_volume > 0 then
    new_available = old_available + avail_delta
end

if delta_volume > 0 then
    local current_cost_total = tonumber(pos.cost or 0) * old_volume
    if new_volume > 0 then
        pos.cost = (current_cost_total + (delta_volume * price)) / new_volume
    else
        pos.cost = 0
    end
end

pos.volume = new_volume
pos.available_volume = new_available
pos.price = price
pos.market_value = new_volume * price

if new_volume <= 0.0001 then
    positions[symbol] = nil
else
    positions[symbol] = pos
end

local total_market_value = 0
for _, p in pairs(positions) do
    total_market_value = total_market_value + (tonumber(p.volume or 0) * tonumber(p.price or 0))
end

account.cash = new_cash
account.positions = positions
account.market_value = total_market_value
account.total_asset = new_cash + total_market_value

redis.call("SET", key, cjson.encode(account))
return cjson.encode({success=true})
"""

        # 交易日开盘前调用：把全部持仓的可卖量补齐为总量，解除 T+1 锁定。
        self._unlock_t1_lua = """
local key = KEYS[1]

local raw = redis.call("GET", key)
if not raw then
    return cjson.encode({success=false, reason="ACCOUNT_NOT_FOUND"})
end

local account = cjson.decode(raw)
local positions = account.positions or {}
local unlocked = 0

for symbol, pos in pairs(positions) do
    local volume = tonumber(pos.volume or 0)
    local available
    if pos.available_volume == nil then
        available = volume
    else
        available = tonumber(pos.available_volume)
    end
    if available < volume then
        unlocked = unlocked + 1
    end
    pos.available_volume = volume
    positions[symbol] = pos
end

account.positions = positions
redis.call("SET", key, cjson.encode(account))
return cjson.encode({success=true, unlocked=unlocked})
"""

    @staticmethod
    def _normalize_tenant(tenant_id: str | None) -> str:
        return (tenant_id or "").strip() or "default"

    @staticmethod
    def _normalize_market(market: str | None) -> str:
        """市场标识（账户 Redis 键维度）。CN 保持无后缀旧键，兼容存量账户。"""
        market_upper = str(market or "CN").upper().strip()
        if market_upper in {"", "CN", "A", "A_SHARE"}:
            return "CN"
        return market_upper

    def _get_key(self, user_id: int, tenant_id: str, market: str = "CN") -> str:
        market = self._normalize_market(market)
        if market == "CN":
            # 存量 A 股账户保持无后缀键，不迁移
            return f"simulation:account:{tenant_id}:{user_id}"
        return f"simulation:account:{tenant_id}:{user_id}:{market}"

    def _get_settings_key(self, user_id: int, tenant_id: str) -> str:
        return f"simulation:settings:{tenant_id}:{user_id}"

    @staticmethod
    def _position_key(symbol: str, position_side: str) -> str:
        side = str(position_side or "long").strip().lower()
        return f"{symbol.upper()}::{side}"

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    async def get_settings(
        self,
        user_id: int,
        tenant_id: str = "default",
        default_initial_cash: float = 1_000_000.0,
        cooldown_days: int = 30,
    ) -> dict[str, Any]:
        tenant_id = self._normalize_tenant(tenant_id)
        key = self._get_settings_key(user_id, tenant_id)
        data = read_json_cache(self.redis, key)

        initial_cash = float(default_initial_cash)
        last_modified_at: str | None = None
        next_allowed_modified_at: str | None = None
        can_modify = True

        if data:
            initial_cash = float(data.get("initial_cash", default_initial_cash))
            last_modified_at = data.get("last_modified_at")
            if last_modified_at:
                try:
                    last_dt = datetime.fromisoformat(last_modified_at.replace("Z", "+00:00"))
                    next_dt = last_dt + timedelta(days=cooldown_days)
                    next_allowed_modified_at = next_dt.isoformat()
                    can_modify = self._utc_now() >= next_dt
                except Exception:
                    logger.warning("Failed to parse simulation settings timestamp for key=%s", key)

        return {
            "initial_cash": initial_cash,
            "last_modified_at": last_modified_at,
            "next_allowed_modified_at": next_allowed_modified_at,
            "can_modify": can_modify,
            "cooldown_days": cooldown_days,
        }

    async def set_initial_cash(
        self,
        user_id: int,
        initial_cash: float,
        tenant_id: str = "default",
    ) -> None:
        """Update initial_cash in settings (used when syncing holdings)."""
        tenant_id = self._normalize_tenant(tenant_id)
        key = self._get_settings_key(user_id, tenant_id)

        # 读取现有 settings，保留其他字段
        data = read_json_cache(self.redis, key) or {}
        data["initial_cash"] = float(initial_cash)
        data["last_modified_at"] = self._utc_now().isoformat()

        write_json_cache(self.redis, key, data)
        logger.info(
            "Updated simulation settings initial_cash for tenant=%s user=%s to %.2f",
            tenant_id,
            user_id,
            initial_cash,
        )

    # set_settings removed as initial cash modification is deprecated.


    async def init_account(
        self,
        user_id: int,
        initial_cash: float = 1_000_000.0,
        tenant_id: str = "default",
        market: str = "CN",
    ) -> dict[str, Any]:
        """Initialize or reset simulation account."""
        tenant_id = self._normalize_tenant(tenant_id)
        key = self._get_key(user_id, tenant_id, market)

        account_data = {
            "cash": initial_cash,
            "total_asset": initial_cash,
            "market_value": 0.0,
            "short_market_value": 0.0,
            "liabilities": 0.0,
            "maintenance_margin_ratio": 0.0,
            "warning_level": "normal",
            "positions": {},
            "market": self._normalize_market(market),
        }

        write_json_cache(self.redis, key, account_data)
        logger.info(
            "Initialized simulation account for tenant=%s user=%s market=%s with %.2f",
            tenant_id,
            user_id,
            market,
            initial_cash,
        )

        return account_data

    async def get_account(
        self, user_id: int, tenant_id: str = "default", market: str = "CN"
    ) -> dict[str, Any] | None:
        """Get simulation account state. Returns None if account not initialized."""
        if not self.redis.client:
            return None

        tenant_id = self._normalize_tenant(tenant_id)
        key = self._get_key(user_id, tenant_id, market)
        data = read_json_cache(self.redis, key)

        # 不再自动初始化，返回 None 表示账户未创建
        return data

    async def update_balance(
        self,
        user_id: int,
        symbol: str,
        delta_cash: float,
        delta_volume: float,
        price: float,
        tenant_id: str = "default",
        trade_action: str | None = None,
        position_side: str = "long",
        is_margin_trade: bool = False,
        market: str = "CN",
        t_plus_1: bool = True,
    ) -> dict[str, Any]:
        """Update account balance after trade execution.

        t_plus_1: 买入是否锁定至次日（CN 规则）。T+0 市场传 False，
        买入即刻计入可卖量。
        """
        if not self.redis.client:
            return {"success": False, "reason": "REDIS_UNAVAILABLE"}

        tenant_id = self._normalize_tenant(tenant_id)
        key = self._get_key(user_id, tenant_id, market)

        # 如果账户不存在，先初始化（交易时需要账户存在）。
        # 新注册/默认用户统一按 100 万初始资金建账；
        # 用户对初始资金的修改已废弃（模拟盘设置修改 deprecated），故不读取 settings 残留值。
        if not self.redis.client.get(key):
            await self.init_account(
                user_id, initial_cash=1_000_000.0, tenant_id=tenant_id, market=market
            )

        if (
            is_margin_trade
            or str(position_side).lower() == "short"
            or (trade_action and trade_action.lower() in {"sell_to_open", "buy_to_close"})
        ):
            return await self._update_balance_margin(
                user_id=user_id,
                symbol=symbol,
                price=price,
                tenant_id=tenant_id,
                trade_action=trade_action,
                quantity=abs(delta_volume),
                market=market,
            )

        # T+0 市场买入即可卖；CN 买入锁定（avail_delta=0）
        avail_delta = float(delta_volume) if (delta_volume > 0 and not t_plus_1) else 0.0

        try:
            result = self.redis.client.eval(
                self._update_balance_lua,
                1,
                key,
                symbol,
                str(delta_cash),
                str(delta_volume),
                str(price),
                str(avail_delta),
            )
            payload = json.loads(result) if isinstance(result, str) else result
            if isinstance(payload, dict):
                return payload
            return {"success": False, "reason": "INVALID_SCRIPT_RESULT"}
        except Exception as e:
            logger.error("Failed to update simulation account atomically: %s", e)
            return {"success": False, "reason": "ATOMIC_UPDATE_FAILED"}

    async def unlock_t1(
        self, user_id: int, tenant_id: str = "default", market: str = "CN"
    ) -> dict[str, Any]:
        """交易日开盘前解除 T+1 锁定：把所有持仓的可卖量补齐为总量。

        仅对 T+1 市场（CN）有意义；对 T+0 市场账户调用无副作用
        （可卖量恒等于总量）。
        """
        if not self.redis.client:
            return {"success": False, "reason": "REDIS_UNAVAILABLE"}

        tenant_id = self._normalize_tenant(tenant_id)
        key = self._get_key(user_id, tenant_id, market)

        try:
            result = self.redis.client.eval(self._unlock_t1_lua, 1, key)
            payload = json.loads(result) if isinstance(result, str) else result
            if isinstance(payload, dict):
                return payload
            return {"success": False, "reason": "INVALID_SCRIPT_RESULT"}
        except Exception as e:
            logger.error("Failed to unlock T+1 for tenant=%s user=%s: %s", tenant_id, user_id, e)
            return {"success": False, "reason": "UNLOCK_T1_FAILED"}

    async def _update_balance_margin(
        self,
        *,
        user_id: int,
        symbol: str,
        price: float,
        tenant_id: str,
        trade_action: str | None,
        quantity: float,
        market: str = "CN",
    ) -> dict[str, Any]:
        key = self._get_key(user_id, tenant_id, market)
        account = await self.get_account(user_id, tenant_id=tenant_id) or {}
        positions = dict(account.get("positions") or {})
        cash = float(account.get("cash") or 0.0)
        liabilities = float(account.get("liabilities") or 0.0)
        short_market_value = float(account.get("short_market_value") or 0.0)
        short_proceeds = float(account.get("short_proceeds") or 0.0)
        maintenance_ratio = float(account.get("maintenance_margin_ratio") or 0.0)
        warning_level = str(account.get("warning_level") or "normal")

        action = str(trade_action or "").lower()
        pos_key = self._position_key(symbol, "short")
        pos = dict(positions.get(pos_key) or {})
        old_qty = float(pos.get("volume") or 0.0)
        gross = float(quantity) * float(price)
        # 统一手续费费率，回测默认约 0.0015 包含印花税，这里简化表示
        borrow_fee = gross * float(settings.DEFAULT_BORROW_RATE) / 252.0

        if action == "sell_to_open":
            new_qty = old_qty + float(quantity)
            total_cost = float(pos.get("cost") or 0.0) * old_qty + gross
            pos["volume"] = new_qty
            pos["cost"] = total_cost / new_qty if new_qty > 0 else 0.0
            pos["price"] = float(price)
            pos["market_value"] = new_qty * float(price)
            pos["side"] = "short"
            pos["borrow_fee"] = float(pos.get("borrow_fee") or 0.0) + borrow_fee

            # 融券所得资金冻结
            short_proceeds += gross
            # 现金只扣减手续费
            cash -= borrow_fee

            liabilities += gross
            short_market_value += pos["market_value"]
        elif action == "buy_to_close":
            if old_qty < float(quantity):
                return {"success": False, "reason": "INSUFFICIENT_SHORT_POSITION"}

            avg_cost = float(pos.get("cost") or 0.0)
            short_entry_val = avg_cost * float(quantity)

            # 实现盈亏 = 融券开仓价值 - 买入平仓成本 - 手续费
            realized = short_entry_val - gross - borrow_fee

            # 只有净盈亏结算至可用现金
            cash += realized

            # 释放对应的冻结本金
            short_proceeds = max(0.0, short_proceeds - short_entry_val)

            new_qty = old_qty - float(quantity)
            liabilities = max(0.0, liabilities - short_entry_val)
            short_market_value = max(0.0, short_market_value - short_entry_val)

            if new_qty <= 1e-6:
                positions.pop(pos_key, None)
            else:
                pos["volume"] = new_qty
                pos["price"] = float(price)
                pos["market_value"] = new_qty * float(price)
                pos["borrow_fee"] = float(pos.get("borrow_fee") or 0.0) + borrow_fee
                pos["realized_pnl"] = float(pos.get("realized_pnl") or 0.0) + realized
                positions[pos_key] = pos
        else:
            return {"success": False, "reason": f"UNSUPPORTED_TRADE_ACTION:{action}"}

        if action == "sell_to_open":
            positions[pos_key] = pos

        total_market_value = 0.0
        for position in positions.values():
            qty = float(position.get("volume") or 0.0)
            px = float(position.get("price") or 0.0)
            side = str(position.get("side") or "long").lower()
            mv = qty * px
            total_market_value += mv if side == "long" else -mv

        equity = cash + short_proceeds + total_market_value

        # 维持担保比例 = 总资产 / 总负债
        if liabilities > 0:
            maintenance_ratio = equity / liabilities if liabilities else 0.0
            if maintenance_ratio <= float(settings.MARGIN_CLOSEOUT_RATIO):
                warning_level = "closeout"
            elif maintenance_ratio <= float(settings.MARGIN_WARNING_RATIO):
                warning_level = "warning"
            else:
                warning_level = "normal"

        account.update(
            {
                "cash": cash,
                "short_proceeds": short_proceeds,
                "positions": positions,
                "market_value": total_market_value,
                "short_market_value": short_market_value,
                "liabilities": liabilities,
                "maintenance_margin_ratio": maintenance_ratio,
                "warning_level": warning_level,
                "total_asset": equity,
            }
        )
        write_json_cache(self.redis, key, account)
        return {"success": True, "reason": "OK", "account": account}
