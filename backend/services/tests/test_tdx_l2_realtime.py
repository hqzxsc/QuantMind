"""TDX L2 实时推理单元测试：截面标准化 / 信号合成 / 触发 / 冷却。

纯函数级测试（Redis 用 mock，不触真实库）：
- _z_score: 截面标准化与极端值 clip
- compute_signal_scores: ICIR 加权 + sigmoid 单调性
- compute_realtime_score: 日频分 × 权重 + 信号分 × 权重
- load/save_l2_config: Redis 读写与非法值保护
- is_cooldown / set_cooldown: 冷却窗口
"""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.trade_shared.redis_client import RedisClient
from backend.services.live_trading.services.tdx_l2_capture_task import FACTOR_ICIR
from backend.services.live_trading.services.tdx_l2_realtime import (
    _z_score,
    compute_realtime_score,
    compute_signal_scores,
    is_cooldown,
    load_l2_config,
    save_l2_config,
    set_cooldown,
)


def _factors(**overrides) -> dict:
    """13 因子基值（中性）。"""
    f = {k: 0.0 for k in FACTOR_ICIR}
    f.update(overrides)
    return f


class TestZScore:
    def test_positive_outliers_get_positive_z(self):
        # Arrange
        values = {"A": 0.9, "B": 0.5, "C": 0.5, "D": 0.5, "E": 0.4}
        # Act
        z = _z_score(values)
        # Assert
        assert z["A"] > 0
        assert z["E"] < 0

    def test_clips_at_3_sigma(self):
        # Arrange: 20 个样本中一个极端离群值 → z≈4.2 被剪到 3
        values = {"A": 100.0, **{f"S{i}": 0.0 for i in range(19)}}
        # Act
        z = _z_score(values)
        # Assert
        assert z["A"] == 3.0
        assert z["S0"] >= -3.0

    def test_degenerate_returns_zero(self):
        # Arrange: 全同值 → 零标准差
        values = {"A": 0.5, "B": 0.5}
        # Act
        z = _z_score(values)
        # Assert
        assert z["A"] == 0.0


class TestComputeSignalScores:
    def test_bullish_stock_scores_higher_than_bearish(self):
        # Arrange: 同一池内 A 全面强于 B
        pool = {
            "SH600000": _factors(
                micro_vpin_vol_ratio=0.6, micro_vpin_amount_ratio=0.5,
                micro_zone_distribution=0.4, micro_open_gap=0.03,
            ),
            "SH600519": _factors(
                micro_vpin_vol_ratio=0.1, micro_vpin_amount_ratio=0.1,
                micro_zone_distribution=-0.3, micro_open_gap=-0.03,
            ),
            "SZ000001": _factors(),
            "SZ000002": _factors(),
            "SZ300750": _factors(),
            "SH601318": _factors(),
        }
        # Act
        signal = compute_signal_scores(pool, None)
        # Assert
        assert signal["SH600000"] > signal["SH600519"]
        assert all(0 <= v <= 100 for v in signal.values())

    def test_symmetric_pool_scores_near_midpoint(self):
        # Arrange: 全部中性 → sigmoid(0) ≈ 50
        pool = {f"SH{s:06d}": _factors() for s in range(10)}
        # Act
        signal = compute_signal_scores(pool, None)
        # Assert
        for v in signal.values():
            assert 40 <= v <= 60

    def test_custom_factor_weights_override(self):
        # Arrange: 只用 vpin 权重
        pool = {
            "A": _factors(micro_vpin_vol_ratio=0.9),
            "B": _factors(micro_vpin_vol_ratio=0.0),
            "C": _factors(micro_vpin_vol_ratio=0.5),
            "D": _factors(micro_vpin_vol_ratio=0.4),
            "E": _factors(micro_vpin_vol_ratio=0.3),
        }
        # Act
        signal = compute_signal_scores(pool, {"micro_vpin_vol_ratio": 1.0})
        # Assert
        assert signal["A"] > signal["C"] > signal["B"]


class TestComputeRealtimeScore:
    def test_fusion_and_signal_blend(self):
        # Arrange: 日频 3.0(=100分) + 信号 80 → 0.6×100 + 0.4×80 = 92
        # Act
        score = compute_realtime_score(fusion_score=3.0, signal_score=80.0)
        # Assert
        assert score == 92.0

    def test_missing_fusion_uses_neutral_50(self):
        # Arrange: 无日频分时按中性 50 处理
        # Act
        score = compute_realtime_score(fusion_score=None, signal_score=50.0)
        # Assert
        assert score == 50.0

    def test_weight_changes_blend(self):
        # Act
        score = compute_realtime_score(3.0, 100.0, daily_weight=0.8, signal_weight=0.2)
        # Assert
        assert score == 100.0


class _FakeRedis:
    """内存 raw 客户端替身（模拟 redis-py）：包装层的 json 序列化在外面完成。"""

    def __init__(self) -> None:
        self.store: dict = {}
        self.expired_at: dict = {}

    def set(self, key: str, value, **kwargs) -> None:
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)

    def scan_iter(self, match: str = "*"):
        # 仅支持单 "*" 通配（项目内用法）
        prefix, suffix = match.split("*", 1)
        for k in list(self.store.keys()):
            if k.startswith(prefix) and k.endswith(suffix):
                yield k

    def expire(self, key: str, ttl: int) -> None:
        self.expired_at[key] = ttl

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


def _patched_redis() -> RedisClient:
    """真实 RedisClient 包装层 + 内存 raw 替身（与生产一致：set 存 json 串）。"""
    rc = RedisClient()
    rc.client = _FakeRedis()
    return rc


class TestL2Config:
    def test_defaults_are_conservative(self):
        # Act: 无 Redis 连接时兜底默认值
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", RedisClient()):
            cfg = load_l2_config()
        # Assert
        assert cfg["enabled"] is False
        assert cfg["buy_trigger"] == 65.0
        assert cfg["sell_trigger"] == 45.0
        assert cfg["cooldown_min"] == 30
        assert cfg["factor_weights"] is None

    def test_save_restricts_pool_size(self):
        # Arrange
        rc = _patched_redis()
        # Act
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc):
            save_l2_config({"pool_size": 999, "buy_trigger": 70, "enabled": True})
            cfg = load_l2_config()
        # Assert
        assert cfg["pool_size"] == 50
        assert cfg["buy_trigger"] == 70.0
        assert cfg["enabled"] is True

    def test_save_ignores_unknown_keys(self):
        # Arrange
        rc = _patched_redis()
        # Act
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc):
            cfg = save_l2_config({"hack": "x", "buy_trigger": 66})
        # Assert
        assert "hack" not in cfg
        assert cfg["buy_trigger"] == 66.0


class TestCooldown:
    def test_cooldown_blocks_within_window(self):
        # Arrange
        redis = MagicMock()
        redis.get.return_value = str(time.time())
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", redis):
            # Act
            blocked = is_cooldown("SH600000", cooldown_min=30)
        # Assert
        assert blocked is True

    def test_cooldown_expired_allows(self):
        # Arrange
        redis = MagicMock()
        redis.get.return_value = str(time.time() - 3600)
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", redis):
            # Act
            blocked = is_cooldown("SH600000", cooldown_min=30)
        # Assert
        assert blocked is False

    def test_set_cooldown_writes_timestamp(self):
        # Arrange
        redis = MagicMock()
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", redis):
            # Act
            set_cooldown("SH600000", 30)
        # Assert
        redis.set.assert_called_once()
        assert "tdx:l2:cooldown:sh600000" in redis.set.call_args[0][0]


# ============ 委托时点行情落盘（"什么点买的"数据源） ============

class TestOrderQuotePersistence:
    def test_save_and_load_order_quote(self):
        # Arrange
        rc = _patched_redis()
        # Act
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc):
            from backend.services.live_trading.services.tdx_l2_realtime import (
                load_order_quotes,
                load_symbol_quotes,
                save_order_quote,
            )

            save_order_quote(
                symbol="SH600000", order_id="1001", plan_id="p1", side="buy",
                volume=100, amount=1050.0, quote_price=10.5,
                market_detail="上证 3512.34 vs MA20 3400 (之上)", index_above=True,
            )
        # Assert: 每笔委托一条 + 每只最新一条
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc):
            quotes = load_order_quotes()
            sym_quotes = load_symbol_quotes()
        assert "1001" in quotes
        assert quotes["1001"]["quote_price"] == 10.5
        assert quotes["1001"]["index_above"] is True
        assert "上证 3512.34" in quotes["1001"]["market_detail"]
        assert sym_quotes["SH600000"]["order_id"] == "1001"
        assert rc.client.expired_at["tdx:l2:order_quote:1001"] == 7 * 3600
        assert rc.client.expired_at["tdx:l2:quotes:sh600000"] == 24 * 3600

    def test_merge_order_states_adds_filled_price(self):
        # Arrange
        rc = _patched_redis()
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc):
            from backend.services.live_trading.services.tdx_l2_realtime import (
                load_order_quotes,
                merge_order_states,
                save_order_quote,
            )

            save_order_quote(
                symbol="SH600000", order_id="2001", plan_id="p2", side="buy",
                volume=200, amount=2100.0, quote_price=10.5,
            )
            # Act: 成交后把桥的实际成交均价并进时点记录
            merge_order_states(
                [{"order_id": "2001", "status": "filled", "filled_price": 10.42, "filled_volume": 200}]
            )
        # Assert: 决策时行情与成交均价双口径并存
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc):
            rec = load_order_quotes()["2001"]
        assert rec["quote_price"] == 10.5
        assert rec["filled_price"] == 10.42
        assert rec["status"] == "filled"

    def test_merge_ignores_unknown_order(self):
        # Arrange
        rc = _patched_redis()
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc):
            from backend.services.live_trading.services.tdx_l2_realtime import merge_order_states

            merge_order_states([{"order_id": "9999", "status": "filled"}])
        # Assert: 无异常且无写入
        assert not rc.client.store


# ============ 在途单注册表（不能多/不能漏） ============

class TestInflightRegistry:
    def test_save_load_clear_roundtrip(self):
        # Arrange
        rc = _patched_redis()
        from backend.services.live_trading.services.tdx_l2_realtime import (
            clear_inflight,
            list_inflight,
            load_inflight,
            save_inflight,
        )

        # Act
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc):
            save_inflight("SH600000", {"side": "buy", "volume": 100, "order_id": "3001", "plan_id": "p3", "ts": 1.0, "retries": 0})
            rec = load_inflight("SH600000")
            all_records = list_inflight()
            clear_inflight("SH600000")
        # Assert
        assert rec["order_id"] == "3001"
        assert all_records["SH600000"]["plan_id"] == "p3"
        assert load_inflight("SH600000") is None
        assert rc.client.expired_at["tdx:l2:inflight:sh600000"] == 4 * 3600

    def test_list_inflight_returns_all_symbols(self):
        # Arrange
        rc = _patched_redis()
        from backend.services.live_trading.services.tdx_l2_realtime import (
            list_inflight,
            save_inflight,
        )

        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc):
            save_inflight("SH600000", {"side": "buy", "volume": 100, "order_id": "a", "ts": 1.0, "retries": 0})
            save_inflight("SZ000001", {"side": "sell", "volume": 100, "order_id": "b", "ts": 1.0, "retries": 0})
            records = list_inflight()
        # Assert
        assert set(records.keys()) == {"SH600000", "SZ000001"}
        assert records["SZ000001"]["side"] == "sell"


# ============ 未成交重挂状态机 ============

class TestRetryInflightOrders:
    def _svc_and_orders(self, orders: list[dict]):
        from backend.services.live_trading.services.tdx_rolling_trade_service import (
            TdxRollingTradeService,
        )

        svc = MagicMock(spec=TdxRollingTradeService)
        svc.cancel_order = AsyncMock(return_value={"success": True, "message": "ok"})
        svc.pull_today_orders = AsyncMock(return_value=orders)
        return svc

    def _run_retry(self, rc, svc, *, inflight, signal_scores=None, pool=None, today_orders=None):
        from backend.services.live_trading.services.tdx_l2_realtime import (
            _retry_inflight_orders,
            save_inflight,
        )

        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc):
            for sym, rec in inflight.items():
                save_inflight(sym, rec)
        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc), \
             patch("backend.services.trade.services.tdx_l2_realtime.tdx_pusher") as pusher:
            pusher.place_order = AsyncMock(
                return_value={"orders": [{"status": "submitted", "order_id": "9001"}]}
            )
            return run_retry_sync(
                _retry_inflight_orders(
                    svc,
                    signal_scores=signal_scores or {},
                    pool_data=pool or {"SH600000": {"now": 10.5}},
                    fixed_buy_amount=10000.0,
                    cooldown_min=30,
                    today_orders=today_orders or [],
                    market_detail="上证 3512.34",
                    index_above=True,
                ),
                pusher,
            )

    def test_filled_clears_and_sets_cooldown(self):
        # Arrange
        rc = _patched_redis()
        svc = self._svc_and_orders(
            [{"order_id": "3001", "stock_code": "SH600000", "side": "buy", "status": "filled", "filled_volume": 100, "total_volume": 100}]
        )
        # Act
        stats, _ = self._run_retry(
            rc, svc,
            inflight={"SH600000": {"side": "buy", "volume": 100, "order_id": "3001", "plan_id": "p3", "ts": time.time() - 10, "retries": 0}},
            signal_scores={"SH600000": 90.0},
            today_orders=[{"order_id": "3001", "stock_code": "SH600000", "side": "buy", "status": "filled", "filled_volume": 100}],
        )
        # Assert: 已成交 → 清档 + 冷却（从成交起算防反复触发）
        assert stats["cleared"] == ["SH600000"]
        assert rc.get("tdx:l2:cooldown:sh600000") is not None
        assert "tdx:l2:inflight:sh600000" not in rc.client.store

    def test_working_young_waits(self):
        # Arrange
        rc = _patched_redis()
        svc = self._svc_and_orders(
            [{"order_id": "3001", "stock_code": "SH600000", "side": "buy", "status": "submitted"}]
        )
        # Act
        stats, _ = self._run_retry(
            rc, svc,
            inflight={"SH600000": {"side": "buy", "volume": 100, "order_id": "3001", "plan_id": "p3", "ts": time.time() - 10, "retries": 0}},
            signal_scores={"SH600000": 90.0},
            today_orders=[{"order_id": "3001", "stock_code": "SH600000", "side": "buy", "status": "submitted"}],
        )
        # Assert: 挂单未超时 → 等撮合, 不撤不重挂
        assert stats["waiting"] == 1
        svc.cancel_order.assert_not_awaited()

    def test_rejected_resubmits_with_new_plan_id(self):
        # Arrange
        rc = _patched_redis()
        svc = self._svc_and_orders(
            [{"order_id": "3001", "stock_code": "SH600000", "side": "buy", "status": "rejected"}]
        )
        # Act
        stats, pusher = self._run_retry(
            rc, svc,
            inflight={"SH600000": {"side": "buy", "volume": 900, "order_id": "3001", "plan_id": "p3", "ts": time.time() - 200, "retries": 0}},
            signal_scores={"SH600000": 90.0},
            today_orders=[{"order_id": "3001", "stock_code": "SH600000", "side": "buy", "status": "rejected"}],
        )
        # Assert: 废单 → 换新 plan_id 按最新实时价重挂, 并保存时点行情
        assert stats["resubmitted"] == ["SH600000"]
        args, kwargs = pusher.place_order.await_args
        assert kwargs["plan_id"].startswith("rolling_l2_SH600000_buy_")
        assert kwargs["plan_id"] != "p3"
        assert kwargs["price"] == 10.5
        rec = rc.get("tdx:l2:inflight:sh600000")
        assert rec["order_id"] == "9001"
        assert rec["retries"] == 1
        quote = rc.get("tdx:l2:order_quote:9001")
        assert quote["quote_price"] == 10.5
        assert quote["market_detail"] == "上证 3512.34"

    def test_signal_gone_cancels_and_clears(self):
        # Arrange
        rc = _patched_redis()
        svc = self._svc_and_orders(
            [{"order_id": "3001", "stock_code": "SH600000", "side": "buy", "status": "submitted"}]
        )
        # Act: 信号消失（不在分数池）→ 撤单收掉
        stats, _ = self._run_retry(
            rc, svc,
            inflight={"SH600000": {"side": "buy", "volume": 100, "order_id": "3001", "plan_id": "p3", "ts": time.time() - 10, "retries": 0}},
            signal_scores={},
            today_orders=[{"order_id": "3001", "stock_code": "SH600000", "side": "buy", "status": "submitted"}],
        )
        # Assert
        svc.cancel_order.assert_awaited_once_with("SH600000", "3001")
        assert stats["cancelled"] == ["SH600000"]
        assert "tdx:l2:inflight:sh600000" not in rc.client.store

    def test_retries_exhausted_gives_up(self):
        # Arrange
        rc = _patched_redis()
        svc = self._svc_and_orders([])
        # Act: 重挂次数用尽 → 放弃, 不再发单
        stats, pusher = self._run_retry(
            rc, svc,
            inflight={"SH600000": {"side": "buy", "volume": 900, "order_id": "3001", "plan_id": "p3", "ts": time.time() - 500, "retries": 10}},
            signal_scores={"SH600000": 90.0},
        )
        # Assert
        assert stats["given_up"] == ["SH600000"]
        pusher.place_order.assert_not_called()


def run_retry_sync(coro, pusher):
    """同步执行重挂协程并返回 (stats, pusher)。"""
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro), pusher


# ============ 主循环稳定性（桥断/engine断/采集陈旧都不停评分） ============

class TestLoopStability:
    def _seed_pool(self, rc, n: int = 6):
        from backend.services.live_trading.services.tdx_l2_realtime import _REALTIME_KEY

        for i in range(n):
            sym = f"SH60000{i}"
            rc.set(
                _REALTIME_KEY.format(symbol=sym),
                {
                    "symbol": sym,
                    "ts": "2026-08-25T10:00:00",
                    "factors": _factors(micro_vpin_vol_ratio=0.5, micro_open_gap=0.02),
                    "now": 10.0 + i,
                },
            )

    def _bootstrap(self, rc, svc):
        from datetime import datetime

        from backend.services.trade.services import tdx_l2_capture_task as cap
        from backend.services.live_trading.services.tdx_l2_realtime import save_l2_config

        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc):
            save_l2_config({
                "enabled": True, "interval_sec": 1,
                "buy_trigger": 65, "sell_trigger": 45, "cooldown_min": 0,
            })
        cap.l2_status.update({
            "running": True,
            "last_cycle_at": datetime.now().isoformat(),
        })
        svc.load_latest_scores = AsyncMock(return_value=(None, {f"SH60000{i}": 3.0 for i in range(6)}, "run-1"))
        svc.is_index_above_ma20 = AsyncMock(return_value=(True, "上证 3512.34 vs MA20 3400.00 (之上)"))
        svc.load_positions_from_tdx = AsyncMock(return_value=([], None))
        svc.pull_today_orders = AsyncMock(return_value=[])
        svc.cancel_order = AsyncMock(return_value={"success": True})
        svc.place_rolling_orders = AsyncMock(return_value=([], []))
        return cap.l2_status

    def _run_loop(self, rc, svc):
        import asyncio

        from backend.services.trade.services import tdx_l2_capture_task as cap
        from backend.services.live_trading.services.tdx_l2_realtime import (
            realtime_status,
            run_tdx_l2_realtime_task,
        )

        # 先完整跑通 1 个周期（评分+状态落盘），第 2 周期注入 CancelledError 退出
        cycles = {"n": 0}
        original_pos = svc.load_positions_from_tdx

        async def stop_after_cycles(*args, **kwargs):
            cycles["n"] += 1
            if cycles["n"] >= 2:
                raise asyncio.CancelledError()
            return await original_pos(*args, **kwargs)

        svc.load_positions_from_tdx = AsyncMock(side_effect=stop_after_cycles)

        with patch("backend.services.trade.services.tdx_l2_realtime.trade_redis", rc), \
             patch("backend.services.trade.services.tdx_l2_realtime.tdx_pusher") as pusher, \
             patch("backend.services.trade.services.tdx_l2_realtime.asyncio.sleep", AsyncMock()), \
             patch("backend.services.trade.services.tdx_rolling_trade_service.TdxRollingTradeService", return_value=svc), \
             patch("backend.services.trade.services.tdx_rolling_trade_service.load_rolling_config", return_value=("tdx", 10000.0, "tdx")), \
             patch("backend.services.trade.services.member_gate.is_paid_member", AsyncMock(return_value=True)), \
             patch("backend.services.trade.services.tdx_l2_capture_task.l2_status", cap.l2_status):
            with pytest.raises(asyncio.CancelledError):
                run_loop_sync(run_tdx_l2_realtime_task(interval_sec=1))
        return pusher, realtime_status

    def test_bridge_down_still_scores_and_skips_execution(self):
        # Arrange
        rc = _patched_redis()
        self._seed_pool(rc)
        svc = MagicMock()
        self._bootstrap(rc, svc)
        svc.load_positions_from_tdx = AsyncMock(side_effect=Exception("bridge unreachable"))
        # Act: 桥断 — 循环仍评分, 不触发执行
        pusher, status = self._run_loop(rc, svc)
        # Assert
        score_keys = [k for k in rc.client.store if k.startswith("tdx:l2:score:")]
        assert len(score_keys) >= 6
        assert status["bridge_ok"] is False
        assert "持仓查询失败" in (status["last_error"] or "")
        pusher.place_order.assert_not_called()

    def test_engine_fusion_failure_degrades_to_neutral(self):
        # Arrange
        rc = _patched_redis()
        self._seed_pool(rc)
        svc = MagicMock()
        self._bootstrap(rc, svc)
        svc.load_latest_scores = AsyncMock(side_effect=Exception("engine down"))
        # Act: engine 断 → 降级中性分, 评分照常落盘
        pusher, status = self._run_loop(rc, svc)
        # Assert
        score_keys = [k for k in rc.client.store if k.startswith("tdx:l2:score:")]
        assert len(score_keys) >= 6
        payload = rc.get("tdx:l2:score:SH600000")
        assert payload["fusion_score"] is None or payload["realtime_score"] >= 0
        assert payload["realtime_score"] < 65  # 中性分不致触发买入
        pusher.place_order.assert_not_called()

    def test_capture_stale_blocks_trigger_but_not_scoring(self):
        # Arrange
        rc = _patched_redis()
        self._seed_pool(rc)
        svc = MagicMock()
        self._bootstrap(rc, svc)
        # 采集链路陈旧（上一周期 1 小时前）
        from datetime import datetime, timedelta

        from backend.services.trade.services import tdx_l2_capture_task as cap

        cap.l2_status["last_cycle_at"] = (
            datetime.now() - timedelta(hours=1)
        ).isoformat()
        # Act
        pusher, status = self._run_loop(rc, svc)
        # Assert: 评分照常, 但禁止触发执行
        score_keys = [k for k in rc.client.store if k.startswith("tdx:l2:score:")]
        assert len(score_keys) >= 6
        assert status["capture_stale"] is True
        pusher.place_order.assert_not_called()


def run_loop_sync(coro):
    """同步执行主循环协程（以 CancelledError 退出）。"""
    import asyncio

    asyncio.get_event_loop().run_until_complete(coro)
