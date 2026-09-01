"""回放游标与日历测试。

验证游标推进逻辑：跳过无分区的日历日、正确计算 next_date。
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.services.simulation.replay.router import (
    _compute_next_date,
    _count_sessions,
)


class TestComputeNextDate:
    """游标推进：从 cursor 推算下一个交易日。"""

    def test_cursor_none_returns_start(self):
        """cursor=None → 返回 >= start_date 的第一个交易日。"""
        sessions = [20240301, 20240304, 20240305]
        start = date(2024, 3, 4)
        end = date(2024, 3, 6)

        result = _compute_next_date(None, start, end, sessions)
        assert result == date(2024, 3, 4)

    def test_cursor_advances_to_next(self):
        """cursor=D → 返回 D 之后的下一个交易日。"""
        sessions = [20240304, 20240305, 20240306]
        start = date(2024, 3, 4)
        end = date(2024, 3, 6)

        result = _compute_next_date(date(2024, 3, 4), start, end, sessions)
        assert result == date(2024, 3, 5)

    def test_cursor_at_last_day_returns_none(self):
        """cursor 已在最后一个交易日 → 返回 None（回放结束）。"""
        sessions = [20240304, 20240305]
        start = date(2024, 3, 4)
        end = date(2024, 3, 5)

        result = _compute_next_date(date(2024, 3, 5), start, end, sessions)
        assert result is None

    def test_next_beyond_end_returns_none(self):
        """下一个交易日超过 end_date → 返回 None。"""
        sessions = [20240304, 20240305, 20240306]
        start = date(2024, 3, 4)
        end = date(2024, 3, 5)

        result = _compute_next_date(date(2024, 3, 5), start, end, sessions)
        assert result is None

    def test_skips_weekends(self):
        """sessions 列表不含周末，游标自动跳过。"""
        # 2024-03-04 (Mon), 2024-03-05 (Tue), 2024-03-06 (Wed)
        # 周六日 03-02, 03-03 不在 sessions 中
        sessions = [20240304, 20240305, 20240306]
        start = date(2024, 3, 4)
        end = date(2024, 3, 6)

        result = _compute_next_date(None, start, end, sessions)
        assert result == date(2024, 3, 4)  # 直接跳到周一

    def test_start_before_first_session(self):
        """start_date 在 sessions[0] 之前 → 返回 sessions[0]。"""
        sessions = [20240304, 20240305]
        start = date(2024, 3, 1)  # 周五，但在 sessions 之前
        end = date(2024, 3, 5)

        result = _compute_next_date(None, start, end, sessions)
        assert result == date(2024, 3, 4)

    def test_empty_sessions_returns_none(self):
        """sessions 为空 → 返回 None。"""
        result = _compute_next_date(None, date(2024, 3, 4), date(2024, 3, 5), [])
        assert result is None


class TestCountSessions:
    """交易日计数。"""

    def test_counts_within_range(self):
        sessions = [20240301, 20240304, 20240305, 20240306]
        assert _count_sessions(date(2024, 3, 4), date(2024, 3, 5), sessions) == 2

    def test_includes_boundaries(self):
        sessions = [20240304, 20240305]
        assert _count_sessions(date(2024, 3, 4), date(2024, 3, 5), sessions) == 2

    def test_no_sessions_in_range(self):
        sessions = [20240301]
        assert _count_sessions(date(2024, 3, 4), date(2024, 3, 5), sessions) == 0

    def test_all_sessions_in_range(self):
        sessions = [20240304, 20240305, 20240306]
        assert _count_sessions(date(2024, 3, 1), date(2024, 3, 31), sessions) == 3
