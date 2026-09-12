"""
conftest.py - 重构服务测试公共 fixtures
"""

import pytest


@pytest.fixture(autouse=True)
def _default_internal_call_secret(monkeypatch):
    """统一内部调用密钥，避免宿主机 .env 注入导致 TestClient 内部鉴权 401。"""
    monkeypatch.setenv("INTERNAL_CALL_SECRET", "dev-internal-call-secret")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: 端到端测试")
    config.addinivalue_line("markers", "integration: 集成测试")
    config.addinivalue_line("markers", "database: 需要真实数据库")
    config.addinivalue_line("markers", "message_broker: 需要消息中间件/Redis")
