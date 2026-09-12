"""
test_stream_service.py - quantmind-stream 服务测试
验证实时流网关服务的健康检查、路由注册和 WebSocket 功能
"""


import pytest

# ============================================================
# 1. App 创建与基础路由测试
# ============================================================


class TestStreamAppCreation:
    """测试 quantmind-stream 应用对象的创建和基础配置"""

    def test_app_instance_exists(self):
        """验证 FastAPI 应用对象可以被导入"""
        from backend.services.stream.main import app

        assert app is not None
        assert app.title == "QuantMind Streaming Service"
        assert app.version == "2.0.0"

    def test_health_endpoint_registered(self):
        """验证 /health 端点已注册"""
        from backend.services.stream.main import app

        routes = [getattr(r, "path", None) for r in app.routes]
        assert "/health" in routes

    def test_root_endpoint_registered(self):
        """验证 / 端点已注册"""
        from backend.services.stream.main import app

        routes = [getattr(r, "path", None) for r in app.routes]
        assert "/" in routes

    def test_websocket_endpoint_registered(self):
        """验证 /ws WebSocket 端点已注册"""
        from backend.services.stream.main import app

        ws_routes = [getattr(r, "path", None) for r in app.routes]
        assert "/ws" in ws_routes, f"未找到 /ws 端点，当前路由: {ws_routes}"

    def test_cors_middleware_configured(self):
        """验证 CORS 中间件已配置"""
        from backend.services.stream.main import app

        middleware_classes = [m.cls.__name__ for m in app.user_middleware]
        assert "CORSMiddleware" in middleware_classes


class TestStreamRouterRegistration:
    """测试行情数据路由模块是否正确注册"""

    def _get_route_paths(self):
        from backend.services.stream.main import app

        # 新版 FastAPI included router 不展开为带 path 的 Route，统一用 OpenAPI 路径。
        return list(app.openapi().get("paths", {}).keys())

    def test_quotes_routes_registered(self):
        """验证行情查询路由已注册"""
        paths = self._get_route_paths()
        quote_paths = [p for p in paths if "quote" in p.lower()]
        assert len(quote_paths) > 0, f"未找到行情路由，当前路由: {paths}"

    def test_klines_routes_registered(self):
        """验证K线路由已注册"""
        paths = self._get_route_paths()
        kline_paths = [p for p in paths if "kline" in p.lower()]
        assert len(kline_paths) > 0, f"未找到K线路由，当前路由: {paths}"


# ============================================================
# 2. HTTP 接口测试
# ============================================================


class TestStreamHealthEndpoints:
    """测试健康检查等无需认证的端点"""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        """设置测试客户端，mock 掉 lifespan 中的外部依赖"""
        from contextlib import asynccontextmanager

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        @asynccontextmanager
        async def mock_lifespan(app: FastAPI):
            yield

        from backend.services.stream import main as stream_main

        original_lifespan = stream_main.app.router.lifespan_context
        stream_main.app.router.lifespan_context = mock_lifespan
        stream_main.app.state.market_db_connected = True
        stream_main.app.state.ws_core_started = True
        self.client = TestClient(stream_main.app)
        yield
        stream_main.app.router.lifespan_context = original_lifespan

    def test_health_returns_200(self):
        """验证 /health 返回 200"""
        response = self.client.get("/health")
        assert response.status_code == 200

    def test_health_response_body(self):
        """验证 /health 返回正确的服务标识"""
        response = self.client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "quantmind-stream"

    def test_health_response_contains_request_id(self):
        """验证响应头包含 X-Request-ID"""
        response = self.client.get("/health")
        assert response.headers.get("X-Request-ID")

    def test_root_returns_200(self):
        """验证 / 返回 200"""
        response = self.client.get("/")
        assert response.status_code == 200

    def test_root_response_body(self):
        """验证 / 返回正确消息"""
        response = self.client.get("/")
        data = response.json()
        assert "QuantMind Stream Service V2" in data["message"]

    def test_nonexistent_route_returns_404(self):
        """验证不存在的路由返回 404"""
        response = self.client.get("/api/v1/nonexistent_endpoint_xyz")
        assert response.status_code == 404

    def test_nonexistent_route_error_contract(self):
        """验证 404 响应符合统一错误契约"""
        response = self.client.get("/api/v1/nonexistent_endpoint_xyz")
        body = response.json()
        assert body["error"]["code"] == "HTTP_404"
        assert body["error"]["request_id"]

    def test_openapi_schema_available(self):
        """验证 OpenAPI 文档可访问"""
        response = self.client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "QuantMind Streaming Service"

    def test_metrics_endpoint_exposes_degraded_gauges(self):
        """验证 /metrics 暴露降级相关指标"""
        # 先触发一次健康检查，确保指标值刷新
        self.client.get("/health")
        response = self.client.get("/metrics")
        assert response.status_code == 200
        body = response.text
        assert "stream_market_db_connected" in body
        assert "stream_service_degraded" in body


# ============================================================
# 3. WebSocket 核心模块测试 (不需要实际连接)
# ============================================================


class TestWSCoreModules:
    """测试 WebSocket 核心模块的基本功能"""

    def test_ws_config_loadable(self):
        """验证 WebSocket 配置可加载"""
        from backend.services.stream.ws_core.ws_config import ws_config

        assert ws_config is not None
        assert hasattr(ws_config, "heartbeat_interval")
        assert hasattr(ws_config, "max_connections")

    def test_exceptions_defined(self):
        """验证异常类已定义"""
        from backend.services.stream.ws_core.exceptions import WebSocketError

        assert WebSocketError is not None

    def test_manager_importable(self):
        """验证连接管理器可导入"""
        from backend.services.stream.ws_core.manager import ConnectionManager

        assert ConnectionManager is not None

    def test_message_queue_importable(self):
        """验证消息队列可导入"""
        from backend.services.stream.ws_core.message_queue import MessageQueue

        assert MessageQueue is not None
