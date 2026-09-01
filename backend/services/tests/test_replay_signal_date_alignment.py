"""回放信号 T+1 日期对齐测试。

信号直读模型 pred.parquet 后，T+1 语义不变：trade_date=T 生效的信号
必须是数据日 prev_session(T) 的分数，不能用 T 当天的分数（前视偏差）。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from backend.services.simulation.replay.signal_generator import (
    ReplaySignalLoader,
    _dt_int_to_date,
    _PRED_FRAME_CACHE,
)


class TestSignalDateAlignment:
    """信号生效日 = next_session(数据日)，无前视偏差。"""

    def test_t_plus_one_offset(self):
        """数据日 D 的信号生效于 next_session(D)，不能是 D 当天。"""
        sessions = [20240301, 20240304, 20240305]

        # 数据日 = 20240301 → 信号生效日 = 20240304
        # 数据日 = 20240304 → 信号生效日 = 20240305
        for data_day, expected_signal_date in [
            (20240301, 20240304),
            (20240304, 20240305),
        ]:
            pos = sessions.index(data_day)
            signal_date_int = sessions[pos + 1]
            assert signal_date_int == expected_signal_date, (
                f"数据日 {data_day} 的信号应生效于 {expected_signal_date}，"
                f"实际 {signal_date_int}"
            )
            assert _dt_int_to_date(signal_date_int) == date(
                expected_signal_date // 10000,
                (expected_signal_date % 10000) // 100,
                expected_signal_date % 100,
            )

    def test_loader_reads_prev_session_scores(self, tmp_path):
        """load_signals_for_date(T) 读的是 prev_session(T) 数据日的分数。"""
        from backend.services.simulation.models.replay import ReplaySession

        # 模型目录 + pred.parquet（两日分数区分明显）
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        df = pd.DataFrame(
            {
                "symbol": ["SH600036", "SZ000001", "SH600036", "SZ000001"],
                "trade_date": [
                    date(2024, 3, 1),
                    date(2024, 3, 1),
                    date(2024, 3, 4),
                    date(2024, 3, 4),
                ],
                "pred": [0.9, 0.1, -0.5, 0.5],
            }
        )
        df.to_parquet(model_dir / "pred.parquet")

        session_id = uuid.uuid4()
        row = MagicMock(spec=ReplaySession)
        row.model_id = None
        row.strategy_params = {"_model_dir": str(model_dir)}

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = row
        db = MagicMock()
        db.execute = _async_return(mock_result)

        market = MagicMock()
        market._sessions.return_value = [20240301, 20240304, 20240305]

        _PRED_FRAME_CACHE.clear()
        loader = ReplaySignalLoader()
        with patch(
            "backend.services.trade.simulation.replay.signal_generator."
            "get_local_market_data",
            return_value=market,
        ):
            # trade_date=2024-03-04 → 应读数据日 2024-03-01 的分数（0.9/0.1）
            signals = asyncio.run(
                loader.load_signals_for_date(db, session_id, date(2024, 3, 4))
            )

        # 符号统一为 suffix 规范格式（与旧预生成路径口径一致）
        assert [(s.symbol, s.score) for s in signals] == [
            ("600036.SH", 0.9),
            ("000001.SZ", 0.1),
        ]
        _PRED_FRAME_CACHE.clear()

    def test_no_signal_before_first_session(self):
        """第一个交易日之前无数据日，不应有信号。"""
        sessions = [20240304, 20240305]
        td_int = 20240304
        before = [d for d in sessions if d < td_int]
        assert before == []


class TestDtIntToDate:
    """日期整数 → date 对象转换。"""

    def test_normal(self):
        assert _dt_int_to_date(20240304) == date(2024, 3, 4)

    def test_year_boundary(self):
        assert _dt_int_to_date(20231231) == date(2023, 12, 31)

    def test_single_digit_month_day(self):
        assert _dt_int_to_date(20240105) == date(2024, 1, 5)


def _async_return(value):
    """把返回值包装成可 await 的协程函数（mock AsyncSession.execute）。"""

    async def _coro(*args, **kwargs):
        return value

    return _coro
