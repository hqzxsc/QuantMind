from __future__ import annotations

from types import SimpleNamespace

from backend.services.engine.inference.router_service import InferenceRouterService


class _FakeInferenceService:
    def __init__(self):
        self.model_loader = SimpleNamespace(get_model_metadata=self._get_meta)
        self._responses = {}

    @staticmethod
    def _get_meta(model_id: str, **_kwargs):
        if model_id == "model_demo":
            return {"feature_columns": [f"feature_{i}" for i in range(4)]}
        if model_id == "model_base":
            return {"feature_columns": ["open", "high", "low", "close"]}
        return {}

    def predict(self, model_id, data, **kwargs):
        fn = self._responses.get(model_id)
        if fn is None:
            return {"status": "error", "model_id": model_id, "error": "not configured"}
        return fn(data)


def test_router_primary_success_without_fallback(monkeypatch):
    svc = _FakeInferenceService()
    svc._responses["model_demo"] = lambda _data: {"status": "success", "model_id": "model_demo", "predictions": [0.1]}
    router = InferenceRouterService(inference_service=svc)
    monkeypatch.setattr(router, "primary_model_id", "model_demo")
    monkeypatch.setattr(router, "fallback_model_id", "model_base")
    monkeypatch.setattr(router, "primary_data_source", "db/qlib_data")

    data = {"feature_0": 1, "feature_1": 2, "feature_2": 3, "feature_3": 4}
    result = router.predict_with_fallback("model_demo", data)
    assert result["status"] == "success"
    assert result["fallback_used"] is False
    assert result["active_model_id"] == "model_demo"
    assert result["active_data_source"] == "db/qlib_data"


def test_router_dimension_insufficient_triggers_fallback(monkeypatch):
    svc = _FakeInferenceService()
    svc._responses["model_base"] = lambda _data: {"status": "success", "model_id": "model_base", "predictions": [0.2]}
    router = InferenceRouterService(inference_service=svc)
    monkeypatch.setattr(router, "primary_model_id", "model_demo")
    monkeypatch.setattr(router, "fallback_model_id", "model_base")
    monkeypatch.setattr(router, "fallback_data_source", "db/ModelBase_bin")

    # 仅 2 个特征，小于 model_demo 期望 4 维
    data = {"feature_0": 1, "feature_1": 2}
    result = router.predict_with_fallback("model_demo", data)
    assert result["status"] == "success"
    assert result["fallback_used"] is True
    assert "维度门禁未通过" in result["fallback_reason"]
    assert result["active_model_id"] == "model_base"
    assert result["active_data_source"] == "db/ModelBase_bin"


def test_router_primary_and_fallback_both_fail(monkeypatch):
    svc = _FakeInferenceService()
    svc._responses["model_demo"] = lambda _data: {"status": "error", "model_id": "model_demo", "error": "primary broken"}
    svc._responses["model_base"] = lambda _data: {"status": "error", "model_id": "model_base", "error": "fallback broken"}
    router = InferenceRouterService(inference_service=svc)
    monkeypatch.setattr(router, "primary_model_id", "model_demo")
    monkeypatch.setattr(router, "fallback_model_id", "model_base")

    data = {"feature_0": 1, "feature_1": 2, "feature_2": 3, "feature_3": 4}
    result = router.predict_with_fallback("model_demo", data)
    assert result["status"] == "error"
    assert result["fallback_used"] is True
    assert "主模型推理失败且兜底失败" in result["fallback_reason"]


def test_router_non_primary_model_uses_matched_data_source(monkeypatch):
    svc = _FakeInferenceService()
    svc._responses["model_base"] = lambda _data: {"status": "success", "model_id": "model_base", "predictions": [0.3]}
    router = InferenceRouterService(inference_service=svc)
    monkeypatch.setattr(router, "primary_model_id", "model_demo")
    monkeypatch.setattr(router, "fallback_model_id", "model_base")
    monkeypatch.setattr(router, "fallback_data_source", "db/ModelBase_bin")

    result = router.predict_with_fallback("model_base", {"open": 1, "high": 2, "low": 1, "close": 2})
    assert result["status"] == "success"
    assert result["fallback_used"] is False
    assert result["active_model_id"] == "model_base"
    assert result["active_data_source"] == "db/ModelBase_bin"


def test_router_sync_resolution_path_uses_sync_model_registry(monkeypatch, tmp_path):
    svc = _FakeInferenceService()
    model_dir = tmp_path / "user_model"
    model_dir.mkdir()
    svc._responses["user_model"] = lambda _data: {"status": "success", "model_id": "user_model", "predictions": [0.4]}

    router = InferenceRouterService(inference_service=svc)
    monkeypatch.setattr(router, "primary_model_id", "model_demo")
    monkeypatch.setattr(router, "fallback_model_id", "model_base")
    monkeypatch.setattr(
        "backend.shared.model_registry.model_registry_service.resolve_effective_model_sync",
        lambda **_kwargs: {
            "effective_model_id": "user_model",
            "model_source": "strategy_binding",
            "fallback_used": False,
            "fallback_reason": "",
            "storage_path": str(model_dir),
            "model_file": "model.lgb",
            "status": "ready",
        },
    )

    result = router.predict_with_fallback(
        "ignored",
        {"feature_0": 1, "feature_1": 2, "feature_2": 3, "feature_3": 4},
        tenant_id="default",
        user_id="79311845",
        strategy_id="48",
    )

    assert result["status"] == "success"
    assert result["fallback_used"] is False
    assert result["active_model_id"] == "user_model"
