#!/usr/bin/env python3
"""期货数据同步入口 — 按勾选数据集分发到 akshare 期货同步脚本。

支持的数据集（与后台 catalog 对应）:
  daily_forward     期货/贵金属日K  → foreign_daily + cn_daily + sge_daily
  futures_realtime  实时行情快照    → foreign_realtime + cn_realtime

用法:
  python backend/scripts/quantfutures_daily_sync.py --days 5
  python backend/scripts/quantfutures_daily_sync.py --datasets daily_forward,futures_realtime
"""

from __future__ import annotations

import sys
from typing import Any

# catalog 数据集名 → akshare_futures_sync 的 field（一个数据集可对应多个数据段）
DATASET_FIELDS: dict[str, list[str]] = {
    "daily_forward": ["foreign_daily", "cn_daily", "sge_daily"],
    "futures_realtime": ["foreign_realtime", "cn_realtime"],
}


def _refresh_l1_dataset(result: dict) -> None:
    """K线更新后增量重算 L1 因子日频分区（训练直读数据集）。"""
    try:
        from backend.scripts.build_ml_l1_dataset import build_l1

        result["l1_dataset"] = build_l1("futures")
    except Exception as exc:  # noqa: BLE001
        result["l1_dataset"] = {"status": "error", "error": str(exc)}


def run(*, days: int = 5, datasets: list[str] | None = None, **kwargs: Any) -> dict:
    """同步期货数据。datasets 为勾选的 catalog 数据集名；None 时全量同步。"""
    from backend.scripts.akshare_futures_sync import sync as ak_futures_sync

    if not datasets:
        result = ak_futures_sync("all")
        if not isinstance(result, dict):
            result = {"result": result}
        # L1 因子直读数据集随日K落盘后刷新（与港股同口径）
        _refresh_l1_dataset(result)
        return {"market": "futures", "days": days, "result": result}

    fields: list[str] = []
    for ds in datasets:
        fields.extend(DATASET_FIELDS.get(ds, []))
    if not fields:
        return {"market": "futures", "days": days, "datasets": datasets, "result": {}}

    result = {}
    for field in fields:
        result[field] = ak_futures_sync(field)

    # L1 因子日频分区（训练直读数据集，随 daily_forward 增量刷新）
    if "daily_forward" in datasets or "l1_factors" in datasets:
        _refresh_l1_dataset(result)
    return {"market": "futures", "days": days, "datasets": datasets, "result": result}


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="期货数据同步")
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--datasets", default=None, help="逗号分隔数据集名")
    args, _ = parser.parse_known_args()

    ds = [d.strip() for d in args.datasets.split(",") if d.strip()] if args.datasets else None
    result = run(days=args.days, datasets=ds)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
