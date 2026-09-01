from __future__ import annotations

import os
from typing import Any, Dict, List

from redis import Redis

from backend.shared.event_bus.schemas import SignalCreatedEvent
from backend.shared.logging_config import get_logger
from backend.shared.redis_sentinel_client import get_redis_sentinel_client

logger = get_logger(__name__)


class EngineSignalStreamPublisher:
    def __init__(self):
        self.enabled = os.getenv("ENABLE_SIGNAL_STREAM_PUBLISH", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.stream_prefix = os.getenv("SIGNAL_STREAM_PREFIX", "qm:signal:stream")
        self.stream_maxlen = int(os.getenv("SIGNAL_STREAM_MAXLEN", "200000"))
        self.default_quantity = int(os.getenv("SIGNAL_EVENT_DEFAULT_QUANTITY", "100"))
        self.latest_key_prefix = os.getenv("SIGNAL_LATEST_KEY_PREFIX", "qm:signal:latest")
        self.stream_redis_host = str(os.getenv("SIGNAL_STREAM_REDIS_HOST", "")).strip()
        self.stream_redis_port = int(os.getenv("SIGNAL_STREAM_REDIS_PORT", "6379"))
        self.stream_redis_db = int(os.getenv("SIGNAL_STREAM_REDIS_DB", "0"))
        self.stream_redis_password = str(os.getenv("SIGNAL_STREAM_REDIS_PASSWORD", "")).strip() or None

    def _get_stream_client(self):
        # 优先使用独立信号流 Redis，避免与 engine 其它缓存/队列 Redis 混用。
        if self.stream_redis_host:
            return Redis(
                host=self.stream_redis_host,
                port=self.stream_redis_port,
                db=self.stream_redis_db,
                password=self.stream_redis_password,
                decode_responses=False,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
                health_check_interval=30,
            )
        return get_redis_sentinel_client()

    def publish_signals(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        trace_id: str,
        signal_source: str,
        signals: list[dict[str, Any]],
    ) -> int:
        if not self.enabled or not signals:
            return 0

        client = self._get_stream_client()
        stream = f"{self.stream_prefix}:{tenant_id or 'default'}"
        published = 0

        for idx, sig in enumerate(signals):
            symbol = str(sig.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            explicit_side = str(sig.get("side") or "").upper().strip()
            if explicit_side in {"BUY", "SELL"}:
                side = explicit_side
            else:
                side = "BUY" if float(sig.get("score", 0.0)) >= 0 else "SELL"
            quantity = int(sig.get("quantity") or self.default_quantity)
            price = float(sig.get("price") or 0.0)
            score = float(sig.get("score") or 0.0)
            trade_action = sig.get("trade_action")
            position_side = sig.get("position_side")
            is_margin_trade = sig.get("is_margin_trade")
            signal_id = str(sig.get("signal_id") or f"{run_id}-{idx:04d}")
            client_order_id = str(sig.get("client_order_id") or f"{signal_id}-coid")

            event = SignalCreatedEvent(
                tenant_id=tenant_id or "default",
                user_id=str(user_id),
                run_id=run_id,
                trace_id=trace_id,
                signal_id=signal_id,
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                trade_action=str(trade_action) if trade_action else None,
                position_side=str(position_side) if position_side else None,
                is_margin_trade=bool(is_margin_trade) if is_margin_trade is not None else None,
                quantity=max(1, quantity),
                price=price,
                score=score,
                signal_source=("fusion_report" if signal_source == "fusion_report" else "inference_fallback"),
            )
            payload = {k: str(v) for k, v in event.model_dump().items() if v is not None}
            client.xadd(
                stream,
                payload,
                maxlen=self.stream_maxlen,
                approximate=True,
            )
            published += 1

        if published:
            logger.info(
                "Published %d signal events to stream=%s run_id=%s",
                published,
                stream,
                run_id,
            )
        return published

    def mark_latest_run(self, *, tenant_id: str, user_id: str, run_id: str, ttl_seconds: int = 86400) -> None:
        if not tenant_id or not user_id or not run_id:
            return
        latest_key = f"{self.latest_key_prefix}:{tenant_id or 'default'}:{str(user_id)}"
        ttl = max(60, int(ttl_seconds))
        # 双写：独立 stream Redis + 哨兵 Redis，保证 api/trade 任意读端都能命中。
        # 单 Redis 环境两者是同一实例，按 id 去重后实际只写一次，幂等。
        candidates: list[Any] = []
        try:
            candidates.append(self._get_stream_client())
        except Exception as exc:
            logger.warning("获取 stream Redis 失败，跳过: %s", exc)
        try:
            candidates.append(get_redis_sentinel_client())
        except Exception as exc:
            logger.warning("获取 sentinel Redis 失败，跳过: %s", exc)

        # 按 id 去重
        seen: set[int] = set()
        clients: list[Any] = []
        for c in candidates:
            if c is None:
                continue
            cid = id(c)
            if cid not in seen:
                seen.add(cid)
                clients.append(c)

        if not clients:
            logger.warning("无可用 Redis 客户端，latest 标记写入跳过: key=%s", latest_key)
            return

        for client in clients:
            try:
                client.set(latest_key, str(run_id), ex=ttl)
            except Exception as exc:
                logger.warning("写入 latest 标记失败 client=%s key=%s: %s", type(client).__name__, latest_key, exc)
                continue
        logger.info(
            "Marked latest signal run: key=%s run_id=%s clients=%d",
            latest_key,
            run_id,
            len(clients),
        )
