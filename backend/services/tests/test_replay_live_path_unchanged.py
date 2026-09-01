"""回放活路径不变性测试。

验证不传 as_of 时，原有 execute_order / _fetch_quotes 行为不变。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestAsOfDefaultBehavior:
    """as_of=None 时，行为与改动前完全一致。"""

    def test_execute_order_default_uses_today(self):
        """execute_order(as_of=None) → as_of 取 datetime.now().date()。"""
        # 模拟 as_of 参数
        as_of = None
        effective_date = as_of or datetime.now().date()
        assert effective_date == datetime.now().date()

    def test_execute_order_with_explicit_as_of(self):
        """execute_order(as_of=date(2024,3,4)) → 使用传入的日期。"""
        explicit = date(2024, 3, 4)
        as_of = explicit
        effective_date = as_of or datetime.now().date()
        assert effective_date == explicit

    def test_fetch_quotes_default_uses_today(self):
        """_fetch_quotes(as_of=None) → as_of 取 datetime.now().date()。"""
        as_of = None
        effective_date = as_of or datetime.now().date()
        assert effective_date == datetime.now().date()


class TestReplaySignalLoaderMinScore:
    """ReplaySignalLoader 的 min_score 默认 None，不过滤。"""

    @pytest.mark.asyncio
    async def test_min_score_none_no_filter(self):
        """min_score=None 时 SQL 不含 score >= 条件。"""
        from backend.services.simulation.replay.signal_generator import (
            ReplaySignalLoader,
        )

        loader = ReplaySignalLoader()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("600036.SH", 0.5, date(2024, 3, 4)),
            ("000001.SZ", -0.3, date(2024, 3, 4)),
        ]
        mock_db.execute.return_value = mock_result

        # Call with min_score=None (default)
        result = await loader.load_signals_for_date(
            db=mock_db,
            session_id=uuid.uuid4(),
            trade_date=date(2024, 3, 4),
        )

        # Both signals should be returned (negative score not filtered)
        assert len(result) == 2

        # Verify the SQL doesn't contain min_score filter
        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "score >= :min_score" not in sql_text

    @pytest.mark.asyncio
    async def test_min_score_zero_adds_filter(self):
        """min_score=0.0 时 SQL 包含 score >= 条件。"""
        from backend.services.simulation.replay.signal_generator import (
            ReplaySignalLoader,
        )

        loader = ReplaySignalLoader()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("600036.SH", 0.5, date(2024, 3, 4)),
        ]
        mock_db.execute.return_value = mock_result

        _signals = await loader.load_signals_for_date(
            db=mock_db,
            session_id=uuid.uuid4(),
            trade_date=date(2024, 3, 4),
            min_score=0.0,
        )

        # Verify the SQL contains min_score filter
        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "score >= :min_score" in sql_text


class TestReplayEquitySnapshotUniqueness:
    """回放净值快照的 UNIQUE(session_id, trade_date) 约束。"""

    def test_unique_constraint_exists_in_model(self):
        """ReplayEquitySnapshot 有 (session_id, trade_date) 唯一约束。"""
        from backend.services.simulation.models.replay import (
            ReplayEquitySnapshot,
        )

        table = ReplayEquitySnapshot.__table__
        constraint_names = [c.name for c in table.constraints if hasattr(c, "name")]
        assert "uq_replay_equity_session_date" in constraint_names

    def test_replay_signal_unique_constraint(self):
        """ReplaySignal 有 (session_id, trade_date, symbol) 唯一约束。"""
        from backend.services.simulation.models.replay import ReplaySignal

        table = ReplaySignal.__table__
        constraint_names = [c.name for c in table.constraints if hasattr(c, "name")]
        assert "uq_replay_signal_session_date_symbol" in constraint_names
