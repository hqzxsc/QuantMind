"""TdxRollingTradeService 单元测试。

- compute_rolling_signals 纯函数级测试：买卖信号计算、大盘 MA20 过滤、持仓滚动。
- 执行模式（off/tdx/paper）配置读写与兼容。
- 模拟盘直接下单：持仓来源、会员门控、paper 成交路径。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.trade.services.tdx_rolling_trade_service import (
    DEFAULT_EXECUTE_MODE,
    DEFAULT_SCORE_THRESHOLD,
    TdxRollingTradeService,
    load_rolling_config,
    save_rolling_config,
)


@pytest.fixture
def svc() -> TdxRollingTradeService:
    return TdxRollingTradeService()


def _pos(symbol, volume=1000, available_volume=None, name=""):
    return {
        "symbol": symbol,
        "name": name or symbol,
        "volume": volume,
        "available_volume": available_volume if available_volume is not None else volume,
        "cost_price": 10.0,
        "market_value": 10000.0,
    }


class TestComputeRollingSignals:
    def test_buys_stocks_above_threshold_when_index_above_ma20(self, svc):
        # Arrange: 000001.SZ 11.25 元/股，1 万元可买一手
        score_map = {"000001.SZ": 2.8, "000002.SZ": 1.5}
        # Act
        result = svc.compute_rolling_signals(
            score_map=score_map, positions=[], index_above_ma20=True
        )
        # Assert
        assert [b["symbol"] for b in result["buys"]] == ["000001.SZ"]
        assert result["sells"] == []

    def test_no_buys_when_index_below_ma20(self, svc):
        # Arrange
        score_map = {"600519.SH": 2.8}
        # Act
        result = svc.compute_rolling_signals(
            score_map=score_map, positions=[], index_above_ma20=False
        )
        # Assert
        assert result["buys"] == []

    def test_sells_held_stock_dropping_below_threshold(self, svc):
        # Arrange
        score_map = {"600519.SH": 1.9}
        positions = [_pos("600519.SH")]
        # Act
        result = svc.compute_rolling_signals(
            score_map=score_map, positions=positions, index_above_ma20=True
        )
        # Assert
        assert [s["symbol"] for s in result["sells"]] == ["600519.SH"]

    def test_sells_held_stock_missing_from_new_run(self, svc):
        # Arrange
        score_map = {}  # 最新推理无该股
        positions = [_pos("600519.SH")]
        # Act
        result = svc.compute_rolling_signals(
            score_map=score_map, positions=positions, index_above_ma20=True
        )
        # Assert
        assert [s["symbol"] for s in result["sells"]] == ["600519.SH"]
        assert "最新推理已无该股" in result["sells"][0]["reason"]

    def test_holds_stock_still_above_threshold(self, svc):
        # Arrange
        score_map = {"600519.SH": 2.5}
        positions = [_pos("600519.SH")]
        # Act
        result = svc.compute_rolling_signals(
            score_map=score_map, positions=positions, index_above_ma20=True
        )
        # Assert
        assert result["sells"] == []
        assert result["buys"] == []
        assert [h["symbol"] for h in result["holds"]] == ["600519.SH"]

    def test_sells_still_happen_when_index_below_ma20(self, svc):
        # Arrange
        score_map = {"600519.SH": 1.5}
        positions = [_pos("600519.SH")]
        # Act
        result = svc.compute_rolling_signals(
            score_map=score_map, positions=positions, index_above_ma20=False
        )
        # Assert
        assert [s["symbol"] for s in result["sells"]] == ["600519.SH"]

    def test_buys_ranked_by_score_desc(self, svc):
        # Arrange: 三只低价股均可买一手
        score_map = {"000001.SZ": 2.3, "000002.SZ": 3.0, "000063.SZ": 2.7}
        # Act
        result = svc.compute_rolling_signals(
            score_map=score_map, positions=[], index_above_ma20=True
        )
        # Assert
        assert [b["symbol"] for b in result["buys"]] == [
            "000002.SZ",
            "000063.SZ",
            "000001.SZ",
        ]

    def test_no_buy_for_already_held_stock(self, svc):
        # Arrange
        score_map = {"600519.SH": 2.8}
        positions = [_pos("600519.SH")]
        # Act
        result = svc.compute_rolling_signals(
            score_map=score_map, positions=positions, index_above_ma20=True
        )
        # Assert
        assert result["buys"] == []

    def test_score_equal_to_threshold_sells(self, svc):
        # 规则边界: 分数 <= 阈值 应卖出（"低于2.2分，第二天要卖出"）
        score_map = {"600519.SH": DEFAULT_SCORE_THRESHOLD}
        positions = [_pos("600519.SH")]
        result = svc.compute_rolling_signals(
            score_map=score_map, positions=positions, index_above_ma20=True
        )
        assert [s["symbol"] for s in result["sells"]] == ["600519.SH"]

    def test_custom_threshold_from_config(self, svc):
        # 阈值可配置: 3.0 时 2.8 分应卖出而不是买入
        score_map = {"600519.SH": 2.8}
        positions = [_pos("600519.SH")]
        result = svc.compute_rolling_signals(
            score_map=score_map,
            positions=positions,
            index_above_ma20=True,
            score_threshold=3.0,
        )
        assert [s["symbol"] for s in result["sells"]] == ["600519.SH"]
        assert result["score_threshold"] == 3.0

    def test_custom_threshold_buys_lower_score(self, svc):
        # 阈值调低到 1.0 时, 1.5 分也应买入
        score_map = {"000001.SZ": 1.5}
        result = svc.compute_rolling_signals(
            score_map=score_map,
            positions=[],
            index_above_ma20=True,
            score_threshold=1.0,
        )
        assert [b["symbol"] for b in result["buys"]] == ["000001.SZ"]


GET_REDIS_PATH = "backend.services.trade.redis_client.get_redis"
ROLLING_MODULE = "backend.services.trade.services.tdx_rolling_trade_service"


def _redis_holding(saved: dict | None):
    mock_redis = MagicMock()
    mock_redis.get.return_value = saved
    return mock_redis


class TestRollingConfigExecuteMode:
    def test_default_off(self):
        with patch(GET_REDIS_PATH, return_value=_redis_holding(None)):
            _, _, mode = load_rolling_config("default", "00000001")
        assert mode == DEFAULT_EXECUTE_MODE

    def test_legacy_auto_place_true_maps_to_tdx(self):
        with patch(GET_REDIS_PATH, return_value=_redis_holding({"auto_place": True})):
            _, _, mode = load_rolling_config("default", "00000001")
        assert mode == "tdx"

    def test_redis_execute_mode_wins(self):
        with patch(GET_REDIS_PATH, return_value=_redis_holding({"execute_mode": "paper"})):
            _, _, mode = load_rolling_config("default", "00000001")
        assert mode == "paper"

    def test_invalid_execute_mode_falls_back_off(self):
        with patch(GET_REDIS_PATH, return_value=_redis_holding({"execute_mode": "hack"})):
            _, _, mode = load_rolling_config("default", "00000001")
        assert mode == "off"

    def test_save_and_reload_round_trip(self):
        mock_redis = MagicMock()
        with patch(GET_REDIS_PATH, return_value=mock_redis):
            save_rolling_config(
                "default", "00000001",
                score_threshold=2.5, fixed_buy_amount=20000, execute_mode="paper",
            )
        saved = mock_redis.set.call_args.args[1]
        assert saved["execute_mode"] == "paper"
        assert saved["auto_place"] is True
        # 旧读取方（只看 auto_place）仍兼容
        with patch(GET_REDIS_PATH, return_value=_redis_holding(saved)):
            _, _, mode = load_rolling_config("default", "00000001")
        assert mode == "paper"


class TestLoadPositionsFromPaper:
    @pytest.mark.asyncio
    async def test_normalizes_long_positions_and_skips_short(self):
        svc = TdxRollingTradeService()
        account = {
            "positions": {
                "SH600519": {"volume": 100, "cost": 1500.0, "market_value": 150000.0},
                "SH600000::short": {"volume": 500, "cost": 10.0, "market_value": 5000.0},
                "empty": {"volume": 0, "cost": 0.0},
            }
        }
        fake_manager = MagicMock()
        fake_manager.get_account = AsyncMock(return_value=account)
        with patch(
            "backend.services.trade.services.simulation_manager.SimulationAccountManager",
            return_value=fake_manager,
        ):
            positions, error = await svc.load_positions_from_paper("default", "00000001")

        assert error == ""
        assert len(positions) == 1
        assert positions[0]["symbol"] == "600519.SH"
        assert positions[0]["volume"] == 100
        assert positions[0]["cost_price"] == 1500.0
        assert positions[0]["raw_symbol"] == "SH600519"

    @pytest.mark.asyncio
    async def test_missing_account_reports_error(self):
        svc = TdxRollingTradeService()
        fake_manager = MagicMock()
        fake_manager.get_account = AsyncMock(return_value=None)
        with patch(
            "backend.services.trade.services.simulation_manager.SimulationAccountManager",
            return_value=fake_manager,
        ):
            _, error = await svc.load_positions_from_paper("default", "00000001")
        assert "未初始化" in error


class TestRunRollingPushExecuteMode:
    @pytest.mark.asyncio
    async def test_paper_mode_places_paper_orders_without_bridge(self):
        svc = TdxRollingTradeService()
        fake_tdx = MagicMock()
        fake_tdx.enabled = False
        buys = [{"symbol": "600519.SH", "score": 2.8, "volume": 100, "close": 1500.0}]
        sells = []
        with (
            patch(f"{ROLLING_MODULE}.load_rolling_config", return_value=(2.2, 10000.0, "paper")),
            patch(f"{ROLLING_MODULE}.tdx_pusher", fake_tdx),
            patch(
                "backend.services.trade.services.member_gate.is_paid_member",
                new=AsyncMock(return_value=True),
            ),
            patch.object(svc, "load_latest_scores", new=AsyncMock(
                return_value=("run1", {"600519.SH": 2.8}, "2026-08-25")
            )),
            patch.object(svc, "load_positions_from_paper", new=AsyncMock(return_value=([], ""))),
            patch.object(svc, "load_positions_from_tdx") as load_tdx,
            patch.object(svc, "is_index_above_ma20", new=AsyncMock(return_value=(True, "ok"))),
            patch.object(svc, "compute_rolling_signals", new=MagicMock(
                return_value={"buys": buys, "sells": sells, "holds": []}
            )),
            patch(
                "backend.services.trade.services.tdx_signal_push_service._batch_lookup_names",
                new=MagicMock(return_value={}),
            ),
            patch.object(svc, "place_paper_orders", new=AsyncMock(return_value=(buys, []))),
            patch.object(svc, "place_rolling_orders") as place_tdx_orders,
        ):
            result = await svc.run_rolling_push(tenant_id="default", user_id="00000001")

        assert result["success"] is True
        assert result["execute_mode"] == "paper"
        assert result["positions_source"] == "paper"
        assert result["placed_orders"] == buys
        load_tdx.assert_not_called()
        place_tdx_orders.assert_not_called()

    @pytest.mark.asyncio
    async def test_direct_order_blocked_for_non_member(self):
        svc = TdxRollingTradeService()
        fake_tdx = MagicMock()
        fake_tdx.enabled = False
        with (
            patch(f"{ROLLING_MODULE}.load_rolling_config", return_value=(2.2, 10000.0, "paper")),
            patch(f"{ROLLING_MODULE}.tdx_pusher", fake_tdx),
            patch(
                "backend.services.trade.services.member_gate.is_paid_member",
                new=AsyncMock(return_value=False),
            ),
            patch.object(svc, "place_paper_orders") as place_paper,
        ):
            result = await svc.run_rolling_push(tenant_id="default", user_id="00000001")

        assert result["success"] is False
        assert "会员" in result["error"]
        place_paper.assert_not_called()

    @pytest.mark.asyncio
    async def test_tdx_mode_requires_bridge(self):
        svc = TdxRollingTradeService()
        fake_tdx = MagicMock()
        fake_tdx.enabled = False
        with (
            patch(f"{ROLLING_MODULE}.load_rolling_config", return_value=(2.2, 10000.0, "tdx")),
            patch(f"{ROLLING_MODULE}.tdx_pusher", fake_tdx),
            patch(
                "backend.services.trade.services.member_gate.is_paid_member",
                new=AsyncMock(return_value=True),
            ),
        ):
            result = await svc.run_rolling_push(tenant_id="default", user_id="00000001")

        assert result["success"] is False
        assert "TDX_BRIDGE_URL" in result["error"]

    @pytest.mark.asyncio
    async def test_warning_only_mode_needs_no_member_and_no_bridge(self):
        svc = TdxRollingTradeService()
        fake_tdx = MagicMock()
        fake_tdx.enabled = False
        with (
            patch(f"{ROLLING_MODULE}.load_rolling_config", return_value=(2.2, 10000.0, "off")),
            patch(f"{ROLLING_MODULE}.tdx_pusher", fake_tdx),
            patch(
                "backend.services.trade.services.member_gate.is_paid_member"
            ) as check_member,
            patch.object(svc, "load_latest_scores", new=AsyncMock(
                return_value=("run1", {"600519.SH": 2.8}, "2026-08-25")
            )),
            patch.object(svc, "load_positions_from_tdx", new=AsyncMock(return_value=([], ""))),
            patch.object(svc, "is_index_above_ma20", new=AsyncMock(return_value=(True, "ok"))),
            patch.object(svc, "compute_rolling_signals", new=MagicMock(
                return_value={"buys": [], "sells": [], "holds": []}
            )),
            patch(
                "backend.services.trade.services.tdx_signal_push_service._batch_lookup_names",
                new=MagicMock(return_value={}),
            ),
        ):
            result = await svc.run_rolling_push(tenant_id="default", user_id="00000001")

        assert result["success"] is True
        assert result["execute_mode"] == "off"
        check_member.assert_not_called()


# ============ 桥交互方法回归（曾因方法插入错位导致 body 错乱） ============

class TestTdxBridgeInteraction:
    @pytest.mark.asyncio
    async def test_load_positions_from_tdx_normalizes_bridge_payload(self):
        svc = TdxRollingTradeService()
        fake_tdx = MagicMock()
        fake_tdx.pull_positions = AsyncMock(return_value=[
            {"stock_code": "600206.SH", "total_volume": 2400, "available_volume": 1000,
             "cost_price": 50.745, "market_value": 126048.0, "stock_name": "有研新材"},
            {"stock_code": "688783.SH", "total_volume": 0, "available_volume": 0,
             "cost_price": 0.0, "market_value": 0.0, "stock_name": "空仓残留"},
        ])
        with patch(f"{ROLLING_MODULE}.tdx_pusher", fake_tdx):
            positions, error = await svc.load_positions_from_tdx()

        assert error == ""
        assert len(positions) == 1  # 已清仓残留(total_volume=0)被过滤
        assert positions[0]["symbol"] == "600206.SH"
        assert positions[0]["volume"] == 2400

    @pytest.mark.asyncio
    async def test_load_positions_from_tdx_returns_error_on_bridge_failure(self):
        svc = TdxRollingTradeService()
        fake_tdx = MagicMock()
        fake_tdx.pull_positions = AsyncMock(side_effect=Exception("bridge down"))
        with patch(f"{ROLLING_MODULE}.tdx_pusher", fake_tdx):
            positions, error = await svc.load_positions_from_tdx()

        assert positions == []
        assert "通达信桥持仓拉取失败" in error

    @pytest.mark.asyncio
    async def test_cancel_order_proxies_bridge_and_returns_result(self):
        svc = TdxRollingTradeService()
        fake_tdx = MagicMock()
        fake_tdx.cancel_order = AsyncMock(
            return_value={"success": True, "message": "已撤"}
        )
        with patch(f"{ROLLING_MODULE}.tdx_pusher", fake_tdx):
            result = await svc.cancel_order("SH600206", "160356")

        assert result["success"] is True
        fake_tdx.cancel_order.assert_awaited_once_with("SH600206", "160356")

    @pytest.mark.asyncio
    async def test_pull_today_orders_proxies_bridge(self):
        svc = TdxRollingTradeService()
        fake_tdx = MagicMock()
        fake_tdx.pull_orders = AsyncMock(return_value=[{"order_id": "160356"}])
        with patch(f"{ROLLING_MODULE}.tdx_pusher", fake_tdx):
            orders = await svc.pull_today_orders("SH600206")

        assert orders == [{"order_id": "160356"}]
        fake_tdx.pull_orders.assert_awaited_once_with("SH600206")
