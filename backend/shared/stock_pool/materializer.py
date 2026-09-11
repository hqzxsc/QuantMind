"""全局股票池 - 物化层。

职责：
1. 大池（成员数 > MEMBER_TABLE_MAX）落 parquet 快照，供 resolver / 训练容器读取；
2. 把任意池物化成 Qlib 可识别的 `instruments/pool_<code>.txt`，
   使回测与 strategy_lab 能用同一个池名（`pool:<code>`）直接解析。

不引入 engine 依赖：本模块只做「符号列表 → 文件」的纯函数式转换。
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from collections.abc import Sequence

from .constants import INSTRUMENT_FILE_PREFIX
from .normalize import normalize_market, normalize_to_qlib
from .schemas import PoolMember

logger = logging.getLogger(__name__)

SNAPSHOT_COLUMNS = ("symbol", "name", "weight", "industry")


def snapshot_dir() -> Path:
    return Path(os.getenv("QM_STOCK_POOL_SNAPSHOT_DIR", "/data/stock_pool"))


def qlib_data_dir() -> Path:
    """Qlib 数据根目录（与 engine 的 QLIB_PROVIDER_URI 默认值一致）。"""
    return Path(os.getenv("QLIB_PROVIDER_URI", "db/qlib_data"))


# ---------------------------------------------------------------------------
# parquet 快照
# ---------------------------------------------------------------------------
def snapshot_path(pool_id: str, version: int) -> Path:
    return snapshot_dir() / f"{pool_id}_v{int(version)}.parquet"


def write_snapshot(pool_id: str, version: int, members: Sequence[PoolMember]) -> str:
    """写 parquet 快照，返回绝对路径。"""
    import pandas as pd

    target = snapshot_path(pool_id, version)
    target.parent.mkdir(parents=True, exist_ok=True)

    rows = [
        {
            "symbol": m.symbol,
            "name": m.name,
            "weight": m.weight,
            "industry": m.industry,
        }
        for m in members
    ]
    df = pd.DataFrame(rows, columns=list(SNAPSHOT_COLUMNS))
    df.to_parquet(target, index=False)

    logger.info(
        "股票池快照已写入 pool_id=%s version=%s rows=%d path=%s",
        pool_id,
        version,
        len(df),
        target,
    )
    return str(target)


def read_snapshot(path: str | Path) -> list[PoolMember]:
    """读 parquet 快照。文件缺失时返回空列表并给出警告（不抛异常）。"""
    import pandas as pd

    fp = Path(path)
    if not fp.exists():
        logger.warning("股票池快照不存在: %s", fp)
        return []

    df = pd.read_parquet(fp, columns=list(SNAPSHOT_COLUMNS))
    members: list[PoolMember] = []
    for row in df.to_dict("records"):
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        members.append(
            PoolMember(
                symbol=symbol,
                name=row.get("name"),
                weight=_safe_float(row.get("weight")),
                industry=row.get("industry"),
            )
        )
    return members


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        import math

        f = float(value)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Qlib instruments 物化
# ---------------------------------------------------------------------------
def instrument_file(pool_code: str) -> Path:
    return qlib_data_dir() / "instruments" / f"{INSTRUMENT_FILE_PREFIX}{pool_code}.txt"


def write_instruments(
    pool_code: str,
    symbols: Sequence[str],
    market: str = "CN",
    *,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
) -> str | None:
    """写 Qlib instruments 文件，格式 `sh600036\\tSTART\\tEND`。

    symbols 为库内后缀式；写文件时转 Qlib 小写前缀口径。
    """
    if not symbols:
        logger.warning("股票池 %s 成员为空，跳过 instruments 物化", pool_code)
        return None

    mk = normalize_market(market)
    start = str(start_date or "1990-01-01")
    end = str(end_date or "2100-01-01")

    target = instrument_file(pool_code)
    target.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    seen: set[str] = set()
    for sym in symbols:
        qs = normalize_to_qlib(sym, mk)
        if not qs or qs in seen:
            continue
        seen.add(qs)
        lines.append(f"{qs}\t{start}\t{end}")

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(
        "股票池 instruments 已物化 %s: %d symbols -> %s",
        pool_code,
        len(lines),
        target,
    )
    return str(target)


def read_instruments(pool_code: str) -> list[str]:
    """读回 instruments 文件（返回 Qlib 口径符号）。"""
    fp = instrument_file(pool_code)
    if not fp.exists():
        return []
    out: list[str] = []
    for line in fp.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split("\t")
        if parts and parts[0]:
            out.append(parts[0])
    return out


def materialize_snapshot(
    snapshot,
    *,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
) -> str | None:
    """把 `PoolSnapshot` 物化成 Qlib instruments 文件，返回绝对路径。

    - `unfiltered`（对应旧 `universe='all'`）→ 返回 None，表示**不过滤**；
    - 空池 → 同样返回 None（**调用方须自行决定是否报错**，不要静默当成不过滤）；
    - 正常池 → 写 `instruments/pool_<code>.txt` 并返回路径。

    回测 / 训练 / 推理等消费方共用，避免各自实现物化逻辑。
    """
    if snapshot is None or snapshot.unfiltered or snapshot.is_empty:
        return None
    raw_code = getattr(snapshot, "code", None) or getattr(snapshot, "pool_id", "") or "pool"
    # 池 code 可能含非法文件名字符，保守清洗
    safe_code = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(raw_code))
    # 文件名带上版本/校验和：并发回测同一池的不同版本（pool:x@1 vs @2）或
    # 发布瞬间跑着的旧任务不会互相覆盖 instruments 文件，路径即版本快照。
    tag = (
        f"v{snapshot.version}"
        if getattr(snapshot, "version", None)
        else f"h{(snapshot.checksum or 'draft')[:10]}"
    )
    return write_instruments(
        f"{safe_code}__{tag}",
        list(snapshot.symbols),
        getattr(snapshot, "market", "CN"),
        start_date=start_date,
        end_date=end_date,
    )
