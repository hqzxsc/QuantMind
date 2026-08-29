"""
Market Analysis 快照只读层（Phase 2）。

读取 pre-computed 快照（scripts/market_snapshot/compute.py 产出）：
  - {root}/latest.json + {YYYY-MM-DD}.json   —— 6 块聚合数据
  - {root}/latest.db + {YYYY-MM-DD}.db       —— 标签( tags ) + 板块市值( sector_mv )

设计：只读文件/SQLite，服务器零计算。各接口优先走本层；返回 None 时由
router 回退到实时 quantdb_feed（DuckDB 聚合），保证快照缺失也能出数。

快照根目录可通过环境变量 QM_MARKET_SNAPSHOT_DIR 覆盖，默认 {cwd}/data/market-analysis。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

_DEFAULT_ROOT = Path(os.getcwd()) / "data" / "market-analysis"


def _root() -> Path:
    env = os.getenv("QM_MARKET_SNAPSHOT_DIR")
    return Path(env).expanduser() if env else _DEFAULT_ROOT


# ---------------------------------------------------------------------------
# JSON 快照
# ---------------------------------------------------------------------------

def _snapshot_json_path(date: Optional[str]) -> Optional[Path]:
    root = _root()
    if date:
        p = root / f"{date}.json"
        return p if p.exists() else None
    latest = root / "latest.json"
    if latest.exists():
        return latest
    # 否则取最新日期文件
    files = sorted([f for f in root.glob("*.json") if f.stem != "latest"], reverse=True)
    return files[0] if files else None


def _load(date: Optional[str]) -> Optional[dict[str, Any]]:
    p = _snapshot_json_path(date)
    if not p:
        return None
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def has_snapshot(date: Optional[str] = None) -> bool:
    return _snapshot_json_path(date) is not None


def available_dates() -> list[str]:
    """列出所有可用快照日期（YYYY-MM-DD，按降序；不含 latest.json）。"""
    root = _root()
    if not root.is_dir():
        return []
    dates = []
    for f in root.glob("????-??-??.json"):
        dates.append(f.stem)
    return sorted(dates, reverse=True)


def full(date: Optional[str] = None) -> Optional[dict]:
    """返回整份快照（供 SSE 一次性分段推送 / 调试用）。"""
    return _load(date)


# ---------------------------------------------------------------------------
# SQLite 标签库
# ---------------------------------------------------------------------------

def _open_tags_db(date: Optional[str]):
    root = _root()
    name = f"{date}.db" if date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else "latest.db"
    p = root / name
    if not p.exists():
        return None
    try:
        return sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 各数据块
# ---------------------------------------------------------------------------

def status(date: Optional[str] = None) -> Optional[dict]:
    snap = _load(date)
    if not snap:
        return None
    return {
        "has_snapshot": True,
        "trade_date": snap.get("trade_date"),
        "generated_at": snap.get("generated_at"),
        "indices_count": len(snap.get("indices") or []),
        "heatmap_shenwan_count": len((snap.get("heatmap") or {}).get("shenwan") or []),
        "stock_flow_count": len(snap.get("stock_flow") or []),
    }


def indices(date: Optional[str] = None) -> Optional[list]:
    snap = _load(date)
    return (snap or {}).get("indices") or None


def breadth(date: Optional[str] = None) -> Optional[dict]:
    snap = _load(date)
    return (snap or {}).get("breadth") or None


def heatmap(category: str = "shenwan", date: Optional[str] = None) -> Optional[dict]:
    """优先 SQLite sector_mv（支持 ?date= 历史），否则 JSON 快照。"""
    db = _open_tags_db(date)
    if db is not None:
        try:
            rows = db.execute(
                "SELECT name, value, pct_change, leader, leader_pct FROM sector_mv WHERE category=? ORDER BY value DESC",
                (category,),
            ).fetchall()
            items = [{"name": r[0], "value": r[1], "pct_change": r[2], "leader": r[3], "leader_pct": r[4]} for r in rows]
            if items:
                meta = db.execute("SELECT value FROM meta WHERE key='trade_date'").fetchone()
                return {"trade_date": meta[0] if meta else (date or None), "category": category, "items": items}
        finally:
            db.close()
    snap = _load(date)
    hm = (snap or {}).get("heatmap") or {}
    items = hm.get(category)
    if items:
        return {"trade_date": (snap or {}).get("trade_date") or date, "category": category, "items": items}
    return None


def stock_flow(limit: int = 20, date: Optional[str] = None) -> Optional[list]:
    snap = _load(date)
    items = (snap or {}).get("stock_flow")
    return (items or [])[:limit] if items else None


def stock_flow_full(date: Optional[str] = None) -> Optional[list]:
    snap = _load(date)
    return (snap or {}).get("stock_flow_full") or None


def sankey(date: Optional[str] = None) -> Optional[dict]:
    snap = _load(date)
    s = (snap or {}).get("sankey")
    if s and (s.get("nodes") or s.get("links")):
        return s
    return None


def money_flow_period(period: str, dimension: str, category: str, limit: int,
                      date: Optional[str] = None) -> Optional[list]:
    snap = _load(date)
    key = f"{period.lower()}_{dimension}_{category}"
    items = ((snap or {}).get("money_flow_periods") or {}).get(key)
    return (items or [])[:limit] if items else None


def trade_date(date: Optional[str] = None) -> Optional[str]:
    snap = _load(date)
    return (snap or {}).get("trade_date")


# ---------------------------------------------------------------------------
# 标签双向查询（SQLite）
# ---------------------------------------------------------------------------

def tag_stats(limit: int = 30, date: Optional[str] = None) -> Optional[dict]:
    db = _open_tags_db(date)
    if db is None:
        return None
    try:
        hot = db.execute(
            "SELECT sector_name, sector_type, COUNT(*) AS n FROM tags "
            "GROUP BY sector_name, sector_type ORDER BY n DESC LIMIT ?", (limit,)
        ).fetchall()
        agg = db.execute(
            "SELECT COUNT(DISTINCT sector_name), COUNT(DISTINCT symbol), COUNT(*) FROM tags"
        ).fetchone()
        avg_row = db.execute("SELECT AVG(n) FROM (SELECT COUNT(*) n FROM tags GROUP BY symbol)").fetchone()
        max_row = db.execute("SELECT MAX(n) FROM (SELECT COUNT(*) n FROM tags GROUP BY symbol)").fetchone()
        meta = db.execute("SELECT value FROM meta WHERE key='trade_date'").fetchone()
        return {
            "trade_date": meta[0] if meta else (date or None),
            "total_sectors": int(agg[0] or 0),
            "total_stocks": int(agg[1] or 0),
            "avg_tags_per_stock": round(float(avg_row[0] or 0), 1),
            "max_tags_per_stock": int(max_row[0] or 0),
            "total_relations": int(agg[2] or 0),
            "hot_tags": [{"name": r[0], "type": r[1] or "通用标签", "count": int(r[2])} for r in hot],
        }
    finally:
        db.close()


def tags_by_stock(symbol: str, date: Optional[str] = None) -> Optional[dict]:
    db = _open_tags_db(date)
    if db is None:
        return None
    try:
        rows = db.execute(
            "SELECT DISTINCT sector_name, sector_type FROM tags WHERE symbol=?", (symbol,)
        ).fetchall()
        grouped: dict[str, list[str]] = {}
        for name, stype in rows:
            grouped.setdefault(stype or "其他", []).append(name)
        return {"symbol": symbol, "tags": grouped}
    finally:
        db.close()


def stocks_by_tag(tag: str, limit: int, date: Optional[str] = None) -> Optional[dict]:
    db = _open_tags_db(date)
    if db is None:
        return None
    try:
        rows = db.execute(
            "SELECT DISTINCT symbol, sector_type FROM tags WHERE sector_name=? LIMIT ?", (tag, limit)
        ).fetchall()
        items = [{"symbol": s, "sector_type": t} for s, t in rows]
        return {"tag": tag, "items": items}
    finally:
        db.close()