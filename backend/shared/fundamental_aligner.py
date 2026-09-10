import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd

from backend.shared.stock_utils import StockCodeUtil

logger = logging.getLogger(__name__)


class FundamentalAligner:
    """
    统一基本面对齐器 (Unified Fundamental Aligner)

    优先从 QuantDB ``features_daily`` 按交易日读取特征，确保训练、回测和
    实盘筛选使用同一份宽表。旧的 ``fundamental_aligned.parquet`` 仅在新表缺少
    对应交易日时作为兼容回退。
    """

    DEFAULT_PATH = "db/custom/fundamental_aligned.parquet"
    DEFAULT_FEATURES_DAILY_PATH = "data/quantdb/6_ml_datasets/features_daily"

    def __init__(self, parquet_path: str | None = None):
        self.parquet_path = parquet_path or os.getenv(
            "FUNDAMENTAL_ALIGN_PATH", self.DEFAULT_PATH
        )
        self._data: pd.DataFrame | None = None
        self._project_root = self._find_project_root()

        if os.path.isabs(self.parquet_path):
            self.full_path = Path(self.parquet_path)
        else:
            self.full_path = self._project_root / self.parquet_path

        configured_features_path = os.getenv("FEATURES_DAILY_PATH", "").strip()
        # QM_QUANTDB_DATA_DIR 指向 quantdb 根目录（容器内默认 /data/quantdb），
        # 是 6_ml_datasets 的真实父目录；只写 /data/6_ml_datasets 会在容器里落空，
        # 导致每次都回退到 2.7GB 的 fundamental_aligned.parquet（慢且缺部分列）。
        quantdb_dir = os.getenv("QM_QUANTDB_DATA_DIR", "").strip()
        feature_candidates = [
            Path(configured_features_path) if configured_features_path else None,
            Path(quantdb_dir) / "6_ml_datasets/features_daily" if quantdb_dir else None,
            Path("/data/quantdb/6_ml_datasets/features_daily"),
            Path("/data/6_ml_datasets/features_daily"),
            self._project_root / self.DEFAULT_FEATURES_DAILY_PATH,
        ]
        self.features_daily_path = next(
            (path for path in feature_candidates if path is not None and path.exists()),
            self._project_root / self.DEFAULT_FEATURES_DAILY_PATH,
        )
        self._feature_snapshot_cache: dict[pd.Timestamp, pd.DataFrame] = {}

    def _find_project_root(self) -> Path:
        curr = Path(__file__).resolve().parent
        for _ in range(10):
            if (curr / "requirements.txt").exists() or (curr / "AGENTS.md").exists():
                return curr
            if curr.parent == curr:
                break
            curr = curr.parent
        return Path(os.getcwd())

    def _load_data(self) -> pd.DataFrame:
        if self._data is not None:
            return self._data

        if not self.full_path.exists():
            logger.warning("FundamentalAligner: 找不到对齐文件 %s", self.full_path)
            self._data = pd.DataFrame()
            return self._data

        try:
            df = pd.read_parquet(self.full_path)
            if df.empty:
                self._data = pd.DataFrame()
                return self._data

            df["trade_date"] = pd.to_datetime(df["trade_date"])

            if os.getenv("MODE") == "production":
                last_date = df["trade_date"].max()
                days_diff = (pd.Timestamp.now().normalize() - last_date.normalize()).days
                if days_diff > 2:
                    logger.error(
                        "CRITICAL: 基本面对齐数据已过期，最后日期=%s，滞后=%s天",
                        last_date.date(),
                        days_diff,
                    )

            self._data = df.set_index(["trade_date", "symbol"]).sort_index()
            logger.info("FundamentalAligner: 成功加载数据，字段数=%s", len(df.columns))
        except Exception as exc:
            logger.error("FundamentalAligner: 加载失败: %s", exc)
            self._data = pd.DataFrame()
        return self._data

    def _load_features_daily_snapshot(self, current_date: Any) -> pd.DataFrame:
        """读取一个交易日的 features_daily 分区，并转换为 Qlib 的前缀代码。"""
        dt = pd.to_datetime(current_date).normalize()
        cached = self._feature_snapshot_cache.get(dt)
        if cached is not None:
            return cached

        day_dir = self.features_daily_path / f"dt={dt.strftime('%Y%m%d')}"
        files = sorted(day_dir.glob("*.parquet")) if day_dir.is_dir() else []
        if not files:
            return pd.DataFrame()

        try:
            snapshot = pd.read_parquet(files)
        except Exception as exc:
            logger.warning(
                "FundamentalAligner: 读取 features_daily 失败 (%s): %s",
                day_dir,
                exc,
            )
            return pd.DataFrame()

        if snapshot.empty or "symbol" not in snapshot.columns:
            return pd.DataFrame()

        snapshot = snapshot.copy()
        snapshot["symbol"] = snapshot["symbol"].map(
            lambda value: StockCodeUtil.to_prefix(str(value or ""))
        )
        snapshot = snapshot[snapshot["symbol"] != ""]
        snapshot = snapshot.drop_duplicates(subset="symbol", keep="last").set_index("symbol")

        # 只缓存有限个交易日，长期回测不会无限占用 worker 内存。
        self._feature_snapshot_cache[dt] = snapshot
        if len(self._feature_snapshot_cache) > 8:
            oldest = min(self._feature_snapshot_cache)
            self._feature_snapshot_cache.pop(oldest, None)
        logger.debug(
            "FundamentalAligner: 使用 features_daily %s，字段数=%s",
            dt.date(),
            len(snapshot.columns),
        )
        return snapshot

    @staticmethod
    def _normalize_instrument(symbol: Any) -> str:
        return StockCodeUtil.to_prefix(str(symbol or ""))

    def _snapshot_for_date(self, current_date: Any) -> pd.DataFrame:
        """新宽表优先；没有分区时回退至历史对齐文件。"""
        feature_snapshot = self._load_features_daily_snapshot(current_date)
        if not feature_snapshot.empty:
            return feature_snapshot

        data = self._load_data()
        if data.empty:
            return pd.DataFrame()
        dt = pd.to_datetime(current_date).normalize()
        try:
            snapshot = data.loc[dt].copy()
        except KeyError:
            return pd.DataFrame()
        snapshot.index = pd.Index(
            [self._normalize_instrument(symbol) for symbol in snapshot.index], name="symbol"
        )
        return snapshot

    def filter_instruments(
        self,
        current_date: Any,
        instruments: list[str],
        constraints: dict[str, Any] | None = None,
    ) -> list[str]:
        if not constraints:
            return instruments

        snapshot = self._snapshot_for_date(current_date)
        if snapshot.empty:
            # 数据缺失时保持历史行为：不因数据源暂不可用而清空组合。
            return instruments

        mask = pd.Series(True, index=snapshot.index)
        for key, target_val in constraints.items():
            if target_val is None:
                continue

            col = key
            op = "eq"
            if key.endswith("_max"):
                col, op = key[:-4], "le"
            elif key.endswith("_min"):
                col, op = key[:-4], "ge"
            elif key.endswith("_in"):
                col, op = key[:-3], "in"
            elif key.endswith("_not"):
                col, op = key[:-4], "ne"

            if col not in snapshot.columns:
                logger.debug("FundamentalAligner: 字段 %s 不存在，跳过", col)
                continue

            col_data = snapshot[col]
            if op == "le":
                mask &= col_data <= float(target_val)
            elif op == "ge":
                mask &= col_data >= float(target_val)
            elif op == "ne":
                mask &= col_data != target_val
            elif op == "in":
                if isinstance(target_val, (list, set, tuple)):
                    mask &= col_data.isin(target_val)
                else:
                    mask &= col_data == target_val
            else:
                mask &= col_data == target_val

        valid_symbols = set(snapshot[mask].index)
        return [
            symbol
            for symbol in instruments
            if self._normalize_instrument(symbol) in valid_symbols
        ]


fundamental_aligner = FundamentalAligner()

