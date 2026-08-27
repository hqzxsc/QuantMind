"""quanthk_daily_sync K线来源接线测试。

口径铁律：daily_forward 由 akshare 独占写入（不复权），雅虎段必须
skip_kline=True；显式只勾 daily_forward 时不触发雅虎其它数据段。
"""

from __future__ import annotations

import pytest

import backend.scripts.quanthk_daily_sync as qds


@pytest.fixture()
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict]]:
    """把雅虎全量/akshare K线/L1生成器替换成记录调用参数的桩。"""
    calls: dict[str, list[dict]] = {"yahoo": [], "kline": [], "l1": [], "south_src": [], "south": []}

    def fake_yahoo(market: str, **kwargs):
        calls["yahoo"].append({"market": market, **kwargs})
        return {"market": market}

    def fake_kline(**kwargs):
        calls["kline"].append(kwargs)
        return {"rows": 1}

    def fake_l1(market: str, **kwargs):
        calls["l1"].append({"market": market, **kwargs})
        return {"status": "ok", "partitions_written": 0}

    monkeypatch.setattr(qds, "_yahoo_run", fake_yahoo)
    monkeypatch.setattr(
        "backend.scripts.quanthk_akshare_kline.sync", fake_kline
    )
    monkeypatch.setattr(
        "backend.scripts.build_ml_l1_dataset.build_l1", fake_l1
    )

    def fake_south_src(days: int):
        calls["south_src"].append({"days": days})
        return {"checked": 0, "synced": 0}

    def fake_south():
        calls["south"].append({})
        return {"status": "ok", "partitions_written": 0}

    monkeypatch.setattr(qds, "_south_source_result", fake_south_src)
    monkeypatch.setattr(
        "backend.scripts.build_ml_l1_dataset.build_south_factors", fake_south
    )
    return calls


def test_full_run_uses_akshare_for_kline_and_yahoo_without_it(recorded):
    # Act：未勾选任何数据集（定时全量路径）
    result = qds.run(days=5)

    # Assert：雅虎跳过K线；akshare 补 K线；L1 数据集跟随刷新
    assert result["yahoo"]["market"] == "HK"
    assert recorded["yahoo"] and recorded["yahoo"][0]["skip_kline"] is True
    assert len(recorded["kline"]) == 1
    assert recorded["kline"][0]["days"] == 5
    assert result["l1_dataset"]["status"] == "ok"
    assert len(recorded["l1"]) == 1 and recorded["l1"][0]["market"] == "hong_kong"
    assert recorded["south_src"] and recorded["south"]


def test_only_daily_forward_selected_skips_yahoo_meta_sections(recorded):
    # Act：仅勾选日线
    result = qds.run(days=3, datasets=["daily_forward"])

    # Assert：不跑雅虎任何段，只有 akshare K线
    assert "yahoo" not in result
    assert len(recorded["yahoo"]) == 0
    assert len(recorded["kline"]) == 1
    assert recorded["kline"][0]["days"] == 3


def test_yahoo_datasets_selection_keeps_skip_kline_true(recorded):
    # Act：勾选雅虎独有数据段（估值快照）
    result = qds.run(days=5, datasets=["valuation"])

    # Assert：雅虎段执行且强制 skip_kline；不触发 akshare K线
    assert result["yahoo"]["market"] == "HK"
    assert recorded["yahoo"][0]["skip_kline"] is True
    assert recorded["kline"] == []


def test_only_l1_selected_refreshes_dataset_without_other_sections(recorded):
    # Act：仅勾选 L1 因子数据集
    result = qds.run(days=5, datasets=["l1_factors"])

    # Assert：只触发 L1 刷新，不拉雅虎/K线
    assert result["l1_dataset"]["status"] == "ok"
    assert recorded["yahoo"] == [] and recorded["kline"] == []


def test_only_south_selected_skips_other_sections(recorded):
    # Act：仅勾选南向因子数据集
    result = qds.run(days=5, datasets=["south_factors"])

    # Assert：只刷南向因子，不拉雅虎/K线/L1
    assert result["south_factors"]["status"] == "ok"
    assert recorded["yahoo"] == [] and recorded["kline"] == [] and recorded["l1"] == []


def test_akshare_kline_failure_does_not_break_full_sync(monkeypatch):
    # Arrange：akshare 抛异常也要给出行结构的错误信息；L1 生成器打桩避免真算
    monkeypatch.setattr(qds, "_yahoo_run", lambda m, **kw: {"market": m})

    def boom(**kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr("backend.scripts.quanthk_akshare_kline.sync", boom)
    monkeypatch.setattr(
        "backend.scripts.build_ml_l1_dataset.build_l1",
        lambda market, **kw: {"status": "ok"},
    )
    monkeypatch.setattr(qds, "_south_source_result", lambda days=5: {"checked": 0})
    monkeypatch.setattr(
        "backend.scripts.build_ml_l1_dataset.build_south_factors",
        lambda: {"status": "ok"},
    )

    # Act
    result = qds.run(days=5)

    # Assert
    assert result["akshare_kline"] == {"error": "upstream down"}
