"""遗留问题修复单测：多市场快照合并 + 调度器交易日历（无 DB 依赖）。

1. _parse_account_key 不再跳过非 CN 市场账户（跨市场合并入用户级快照）
2. SimulationScheduler._is_trading_day：日历不可用时回退周判断
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.services.simulation.scheduler import SimulationScheduler
from backend.services.simulation.services.fund_snapshot_service import (
    _parse_account_key,
)


def test_parse_account_key_accepts_all_markets():
    # CN（无后缀）与带市场后缀的键都应解析成功，交给 capture_all 合并
    assert _parse_account_key("simulation:account:default:1") == ("default", "1")
    assert _parse_account_key("simulation:account:default:1:CN") == ("default", "1")
    assert _parse_account_key("simulation:account:default:1:HK") == ("default", "1")
    assert _parse_account_key("simulation:account:default:1:US") == ("default", "1")
    # 非账户键返回 None
    assert _parse_account_key("simulation:settings:default:1") is None
    assert _parse_account_key("") is None


def test_scheduler_trading_day_fallback():
    scheduler = SimulationScheduler.__new__(SimulationScheduler)  # 不走 __init__（避免连 Redis）
    sh = ZoneInfo("Asia/Shanghai")
    # 周六无论日历与否必非交易日（日历可用时同样返回 False）
    saturday = datetime(2026, 9, 12, 9, 35, tzinfo=sh)
    assert scheduler._is_trading_day(saturday) is False
    # 周一：日历可用时按 XSHG 判断（True）；不可用回退周判断（True）。均为 True。
    monday = datetime(2026, 9, 14, 9, 35, tzinfo=sh)
    assert scheduler._is_trading_day(monday) is True
