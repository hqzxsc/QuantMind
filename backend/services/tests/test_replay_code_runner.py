"""回放 code 模式执行器测试（DB-free：inline list 池 + stub 行情）."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from backend.services.simulation.replay import code_runner


def _bar(price: float) -> SimpleNamespace:
    return SimpleNamespace(
        open=price, high=price, low=price, close=price, volume=10000.0,
        pre_close=price, limit_up=price * 1.1, limit_down=price * 0.9,
        suspended=False,
    )


class _StubMD:
    def __init__(self) -> None:
        self.days = [date(2024, 1, d) for d in (2, 3, 4, 5, 8)]

    def _sessions(self) -> list[int]:
        return [d.year * 10000 + d.month * 100 + d.day for d in self.days]

    def load_date(self, trade_date, symbols=None):
        bars = {"600036.SH": _bar(10.0), "000001.SZ": _bar(20.0)}
        if symbols is None:
            return dict(bars)
        wanted = set(symbols)
        return {k: v for k, v in bars.items() if k in wanted}


_STRATEGY = """
def setup(ctx):
    ctx.universe = ['SH600036', 'SZ000001']
    ctx.start = '2024-01-02'
    ctx.end = '2024-01-08'
    ctx.cash = 1000000

def on_universe(ctx, date, snapshot):
    ctx.buy('SH600036', weight=0.5, reason='test')
"""


def _account(cash: float = 1_000_000.0) -> dict:
    return {"cash": cash, "positions": {}, "total_asset": cash}


def test_prepare_applies_session_pool():
    sid = uuid.uuid4()
    try:
        compiled = code_runner.prepare_session(
            sid, _STRATEGY,
            market_data=_StubMD(),
            start=date(2024, 1, 2), end=date(2024, 1, 8), cash=1_000_000.0,
            pool_ref="list:SH600036",
            tenant_id="default", user_id="0",
        )
    finally:
        pass
    try:
        assert compiled.symbols == ["SH600036"]
        assert compiled.pool_id == "inline"
    finally:
        code_runner.drop_session(sid)


def test_prepare_code_pool_wins_over_session_pool():
    code = _STRATEGY.replace(
        "ctx.cash = 1000000",
        "ctx.cash = 1000000\n    ctx.stock_pool = 'list:SZ000001'",
    )
    sid = uuid.uuid4()
    try:
        compiled = code_runner.prepare_session(
            sid, code,
            market_data=_StubMD(),
            start=date(2024, 1, 2), end=date(2024, 1, 8), cash=1_000_000.0,
            pool_ref="list:SH600036",
            tenant_id="default", user_id="0",
        )
        assert compiled.symbols == ["SZ000001"]
    finally:
        code_runner.drop_session(sid)


def test_prepare_rejects_unsafe_code():
    with pytest.raises(ValueError, match="安全检查"):
        code_runner.prepare_session(
            uuid.uuid4(), "import os\nos.system('x')",
            market_data=_StubMD(),
            start=date(2024, 1, 2), end=date(2024, 1, 8), cash=1_000_000.0,
        )


def test_run_code_day_emits_pool_scoped_orders():
    sid = uuid.uuid4()
    md = _StubMD()
    try:
        code_runner.prepare_session(
            sid, _STRATEGY,
            market_data=md,
            start=date(2024, 1, 2), end=date(2024, 1, 8), cash=1_000_000.0,
            pool_ref="list:SH600036",
            tenant_id="default", user_id="0",
        )
        bars = md.load_date(date(2024, 1, 3), None)
        orders, info = code_runner.run_code_day(
            sid, date(2024, 1, 3), _account(), bars, md
        )
    finally:
        code_runner.drop_session(sid)
    assert len(orders) == 1
    o = orders[0]
    assert o.symbol == "600036.SH"
    assert o.side == "BUY"
    # 100 万 * 0.5 / 10 元 = 50000 股（整手）
    assert o.quantity == 50000
    assert o.price == 10.0
    assert info["signal_count"] == 1
    assert info["pool_id"] == "inline"


def test_intents_outside_pool_dropped():
    from backend.services.engine.strategy_lab.sdk.context import Context

    sid = uuid.uuid4()
    md = _StubMD()
    code = _STRATEGY.replace(
        "ctx.buy('SH600036', weight=0.5, reason='test')",
        "ctx.buy('SZ000001', weight=0.5, reason='outside')",
    )
    try:
        code_runner.prepare_session(
            sid, code,
            market_data=md,
            start=date(2024, 1, 2), end=date(2024, 1, 8), cash=1_000_000.0,
            pool_ref="list:SH600036",
            tenant_id="default", user_id="0",
        )
        bars = md.load_date(date(2024, 1, 3), None)
        orders, _ = code_runner.run_code_day(
            sid, date(2024, 1, 3), _account(), bars, md
        )
    finally:
        code_runner.drop_session(sid)
    # 池外手写单被安全网丢弃
    assert orders == []
    assert Context is not None
