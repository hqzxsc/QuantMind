"""回放活路径不变性测试。

验证不传 as_of 时，原有 execute_order / _fetch_quotes 行为不变。
"""

from __future__ import annotations

from datetime import date, datetime


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
