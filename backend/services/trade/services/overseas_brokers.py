"""海外/港美股实盘券商 Broker 三件套：老虎(Tiger) / 富途(Futu) / 盈透(IB)。

统一实现 broker_client.BaseBroker 接口，由 live_trade_config 的 broker 字段
路由。三家 SDK 均为同步阻塞，全部经 asyncio.to_thread 包装，不阻塞事件循环。

部署前提（各自独立）：
- TigerBroker:  pip install tigeropen；.env 提供 TIGER_ID / TIGER_RSA_PRIVATE_KEY
  （老虎 OpenAPI 平台生成 RSA 密钥对，公钥绑定账户）。纯云端 REST，无需网关。
  TIGER_ACCOUNT 指定交易账户：实盘 U 开头、模拟 SIM 开头，模拟账户可直接
  用作"实盘链路演练"。
- FutuBroker:   pip install futu-api；本机/内网常驻 FutuOpenD 网关
  （.env: FUTU_OPEND_HOST/PORT，默认 127.0.0.1:11111）。FUTU_TRADE_ENV
  = REAL/SIMULATE；下单前需 unlock_trade（FUTU_TRADE_PWD_MD5）。
  OpenD 登录需人工扫码/设备验证一次，掉线需重登——日志会显式提示。
- IBBroker:     pip install ib_async（原 ib_insync）；常驻 IB Gateway
  容器（.env: IB_GATEWAY_HOST/PORT，paper 4002 / real 4001，IB_CLIENT_ID）。
  账户需在 IB 端开通对应市场行情/交易权限。

QuantMind 符号 → 券商代码映射：
  AAPL    → Tiger: AAPL        / Futu: US.AAPL   / IB: Stock(AAPL, SMART, USD)
  0001.HK → Tiger: 0001        / Futu: HK.0001   / IB: Stock(0700, SEHK, HKD)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import httpx

from backend.services.trade.services.broker_client import BaseBroker, BrokerResult

logger = logging.getLogger(__name__)

_DEFAULT_MARKET_URL = "http://quantmind-stream:8003"


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _require_env(key: str) -> str:
    value = _env(key)
    if not value:
        raise RuntimeError(
            f"配置 {key} 未提供：请在「模拟交易设置 → 券商接入」填写，或在 .env 中配置"
        )
    return value


def _setting(broker: str, field: str, env_key: str, default: str = "") -> str:
    """券商配置读取：Trade Redis 的 broker:config 优先，回退环境变量。"""
    try:
        from backend.services.trade.routers.broker_config import get_broker_setting

        value = get_broker_setting(broker, field)
        if value:
            return value
    except Exception:  # noqa: BLE001
        pass
    return _env(env_key) or default


def _futu_code(symbol: str) -> str:
    """QuantMind 符号 → 富途代码（HK.0001 / US.AAPL / SH.600036）。"""
    upper = symbol.upper()
    if upper.endswith(".HK"):
        return f"HK.{upper.split('.')[0]}"
    if upper.endswith(".CN") or upper.endswith(".FUT"):
        return symbol  # 期货走富途期货账户，代码原样（如 CL.FUT 视账户支持）
    if "." in upper:
        code, suffix = upper.split(".", 1)
        return f"{suffix}.{code}"
    if re.fullmatch(r"\d{6}", upper):
        market = "SH" if upper.startswith(("6", "9")) else "SZ"
        return f"{market}.{upper}"
    return f"US.{upper}"


def _tiger_code(symbol: str) -> str:
    """QuantMind 符号 → 老虎代码（美股 ticker 原样、港股数字代码）。"""
    upper = symbol.upper()
    if "." in upper:
        return upper.split(".")[0]
    return upper


def _ib_contract_params(symbol: str) -> tuple[str, str, str]:
    """QuantMind 符号 → (IB symbol, exchange, currency)。"""
    upper = symbol.upper()
    if upper.endswith(".HK"):
        code = upper.split(".")[0]
        return code, "SEHK", "HKD"
    if "." in upper and not upper.endswith(".CN"):
        code, suffix = upper.split(".", 1)
        return code, suffix, "USD"
    return upper, "SMART", "USD"


class _StreamQuoteMixin:
    """行情查询走平台行情网关（与 PaperTradingBroker 同源），不依赖券商行情权限。"""

    market_url: str = _DEFAULT_MARKET_URL
    _http: httpx.AsyncClient | None = None

    async def _http_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=5.0)
        return self._http

    async def query_quote(self, symbol: str) -> dict[str, Any]:
        try:
            from backend.shared.auth import get_internal_call_secret

            client = await self._http_client()
            resp = await client.get(
                f"{self.market_url.rstrip('/')}/api/v1/quotes/{symbol}",
                headers={"X-Internal-Call": get_internal_call_secret()},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "symbol": symbol,
                    "price": float(data.get("current_price") or data.get("last_price") or 0),
                    "pre_close": float(data.get("pre_close") or 0),
                    "suspended": bool(data.get("suspended") or data.get("is_suspended")),
                }
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] query_quote %s failed: %s", type(self).__name__, symbol, e)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 老虎证券
# ─────────────────────────────────────────────────────────────────────────────


class TigerBroker(_StreamQuoteMixin, BaseBroker):
    """老虎证券 OpenAPI（tigeropen，纯云端 REST）。

    TIGER_ACCOUNT 缺省取 TIGER_SIM_ACCOUNT（模拟）或环境首个授权账户。
    模拟账户（SIM 开头）与实盘账户同一套 SDK，适合实盘链路演练。
    """

    def __init__(self) -> None:
        self._config: Any = None
        self._lock = asyncio.Lock()

    def _get_config(self) -> Any:
        if self._config is None:
            from tigeropen.common.consts import Language
            from tigeropen.tiger_open_config import TigerOpenConfig

            tiger_id = _setting("tiger", "tiger_id", "TIGER_ID")
            private_key = _setting("tiger", "rsa_private_key", "TIGER_RSA_PRIVATE_KEY")
            if not tiger_id or not private_key:
                raise RuntimeError(
                    "老虎证券接入未配置：请在「模拟交易设置 → 券商接入」填写 TIGER_ID 与 RSA 私钥"
                )
            # 兼容两种形态：PEM 文本（含 BEGIN 头）直接传入，否则视为文件路径
            is_path = "BEGIN" not in private_key
            self._config = TigerOpenConfig(
                tiger_id, private_key, is_path=is_path, language=Language.zh_CN
            )
        return self._config

    def _get_account(self) -> str:
        return _setting("tiger", "account", "TIGER_ACCOUNT") or _env("TIGER_SIM_ACCOUNT") or ""

    async def place_order(
        self,
        user_id: int,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: float | None = None,
        tenant_id: str = "default",
    ) -> BrokerResult:
        def _place() -> dict[str, Any]:
            from tigeropen.common.consts import Currency, OrderAction, OrderType
            from tigeropen.trade.context import TradeContext
            from tigeropen.trade.order.order import StockOrder

            account = self._get_account()
            ctx = TradeContext(self._get_config(), account=account)
            action = OrderAction.BUY if str(side).upper() == "BUY" else OrderAction.SELL
            sdk_order_type = (
                OrderType.MKT if str(order_type).lower() == "market" else OrderType.LMT
            )
            order = StockOrder(
                account=account,
                symbol=_tiger_code(symbol),
                action=action,
                order_type=sdk_order_type,
                quantity=float(quantity),
                limit_price=float(price) if price else None,
                currency=Currency.HKD if symbol.upper().endswith(".HK") else Currency.USD,
            )
            placed = ctx.place_order(self._get_config(), order)
            return {
                "order_id": str(getattr(placed, "id", "") or ""),
                "filled_price": 0.0,
                "filled_quantity": 0.0,
                "message": str(getattr(placed, "status", "") or "SUBMITTED"),
            }

        try:
            data = await asyncio.to_thread(_place)
            return BrokerResult(
                success=True,
                exchange_order_id=data.get("order_id", ""),
                message=data.get("message", ""),
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[TigerBroker] place_order %s failed: %s", symbol, e)
            return BrokerResult(success=False, message=str(e))

    async def query_account(self, user_id: str, tenant_id: str = "default") -> dict[str, Any]:
        def _query() -> dict[str, Any]:
            from tigeropen.trade.context import TradeContext

            account = self._get_account()
            ctx = TradeContext(self._get_config(), account=account)
            assets = ctx.get_assets(self._get_config(), account=account)
            if not assets:
                return {}
            asset = assets[0]
            positions = {}
            for pos in getattr(assets, "positions", []) or []:
                positions[str(getattr(pos, "symbol", ""))] = {
                    "volume": float(getattr(pos, "quantity", 0) or 0),
                    "available_volume": float(getattr(pos, "qty_available", 0) or 0),
                    "price": float(getattr(pos, "latest_price", 0) or 0),
                    "market_value": float(getattr(pos, "market_value", 0) or 0),
                    "cost": float(getattr(pos, "avg_cost", 0) or 0),
                }
            return {
                "total_asset": float(getattr(asset, "net_liquidation", 0) or 0),
                "cash": float(getattr(asset, "cash", 0) or 0),
                "market_value": float(getattr(asset, "market_value", 0) or 0),
                "positions": positions,
            }

        try:
            return await asyncio.to_thread(_query)
        except Exception as e:  # noqa: BLE001
            logger.error("[TigerBroker] query_account failed: %s", e)
            return {}

    async def cancel_order(self, exchange_order_id: str, **kwargs) -> bool:
        def _cancel() -> bool:
            from tigeropen.trade.context import TradeContext

            account = self._get_account()
            ctx = TradeContext(self._get_config(), account=account)
            result = ctx.cancel_order(self._get_config(), account=account, id=int(exchange_order_id))
            return bool(getattr(result, "success", True))

        try:
            return await asyncio.to_thread(_cancel)
        except Exception as e:  # noqa: BLE001
            logger.error("[TigerBroker] cancel_order %s failed: %s", exchange_order_id, e)
            return False


# ─────────────────────────────────────────────────────────────────────────────
# 富途证券
# ─────────────────────────────────────────────────────────────────────────────


class FutuBroker(_StreamQuoteMixin, BaseBroker):
    """富途 OpenAPI（futu-api，经 FutuOpenD 网关）。

    FUTU_TRADE_ENV = REAL / SIMULATE（模拟环境无需真实资金）。
    港股/美股实盘下单前需交易解锁（FUTU_TRADE_PWD_MD5，MD5 后的交易密码）。
    """

    def __init__(self) -> None:
        self.host = _setting("futu", "opend_host", "FUTU_OPEND_HOST", "127.0.0.1")
        try:
            self.port = int(_setting("futu", "opend_port", "FUTU_OPEND_PORT", "11111"))
        except ValueError:
            self.port = 11111
        self.trade_env_real = _setting(
            "futu", "trade_env", "FUTU_TRADE_ENV", "SIMULATE"
        ).upper() == "REAL"
        self._pwd_md5 = _setting("futu", "trade_pwd_md5", "FUTU_TRADE_PWD_MD5")
        # OpenD 跨网(非127.0.0.1)访问时交易接口强制 RSA 加密：
        # OpenD 端持私钥（官方 rsa_private_key 配置），SDK 端 is_encrypt=True 即可

    def _trade_env(self) -> Any:
        from futu import TrdEnv

        return TrdEnv.REAL if self.trade_env_real else TrdEnv.SIMULATE

    async def place_order(
        self,
        user_id: int,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: float | None = None,
        tenant_id: str = "default",
    ) -> BrokerResult:
        def _place() -> dict[str, Any]:
            from futu import OpenSecTradeContext, OrderType as FutuOrderType, TrdSide, TrdMarket
            FutuBroker._apply_sdk_rsa()

            is_hk = symbol.upper().endswith(".HK")
            trd_market = TrdMarket.HK if is_hk else TrdMarket.US
            ctx = OpenSecTradeContext(
                filter_trdmarket=trd_market, host=self.host, port=self.port, security_firm="FUTUSECURITIES", is_encrypt=True
            )
            try:
                if is_hk and self.trade_env_real:
                    pwd = self._pwd_md5
                    if pwd:
                        ctx.unlock_trade(pwd)
                futu_side = TrdSide.BUY if str(side).upper() == "BUY" else TrdSide.SELL
                futu_type = (
                    FutuOrderType.MARKET if str(order_type).lower() == "market" else FutuOrderType.NORMAL
                )
                ret, data = ctx.place_order(
                    code=_futu_code(symbol),
                    price=float(price) if price else 0.0,
                    quantity=float(quantity),
                    order_type=futu_type,
                    trd_side=futu_side,
                    trd_env=self._trade_env(),
                    adjust_limit=0.0 if is_hk else None,
                )
                if ret != 0:
                    raise RuntimeError(str(data))
                return {"order_id": str(data.get("order_id", "")), "message": "SUBMITTED"}
            finally:
                ctx.close()

        try:
            data = await asyncio.to_thread(_place)
            return BrokerResult(
                success=True,
                exchange_order_id=data.get("order_id", ""),
                message=data.get("message", ""),
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[FutuBroker] place_order %s failed: %s", symbol, e)
            return BrokerResult(success=False, message=str(e))

    async def query_account(self, user_id: str, tenant_id: str = "default") -> dict[str, Any]:
        def _query() -> dict[str, Any]:
            from futu import OpenSecTradeContext, TrdMarket
            FutuBroker._apply_sdk_rsa()

            ctx = OpenSecTradeContext(
                filter_trdmarket=TrdMarket.HK, host=self.host, port=self.port, security_firm="FUTUSECURITIES"
            )
            try:
                ret, data = ctx.accinfo_query(trd_env=self._trade_env())
                if ret != 0:
                    raise RuntimeError(str(data))
                row = data.iloc[0] if hasattr(data, "iloc") and len(data) else {}
                positions: dict[str, Any] = {}
                ret2, pos_data = ctx.position_list_query(trd_env=self._trade_env())
                if ret2 == 0 and hasattr(pos_data, "iloc"):
                    for _, p in pos_data.iterrows():
                        positions[str(p.get("code", ""))] = {
                            "volume": float(p.get("qty", 0) or 0),
                            "available_volume": float(p.get("can_sell_qty", 0) or 0),
                            "price": float(p.get("current_price", 0) or 0),
                            "market_value": float(p.get("market_val", 0) or 0),
                            "cost": float(p.get("cost_price", 0) or 0),
                        }
                return {
                    "total_asset": float(row.get("total_assets", 0) or 0),
                    "cash": float(row.get("cash", 0) or 0),
                    "market_value": float(row.get("market_val", 0) or 0),
                    "positions": positions,
                }
            finally:
                ctx.close()

        try:
            return await asyncio.to_thread(_query)
        except Exception as e:  # noqa: BLE001
            logger.error("[FutuBroker] query_account failed: %s", e)
            return {}

    async def cancel_order(self, exchange_order_id: str, **kwargs) -> bool:
        def _cancel() -> bool:
            from futu import ModifyOrderOp, OpenSecTradeContext, TrdMarket

            ctx = OpenSecTradeContext(
                filter_trdmarket=TrdMarket.HK, host=self.host, port=self.port, security_firm="FUTUSECURITIES"
            )
            try:
                ret, _ = ctx.modify_order(
                    ModifyOrderOp.CANCEL,
                    order_id=exchange_order_id,
                    qty=0,
                    price=0,
                    trd_env=self._trade_env(),
                )
                return ret == 0
            finally:
                ctx.close()

        try:
            return await asyncio.to_thread(_cancel)
        except Exception as e:  # noqa: BLE001
            logger.error("[FutuBroker] cancel_order %s failed: %s", exchange_order_id, e)
            return False


# ─────────────────────────────────────────────────────────────────────────────
# 盈透证券（IB）
# ─────────────────────────────────────────────────────────────────────────────


class IBBroker(_StreamQuoteMixin, BaseBroker):
    """盈透证券 TWS API（ib_async / ib_insync + IB Gateway）。

    IB_GATEWAY_HOST/PORT 指向常驻 IB Gateway 容器（paper 4002 / real 4001）。
    IB 连接为长连接，懒建立、断线自动重连。
    """

    def __init__(self) -> None:
        self.host = _setting("ib", "gateway_host", "IB_GATEWAY_HOST", "127.0.0.1")
        try:
            self.port = int(_setting("ib", "gateway_port", "IB_GATEWAY_PORT", "4002"))
        except ValueError:
            self.port = 4002
        try:
            self.client_id = int(_setting("ib", "client_id", "IB_CLIENT_ID", "7"))
        except ValueError:
            self.client_id = 7
        self._ib: Any = None
        self._lock = asyncio.Lock()

    async def _get_ib(self) -> Any:
        async with self._lock:
            if self._ib is None or not self._ib.isConnected():
                from ib_async import IB

                ib = IB()
                await ib.connectAsync(self.host, self.port, clientId=self.client_id)
                self._ib = ib
            return self._ib

    async def place_order(
        self,
        user_id: int,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: float | None = None,
        tenant_id: str = "default",
    ) -> BrokerResult:
        try:
            from ib_async import LimitOrder, MarketOrder, Stock

            ib = await self._get_ib()
            ib_symbol, exchange, currency = _ib_contract_params(symbol)
            contract = Stock(ib_symbol, exchange, currency)
            action = "BUY" if str(side).upper() == "BUY" else "SELL"
            if str(order_type).lower() == "market" or not price:
                order = MarketOrder(action, float(quantity))
            else:
                order = LimitOrder(action, float(quantity), float(price))
            trade = ib.placeOrder(contract, order)
            return BrokerResult(
                success=True,
                exchange_order_id=str(trade.order.orderId),
                message=str(getattr(trade.orderStatus, "status", "Submitted")),
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[IBBroker] place_order %s failed: %s", symbol, e)
            return BrokerResult(success=False, message=str(e))

    async def query_account(self, user_id: str, tenant_id: str = "default") -> dict[str, Any]:
        try:
            ib = await self._get_ib()
            summary = await ib.accountSummaryAsync()
            values = {item.tag: item.value for item in summary}
            positions: dict[str, Any] = {}
            for pos in await ib.positionsAsync():
                contract = pos.contract
                key = getattr(contract, "localSymbol", "") or contract.symbol
                positions[key] = {
                    "volume": float(pos.position),
                    "available_volume": float(pos.position),
                    "price": float(getattr(pos, "marketPrice", 0) or 0),
                    "market_value": float(pos.position) * float(getattr(pos, "marketPrice", 0) or 0),
                    "cost": float(pos.avgCost),
                }
            return {
                "total_asset": float(values.get("NetLiquidation", 0) or 0),
                "cash": float(values.get("AvailableFunds", values.get("TotalCashValue", 0)) or 0),
                "market_value": float(values.get("GrossPositionValue", 0) or 0),
                "positions": positions,
            }
        except Exception as e:  # noqa: BLE001
            logger.error("[IBBroker] query_account failed: %s", e)
            return {}

    async def cancel_order(self, exchange_order_id: str, **kwargs) -> bool:
        try:
            ib = await self._get_ib()
            for trade in ib.openTrades():
                if str(trade.order.orderId) == str(exchange_order_id):
                    ib.cancelOrder(trade.contract, trade.order)
                    return True
            return False
        except Exception as e:  # noqa: BLE001
            logger.error("[IBBroker] cancel_order %s failed: %s", exchange_order_id, e)
            return False


def get_overseas_broker(broker_type: str) -> BaseBroker:
    """按 broker_type 构建海外券商 broker（live_trade_config.broker 路由）。"""
    broker_type = str(broker_type or "").lower().strip()
    if broker_type == "tiger":
        return TigerBroker()
    if broker_type == "futu":
        return FutuBroker()
    if broker_type == "ib":
        return IBBroker()
    raise ValueError(
        f"未知券商类型: {broker_type}（可选 tiger / futu / ib）"
    )
