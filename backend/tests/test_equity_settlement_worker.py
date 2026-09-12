"""权益结算 worker 纯函数单测（无 Redis/DB 依赖，可本地跑）。

覆盖：
1. 持仓键解析：SYMBOL::long / SYMBOL / SYMBOL:short 三种历史键形
2. 持仓汇总：多空市值与净值口径（market_value=long，total=cash+long-short）
3. 重估更新构建：价格变化才产生更新；价格无效（0/缺）跳过保留原值
4. worker 配置默认值：30s 周期、默认启用
"""

from backend.services.simulation.services.equity_settlement_worker import (
    build_remark_updates,
    settle_enabled,
    settle_interval_seconds,
    split_position_key,
    summarize_positions,
)


def test_split_position_key_all_key_shapes():
    assert split_position_key("SH600036::long") == ("SH600036", "long")
    assert split_position_key("SH600036") == ("SH600036", "long")
    assert split_position_key("SH600036:short") == ("SH600036", "short")
    assert split_position_key("SH600036::short") == ("SH600036", "short")
    assert split_position_key("600036.SH") == ("600036.SH", "long")
    assert split_position_key("") == ("", "long")


def test_summarize_positions_long_short_net():
    positions = {
        "SH600036::long": {"market_value": 10000.0},
        "SZ000001": {"market_value": 5000.0},
        "SH600519:short": {"market_value": 2000.0},
    }
    long_mv, short_mv, net = summarize_positions(positions)
    assert long_mv == 15000.0
    assert short_mv == 2000.0
    assert net == 13000.0
    # 非法输入不抛异常
    assert summarize_positions(None) == (0.0, 0.0, 0.0)
    assert summarize_positions({"bad": 1}) == (0.0, 0.0, 0.0)


def test_build_remark_updates_only_changed():
    account = {
        "positions": {
            # 价格变化 → 产生更新
            "SH600036::long": {"volume": 1000, "price": 9.0, "market_value": 9000.0},
            # 价格未变 → 跳过
            "SZ000001::long": {"volume": 200, "price": 5.0, "market_value": 1000.0},
            # 无有效价 → 保留原值
            "SH600519::long": {"volume": 100, "price": 0, "market_value": 0},
        }
    }
    updates = build_remark_updates(account, {"SH600036": 10.0, "SZ000001": 5.0})
    assert list(updates) == ["SH600036::long"]
    assert updates["SH600036::long"] == {"price": 10.0, "market_value": 10000.0}


def test_worker_config_defaults(monkeypatch):
    monkeypatch.delenv("SIM_EQUITY_SETTLE_ENABLED", raising=False)
    monkeypatch.delenv("SIM_EQUITY_SETTLE_INTERVAL_SECONDS", raising=False)
    assert settle_enabled() is True
    assert settle_interval_seconds() == 30

    monkeypatch.setenv("SIM_EQUITY_SETTLE_INTERVAL_SECONDS", "5")
    # 下限保护：低于 10s 钳到 10s
    assert settle_interval_seconds() == 10
    monkeypatch.setenv("SIM_EQUITY_SETTLE_ENABLED", "false")
    assert settle_enabled() is False
