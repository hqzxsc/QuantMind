"""市场定时同步调度器测试。

覆盖 _normalize 按市场合并默认配置（MARKET_DEFAULT_SCHEDULES）与
显式保存配置的覆盖行为，Redis 用桩对象替代。
"""

from __future__ import annotations

import pytest

from backend.services.engine.tasks.market_sync_scheduler import (
    DEFAULT_SCHEDULE,
    get_schedule,
    save_schedule,
)


class _StubRedis:
    """最小 Redis 桩：仅 get/set/exists，单测不依赖真实 Redis。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value

    def exists(self, key: str) -> bool:
        return key in self._data


@pytest.fixture()
def stub_redis(monkeypatch: pytest.MonkeyPatch) -> _StubRedis:
    stub = _StubRedis()
    monkeypatch.setattr(
        "backend.services.engine.tasks.market_sync_scheduler._redis", lambda: stub
    )
    return stub


def test_hk_schedule_is_enabled_by_default_without_redis_config(stub_redis):
    # Act：Redis 里没有任何 HK 配置时读取
    cfg = get_schedule("HK")

    # Assert：港股开箱即定时同步（排在 A 股 23:30 之后），其余字段沿用全局默认
    assert cfg["enabled"] is True
    assert cfg["time"] == "23:50"
    assert cfg["days"] == DEFAULT_SCHEDULE["days"]
    assert cfg["datasets"] == []


def test_markets_without_override_stay_disabled_by_default(stub_redis):
    # Act
    cfg = get_schedule("US")

    # Assert：未列入默认表的市场保持关闭，保持全局默认时间
    assert cfg["enabled"] is False
    assert cfg["time"] == "03:00"


def test_explicit_saved_config_overrides_market_default(stub_redis):
    # Arrange：用户在前端显式关闭港股定时
    save_schedule("HK", {"enabled": False})

    # Act
    cfg = get_schedule("HK")

    # Assert：显式关闭优先于市场默认开启；未覆盖字段沿用港股市场默认
    assert cfg["enabled"] is False
    assert cfg["time"] == "23:50"


def test_save_and_get_roundtrip_keeps_fields_not_set_by_caller(stub_redis):
    # Arrange：只传 enabled/time 的部分配置
    saved = save_schedule("HK", {"enabled": True, "time": "22:30"})

    # Act
    loaded = get_schedule("HK")

    # Assert：保存与读回一致，调用方未传字段沿用默认值
    assert saved == loaded
    assert loaded["time"] == "22:30"
    assert loaded["days"] == DEFAULT_SCHEDULE["days"]


def test_invalid_time_in_stored_config_falls_back_to_global_default(stub_redis):
    # Arrange：绕过 API 层校验，直接把坏时间写进 Redis 配置
    save_schedule("HK", {"time": "25:00"})

    # Act
    cfg = get_schedule("HK")

    # Assert：非法 HH:MM 回退到全局默认时间而不是抛错
    assert cfg["time"] == "03:00"


def test_normalize_of_missing_config_for_unknown_market_uses_global_defaults():
    from backend.services.engine.tasks.market_sync_scheduler import _normalize

    # Act / Assert：未知 market 传入时仅应用全局默认，不抛错
    assert _normalize(None, "XX") == dict(DEFAULT_SCHEDULE)
