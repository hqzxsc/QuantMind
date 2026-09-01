"""回放模型目录解析：生产模型 + 用户训练模型两类存放位置。

回归背景：原实现只查 MODELS_PRODUCTION/<model_id>，用户在向导里选自己
训练的模型（存在 qm_user_models.storage_path 下）必然 400「模型不存在」。

规则：
- 生产目录命中 → 直接用
- 生产目录未命中 → 查用户模型注册表，用 storage_path 定位
- 两者都未命中 → 400（不静默回落到 model_qlib，否则用户以为在跑自选模型）
- 目录存在但 metadata.json / 权重文件缺失 → 400（在创建时就拦住，
  不要等后台推理阶段才炸）
- user_id 有裸数字和 8 位补齐两种形态，都要能查到
"""

import asyncio
import json

import pytest
from fastapi import HTTPException

from backend.services.simulation.replay import router as replay_router


def _resolve(model_id, tenant_id="default", user_id="00000001"):
    return asyncio.run(
        replay_router._resolve_model_dir_for_user(model_id, tenant_id, user_id)
    )


def _write_model_dir(base, model_id, model_file="model.lgb", meta_extra=None):
    """造一个结构完整的模型目录（metadata.json + 权重文件）。"""
    d = base / model_id
    d.mkdir(parents=True, exist_ok=True)
    meta = {"framework": "lightgbm", "model_file": model_file}
    if meta_extra:
        meta.update(meta_extra)
    (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / model_file).write_bytes(b"fake-weights")
    return d


@pytest.fixture
def prod_root(tmp_path, monkeypatch):
    root = tmp_path / "production"
    root.mkdir()
    monkeypatch.setenv("MODELS_PRODUCTION", str(root))
    return root


@pytest.fixture
def fake_registry(monkeypatch):
    """替换 model_registry_service.get_model，避免测试依赖真实 DB。"""
    store: dict[tuple[str, str, str], dict] = {}

    class _FakeRegistry:
        async def get_model(self, *, tenant_id, user_id, model_id):
            return store.get((tenant_id, user_id, model_id))

    import backend.shared.model_registry as mr

    monkeypatch.setattr(mr, "model_registry_service", _FakeRegistry())
    return store


# ---------------------------------------------------------------------------
# 生产模型
# ---------------------------------------------------------------------------


def test_resolves_production_model(prod_root):
    expected = _write_model_dir(prod_root, "model_qlib")

    got = _resolve("model_qlib")

    assert got == expected


# ---------------------------------------------------------------------------
# 用户训练模型
# ---------------------------------------------------------------------------


def test_resolves_user_model_via_storage_path(tmp_path, prod_root, fake_registry):
    users_root = tmp_path / "users" / "default" / "00000001"
    model_id = "mdl_train_20260803_abc"
    expected = _write_model_dir(users_root, model_id, model_file="model.pkl")
    fake_registry[("default", "00000001", model_id)] = {
        "storage_path": str(expected),
        "model_file": "model.pkl",
    }

    got = _resolve(model_id)

    assert got == expected


def test_user_id_zero_padded_variant_is_tried(tmp_path, prod_root, fake_registry):
    """注册表里存 8 位补齐，JWT 传裸数字 —— 也要能查到。"""
    users_root = tmp_path / "users" / "default" / "00000001"
    model_id = "mdl_train_padded"
    expected = _write_model_dir(users_root, model_id)
    fake_registry[("default", "00000001", model_id)] = {"storage_path": str(expected)}

    got = _resolve(model_id, user_id="1")

    assert got == expected


def test_user_id_stripped_variant_is_tried(tmp_path, prod_root, fake_registry):
    """注册表里存裸数字，JWT 传 8 位补齐 —— 反方向也要能查到。"""
    users_root = tmp_path / "users" / "default" / "1001"
    model_id = "mdl_train_stripped"
    expected = _write_model_dir(users_root, model_id)
    fake_registry[("default", "1001", model_id)] = {"storage_path": str(expected)}

    got = _resolve(model_id, user_id="00001001")

    assert got == expected


# ---------------------------------------------------------------------------
# 失败路径：必须 400，不能静默回落
# ---------------------------------------------------------------------------


def test_unknown_model_raises_400_not_fallback(prod_root, fake_registry):
    """关键：即使 model_qlib 存在，未知 id 也要报错而不是回落过去。"""
    _write_model_dir(prod_root, "model_qlib")

    with pytest.raises(HTTPException) as exc:
        _resolve("nonexistent_xyz")

    assert exc.value.status_code == 400
    assert "模型不存在" in str(exc.value.detail)


def test_registry_hit_but_directory_missing_raises_400(
    tmp_path, prod_root, fake_registry
):
    """注册表有记录但磁盘目录被删了 —— 报错要带上 storage_path 便于排查。"""
    model_id = "mdl_train_ghost"
    ghost = tmp_path / "users" / "default" / "00000001" / model_id
    fake_registry[("default", "00000001", model_id)] = {"storage_path": str(ghost)}

    with pytest.raises(HTTPException) as exc:
        _resolve(model_id)

    assert exc.value.status_code == 400
    assert "模型目录不存在" in str(exc.value.detail)


def test_missing_metadata_raises_400(prod_root):
    (prod_root / "broken").mkdir()

    with pytest.raises(HTTPException) as exc:
        _resolve("broken")

    assert exc.value.status_code == 400
    assert "metadata.json" in str(exc.value.detail)


def test_missing_weight_file_raises_400(prod_root):
    """断链 symlink / 缺权重：创建时就要拦住，别等推理阶段。"""
    d = prod_root / "no_weights"
    d.mkdir()
    (d / "metadata.json").write_text(
        json.dumps({"framework": "lightgbm", "model_file": "model.lgb"}),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc:
        _resolve("no_weights")

    assert exc.value.status_code == 400
    assert "不可读" in str(exc.value.detail)


def test_corrupt_metadata_raises_400(prod_root):
    d = prod_root / "corrupt"
    d.mkdir()
    (d / "metadata.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        _resolve("corrupt")

    assert exc.value.status_code == 400
    assert "无法解析" in str(exc.value.detail)
