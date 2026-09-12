"""自选持仓同步（watchlist sync-positions）测试。"""

import sys
import types

import pytest

auth_module = types.ModuleType("backend.services.api.user_app.middleware.auth")
auth_module.get_current_user = lambda: {}
sys.modules.setdefault("backend.services.api.user_app.middleware.auth", auth_module)

from backend.services.api.routers import research  # noqa: E402
from backend.services.api.routers import research_service  # noqa: E402

import httpx  # noqa: E402


# ---------------- 路由顺序 ----------------


def test_sync_positions_route_before_symbol_route():
    paths = [r.path for r in research.router.routes if getattr(r, "path", None)]
    sync = next(i for i, p in enumerate(paths) if p.endswith("/watchlist/sync-positions"))
    symbol = next(i for i, p in enumerate(paths) if p.endswith("/watchlist/{symbol}"))
    assert sync < symbol


# ---------------- _fetch_simulation_positions ----------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload=None, exc=None, *args, **kwargs):
        self._payload = payload
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        if self._exc:
            raise self._exc
        return _FakeResponse(self._payload)


@pytest.mark.asyncio
async def test_fetch_positions_normalizes_prefix_and_filters_markets(monkeypatch):
    payload = {
        "success": True,
        "data": {
            "positions": {
                "SH600036::long": {"volume": 100, "cost": 10.0},
                "600000.SH::short": {"volume": 200, "cost": 8.0},
                "99999.HK::long": {"volume": 500, "cost": 50.0},
            }
        },
    }
    monkeypatch.setattr(
        research_service.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(payload=payload),
    )

    positions = await research_service._fetch_simulation_positions("Bearer abc", "1", "default")
    # 去侧标 + to_prefix 归一，港股被白名单过滤
    assert positions == ["SH600000", "SH600036"]


@pytest.mark.asyncio
async def test_fetch_positions_fail_soft_on_http_error(monkeypatch):
    monkeypatch.setattr(
        research_service.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(exc=httpx.ConnectError("boom")),
    )
    result = await research_service._fetch_simulation_positions("Bearer x", "1", "default")
    assert result == []


# ---------------- sync_watchlist_positions_service ----------------


@pytest.mark.asyncio
async def test_sync_positions_service_upserts_with_names(monkeypatch):
    called = []

    async def _fake_fetch(_auth, _uid, _tid):
        return ["SH600000", "SH600036"]

    monkeypatch.setattr(research_service, "_fetch_simulation_positions", _fake_fetch)
    monkeypatch.setattr(
        research_service,
        "_get_quantdb_stock_names",
        lambda: {"600036.SH": "招商银行", "600000.SH": "浦发银行"},
    )

    async def _fake_upsert(tid, uid, symbol, stock_name):
        called.append((tid, uid, symbol, stock_name))

    monkeypatch.setattr(research_service, "_upsert_watchlist_position", _fake_upsert)

    result = await research_service.sync_watchlist_positions_service("default", "u1", "Bearer x")

    assert result == {"code": 200, "data": {"positions": ["SH600000", "SH600036"]}}
    assert called == [
        ("default", "u1", "SH600000", "浦发银行"),
        ("default", "u1", "SH600036", "招商银行"),
    ]


@pytest.mark.asyncio
async def test_sync_positions_service_fails_soft_with_no_upserts(monkeypatch):
    called = []

    async def _fake_fetch(_auth, _uid, _tid):
        # 资产不可达 / 模拟账号未初始化时 _fetch 返回空列表
        return []

    monkeypatch.setattr(research_service, "_fetch_simulation_positions", _fake_fetch)

    async def _fake_upsert(tid, uid, symbol, stock_name):
        called.append((tid, uid, symbol, stock_name))

    monkeypatch.setattr(research_service, "_upsert_watchlist_position", _fake_upsert)

    result = await research_service.sync_watchlist_positions_service("default", "u1", "Bearer x")

    assert result == {"code": 200, "data": {"positions": []}}
    assert called == []
