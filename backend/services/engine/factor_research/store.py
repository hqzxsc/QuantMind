"""因子研究 —— 快照（artifact）读取层。

产物由 ``backend/scripts/build_factor_research.py`` 写入
``<quantdb>/factor_research/``；本层只读 + 短 TTL 缓存，不触发计算。
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import pandas as pd

TTL_SECONDS = 600  # 快照读取缓存（构建脚本重跑后 10 分钟内自动失效）
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _sanitize(o):
    """NaN/Inf → None（快照 JSON 由 pandas 写出，可能含非有限值；FastAPI 序列化会 500）。"""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sanitize(v) for v in o]
    return o


def artifact_dir() -> Path:
    from backend.shared.quantdb_paths import resolve_quantdb_dir

    return resolve_quantdb_dir() / "factor_research"


def _load(name: str, loader, key_suffix: str = ""):
    path = artifact_dir() / name
    key = str(path) + key_suffix  # 带过滤参数（同文件不同 filters 不能互相命中缓存）
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL_SECONDS:
            return hit[1]
    obj = loader(path)
    with _lock:
        _cache[key] = (now, obj)
    return obj


def load_json(name: str):
    def _rd(p: Path):
        if not p.exists():
            return None
        return _sanitize(json.loads(p.read_text(encoding="utf-8")))

    return _load(name, _rd)


def load_parquet(name: str, **kwargs) -> pd.DataFrame | None:
    def _rd(p: Path):
        if not p.exists():
            return None
        return pd.read_parquet(p, **kwargs)

    return _load(name, _rd, repr(sorted(kwargs.items(), key=str)))


def metrics() -> dict:
    return load_json("metrics.json") or {"leaderboard": [], "metrics": {}, "meta": {}}


def holdings() -> dict:
    return load_json("holdings_latest.json") or {"date": None, "holdings": {}}


def ic_table() -> pd.DataFrame | None:
    return load_parquet("ic.parquet")


def nav_table() -> pd.DataFrame | None:
    return load_parquet("nav.parquet")


def corr_table() -> pd.DataFrame | None:
    return load_parquet("corr.parquet")


def benchmark_table() -> pd.DataFrame | None:
    return load_parquet("benchmarks.parquet")


def scores_for(codes: list[str]) -> pd.DataFrame | None:
    """按因子过滤读取月末打分（parquet 行组过滤，避免全量加载）。"""
    return load_parquet(
        "monthly_scores.parquet", filters=[("factor_code", "in", list(codes))]
    )


def fwd_returns() -> pd.DataFrame | None:
    return load_parquet("fwd_returns.parquet")


def screening() -> dict:
    """因子筛选结果（screen_factors.py 产物；缺失时返回空结构）。"""
    return load_json("screening/factor_selection.json") or {
        "counts": {
            "candidates": 0,
            "kept": 0,
            "gated_out": 0,
            "deduped": 0,
            "total_considered": 0,
        },
        "kept": [],
        "dropped_gated": [],
        "dropped_duplicate": [],
        "gates": {},
        "cross_corr": "",
        "generated_at": None,
    }


def clear_cache() -> None:
    with _lock:
        _cache.clear()
