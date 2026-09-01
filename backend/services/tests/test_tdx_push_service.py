"""tdx_push_service._sync_orders_to_pg 测试: 桥当日委托 → orders 表 UPSERT。

修复背景: 原实现只 INSERT + 按 exchange_order_id 去重跳过, 已存在的订单
状态永不更新, 全部停留在 SUBMITTED 后被超时扫描器误标 EXPIRED,
表现为交易记录"全部过期、成交为 0"。现在已存在行用桥最新状态/成交回报刷新。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.sql.selectable import Select

from backend.services.live_trading.services.tdx_push_service import (
    TdxPushService,
    estimate_order_fee,
)


class _RowsResult:
    """模拟 execute 返回: fetchall() 给 SELECT, scalar() 给 RETURNING。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    """迷你内存库: 记住 existing 映射, UPDATE/INSERT 记录参数。"""

    def __init__(self, existing=None):
        # {exchange_order_id: order_id}
        self.existing = dict(existing or {})
        self.updates: list[dict] = []
        self.inserts: list[dict] = []

    async def execute(self, stmt, params=None):
        if isinstance(stmt, Select):
            return _RowsResult(
                [(eid, oid) for eid, oid in self.existing.items()]
            )
        if not isinstance(stmt, TextClause):
            return _RowsResult([])
        sql = str(stmt).strip().upper()
        if sql.startswith("UPDATE"):
            self.updates.append(params or {})
            return _RowsResult([])
        if sql.startswith("INSERT"):
            params = params or {}
            new_id = str(uuid4())
            self.inserts.append(params)
            self.existing[params["exchange_order_id"]] = new_id
            return _RowsResult([new_id])
        return _RowsResult([])


def _svc_with(pull_orders_result):
    svc = TdxPushService()

    async def _pull_orders(stock_code: str = ""):
        return list(pull_orders_result)

    svc.pull_orders = _pull_orders
    return svc


_BRIDGE_ORDER = {
    "order_id": "160356",
    "stock_code": "SH600206",
    "time": "093000",
    "side": "buy",
    "status": "filled",
    "order_price": 50.78,
    "filled_price": 50.90,
    "filled_volume": 2400,
    "total_volume": 2400,
}


async def _run_sync(db, orders, user_id="1001"):
    svc = _svc_with(orders)
    await svc._sync_orders_to_pg(
        db=db, tenant_id="default", user_id=user_id, now=datetime(2026, 8, 25, 10, 0, 0)
    )
    return svc


class TestEstimateOrderFee:
    """费用估算: 佣金(万2.5 最低5元, 双边) + 印花税(万5, 仅卖出) + 过户费(万0.1, 双边)。"""

    def test_buy_fee_commission_plus_transfer(self):
        assert estimate_order_fee(100000, "buy") == 26.0  # 25 佣金 + 1 过户

    def test_sell_fee_adds_stamp_tax(self):
        assert estimate_order_fee(100000, "sell") == 76.0  # + 50 印花税

    def test_min_commission_applies(self):
        assert estimate_order_fee(10000, "buy") == 5.1  # 佣金按最低 5 元

    def test_zero_filled_value_no_fee(self):
        assert estimate_order_fee(0, "buy") == 0.0
        assert estimate_order_fee(0, "sell") == 0.0


@pytest.mark.asyncio
async def test_sync_inserts_new_bridge_order():
    db = _FakeDb()
    await _run_sync(db, [_BRIDGE_ORDER])

    assert len(db.inserts) == 1
    row = db.inserts[0]
    assert row["exchange_order_id"] == "160356"
    assert row["status"] == "filled"
    assert row["filled_quantity"] == 2400
    assert row["average_price"] == 50.90
    assert row["filled_value"] == round(2400 * 50.90, 2)
    # 122160 × 0.00025 = 30.54 佣金 + 1.2216 过户费 = 31.76
    assert row["commission"] == 31.76
    assert row["trading_mode"] == "REAL"
    assert row["submitted_at"].hour == 9 and row["submitted_at"].minute == 30
    assert row["filled_at"] == row["submitted_at"]


@pytest.mark.asyncio
async def test_sync_updates_existing_order_with_latest_fill():
    db = _FakeDb(existing={"160356": "pg-order-1"})
    # 桥: 已从 pending 变成 filled
    order = {**_BRIDGE_ORDER}
    await _run_sync(db, [order])

    assert db.inserts == []
    assert len(db.updates) == 1
    upd = db.updates[0]
    assert upd["order_id"] == "pg-order-1"
    assert upd["status"] == "filled"
    assert upd["filled_quantity"] == 2400
    assert upd["average_price"] == 50.90
    assert upd["filled_value"] == round(2400 * 50.90, 2)
    assert upd["commission"] == 31.76
    assert upd["filled_at"] is not None


@pytest.mark.asyncio
async def test_sync_maps_partial_fill_rejected_cancelled():
    db = _FakeDb()
    orders = [
        {
            "order_id": "1001",
            "stock_code": "SH688999",
            "time": "123456",
            "side": "sell",
            "status": "partial_fill",
            "order_price": 10.0,
            "filled_price": 10.05,
            "filled_volume": 300,
            "total_volume": 500,
        },
        {
            "order_id": "1002",
            "stock_code": "SH600000",
            "time": "130000",
            "side": "buy",
            "status": "rejected",
            "order_price": 11.0,
            "filled_price": 0,
            "filled_volume": 0,
            "total_volume": 100,
        },
        {
            "order_id": "1003",
            "stock_code": "SH601000",
            "time": "131500",
            "side": "buy",
            "status": "partial_cancelled",
            "order_price": 12.0,
            "filled_price": 12.1,
            "filled_volume": 200,
            "total_volume": 500,
        },
    ]
    await _run_sync(db, orders)

    by_id = {r["exchange_order_id"]: r for r in db.inserts}
    assert by_id["1001"]["status"] == "partially_filled"
    # 卖出 300×10.05=3015: 佣金最低5 + 印花1.5075 + 过户0.0302 = 6.54
    assert by_id["1001"]["commission"] == 6.54
    assert by_id["1002"]["status"] == "rejected"
    assert by_id["1002"]["commission"] == 0.0  # 未成交无费用
    # 部分撤单: 终态为撤单, 但保留已成交数量/均价
    assert by_id["1003"]["status"] == "cancelled"
    assert by_id["1003"]["filled_quantity"] == 200
    assert by_id["1003"]["average_price"] == 12.1


@pytest.mark.asyncio
async def test_sync_skips_order_without_exchange_id_and_symbol():
    db = _FakeDb()
    orders = [
        {"order_id": "", "stock_code": "SH600000", "status": "filled"},
        {"order_id": "9999", "stock_code": "", "status": "filled"},
    ]
    await _run_sync(db, orders)

    assert db.inserts == []
    assert db.updates == []
