"""runtime_secrets.get_secret 动态读取语义的单元测试。

核心场景：Celery worker 常驻运行，启动时经 load_runtime_env 注入的键，
在管理台改写 runtime.env 后，get_secret 必须返回新值（无需重启进程）；
而部署级真实环境变量（非空）的优先级保持不变。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from backend.shared.runtime_secrets import (
    _injected_keys,
    get_secret,
    load_runtime_env,
    set_secret,
)


@pytest.fixture(autouse=True)
def _isolated_runtime_env(tmp_path, monkeypatch):
    """隔离：独立 runtime.env 文件 + 干净的注入记录。"""
    monkeypatch.setenv("QM_RUNTIME_ENV_FILE", str(tmp_path / "runtime.env"))
    monkeypatch.setattr("backend.shared.runtime_secrets._injected_keys", set())
    yield


def test_injected_key_rereads_runtime_env_without_restart(tmp_path, monkeypatch):
    """启动注入的键：改 runtime.env 后 get_secret 返回新值，进程 env 不变。"""
    env_file = tmp_path / "runtime.env"
    env_file.write_text("QUANTDB_API_KEY=old_key_123\n", encoding="utf-8")
    assert load_runtime_env() == 1
    assert get_secret("QUANTDB_API_KEY") == "old_key_123"

    # 模拟管理台换 key：只改文件（相当于另一个进程 set_secret）
    env_file.write_text("QUANTDB_API_KEY=new_key_456\n", encoding="utf-8")
    assert os_environ_get("QUANTDB_API_KEY") == "old_key_123"  # 进程快照未变
    assert get_secret("QUANTDB_API_KEY") == "new_key_456"  # 动态读取拿到新值


def test_real_env_var_keeps_priority_over_runtime_env(tmp_path):
    """部署级真实环境变量（非注入）优先，runtime.env 不抢占。"""
    (tmp_path / "runtime.env").write_text("QUANTDB_API_KEY=from_file\n", encoding="utf-8")
    set_secret("OTHER_KEY", "x")  # 确保文件存在且可写
    import os

    monkey_env = "deploy_env_key"
    os.environ["QUANTDB_API_KEY"] = monkey_env
    try:
        assert load_runtime_env() == 0  # 真实 env 非空，不注入
        assert get_secret("QUANTDB_API_KEY") == monkey_env
    finally:
        os.environ.pop("QUANTDB_API_KEY", None)


def test_set_secret_immediately_visible(tmp_path):
    env_file = tmp_path / "runtime.env"
    set_secret("QUANTDB_API_KEY", "live_key")
    assert env_file.exists()
    assert get_secret("QUANTDB_API_KEY") == "live_key"


def test_missing_everywhere_returns_default():
    assert get_secret("NOT_SET_ANYWHERE", "fallback") == "fallback"


def os_environ_get(key: str) -> str:
    import os

    return os.environ.get(key, "")
