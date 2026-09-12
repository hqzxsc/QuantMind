#!/usr/bin/env python3
"""
从 eltdx 拉取最新交易日数据，增量更新 Qlib 本地数据。

用法:
    python eltdx_daily_update.py              # 更新最新交易日
    python eltdx_daily_update.py --date 2026-05-13  # 更新指定日期
    python eltdx_daily_update.py --date-range 2026-05-01 2026-05-13  # 更新日期范围
    python eltdx_daily_update.py --check      # 仅检查状态，不写入

Qlib 数据格式 (pyqlib 0.9.x):
    - calendars/day.txt: 每行一个交易日
    - instruments/all.txt: symbol\tstart_date\tend_date
    - features/{symbol}/{field}.day.bin: float32 little-endian，首个值为 start_index
"""

from __future__ import annotations

import argparse
import logging
import os
import struct
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("eltdx_daily_update")

# ======================================================================
# 路径配置
# ======================================================================

PROJECT_ROOT = Path(os.getenv("QLIB_PROJECT_ROOT", "/opt/quantmind"))
QLIB_DATA_DIR = PROJECT_ROOT / "db" / "qlib_data"
CALENDAR_FILE = QLIB_DATA_DIR / "calendars" / "day.txt"
INSTRUMENTS_FILE = QLIB_DATA_DIR / "instruments" / "all.txt"
FEATURES_DIR = QLIB_DATA_DIR / "features"

QlibField = str  # Qlib 特征字段名

# 从 eltdx 拉取的字段 → Qlib 字段映射
FIELD_MAP: dict[str, QlibField] = {
    "open_price": "open",
    "high_price": "high",
    "low_price": "low",
    "close_price": "close",
    "volume": "volume",
}

# eltdx 支持的日 K 线周期
ELTDX_KLINE_PERIOD = "day"


# ======================================================================
# eltdx 数据获取
# ======================================================================

def get_eltdx_client():
    """获取 eltdx TdxClient 实例"""
    from eltdx import TdxClient
    hosts_env = os.getenv("ELTDX_HOSTS")
    if hosts_env:
        hosts = [h.strip() for h in hosts_env.split(",") if h.strip()]
        return TdxClient(hosts=hosts, pool_size=2, timeout=8.0, probe_hosts=True)
    return TdxClient(pool_size=2, timeout=8.0, probe_hosts=False)


def fetch_all_a_share_codes(client) -> list[str]:
    """获取全部 A 股代码 (eltdx 格式: sh600000, sz000001)"""
    codes = []
    for ex in ("sh", "sz", "bj"):
        items = client.get_codes_all(ex)
        for item in items:
            from eltdx.protocol.unit import is_a_share_entry
            if is_a_share_entry(item.full_code):
                codes.append(item.full_code)
    return codes


def fetch_daily_kline(client, code: str) -> dict | None:
    """获取单只股票最新一根日 K 线"""
    try:
        response = client.get_kline(ELTDX_KLINE_PERIOD, code, count=1)
        if not response or not response.items:
            return None
        item = response.items[0]
        return {
            "datetime": item.time,
            "open_price": item.open_price,
            "high_price": item.high_price,
            "low_price": item.low_price,
            "close_price": item.close_price,
            "volume": item.volume * 100,  # 手 → 股
            "amount": item.amount,
        }
    except Exception as e:
        logger.debug(f"获取 {code} 日 K 失败: {e}")
        return None


def fetch_daily_kline_batch(client, codes: list[str], batch_size: int = 50) -> dict[str, dict]:
    """批量获取多只股票的日 K 线"""
    results = {}
    total = len(codes)
    for i in range(0, total, batch_size):
        batch = codes[i:i + batch_size]
        batch_results = {}
        for code in batch:
            data = fetch_daily_kline(client, code)
            if data:
                batch_results[code] = data
        results.update(batch_results)
        if (i + batch_size) % 500 == 0 or i + batch_size >= total:
            logger.info(f"  已获取 {min(i + batch_size, total)}/{total} 只股票")
    return results


# ======================================================================
# Qlib 日历操作
# ======================================================================

def load_calendar() -> list[str]:
    """加载交易日历，返回排序后的日期列表 (ISO 格式)"""
    if not CALENDAR_FILE.exists():
        return []
    return [
        line.strip()
        for line in CALENDAR_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def get_last_calendar_date() -> date | None:
    """获取最后一个交易日"""
    cal = load_calendar()
    if not cal:
        return None
    return datetime.fromisoformat(cal[-1]).date()


def append_to_calendar(trade_date: date) -> None:
    """追加新交易日到日历"""
    date_str = trade_date.isoformat()
    with CALENDAR_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{date_str}\n")
    logger.info(f"  日历已追加: {date_str}")


def get_calendar_index(trade_date: date) -> int:
    """获取日期在日历中的索引 (0-based)"""
    cal = load_calendar()
    date_str = trade_date.isoformat()
    try:
        return cal.index(date_str)
    except ValueError:
        return -1


# ======================================================================
# Qlib 特征文件操作
# ======================================================================

def get_feature_path(symbol: str, field: str) -> Path:
    """获取特征文件路径: features/{symbol}/{field}.day.bin"""
    return FEATURES_DIR / symbol.lower() / f"{field.lower()}.day.bin"


def read_feature_file(filepath: Path) -> np.ndarray | None:
    """读取特征文件，返回 data (不含 start_index)"""
    if not filepath.exists():
        return None
    size = filepath.stat().st_size
    if size < 4:
        return None
    with filepath.open("rb") as f:
        data = np.frombuffer(f.read(), dtype="<f4")
    return data[1:]  # 跳过 start_index


def get_feature_start_index(filepath: Path) -> int | None:
    """获取特征文件的起始索引"""
    if not filepath.exists():
        return None
    with filepath.open("rb") as f:
        raw = np.frombuffer(f.read(4), dtype="<f4")
    return int(raw[0])


def append_to_feature(filepath: Path, value: float, index: int) -> None:
    """
    追加单个值到特征文件。

    Qlib 格式: [start_index(float32), data0(float32), data1(float32), ...]
    如果 index > end_index + 1，中间填充 NaN。
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)

    if not filepath.exists():
        # 新文件: start_index + 单个值
        with filepath.open("wb") as f:
            np.array([np.float32(index), np.float32(value)], dtype="<f4").tofile(f)
        return

    # 读取现有数据
    with filepath.open("rb") as f:
        raw = np.frombuffer(f.read(), dtype="<f4")

    start_idx = int(raw[0])
    existing_data = raw[1:]
    end_idx = start_idx + len(existing_data) - 1

    if index <= end_idx:
        # 覆盖已有位置
        existing_data[index - start_idx] = np.float32(value)
        with filepath.open("wb") as f:
            np.hstack([np.float32(start_idx), existing_data]).astype("<f4").tofile(f)
        return

    # 追加新值，中间填充 NaN
    gap = index - end_idx - 1
    if gap > 0:
        padding = np.full(gap, np.nan, dtype="<f4")
        new_data = np.concatenate([existing_data, padding, [np.float32(value)]])
    else:
        new_data = np.concatenate([existing_data, [np.float32(value)]])

    with filepath.open("wb") as f:
        np.hstack([np.float32(start_idx), new_data]).astype("<f4").tofile(f)


def write_stock_features(symbol: str, kline_data: dict, calendar_index: int, eltdx_fields: list[str] | None = None) -> None:
    """将单只股票的 K 线数据写入 Qlib 特征文件"""
    if eltdx_fields is None:
        eltdx_fields = list(FIELD_MAP.keys())

    for eltdx_field, qlib_field in FIELD_MAP.items():
        if eltdx_field not in kline_data:
            continue
        value = kline_data[eltdx_field]
        if value is None:
            value = np.nan
        filepath = get_feature_path(symbol, qlib_field)
        append_to_feature(filepath, float(value), calendar_index)


# ======================================================================
# Qlib instruments 操作
# ======================================================================

def load_instruments() -> dict[str, list[tuple[date, date]]]:
    """加载 instruments 文件: {symbol: [(start, end), ...]}"""
    if not INSTRUMENTS_FILE.exists():
        return {}
    result = {}
    for line in INSTRUMENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        symbol = parts[0].lower()
        start = datetime.fromisoformat(parts[1]).date()
        end = datetime.fromisoformat(parts[2]).date()
        result.setdefault(symbol, []).append((start, end))
    return result


def update_instrument_end_date(symbol: str, new_end_date: date) -> None:
    """更新单只股票的结束日期"""
    inst = load_instruments()
    symbol_lower = symbol.lower()
    if symbol_lower not in inst:
        return
    # 更新最后一个区间
    intervals = inst[symbol_lower]
    intervals[-1] = (intervals[-1][0], new_end_date)
    inst[symbol_lower] = intervals
    _write_instruments(inst)


def add_new_instrument(symbol: str, start_date: date, end_date: date) -> None:
    """添加新股票到 instruments"""
    inst = load_instruments()
    symbol_lower = symbol.lower()
    inst[symbol_lower] = [(start_date, end_date)]
    _write_instruments(inst)
    logger.info(f"  新增标的: {symbol} ({start_date} ~ {end_date})")


def _write_instruments(inst: dict[str, list[tuple[date, date]]]) -> None:
    """写回 instruments 文件"""
    INSTRUMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for symbol, intervals in sorted(inst.items()):
        for start, end in intervals:
            lines.append(f"{symbol}\t{start.isoformat()}\t{end.isoformat()}")
    INSTRUMENTS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ======================================================================
# 主流程
# ======================================================================

def run_single_day(trade_date: date, dry_run: bool = False) -> dict:
    """
    更新指定交易日的 Qlib 数据。

    流程:
    1. 检查该日期是否已在日历中
    2. 从 eltdx 拉取全市场日 K
    3. 更新日历、特征、instruments
    """
    result = {
        "date": trade_date.isoformat(),
        "status": "pending",
        "total_stocks": 0,
        "success_count": 0,
        "new_stocks": 0,
        "skipped": False,
    }

    # 1. 检查是否已存在
    cal = load_calendar()
    date_str = trade_date.isoformat()
    if date_str in cal:
        logger.info(f"[{date_str}] 已在日历中，跳过")
        result["skipped"] = True
        result["status"] = "already_exists"
        return result

    # 获取应该使用的索引
    calendar_index = len(cal)

    if dry_run:
        logger.info(f"[DRY RUN] 将更新 {date_str}，索引={calendar_index}")
        result["status"] = "dry_run"
        return result

    logger.info(f"[{date_str}] 开始更新，索引={calendar_index}")

    # 2. 从 eltdx 拉取数据
    start_time = time.time()
    client = get_eltdx_client()
    all_codes = fetch_all_a_share_codes(client)
    result["total_stocks"] = len(all_codes)
    logger.info(f"  获取到 {len(all_codes)} 只 A 股")

    kline_data = fetch_daily_kline_batch(client, all_codes)
    elapsed = time.time() - start_time
    logger.info(f"  成功获取 {len(kline_data)} 只股票数据 (耗时 {elapsed:.1f}s)")

    # 3. 更新日历
    append_to_calendar(trade_date)

    # 4. 更新每只股票的特征
    inst = load_instruments()
    success_count = 0
    new_stocks = 0

    for code, data in kline_data.items():
        code_lower = code.lower()
        is_new = code_lower not in inst

        # 写入特征
        write_stock_features(code, data, calendar_index)
        success_count += 1

        # 更新或新增 instruments
        if is_new:
            add_new_instrument(code, trade_date, trade_date)
            new_stocks += 1
        else:
            update_instrument_end_date(code, trade_date)

    result["success_count"] = success_count
    result["new_stocks"] = new_stocks
    result["status"] = "done"

    logger.info(f"[{date_str}] 完成: {success_count}/{len(all_codes)} 成功, {new_stocks} 新股")
    return result


def run_date_range(start: date, end: date, dry_run: bool = False) -> list[dict]:
    """更新一个日期范围内的所有缺失交易日"""
    results = []
    current = start
    while current <= end:
        # 跳过周末
        if current.weekday() < 5:  # Mon-Fri
            result = run_single_day(current, dry_run=dry_run)
            results.append(result)
        current += timedelta(days=1)
    return results


def check_status():
    """检查当前数据状态"""
    cal = load_calendar()
    last_date = get_last_calendar_date()
    inst = load_instruments()

    today = date.today()

    print("\n" + "=" * 60)
    print("QuantMind Qlib 数据状态")
    print("=" * 60)
    print(f"  数据目录: {QLIB_DATA_DIR}")
    print(f"  交易日数: {len(cal)}")
    print(f"  最后交易日: {last_date}")
    print(f"  距离今天: {(today - last_date).days if last_date else 'N/A'} 天")
    print(f"  标的数量: {len(inst)}")
    print()

    # 统计特征文件
    total_features = 0
    for d in FEATURES_DIR.iterdir():
        if d.is_dir():
            total_features += len(list(d.glob("*.bin")))
    print(f"  特征文件数: {total_features}")

    # 检查最近几天的数据可用性
    if cal:
        last_5 = cal[-5:]
        print("\n  最近 5 个交易日:")
        for d in last_5:
            # 随机检查一只股票
            sample = list(inst.keys())[:1]
            if sample:
                sym = sample[0]
                close_path = get_feature_path(sym, "close")
                if close_path.exists():
                    data = read_feature_file(close_path)
                    if data is not None and len(data) > 0:
                        idx = cal.index(d)
                        if idx < len(data):
                            val = data[idx]
                            print(f"    {d}: {sym} close={val:.2f}")
                        else:
                            print(f"    {d}: 数据未写入 (索引 {idx} >= {len(data)})")
                    else:
                        print(f"    {d}: 特征文件为空")
                else:
                    print(f"    {d}: 特征文件不存在")

    print("\n" + "=" * 60)


# ======================================================================
# CLI
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description="从 eltdx 增量更新 Qlib 日 K 数据")
    parser.add_argument("--date", type=str, help="指定日期 (YYYY-MM-DD)")
    parser.add_argument("--date-range", nargs=2, metavar=("START", "END"), help="更新日期范围")
    parser.add_argument("--check", action="store_true", help="仅检查状态")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行，不写入数据")
    args = parser.parse_args()

    if args.check:
        check_status()
        return

    if args.date_range:
        start = datetime.fromisoformat(args.date_range[0]).date()
        end = datetime.fromisoformat(args.date_range[1]).date()
        logger.info(f"更新日期范围: {start} ~ {end}")
        results = run_date_range(start, end, dry_run=args.dry_run)
        done = sum(1 for r in results if r["status"] == "done")
        skipped = sum(1 for r in results if r["skipped"])
        logger.info(f"完成: {done} 天更新成功, {skipped} 天跳过")
        return

    if args.date:
        trade_date = datetime.fromisoformat(args.date).date()
    else:
        # 默认使用今天
        trade_date = date.today()

    result = run_single_day(trade_date, dry_run=args.dry_run)
    logger.info(f"结果: {result}")


if __name__ == "__main__":
    main()
