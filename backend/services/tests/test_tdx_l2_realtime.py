"""TDX L2 实时推理单元测试：截面标准化 / 信号合成 / 触发 / 冷却。

纯函数级测试（Redis 用 mock，不触真实库）：
- _z_score: 截面标准化与极端值 clip
- compute_signal_scores: ICIR 加权 + sigmoid 单调性
- compute_realtime_score: 日频分 × 权重 + 信号分 × 权重
- load/save_l2_config: Redis 读写与非法值保护
- is_cooldown / set_cooldown: 冷却窗口
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from backend.services.trade.redis_client import RedisClient
from backend.services.trade.services.tdx_l2_capture_task import FACTOR_ICIR
from backend.services.trade.services.tdx_l2_realtime import (
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

    def set(self, key: str, value, **kwargs) -> None:
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)


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
