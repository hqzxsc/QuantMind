"""quantdb_local_scan 本地扫描建立同步状态库的单元测试。

场景：用户离线下载数据落地后（配置 QuantDB API key 之前），本地扫描把
md5/sha256/size 登记进状态库；之后 sync 的增量 fast-path（按 key 查库 +
path 存在即跳过）直接命中，不整库重下。
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import pytest

from backend.scripts.quantdb_daily_sync import _state_path
from backend.scripts.quantdb_local_scan import (
    hash_parquet,
    iter_dataset_files,
    scan_local_data,
)

pytestmark = pytest.mark.skipif(pd is None, reason="pandas 未安装")


def _write_parquet(path: Path, rows: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"symbol": [f"S{i}" for i in range(rows)], "close": [1.0] * rows}).to_parquet(path)


@pytest.fixture()
def offline_root(tmp_path, monkeypatch):
    """构造一个迷你离线数据根：V2 分区 + V1 symbol + single + 发布清单。"""
    root = tmp_path / "quantdb"
    # V2 partition：两个合法分区 + 一个截断损坏的分区
    _write_parquet(root / "1_kline_data" / "daily_forward" / "dt=20260801" / "data.parquet")
    _write_parquet(root / "1_kline_data" / "daily_forward" / "dt=20260803" / "data.parquet")
    corrupt = root / "1_kline_data" / "daily_forward" / "dt=20260804" / "data.parquet"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"PAR1" + b"\x00" * 32)  # 头对尾不对，非法 parquet
    # partition 数据集目录下与分区并存的按标的采集文件（云端不存在，应被忽略）
    _write_parquet(root / "1_kline_data" / "daily_forward" / "000001.SZ.parquet")
    # V1 symbol
    _write_parquet(root / "3_financial_data" / "balance" / "000001.SZ.parquet")
    # single
    _write_parquet(root / "2_base_sector" / "trading_calendar" / "trading_days.parquet")
    # 发布清单（releases 目录）
    manifest = {
        "release_id": "20260804.1",
        "objects": [
            {"dataset": "daily_forward", "key": "1_kline_data/daily_forward/dt=20260804/data.parquet"},
            {"dataset": "trading_calendar", "key": "2_base_sector/trading_calendar/trading_days.parquet"},
        ],
    }
    rel = root / "releases" / "20260804.1" / "manifest.json"
    rel.parent.mkdir(parents=True, exist_ok=True)
    rel.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(root))
    monkeypatch.setenv("QUANTDB_STATE_DIR", str(tmp_path / "state"))
    return root


def test_hash_parquet_valid_and_invalid(tmp_path):
    good = tmp_path / "good.parquet"
    _write_parquet(good)
    h = hash_parquet(good)
    assert h is not None
    md5_hex, sha_hex, size = h
    assert len(md5_hex) == 32 and len(sha_hex) == 64 and size > 8

    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"PAR1" + b"\x00" * 10)
    assert hash_parquet(bad) is None


def test_iter_dataset_files_skips_collection_symbol_files(offline_root):
    files = iter_dataset_files(offline_root, "1_kline_data/daily_forward", "partition")
    names = [f.parent.name for f in files]
    assert names == ["dt=20260801", "dt=20260803", "dt=20260804"]
    # 采集侧按标的文件不是云端对象，不能出现在分区枚举里
    assert all(f.name == "data.parquet" for f in files)


def test_iter_dataset_files_flat_shard_fallback(offline_root):
    """tick_data 无 dt= 目录、按日分片平铺 → 回退平铺枚举。"""
    shard = offline_root / "1_kline_data" / "tick_data" / "000001_SZ_20260720.parquet"
    _write_parquet(shard)
    files = iter_dataset_files(offline_root, "1_kline_data/tick_data", "partition")
    assert [f.name for f in files] == ["000001_SZ_20260720.parquet"]


def test_scan_registers_state_dbs_and_releases(offline_root):
    summary = scan_local_data(
        root=offline_root,
        datasets=["daily_forward", "balance", "trading_calendar"],
    )
    assert summary["registered"] == 4  # 2 分区 + 1 symbol + 1 single
    assert summary["invalid_files"] == 1  # 截断分区不登记
    assert summary["same_root"] is True
    assert summary["per_dataset"]["daily_forward"]["registered"] == 2
    assert summary["releases"]["latest_by_dataset"]["daily_forward"] == "20260804.1"

    # 两份状态库（QuantMind 命名规则 + SDK <root>/quantdb_sync.sqlite）都写入
    qm_db = Path(summary["state_dbs"]["quantmind"])
    assert qm_db == _state_path(offline_root) and qm_db.exists()
    sdk_db = Path(summary["state_dbs"]["sdk"])
    assert sdk_db.exists()

    for db in (qm_db, sdk_db):
        conn = sqlite3.connect(str(db))
        rows = {
            r[0]: r for r in conn.execute(
                "SELECT key, etag, sha256, size, path, layout, dataset FROM objects"
            )
        }
        assert "1_kline_data/daily_forward/dt=20260801/data.parquet" in rows
        row = rows["1_kline_data/daily_forward/dt=20260801/data.parquet"]
        assert len(row[1]) == 32 and len(row[2]) == 64  # md5(etag) + sha256 都已登记
        assert row[3] > 0 and Path(row[4]).exists()  # fast-path: path 存在
        assert row[5] == "v2_daily_partition" and row[6] == "daily_forward"
        # 云端不存在的采集文件未被登记
        assert "1_kline_data/daily_forward/000001.SZ.parquet" not in rows
        releases = dict(conn.execute("SELECT dataset, release_id FROM releases"))
        assert releases["daily_forward"] == "20260804.1"
        conn.close()


def test_scan_repeat_reuses_hashes_without_force(offline_root):
    first = scan_local_data(root=offline_root, datasets=["daily_forward"])
    assert first["reused"] == 0
    second = scan_local_data(root=offline_root, datasets=["daily_forward"])
    assert second["reused"] == 2
    assert second["registered"] == first["registered"]


def test_scan_rejects_unknown_dataset(offline_root):
    with pytest.raises(ValueError, match="未知数据集"):
        scan_local_data(root=offline_root, datasets=["nonexistent_dataset"])


def test_scan_external_root_warns(offline_root, tmp_path, monkeypatch):
    """扫描目录 ≠ 当前数据目录时给出迁移警告，但仍登记。"""
    other = tmp_path / "elsewhere"
    _write_parquet(other / "3_financial_data" / "balance" / "600519.SH.parquet")
    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(offline_root))
    summary = scan_local_data(root=other, datasets=["balance"])
    assert summary["same_root"] is False
    assert summary["registered"] == 1
    assert any("不一致" in w for w in summary["warnings"])
