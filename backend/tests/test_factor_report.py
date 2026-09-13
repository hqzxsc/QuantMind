"""因子报告：纯函数与快照切片单测（无 DB、无 parquet 依赖）。

覆盖三处容易错的口径：
1. 秩相关（Spearman）——因子报告的 IC 与相关性都建立在它上面，符号/边界错会整页失真
2. 相关性切片——页面按 [当前因子, ...高相关因子] 取子矩阵，顺序必须与请求一致
3. 高相关因子排序——按 |ρ| 降序、排除自身（用于「这只因子是不是别人的复制品」）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.engine.factor_report import service


@pytest.mark.parametrize(
    "reverse,expected",
    [(False, 1.0), (True, -1.0)],
)
def test_spearman_单调情形(reverse, expected):
    import numpy as np

    n = 25  # _spearman 要求 ≥20 个样本（少于该数视为不可靠）
    a = np.arange(n, dtype=float)
    b = a[::-1].copy() if reverse else a * 3.0 + 1.0

    assert service._spearman(a, b) == pytest.approx(expected)


def test_spearman_常量序列返回_nan():
    import numpy as np

    v = service._spearman(np.arange(30, dtype=float), np.full(30, 7.0))
    assert v != v  # NaN —— 常量序列的秩相关未定义，必须判在转秩之前


def test_spearman_样本过少返回_nan():
    import numpy as np

    v = service._spearman(np.array([1.0, 2.0]), np.array([2.0, 1.0]))
    assert v != v


def test_rank_与_scipy_口径一致():
    import numpy as np

    x = np.array([3.0, 1.0, 2.0, 5.0, 4.0])
    assert service._rank(x).tolist() == [3.0, 1.0, 2.0, 5.0, 4.0]


# ── 快照切片（monkeypatch 掉快照读取，避免依赖真实数据文件）──────────────

_FAKE_SNAPSHOT = {
    "meta": {"horizon": "fwd_ret_5"},
    "factors": [
        {"name": "f1", "library": "alpha158", "ic_mean": 0.01},
        {"name": "f2", "library": "gtja191", "ic_mean": -0.02},
        {"name": "f3", "library": "alpha101", "ic_mean": 0.03},
    ],
    "correlation": {
        "factors": ["f1", "f2", "f3"],
        "matrix": [
            [1.0, -0.9, 0.2],
            [-0.9, 1.0, -0.1],
            [0.2, -0.1, 1.0],
        ],
    },
}


@pytest.fixture()
def fake_snapshot(monkeypatch):
    monkeypatch.setattr(service, "load_snapshot", lambda: json.loads(json.dumps(_FAKE_SNAPSHOT)))


def test_相关性切片保持请求顺序(fake_snapshot):
    res = service.correlation_slice(["f3", "f1"])

    assert res["available"] is True
    assert res["factors"] == ["f3", "f1"]           # 顺序 = 请求顺序（页面靠它对齐标签）
    assert res["matrix"][0][1] == pytest.approx(0.2)
    assert res["matrix"][1][0] == pytest.approx(0.2)


def test_相关性切片忽略不存在的因子(fake_snapshot):
    res = service.correlation_slice(["f2", "不存在"])

    assert res["factors"] == ["f2"]
    assert len(res["matrix"]) == 1


def test_高相关因子按绝对值降序且排除自身(fake_snapshot):
    res = service.top_correlated("f1", top=5)

    assert [x["name"] for x in res] == ["f2", "f3"]   # |-0.9| > |0.2|
    assert res[0]["corr"] == pytest.approx(-0.9)


def test_快照缺失时返回未生成(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(service, "snapshot_path", lambda: tmp_path / "nope.json")

    assert service.load_snapshot() is None
    res = service.correlation_slice(["f1"])
    assert res["available"] is False
    assert "尚未生成" in res["reason"]
