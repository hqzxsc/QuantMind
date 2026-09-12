from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.services.api.main import app

    # Mock lifespan to avoid DB connection issues during simple unit tests
    with patch("backend.services.api.main.lifespan", MagicMock()):
        with TestClient(app) as client:
            yield client


class TestAPIExtendedIntegration:
    """验证 Consolidated API 中各集成应用的响应性"""

    def test_admin_dashboard_integration(self, client):
        """验证管理后台 Dashboard 路由是否可达"""
        from backend.services.api.main import app
        from backend.services.api.user_app.middleware.auth import require_admin

        app.state.started_at = datetime.now(timezone.utc) - timedelta(days=2, hours=6)
        # 覆盖权限检查依赖
        app.dependency_overrides[require_admin] = lambda: {
            "id": "admin",
            "role": "admin",
        }

        async def _mock_get(self, url, *args, **kwargs):
            payload = {"status": "healthy", "service": url.rsplit("/", 1)[0].split("//", 1)[-1]}
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

        with patch("httpx.AsyncClient.get", _mock_get):
            try:
                response = client.get("/api/v1/admin/dashboard/metrics")
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert "users" in data["data"]
                assert data["data"]["system"]["health_score"] == 100
                assert data["data"]["system"]["uptime_days"] >= 0
                assert data["data"]["system"]["status"] == "healthy"
                assert len(data["data"]["system"]["services"]) == 4
            finally:
                # 清理覆盖，避免影响其他测试
                app.dependency_overrides.clear()

    def test_admin_models_data_status_integration(self, client):
        """验证管理端数据状态路由可达并返回结构化字段"""
        from backend.services.api.main import app
        from backend.services.api.user_app.middleware.auth import require_admin

        app.dependency_overrides[require_admin] = lambda: {
            "id": "admin",
            "role": "admin",
        }

        try:
            response = client.get("/api/v1/admin/models/data-status")
            assert response.status_code == 200
            payload = response.json()
            assert "checked_at" in payload
            assert "trade_date" in payload
            assert "qlib_data" in payload
            assert "latest_date_coverage" in payload["qlib_data"]
        finally:
            app.dependency_overrides.clear()

    def test_community_integration(self, client):
        """验证社区路由集成"""
        response = client.get("/api/v1/community/posts?page=1&pageSize=10")
        # 即使没有数据或没权限，也不应该是 404
        assert response.status_code != 404

    def test_notifications_integration(self, client):
        """验证通知路由集成"""
        response = client.get("/api/v1/notifications")
        assert response.status_code != 404

    def test_model_training_user_feature_catalog_integration(self, client):
        """验证用户态模型训练特征字典路由可达"""
        from backend.services.api.main import app
        from backend.services.api.user_app.middleware.auth import get_current_user

        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "u1",
            "tenant_id": "t1",
            "is_admin": False,
        }

        with patch(
            "backend.services.api.routers.model_training.load_quantdb_training_catalog",
            AsyncMock(return_value={
                "version_id": "v1", "version_name": "v1", "feature_count": 1,
                "categories": [], "source": "quantdb_factor_catalog",
                "catalog_status": "ready",
            }),
        ):
            try:
                response = client.get("/api/v1/models/feature-catalog")
                assert response.status_code == 200
                data = response.json()
                assert data["version_id"] == "v1"
                assert data["catalog_status"] == "ready"
            finally:
                app.dependency_overrides.clear()

    def test_model_training_user_run_and_status_integration(self, client):
        """验证用户态模型训练启动/状态接口可达并返回结构化数据"""
        from backend.services.api.main import app
        from backend.services.api.user_app.middleware.auth import get_current_user

        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "u1",
            "tenant_id": "t1",
            "is_admin": False,
        }

        with patch(
            "backend.services.api.routers.model_training.submit_training_job",
            AsyncMock(return_value={"runId": "r1", "status": "pending", "payload": {"foo": "bar"}}),
        ):
            with patch(
                "backend.services.api.routers.model_training.get_training_run_for_owner",
                AsyncMock(return_value={"runId": "r1", "status": "running", "progress": 30, "logs": "", "result": {}, "isCompleted": False}),
            ):
                try:
                    response = client.post("/api/v1/models/run-training", json={"features": [], "lgb_params": {}})
                    assert response.status_code == 200
                    assert response.json()["runId"] == "r1"

                    status_resp = client.get("/api/v1/models/training-runs/r1")
                    assert status_resp.status_code == 200
                    assert status_resp.json()["status"] == "running"
                finally:
                    app.dependency_overrides.clear()

    def test_model_training_admin_alias_route_integration(self, client):
        """验证 admin 兼容别名路由仍可访问"""
        from backend.services.api.main import app
        from backend.services.api.user_app.middleware.auth import require_admin

        app.dependency_overrides[require_admin] = lambda: {
            "user_id": "admin",
            "tenant_id": "default",
            "is_admin": True,
        }

        with patch(
            "backend.services.api.routers.admin.admin_training.submit_training_job",
            AsyncMock(return_value={"runId": "admin_r1", "status": "pending", "payload": {}}),
        ):
            try:
                response = client.post("/api/v1/admin/models/run-training", json={"features": [], "lgb_params": {}})
                assert response.status_code == 200
                assert response.json()["runId"] == "admin_r1"
            finally:
                app.dependency_overrides.clear()
