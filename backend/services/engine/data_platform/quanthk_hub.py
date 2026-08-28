"""QuantHK 数据中枢 — 港股本地 parquet 读取的单一入口。

复用 QuantDBDataHub 的查询基础设施（DuckDB 连接管理、视图挂载、K线/列
标准化），仅替换数据目录与视图命名空间（qhk_*），避免与 A 股/美股视图串扰。

数据目录：环境变量 QM_QUANTHK_DATA_DIR，默认 data/quanthk/。
目录结构与 QuantDB 对齐（日线 / 指数 / 估值 / 财务 / 标的池）。
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

from backend.services.engine.data_platform.quantdb_hub import (
    QuantDBDataHub,
    _dt_conditions,
)

logger = logging.getLogger(__name__)

_QUANTHK_DATA_DIR_ENV = "QM_QUANTHK_DATA_DIR"
_QUANTHK_DEFAULT_DATA_DIRS = [
    "/data/quanthk",  # Docker 容器内（挂载点）
    str(Path(__file__).resolve().parents[4] / "data" / "quanthk"),  # 项目根/data/quanthk
]


def _resolve_quanthk_data_dir() -> Path:
    env_val = os.getenv(_QUANTHK_DATA_DIR_ENV, "").strip()
    if env_val:
        p = Path(env_val)
        if p.is_dir():
            return p
        logger.warning("QM_QUANTHK_DATA_DIR=%s 不存在，尝试默认路径", env_val)
    for d in _QUANTHK_DEFAULT_DATA_DIRS:
        p = Path(d)
        if p.is_dir():
            return p
    return Path(_QUANTHK_DEFAULT_DATA_DIRS[-1])


class QuantHKDataHub(QuantDBDataHub):
    """港股本地 parquet 数据中枢。视图命名空间 qhk_*。"""

    _instance: QuantHKDataHub | None = None
    _instance_lock = threading.Lock()

    def __init__(self, data_dir: str | Path | None = None) -> None:
        super().__init__(data_dir=data_dir or _resolve_quanthk_data_dir())

    @classmethod
    def get_instance(cls) -> QuantHKDataHub:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _mount_views(self, conn) -> None:
        """用 qhk_* 前缀挂载分区视图，避免与 A 股/美股视图冲突。"""
        conn_id = id(conn)
        if conn_id in self._views_mounted_per_conn:
            return
        dd = self._data_dir
        partitioned_views = {
            "qhk_daily_forward": "1_kline_data/daily_forward",
            "qhk_index_daily": "1_kline_data/index_daily",
            "qhk_valuation": "5_technical_derived/valuation",
            "qhk_features_daily": "6_ml_datasets/features_daily",
        }
        for view_name, rel_path in partitioned_views.items():
            full_path = dd / rel_path
            if not full_path.exists():
                continue
            parquet_glob = str(full_path / "**" / "*.parquet")
            try:
                conn.execute(
                    f"CREATE VIEW IF NOT EXISTS {view_name} AS "
                    f"SELECT * FROM read_parquet('{parquet_glob}', hive_partitioning=1, union_by_name=true)"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("创建 DuckDB 视图 %s 失败: %s", view_name, exc)
        self._views_mounted_per_conn.add(conn_id)

    # ---- 港股查询（视图名带 qhk_ 前缀） ----
    def fetch_daily_kline(self, symbol: str, start, end, *, adjust: str = "qfq"):
        """港股日线。symbol 为 suffix 格式（0700.HK）。"""
        view_name = "qhk_daily_forward"
        if not self._view_exists(view_name):
            return self._read_daily_kline_from_files(symbol, start, end, adjust="qfq")
        conn = self._get_duck_conn()
        conditions = [f"symbol = '{symbol}'"] + _dt_conditions(start, end)
        where = " AND ".join(conditions)
        df = conn.execute(f"SELECT * FROM {view_name} WHERE {where} ORDER BY dt").fetchdf()
        return self._normalize_kline(df)

    def fetch_index_kline(self, symbol: str, start, end) -> pd.DataFrame:
        """港股指数日线（恒生/国企/红筹）。symbol 如 HSI.HK。"""
        view_name = "qhk_index_daily"
        if not self._view_exists(view_name):
            return self._empty_df()
        conn = self._get_duck_conn()
        conditions = [f"symbol = '{symbol}'"] + _dt_conditions(start, end)
        where = " AND ".join(conditions)
        df = conn.execute(f"SELECT * FROM {view_name} WHERE {where} ORDER BY dt").fetchdf()
        return self._normalize_kline(df)

    # akshare 估值中文列 → 标准估值列（pe_ttm/pb/ps_ttm 等）
    _AKSHARE_VAL_COLS = {
        "市盈率-TTM": "pe_ttm",
        "市盈率-LYR": "pe_static",
        "市净率-MRQ": "pb",
        "市净率-LYR": "pb_static",
        "市销率-TTM": "ps_ttm",
        "市销率-LYR": "ps_static",
        "市现率-TTM": "pcf_ttm",
        "市现率-LYR": "pcf_static",
    }

    def _fetch_akshare_valuation(self, symbol: str | None = None) -> pd.DataFrame:
        """读取 akshare 估值（真实 PE/PB/PS，覆盖 1611 只港股）。

        akshare 估值按标的落盘为 2_base_sector/akshare_valuation/{symbol}.parquet，
        为当前快照（非时间序列）。
        """
        import pandas as pd

        d = self._data_dir / "2_base_sector" / "akshare_valuation"
        if not d.is_dir():
            return pd.DataFrame()
        if symbol:
            # 兼容 0700.HK 与 00700 两种入参
            candidates = []
            sym = symbol.upper()
            candidates.append(sym)
            if sym.endswith(".HK"):
                candidates.append(sym[:-3].lstrip("0").zfill(5))
            else:
                candidates.append(f"{sym.lstrip('0').zfill(4)}.HK")
            for cand in candidates:
                f = d / f"{cand}.parquet"
                if f.exists():
                    df = pd.read_parquet(f)
                    return self._normalise_akshare_valuation(df)
            return pd.DataFrame()
        # 全量：单次读取目录所有 parquet（pandas 内部 union 处理不同列，避免逐帧 concat）
        import glob

        files = sorted(glob.glob(str(d / "*.parquet")))
        if not files:
            return pd.DataFrame()
        df = pd.read_parquet(files)
        if df is None or df.empty:
            return pd.DataFrame()
        return self._normalise_akshare_valuation(df)

    def _normalise_akshare_valuation(self, df: pd.DataFrame) -> pd.DataFrame:
        """akshare 估值中文列 → 标准估值列，并加 source 标记。"""
        import pandas as pd

        if df.empty:
            return df
        out = df.rename(columns=self._AKSHARE_VAL_COLS)
        # 只保留标准列 + symbol，避免中文排名列污染下游
        keep = ["symbol"] + list(self._AKSHARE_VAL_COLS.values())
        keep = [c for c in keep if c in out.columns]
        out = out[keep]
        out["source"] = "akshare"
        out["time"] = pd.Timestamp.now().normalize()
        return out

    def fetch_valuation(self, symbol: str | None = None, start=None, end=None):
        """港股估值指标。优先 akshare 真实估值，回退 yahoo 快照。"""
        ak = self._fetch_akshare_valuation(symbol)
        if not ak.empty:
            return self._normalize_columns(ak)
        if not self._view_exists("qhk_valuation"):
            return self._empty_df()
        conn = self._get_duck_conn()
        conditions = []
        if symbol:
            conditions.append(f"symbol = '{symbol}'")
        conditions.extend(_dt_conditions(start, end))
        where = " AND ".join(conditions) if conditions else "1=1"
        df = conn.execute(f"SELECT * FROM qhk_valuation WHERE {where} ORDER BY dt").fetchdf()
        return self._normalize_columns(df)

    def fetch_ccass(self, symbol: str | None = None, start=None, end=None):
        """港股 CCASS top50 机构持仓（按日分区，stock_code 为后缀格式如 0700.HK）。

        Args:
            symbol: 港股代码（0700.HK 或旧 5 位 00700，自动归一），为空返回全部
            start/end: 交易日范围（date）
        """
        from backend.shared.stock_utils import StockCodeUtil

        rel = self._data_dir / "2_base_sector" / "ccass_top50"
        if not rel.is_dir():
            return self._empty_df()

        # 入参归一：00700 → 0700.HK，兼容旧 5 位
        symbol_q = None
        if symbol:
            sym = str(symbol).strip()
            symbol_q = StockCodeUtil.to_hk_suffix(sym)

        # 用 DuckDB 视图读取分区（hive_partitioning），避免逐文件拼接
        if not self._view_exists("qhk_ccass"):
            self._mount_ccass_view(rel)
        if self._view_exists("qhk_ccass"):
            conn = self._get_duck_conn()
            conditions = _dt_conditions(start, end)
            if symbol_q:
                conditions.append(f"stock_code = '{symbol_q}'")
            where = " AND ".join(conditions) if conditions else "1=1"
            df = conn.execute(f"SELECT * FROM qhk_ccass WHERE {where} ORDER BY dt").fetchdf()
            return self._normalize_columns(df)

        # 回退：直接读分区文件
        import pandas as pd

        frames = []
        for dt_dir in sorted(rel.glob("dt=*")):
            if start and dt_dir.name[3:] < start.strftime("%Y%m%d"):
                continue
            if end and dt_dir.name[3:] > end.strftime("%Y%m%d"):
                continue
            for pf in dt_dir.glob("*.parquet"):
                chunk = pd.read_parquet(pf)
                if symbol_q and "stock_code" in chunk.columns:
                    chunk = chunk[chunk["stock_code"] == symbol_q]
                if not chunk.empty:
                    frames.append(chunk)
        if not frames:
            return self._empty_df()
        return pd.concat(frames, ignore_index=True)

    def fetch_hsgt_south(self, symbol: str | None = None, start=None, end=None):
        """港股通南向资金持仓（symbol 为 4位+.HK 如 0700.HK）。

        数据为混合格式：
          - per-stock 平铺 parquet（{symbol}.parquet，2024-11~2025-12 历史）
          - dt=YYYYMMDD 按日分区（2026-08 起 HKEX 抓取）
        统一按 query_date 过滤返回。

        Args:
            symbol: 4位+.HK 代码（如 0700.HK），为空返回全部
            start/end: 交易日范围（date）
        """
        import pandas as pd

        rel = self._data_dir / "2_base_sector" / "hsgt_south"
        if not rel.is_dir():
            return self._empty_df()

        frames = []
        # per-stock 平铺 parquet（历史日频）
        for pf in rel.glob("*.parquet"):
            try:
                chunk = pd.read_parquet(pf)
            except Exception:  # noqa: BLE001
                continue
            if chunk.empty:
                continue
            if symbol and "symbol" in chunk.columns:
                chunk = chunk[chunk["symbol"] == symbol]
            if chunk.empty:
                continue
            if "query_date" in chunk.columns:
                chunk["query_date"] = pd.to_datetime(chunk["query_date"], errors="coerce")
                if start:
                    chunk = chunk[chunk["query_date"] >= pd.Timestamp(start)]
                if end:
                    chunk = chunk[chunk["query_date"] <= pd.Timestamp(end)]
            frames.append(chunk)

        # dt= 按日分区（HKEX 抓取，query_date 列 + Hive dt 分区）
        for dt_dir in sorted(rel.glob("dt=*")):
            if start and dt_dir.name[3:] < start.strftime("%Y%m%d"):
                continue
            if end and dt_dir.name[3:] > end.strftime("%Y%m%d"):
                continue
            for pf in dt_dir.glob("*.parquet"):
                try:
                    chunk = pd.read_parquet(pf)
                except Exception:  # noqa: BLE001
                    continue
                if chunk.empty:
                    continue
                if symbol and "symbol" in chunk.columns:
                    chunk = chunk[chunk["symbol"] == symbol]
                if not chunk.empty:
                    frames.append(chunk)

        if not frames:
            return self._empty_df()
        out = pd.concat(frames, ignore_index=True)
        if "query_date" in out.columns:
            out["query_date"] = pd.to_datetime(out["query_date"], errors="coerce")
            out = out.sort_values("query_date")
        return out

    def _mount_hsgt_south_view(self, rel: Path) -> None:
        """挂载 hsgt_south 分区视图（qhk_hsgt_south）。"""
        try:
            conn = self._get_duck_conn()
            parquet_glob = str(rel / "**" / "*.parquet")
            conn.execute(
                f"CREATE VIEW IF NOT EXISTS qhk_hsgt_south AS "
                f"SELECT * FROM read_parquet('{parquet_glob}', hive_partitioning=1, union_by_name=true)"
            )
            self._views_mounted_per_conn.add(id(conn))
        except Exception as exc:  # noqa: BLE001
            logger.warning("创建 qhk_hsgt_south 视图失败: %s", exc)

    def _mount_ccass_view(self, rel: Path) -> None:
        """挂载 ccass 分区视图（qhk_ccass）。"""
        try:
            conn = self._get_duck_conn()
            parquet_glob = str(rel / "**" / "*.parquet")
            conn.execute(
                f"CREATE VIEW IF NOT EXISTS qhk_ccass AS "
                f"SELECT * FROM read_parquet('{parquet_glob}', hive_partitioning=1, union_by_name=true)"
            )
            self._views_mounted_per_conn.add(id(conn))
        except Exception as exc:  # noqa: BLE001
            logger.warning("创建 qhk_ccass 视图失败: %s", exc)

    def fetch_stock_list(self):
        """港股标的池（instrument_detail.parquet）。"""
        import pandas as pd

        detail_dir = self._data_dir / "2_base_sector" / "instrument_detail"
        file_path = detail_dir / "instrument_list.parquet"
        if not file_path.exists():
            file_path = detail_dir / "instrument_detail.parquet"
        if not file_path.exists():
            return pd.DataFrame()
        return pd.read_parquet(file_path)

    # ---- 通用数据段读取（分红/财务/评级/持仓/期权等） ----
    DATASET_DIRS = {
        "dividend": "3_financial_data/dividend",
        "splits": "3_financial_data/splits",
        "balance": "3_financial_data/balance",
        "income": "3_financial_data/income",
        "cashflow": "3_financial_data/cashflow",
        "sector": "2_base_sector/sector",
        "f10": "2_base_sector/f10",
        "recommendations": "4_analyst/recommendations",
        "upgrades_downgrades": "4_analyst/upgrades_downgrades",
        "earnings_history": "4_analyst/earnings_history",
        "earnings_dates": "4_analyst/earnings_dates",
        "earnings_estimate": "4_analyst/earnings_estimate",
        "revenue_estimate": "4_analyst/revenue_estimate",
        "growth_estimates": "4_analyst/growth_estimates",
        "analyst_price_targets": "4_analyst/analyst_price_targets",
        "major_holders": "4_analyst/major_holders",
        "mutual_fund_holders": "4_analyst/mutual_fund_holders",
        "calendar": "4_analyst/calendar",
        "insider_transactions": "4_analyst/insider_transactions",
        "options_chain": "4_options",
        "ah_membership": "2_base_sector/ah_membership",
        "hsgt_membership": "2_base_sector/hsgt_membership",
        "index_weights": "2_base_sector/index_weights",
        "adjust_factors": "2_base_sector/adjust_factors",
        "ah_premium": "2_base_sector/ah_premium",
    }

    def fetch_dataset(self, dataset: str, symbol: str | None = None):
        """读取任一数据段（标的级 parquet）。"""
        import pandas as pd

        rel_dir = self.DATASET_DIRS.get(dataset)
        if rel_dir is None:
            return pd.DataFrame()
        d = self._data_dir / rel_dir
        if not d.is_dir():
            return pd.DataFrame()
        if symbol:
            file_path = d / f"{symbol}.parquet"
            if not file_path.exists():
                return pd.DataFrame()
            df = pd.read_parquet(file_path)
        else:
            files = sorted(d.glob("*.parquet"))
            if not files:
                return pd.DataFrame()
            df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        return df

    @staticmethod
    def _empty_df():
        import pandas as _pd

        return _pd.DataFrame()
