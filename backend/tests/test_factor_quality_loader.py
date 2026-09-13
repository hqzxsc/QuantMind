"""backend.shared.factor_quality 路径加载器 + 训练侧模块一致性。

加载器是训练侧与后端侧共用同一份 PFS/多样性公式的接缝：这里锁定
「能加载」「加载到的是同一实现」「端口不可用时优雅返回 None」。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_direct():
    """直接按路径加载训练侧实现（对照组）。"""
    spec = importlib.util.spec_from_file_location(
        "fq_direct", ROOT / "docker" / "training" / "data" / "factor_quality.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_loader_returns_training_module() -> None:
    from backend.shared.factor_quality import load_factor_quality

    mod = load_factor_quality()
    assert mod is not None
    for fn in ("compute_pfs", "effective_factors", "diversity_entropy"):
        assert callable(getattr(mod, fn))
    # 二次调用命中缓存（同一对象）
    assert load_factor_quality() is mod


def test_loader_same_implementation_as_training() -> None:
    """加载器与训练侧直读必须得到同一结果（防公式漂移）。"""
    from backend.shared.factor_quality import load_factor_quality

    loaded = load_factor_quality()
    direct = _load_direct()
    corr = np.array([[1.0, 0.5, 0.2], [0.5, 1.0, 0.1], [0.2, 0.1, 1.0]])
    assert loaded.effective_factors(corr) == direct.effective_factors(corr)
    assert loaded.diversity_entropy(corr) == direct.diversity_entropy(corr)

    rng = np.random.default_rng(1)
    df = pd.DataFrame({
        "trade_date": np.repeat(pd.date_range("2024-01-01", periods=25, freq="B"), 60),
        "symbol": np.tile([f"s{i:03d}" for i in range(60)], 25),
        "factor": rng.normal(size=25 * 60),
    })
    a = loaded.compute_pfs(df, ["factor"])
    b = direct.compute_pfs(df, ["factor"])
    assert a["factor"]["pfs"] == b["factor"]["pfs"]


def test_loader_graceful_when_missing(monkeypatch) -> None:
    """端口未挂载 docker/training 时返回 None，不抛异常。"""
    import backend.shared.factor_quality as fq

    monkeypatch.setattr(fq, "_CANDIDATES", (Path("/nonexistent/factor_quality.py"),))
    monkeypatch.setattr(fq, "_cached", None)
    monkeypatch.setattr(fq, "_loaded", False)
    assert fq.load_factor_quality() is None
