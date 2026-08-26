"""大盘分析实时数据接入模块

从 Stream 服务推送的 Redis 行情数据中获取实时快照，
支持批量获取 (pipeline)、板块指标聚合与 Pub/Sub 订阅。

Stream 服务推送格式:
    - 行情 Hash key: ``quantmind:quotes:{symbol}``
    - 字段: last_price, pct_change, volume, amount, open, high, low, pre_close, timestamp
    - 板块更新频道: ``quantmind:sector:{sector_id}:updates``

设计原则:
    - 所有方法具备降级能力: Redis 不可用时返回空数据，不抛异常
    - 通过依赖注入 Redis 客户端与成分股提供者，便于单元测试
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable, Optional

import redis.asyncio as aioredis

from .domain import classify_sentiment

logger = logging.getLogger(__name__)

# ---- 常量 ----

# 行情 Hash key 前缀，完整格式: quantmind:quotes:{symbol}
QUOTE_KEY_PREFIX = "quantmind:quotes:"

# 板块更新 Pub/Sub 频道前缀，完整格式: quantmind:sector:{sector_id}:updates
SECTOR_CHANNEL_PREFIX = "quantmind:sector:"
SECTOR_CHANNEL_SUFFIX = ":updates"

# 行情 Hash 标准字段名
FIELD_LAST_PRICE = "last_price"
FIELD_PCT_CHANGE = "pct_change"
FIELD_VOLUME = "volume"
FIELD_AMOUNT = "amount"
FIELD_OPEN = "open"
FIELD_HIGH = "high"
FIELD_LOW = "low"
FIELD_PRE_CLOSE = "pre_close"
FIELD_TIMESTAMP = "timestamp"

# 成分股提供者类型: async (sector_id) -> list[str]
ConstituentsProvider = Callable[[str], Awaitable[list[str]]]
# 订阅回调类型: (payload: dict) -> None 或 coroutine
SectorUpdateCallback = Callable[[dict[str, Any]], Optional[Awaitable[None]]]


# ---- 工具函数 ----

def _to_float(value: Any) -> float | None:
    """安全转换为 float，失败或空值返回 None"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    """安全转换为 int，失败或空值返回 None"""
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float, hi: float) -> float:
    """将值限制在 [lo, hi] 范围内"""
    return max(lo, min(hi, value))


# ---- RedisStreamAdapter ----

class RedisStreamAdapter:
    """Redis 操作封装

    从 Stream 服务推送的 Redis Hash 中读取实时行情快照。
    Key 格式: ``quantmind:quotes:{symbol}``

    所有方法均具备降级能力: Redis 不可用时返回空数据，不抛异常。
    """

    def __init__(self, redis_client: aioredis.Redis | None = None):
        """
        Args:
            redis_client: 可选的 ``redis.asyncio.Redis`` 客户端实例。
                          为 None 时按环境变量自动创建连接 (延迟初始化)。
        """
        self._client = redis_client
        # 标记是否自建连接 (需在 close 时清理)
        self._owns_client = redis_client is None

    async def _get_client(self) -> aioredis.Redis:
        """获取 Redis 客户端 (延迟初始化)"""
        if self._client is None:
            host = os.getenv("REDIS_HOST", "127.0.0.1")
            port = int(os.getenv("REDIS_PORT", "6379"))
            password = os.getenv("REDIS_PASSWORD") or None
            db = int(os.getenv("REDIS_DB", "0"))
            self._client = aioredis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=5,
            )
        return self._client

    @staticmethod
    def _quote_key(symbol: str) -> str:
        """构造行情 Hash key"""
        return f"{QUOTE_KEY_PREFIX}{symbol}"

    # ---- 单个 / 批量获取 ----

    async def get_quote(self, symbol: str) -> dict[str, Any] | None:
        """获取单个标的实时行情快照

        Returns:
            行情字典，无数据或异常时返回 None
        """
        try:
            client = await self._get_client()
            data = await client.hgetall(self._quote_key(symbol))
            raw = dict(data) if data else {}
            if not raw:
                return None
            return self._parse_quote(symbol, raw)
        except Exception as e:
            logger.warning("获取实时行情失败 %s: %s", symbol, e)
            return None

    async def get_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """批量获取实时行情 (pipeline)

        使用 Redis pipeline 一次性查询多个标的，减少网络往返。

        Returns:
            ``{symbol: {行情字段}}`` 字典；无数据的标的不会出现在结果中。
            Redis 异常时返回空字典，不抛异常。
        """
        if not symbols:
            return {}

        try:
            client = await self._get_client()
            async with client.pipeline(transaction=False) as pipe:
                for symbol in symbols:
                    pipe.hgetall(self._quote_key(symbol))
                results: list[dict[str, Any]] = await pipe.execute()
        except Exception as e:
            logger.warning("批量获取行情失败: %s", e)
            return {}

        quotes: dict[str, dict[str, Any]] = {}
        for symbol, raw in zip(symbols, results):
            if not raw:
                continue
            parsed = self._parse_quote(symbol, raw)
            if parsed is not None:
                quotes[symbol] = parsed
        return quotes

    @staticmethod
    def _parse_quote(symbol: str, raw: dict[str, Any]) -> dict[str, Any] | None:
        """解析 Redis Hash 字段为行情字典

        必须包含 last_price 字段，否则视为无效数据返回 None。
        """
        last_price = _to_float(raw.get(FIELD_LAST_PRICE))
        if last_price is None:
            return None
        return {
            "symbol": symbol,
            "last_price": last_price,
            "pct_change": _to_float(raw.get(FIELD_PCT_CHANGE)),
            "volume": _to_int(raw.get(FIELD_VOLUME)),
            "amount": _to_float(raw.get(FIELD_AMOUNT)),
            "open": _to_float(raw.get(FIELD_OPEN)),
            "high": _to_float(raw.get(FIELD_HIGH)),
            "low": _to_float(raw.get(FIELD_LOW)),
            "pre_close": _to_float(raw.get(FIELD_PRE_CLOSE)),
            "timestamp": raw.get(FIELD_TIMESTAMP),
        }

    # ---- Pub/Sub 订阅 ----

    async def subscribe_channel(
        self,
        channel: str,
        callback: SectorUpdateCallback,
    ) -> asyncio.Task[None]:
        """订阅 Redis 频道，收到消息后调用回调

        Args:
            channel: 频道名称
            callback: 消息回调，接收 payload 字典；可为同步或协程

        Returns:
            后台监听任务，调用方可 cancel() 停止订阅
        """
        client = await self._get_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)

        async def _listen() -> None:
            """监听循环"""
            try:
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    data = message.get("data")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="replace")
                    # 尝试 JSON 解析，失败时包装原始字符串
                    if isinstance(data, str):
                        try:
                            payload: Any = json.loads(data)
                        except (json.JSONDecodeError, TypeError):
                            payload = {"raw": data}
                    else:
                        payload = data if isinstance(data, dict) else {"raw": data}

                    try:
                        result = callback(payload)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("订阅回调执行错误 %s: %s", channel, e)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error("订阅监听异常 %s: %s", channel, e)
            finally:
                try:
                    await pubsub.unsubscribe(channel)
                    await pubsub.aclose()
                except Exception:
                    pass

        return asyncio.create_task(_listen())

    async def close(self) -> None:
        """关闭自建的 Redis 连接"""
        if self._owns_client and self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None


# ---- MarketRealtimeAdapter ----

class MarketRealtimeAdapter:
    """大盘实时行情适配器

    基于 ``RedisStreamAdapter`` 提供面向大盘分析的高层接口:
        - 批量获取实时行情
        - 从成分股实时行情聚合板块指标 (avg_pct_change, advance/decline, sentiment)
        - 订阅板块实时更新 (Redis Pub/Sub)
    """

    def __init__(
        self,
        stream_adapter: RedisStreamAdapter,
        constituents_provider: ConstituentsProvider | None = None,
    ):
        """
        Args:
            stream_adapter: Redis 流适配器实例
            constituents_provider: 异步成分股提供者，签名 ``async (sector_id) -> list[str]``。
                                   为 None 时 ``get_sector_realtime_metrics`` 返回空指标。
        """
        self.stream = stream_adapter
        self._get_constituents = constituents_provider

    async def get_realtime_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """批量获取实时行情快照

        Args:
            symbols: 标的代码列表 (Prefix 格式，如 SH600036)

        Returns:
            ``{symbol: {行情字段}}`` 字典；Redis 异常时返回空字典
        """
        return await self.stream.get_quotes(symbols)

    async def get_sector_realtime_metrics(self, sector_id: str) -> dict[str, Any]:
        """从成分股实时行情聚合板块指标

        聚合内容包括:
            - avg_pct_change: 成分股涨跌幅均值 (百分比点)
            - advance_count / decline_count / flat_count: 涨 / 跌 / 平家数
            - sentiment_score / sentiment: 情绪分数与标签
            - total_volume / total_amount: 成交量与成交额汇总

        Args:
            sector_id: 板块 ID

        Returns:
            板块实时指标字典。无成分股或 Redis 异常时返回空指标 (count=0)。
        """
        # 1. 获取成分股列表
        instruments: list[str] = []
        if self._get_constituents is not None:
            try:
                instruments = await self._get_constituents(sector_id)
            except Exception as e:
                logger.warning("获取板块 %s 成分股失败: %s", sector_id, e)
                instruments = []

        if not instruments:
            return self._empty_metrics(sector_id)

        # 2. 批量获取实时行情
        quotes = await self.stream.get_quotes(instruments)

        # 3. 聚合指标
        return self._aggregate(sector_id, instruments, quotes)

    async def subscribe_sector_updates(
        self,
        sector_id: str,
        callback: SectorUpdateCallback,
    ) -> asyncio.Task[None]:
        """订阅板块实时更新 (Redis Pub/Sub)

        频道: ``quantmind:sector:{sector_id}:updates``

        Args:
            sector_id: 板块 ID
            callback: 更新回调，接收 payload 字典

        Returns:
            后台监听任务，调用方可 cancel() 停止订阅
        """
        channel = f"{SECTOR_CHANNEL_PREFIX}{sector_id}{SECTOR_CHANNEL_SUFFIX}"
        return await self.stream.subscribe_channel(channel, callback)

    # ---- 内部方法 ----

    @staticmethod
    def _empty_metrics(sector_id: str) -> dict[str, Any]:
        """构造空指标 (无成分股或异常时的降级返回)"""
        return {
            "sector_id": sector_id,
            "constituent_count": 0,
            "quote_count": 0,
            "avg_pct_change": None,
            "advance_count": 0,
            "decline_count": 0,
            "flat_count": 0,
            "sentiment_score": None,
            "sentiment": "neutral",
            "total_volume": None,
            "total_amount": None,
        }

    @staticmethod
    def _aggregate(
        sector_id: str,
        instruments: list[str],
        quotes: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """从成分股行情聚合板块指标"""
        advance = 0  # 涨家数
        decline = 0  # 跌家数
        flat = 0  # 平家数
        pct_sum = 0.0
        pct_count = 0
        total_volume = 0
        total_amount = 0.0

        for symbol in instruments:
            quote = quotes.get(symbol)
            if quote is None:
                continue
            pct = quote.get("pct_change")
            if pct is not None:
                if pct > 0:
                    advance += 1
                elif pct < 0:
                    decline += 1
                else:
                    flat += 1
                pct_sum += pct
                pct_count += 1
            else:
                flat += 1

            vol = quote.get("volume")
            if vol is not None:
                total_volume += vol
            amt = quote.get("amount")
            if amt is not None:
                total_amount += amt

        avg_pct_change = (pct_sum / pct_count) if pct_count > 0 else None

        # 情绪分数: 将 avg_pct_change 归一化到 [-1, 1] (±10% 对应 ±1)
        sentiment_score: float | None = None
        if avg_pct_change is not None:
            sentiment_score = round(_clamp(avg_pct_change / 10.0, -1.0, 1.0), 4)

        sentiment = classify_sentiment(sentiment_score) if sentiment_score is not None else "neutral"

        return {
            "sector_id": sector_id,
            "constituent_count": len(instruments),
            "quote_count": len(quotes),
            "avg_pct_change": round(avg_pct_change, 4) if avg_pct_change is not None else None,
            "advance_count": advance,
            "decline_count": decline,
            "flat_count": flat,
            "sentiment_score": sentiment_score,
            "sentiment": sentiment,
            "total_volume": total_volume if total_volume > 0 else None,
            "total_amount": round(total_amount, 2) if total_amount > 0 else None,
        }
