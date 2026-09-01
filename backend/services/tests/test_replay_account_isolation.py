"""回放账户隔离测试。

验证回放操作不会污染 sim_orders / sim_trades / simulation:account:*。
同时验证 ReplayAccountManager 的 nil 兼容分支。
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.simulation.replay.account import ReplayAccountManager


class TestReplayAccountKeyIsolation:
    """回放账户 Redis key 与活盘账户完全隔离。"""

    def test_key_prefix_differs_from_simulation(self):
        """replay:account:* 不匹配 simulation:account:* 模式。"""
        sid = uuid.uuid4()
        mgr = ReplayAccountManager(session_id=sid)

        # 回放 key
        replay_key = mgr._get_key(user_id=0, tenant_id="default")
        assert replay_key == f"replay:account:{sid}"

        # 活盘 key 的模式
        assert not replay_key.startswith("simulation:account:")
        # capture_all 的全局 SCAN 用 simulation:account:*，
        # replay: 前缀不匹配
        assert "simulation:account" not in replay_key

    def test_settings_key_isolation(self):
        """回放 settings key 也用 replay: 前缀。"""
        sid = uuid.uuid4()
        mgr = ReplayAccountManager(session_id=sid)

        settings_key = mgr._get_settings_key(user_id=0, tenant_id="default")
        assert settings_key == f"replay:settings:{sid}"
        assert not settings_key.startswith("simulation:")


class TestReplayAccountConvenienceMethods:
    """便捷方法签名正确，传占位 user_id/tenant_id。"""

    def test_init_calls_parent_with_defaults(self):
        """init() 传 user_id=0, tenant_id='default'。"""
        sid = uuid.uuid4()
        mock_redis = MagicMock()
        mgr = ReplayAccountManager(session_id=sid, redis=mock_redis)

        with patch.object(
            type(mgr).__bases__[0],
            "init_account",
            new_callable=AsyncMock,
        ) as mock_init:
            import asyncio

            asyncio.run(mgr.init(initial_cash=500000.0))
            mock_init.assert_called_once_with(
                user_id=0, initial_cash=500000.0, tenant_id="default"
            )

    def test_get_calls_parent_with_defaults(self):
        """get() 传 user_id=0, tenant_id='default'。"""
        sid = uuid.uuid4()
        mock_redis = MagicMock()
        mgr = ReplayAccountManager(session_id=sid, redis=mock_redis)

        with patch.object(
            type(mgr).__bases__[0],
            "get_account",
            new_callable=AsyncMock,
            return_value={"cash": 1000000},
        ) as mock_get:
            import asyncio

            result = asyncio.run(mgr.get())
            mock_get.assert_called_once_with(user_id=0, tenant_id="default")
            assert result["cash"] == 1000000


class TestReplayAccountDrop:
    """丢弃会话时清除 Redis。"""

    def test_drop_deletes_both_keys(self):
        """drop() 删除 account 和 settings 两个 key。"""
        sid = uuid.uuid4()
        mock_redis = MagicMock()
        mock_client = MagicMock()
        mock_redis.client = mock_client
        mgr = ReplayAccountManager(session_id=sid, redis=mock_redis)

        mgr.drop()

        expected_account_key = f"replay:account:{sid}"
        expected_settings_key = f"replay:settings:{sid}"
        calls = mock_client.delete.call_args_list
        assert len(calls) == 2
        assert calls[0][0][0] == expected_account_key
        assert calls[1][0][0] == expected_settings_key

    def test_drop_noop_if_no_client(self):
        """Redis client 为 None 时 drop() 不报错。"""
        sid = uuid.uuid4()
        mock_redis = MagicMock()
        mock_redis.client = None
        mgr = ReplayAccountManager(session_id=sid, redis=mock_redis)

        mgr.drop()  # 不应抛异常
