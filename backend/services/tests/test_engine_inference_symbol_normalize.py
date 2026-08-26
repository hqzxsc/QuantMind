"""engine_signal_scores.symbol 归一化回归测试。

修复背景: QuantDB 直读模型(quantdb_factors)的 symbol 是前缀格式
(SH600097/SZ000401), 而 engine_signal_scores.symbol 全历史约定为纯数字
(600519)。前缀 symbol 落库后, 个股终端/选股/position_score 等按纯数字
或后缀匹配的消费方全部失配, 表现为「有推理数据但列表不显示」。
修复: _persist_and_publish 落库前统一归一为纯数字, 原始格式仅保留
给行情 Redis 查价(需要市场前缀定位 stock:{code}.SH)。
"""

from __future__ import annotations

import pytest

from backend.services.engine.inference.script_runner import InferenceScriptRunner

_runner = InferenceScriptRunner()


class TestNormalizeSignalSymbols:
    """_normalize_signal_symbols: (raw, plain) 成对返回, plain 恒为纯数字。"""

    def test_prefix_symbols_normalized_to_plain(self):
        # Arrange: QuantDB 直读模型输出前缀格式
        signals = [
            {"symbol": "SH600097", "score": 0.8},
            {"symbol": "SZ000401", "score": 0.7},
            {"symbol": "BJ430047", "score": 0.6},
        ]
        signals_sorted = sorted(signals, key=lambda x: x["score"], reverse=True)

        # Act
        raw, plain = _runner._normalize_signal_symbols(signals_sorted)

        # Assert: raw 保留原始前缀(供行情查价), plain 归一为纯数字
        assert raw == ["SH600097", "SZ000401", "BJ430047"]
        assert plain == ["600097", "000401", "430047"]

    def test_suffix_and_plain_symbols_untouched(self):
        # Arrange: 旧管线输出后缀/纯数字, 归一后不变
        signals = [
            {"symbol": "600519.SH", "score": 0.9},
            {"symbol": "000001", "score": 0.5},
        ]

        # Act
        raw, plain = _runner._normalize_signal_symbols(signals)

        # Assert
        assert raw == ["600519.SH", "000001"]
        assert plain == ["600519", "000001"]

    def test_empty_signals(self):
        assert _runner._normalize_signal_symbols([]) == ([], [])

    def test_mixed_formats_in_one_run(self):
        # Arrange: 同一 run 混用多种格式(前缀/后缀/纯数字)
        signals = [
            {"symbol": "SH600000", "score": 0.1},
            {"symbol": "600036.SH", "score": 0.2},
            {"symbol": "300750", "score": 0.3},
        ]

        # Act
        _, plain = _runner._normalize_signal_symbols(signals)

        # Assert: 全部归一为纯数字, 且与模型无关的格式被规范
        assert sorted(plain) == ["300750", "600000", "600036"]


class TestNormalizeCode:
    """_normalize_code 纯数字提取(覆盖前缀/后缀/带空格/小写)。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("SH600097", "600097"),
            ("sz000401", "000401"),  # 小写前缀
            ("600519.SH", "600519"),
            ("600519", "600519"),
            ("BJ430047", "430047"),
            (" 600519 ", "600519"),  # 首尾空格
            ("688001.SH", "688001"),
        ],
    )
    def test_normalize_variants(self, raw: str, expected: str):
        assert InferenceScriptRunner._normalize_code(raw) == expected
