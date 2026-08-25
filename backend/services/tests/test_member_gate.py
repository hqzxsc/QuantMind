"""会员门控单元测试：QuantDB 付费会员在期判定。"""
from unittest.mock import MagicMock, patch

import pytest

from backend.services.trade.services.member_gate import (
    MEMBER_CACHE_KEY,
    is_paid_member,
)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, sql, params=None):
        return _FakeResult(self._rows)


GET_REDIS_PATH = "backend.services.trade.redis_client.get_redis"
GET_SESSION_PATH = "backend.shared.database_manager_v2.get_session"


def _redis_with_cache(value=None):
    mock_redis = MagicMock()
    mock_redis.get.return_value = value
    return mock_redis


class TestIsPaidMember:
    @pytest.mark.asyncio
    async def test_active_paid_subscription_allowed(self):
        mock_redis = _redis_with_cache(None)
        with patch(GET_REDIS_PATH, return_value=mock_redis), patch(
            GET_SESSION_PATH, return_value=_FakeDb([(1,)])
        ):
            assert await is_paid_member("default", "00000001") is True
        # 结果回写缓存
        mock_redis.set.assert_called_once_with(
            MEMBER_CACHE_KEY.format(tenant_id="default", user_id="00000001"),
            1,
            ttl=60,
        )

    @pytest.mark.asyncio
    async def test_no_subscription_denied(self):
        mock_redis = _redis_with_cache(None)
        with patch(GET_REDIS_PATH, return_value=mock_redis), patch(
            GET_SESSION_PATH, return_value=_FakeDb([])
        ):
            assert await is_paid_member("default", "00000001") is False
        mock_redis.set.assert_called_once()
        assert mock_redis.set.call_args.args[1] == 0

    @pytest.mark.asyncio
    async def test_cache_hit_true_skips_db(self):
        mock_redis = _redis_with_cache(1)
        with patch(GET_REDIS_PATH, return_value=mock_redis) as redis_factory, patch(
            GET_SESSION_PATH
        ) as session_factory:
            assert await is_paid_member("default", "00000001") is True
        redis_factory.assert_called_once()
        session_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_hit_false_skips_db(self):
        mock_redis = _redis_with_cache(0)
        with patch(GET_REDIS_PATH, return_value=mock_redis), patch(GET_SESSION_PATH) as session_factory:
            assert await is_paid_member("default", "00000001") is False
        session_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_down_fails_closed(self):
        with patch(GET_REDIS_PATH, side_effect=RuntimeError("redis down")):
            assert await is_paid_member("default", "00000001") is False

    @pytest.mark.asyncio
    async def test_db_error_fails_closed(self):
        mock_redis = _redis_with_cache(None)
        with patch(GET_REDIS_PATH, return_value=mock_redis), patch(
            GET_SESSION_PATH, side_effect=RuntimeError("pg down")
        ):
            assert await is_paid_member("default", "00000001") is False

    @pytest.mark.asyncio
    async def test_cache_write_failure_does_not_break(self):
        mock_redis = _redis_with_cache(None)
        mock_redis.set.side_effect = RuntimeError("redis write fail")
        with patch(GET_REDIS_PATH, return_value=mock_redis), patch(
            GET_SESSION_PATH, return_value=_FakeDb([(1,)])
        ):
            # 缓存写失败仍按查库结果放行
            assert await is_paid_member("default", "00000001") is True
