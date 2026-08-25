"""ReplaySignalGenerator 信号预生成回归测试。

修复背景: 历史年份 parquet 特征列可能是 nullable Int64 掩码数组
（如 2025 年 model_features_2025.parquet 的 micro_jump_flag），
metadata fill_values 里是浮点补值（如 0.606385350227356），
直接 fillna(浮点) 抛 TypeError: Invalid value '...' for dtype 'Int64'，
表现为时光回放「信号生成失败」。
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.services.trade.simulation.replay.signal_generator import (
    ReplaySignalGenerator,
    _coerce_feature_columns,
)


def _gen() -> ReplaySignalGenerator:
    return ReplaySignalGenerator(model_id="unused")


class TestCoerceFeatureColumns:
    """特征列统一为 float64，兼容 Int64 掩码数组与缺失列。"""

    def test_int64_masked_column_fillna_float_no_error(self):
        # Arrange: 2025 parquet 的 micro_jump_flag 是 Int64 且含 NA
        df = pd.DataFrame(
            {
                "symbol": ["600206"],
                "micro_jump_flag": pd.array([pd.NA], dtype="Int64"),
                "vol": [1.5],
            }
        )

        # Act
        out = _coerce_feature_columns(df, ["micro_jump_flag", "vol"])

        # Assert: 已转为 float64，NA 保留，fillna(浮点) 不再抛 TypeError
        assert str(out["micro_jump_flag"].dtype) == "float64"
        assert pd.isna(out["micro_jump_flag"][0])
        out["micro_jump_flag"] = out["micro_jump_flag"].fillna(0.606385350227356)
        assert out["micro_jump_flag"][0] == 0.606385350227356

    def test_missing_feature_column_defaults_to_zero(self):
        # Arrange: 2026 parquet 里没有 micro_jump_flag 列
        df = pd.DataFrame({"symbol": ["600206"], "vol": [1.5]})

        # Act
        out = _coerce_feature_columns(df, ["micro_jump_flag", "vol"])

        # Assert: 缺失列补 0.0，且为 float64
        assert "micro_jump_flag" in out.columns
        assert out["micro_jump_flag"].tolist() == [0.0]
        assert str(out["micro_jump_flag"].dtype) == "float64"

    def test_int64_feature_column_fillna_zero_no_error(self):
        # Arrange: 特征列本身是 Int64（fillna(0.0) 同样会抛 TypeError）
        df = pd.DataFrame(
            {
                "symbol": ["600206", "600036"],
                "gtja_083": pd.array([pd.NA, 5], dtype="Int64"),
            }
        )

        # Act
        out = _coerce_feature_columns(df, ["gtja_083"])

        # Assert
        X = out[["gtja_083"]].fillna(0.0).values.astype("float32")
        assert list(X[:, 0]) == [0.0, 5.0]

    def test_numeric_object_column_is_coerced(self):
        # Arrange: 训练快照个别年份可能是 object 数值列
        df = pd.DataFrame({"symbol": ["600206", "600036"], "rsi_14": ["12.5", None]})

        # Act
        out = _coerce_feature_columns(df, ["rsi_14"])

        # Assert
        assert str(out["rsi_14"].dtype) == "float64"
        assert out["rsi_14"].tolist()[0] == 12.5
        assert pd.isna(out["rsi_14"].tolist()[1])
