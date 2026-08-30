"""回放信号批量预生成器。

不走 InferenceScriptRunner（它有 30 天保留期清理、Redis 覆写、ST 前视三个坑）。
信号来源优先级：
  1. pred.parquet 直读 —— 训练产出 + 每日推理回写的全量历史分数，与生产
     个股分数曲线/推理覆盖同一数据源，无需重新推理（快且与展示口径一致）；
  2. 无 pred.parquet 时才回退：读特征 + 载模型 + 批量 predict。
结果统一写入 replay_signals 表，窗口内只读一次数据、按交易日切片。

T+1 偏移说明：
  parquet 里的 trade_date 是「数据日 D」（用 D 日的行情/特征推理）。
  信号在 D+1（下一个交易日）才生效，所以 replay_signals.trade_date = next_session(D)。
  day_runner.run_day(trade_date=T) 调 ReplaySignalLoader.load_signals_for_date(T)，
  查 replay_signals WHERE trade_date = T，拿到的是用 T-1 数据推理的信号 ——
  与 engine_signal_scores 的语义完全一致，无前视偏差。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.trade.simulation.models.replay import ReplaySignal
from backend.services.trade.simulation.services.local_market_data import (
    LocalMarketData,
    get_local_market_data,
)
from backend.shared.stock_utils import StockCodeUtil

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _resolve_model_dir(model_id: str | None = None) -> Path:
    """定位模型目录。优先 model_id 对应的子目录。"""
    base = Path(os.getenv("MODELS_PRODUCTION", "/app/models/production"))
    if model_id:
        candidate = base / model_id
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(f"找不到可用模型目录 (base={base}, model_id={model_id})")


def _resolve_data_dir() -> Path:
    return Path(os.getenv("MODEL_TRAINING_DATA_DIR", "/app/db/feature_snapshots"))


def _load_metadata(model_dir: Path) -> dict[str, Any]:
    meta_path = model_dir / "metadata.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"metadata.json 不存在: {meta_path}")
    with open(meta_path) as f:
        return json.load(f)


def _load_model(model_dir: Path, meta: dict[str, Any]) -> Any:
    """载入 LightGBM / XGBoost / CatBoost / sklearn 模型。"""
    framework = str(meta.get("framework") or meta.get("model_type") or "").lower()
    model_file = meta.get("model_file", "model.lgb")
    model_path = model_dir / model_file

    if not model_path.is_file():
        raise FileNotFoundError(f"模型文件不存在: {model_path}")

    if framework in ("lightgbm", "lgb"):
        import lightgbm as lgb

        return lgb.Booster(model_file=str(model_path))
    elif framework in ("xgboost", "xgb"):
        import xgboost as xgb

        return xgb.Booster(model_file=str(model_path))
    elif framework in ("catboost", "cat"):
        from catboost import CatBoost

        model = CatBoost()
        model.load_model(str(model_path), format="cbm")
        return model
    else:
        import pickle

        with open(model_path, "rb") as f:
            return pickle.load(f)


def _predict(model: Any, X: np.ndarray, meta: dict[str, Any]) -> np.ndarray:
    """统一 predict 接口。"""
    framework = str(meta.get("framework") or meta.get("model_type") or "").lower()
    best_iter = meta.get("best_iteration")

    if framework in ("lightgbm", "lgb"):
        return model.predict(X, num_iteration=best_iter)
    elif framework in ("xgboost", "xgb"):
        import xgboost as xgb

        dmat = xgb.DMatrix(X)
        it_range = (0, best_iter) if best_iter else None
        return model.predict(dmat, iteration_range=it_range)
    elif framework in ("catboost", "cat"):
        return model.predict(X)
    else:
        if hasattr(model, "predict_proba"):
            return model.predict_proba(X)[:, 1]
        return model.predict(X)


def _parquet_paths_for_range(data_dir: Path, start: date, end: date) -> list[Path]:
    """返回覆盖 [start, end] 的 parquet 文件路径列表。"""
    paths: list[Path] = []
    for year in range(start.year, end.year + 1):
        p = data_dir / f"model_features_{year}.parquet"
        if p.is_file():
            paths.append(p)
    if not paths:
        legacy = data_dir / "model_features.parquet"
        if legacy.is_file():
            paths.append(legacy)
    return paths


def _find_pred_parquet(model_dir: Path) -> Path | None:
    """定位模型目录的 pred.parquet（兼容 pred/ 子目录存法）。"""
    for candidate in (model_dir / "pred.parquet", model_dir / "pred" / "pred.parquet"):
        if candidate.is_file():
            return candidate
    return None


def _load_pred_parquet(
    parquet_file: Path, start: date, end: date
) -> pd.DataFrame | None:
    """直读训练产出的全量历史分数（pred.parquet）。

    列：symbol（SH/SZ 前缀式）/ trade_date（数据日 T）/ label / pred（或
    fusion_score）/ split。trade_date 语义与重新推理路径的「数据日 D」一致，
    下游 T+1 偏移逻辑可直接复用。失败返回 None。
    """
    import duckdb

    try:
        con = duckdb.connect()
        try:
            cols = [
                r[0]
                for r in con.execute(
                    f"SELECT * FROM read_parquet('{parquet_file}') LIMIT 0"
                ).description
            ]
            score_col = (
                "pred"
                if "pred" in cols
                else "fusion_score"
                if "fusion_score" in cols
                else None
            )
            date_col = "trade_date" if "trade_date" in cols else (
                "date" if "date" in cols else None
            )
            if not score_col or not date_col or "symbol" not in cols:
                logger.error("回放信号: pred.parquet 缺必要列: %s", cols)
                return None
            frame = con.execute(
                f"""
                SELECT symbol,
                       CAST({date_col} AS DATE) AS trade_date,
                       CAST({score_col} AS DOUBLE) AS score
                FROM read_parquet('{parquet_file}')
                WHERE CAST({score_col} AS DOUBLE) IS NOT NULL
                  AND CAST({date_col} AS DATE) BETWEEN CAST(? AS DATE)
                      AND CAST(? AS DATE)
                """,
                [start.isoformat(), end.isoformat()],
            ).fetchdf()
        finally:
            con.close()
        if frame.empty:
            return None
        return frame.dropna(subset=["symbol", "trade_date", "score"])
    except Exception as exc:  # noqa: BLE001 - 直读失败降级为重新推理
        logger.error("回放信号: pred.parquet 读取失败: %s", exc)
        return None


def _load_quantdb_features(
    meta: dict[str, Any], feature_cols: list[str], start: date, end: date
) -> pd.DataFrame | None:
    """QuantDB 直读模型（data_source=quantdb_factors）的特征读取。

    与推理模板 inference_parquet.py 的 _quantdb_reader 同口径：优先用模型
    metadata 固化的 quantdb_dir，缺失时由 reader 按市场解析（CN → /data/quantdb）。
    返回 symbol（前缀式）/trade_date/特征列；失败返回 None。
    """
    try:
        from backend.services.engine.data_platform.quantdb_factor_reader import (
            QuantDBFactorReader,
        )

        pinned_dir = Path(str(meta.get("quantdb_dir") or ""))
        reader = QuantDBFactorReader(pinned_dir if pinned_dir.is_dir() else None)
        source = str(meta.get("factor_source") or "l1_l2_factors")

        # 分区目录名快速路径（读目录名，不扫数据）：把请求区间钳制到实际已发布
        # 分区范围内。回放默认结束于「今天」，而当日分区往往尚未发布（周一早上
        # 还差周五数据），read_range 的 assert_ready 对越界区间直接抛错，会让整个
        # 读取失败；钳制后缺数据的日子由下游逐日循环自然跳过。
        try:
            in_range = reader.available_dates(
                source,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
            )
        except Exception:  # noqa: BLE001 - 快速路径失败回退全量校验
            in_range = None
        if in_range is not None:
            if not in_range:
                logger.error(
                    "回放信号: QuantDB %s 在 %s~%s 无分区", source, start, end
                )
                return None
            clamped_start = date.fromisoformat(in_range[0])
            clamped_end = date.fromisoformat(in_range[-1])
            if (clamped_start, clamped_end) != (start, end):
                logger.info(
                    "回放信号: QuantDB %s 读取区间由 %s~%s 钳制为 %s~%s（按已发布分区）",
                    source,
                    start,
                    end,
                    clamped_start,
                    clamped_end,
                )
            start, end = clamped_start, clamped_end

        return reader.read_range(
            source,
            features=list(feature_cols),
            feature_sources=meta.get("factor_field_sources") or None,
            start=start,
            end=end,
        )
    except Exception as exc:  # noqa: BLE001 - 直读失败降级为快照路径报错
        logger.error("回放信号: QuantDB 直读失败: %s", exc)
        return None


def _filter_untradable(df: pd.DataFrame) -> pd.DataFrame:
    """过滤停牌/零成交行。用行内历史 is_st，不用今天的 ST 名单。"""
    if "volume" in df.columns:
        df = df[df["volume"] > 0]
    if "is_st" in df.columns:
        df = df[df["is_st"] != 1]
    return df


def _coerce_feature_columns(
    day_df: pd.DataFrame, feature_cols: list[str]
) -> pd.DataFrame:
    """统一特征列为 float64，兼容不同年份 parquet 的 dtype 差异。

    历史年份快照（如 2025）个别特征列是 nullable Int64 掩码数组，
    直接 fillna(浮点补值) 会抛 TypeError: Invalid value '...' for dtype 'Int64'。
    这里全部转 float64（NA 保留，由 fill_values/fillna(0.0) 兜底），
    缺失列补 0.0。
    """
    for c in feature_cols:
        if c not in day_df.columns:
            day_df[c] = 0.0
        else:
            day_df[c] = pd.to_numeric(day_df[c], errors="coerce").astype("float64")
    return day_df


def _dt_int_to_date(dt_int: int) -> date:
    return date(dt_int // 10000, (dt_int % 10000) // 100, dt_int % 100)


# ---------------------------------------------------------------------------
# ReplaySignalGenerator
# ---------------------------------------------------------------------------


class ReplaySignalGenerator:
    """批量预生成回放信号。

    分两步：
    1. predict_all() — 同步，CPU 密集（读 parquet + 载模型 + predict），
       适合在 run_in_executor 中执行。
    2. persist_all() — 异步，写 DB（在主事件循环中执行）。
    """

    def __init__(
        self,
        model_id: str | None = None,
        market_data: LocalMarketData | None = None,
        model_dir: Path | None = None,
    ):
        self._model_id = model_id
        self._market_data = market_data or get_local_market_data()
        # 显式模型目录。用户模型不在 MODELS_PRODUCTION 下，由调用方
        # （router）从 qm_user_models.storage_path 解析后传入。
        self._model_dir = model_dir

    def predict_all(
        self,
        session_id: uuid.UUID,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """同步执行 parquet 读取 + 模型推理，返回内存中的信号数据。

        返回 {
            "total_days": N,
            "signals_by_date": { "2024-03-05": [(symbol, score), ...], ... },
            "errors": [...],
        }
        """
        model_dir = self._model_dir or _resolve_model_dir(self._model_id)
        meta = _load_metadata(model_dir)
        feature_cols = meta.get("feature_columns") or meta.get("features", [])
        fill_values = meta.get("fill_values", {})

        # 1. 确定交易日序列（数据日）
        sessions = self._market_data._sessions()
        start_int = int(start_date.strftime("%Y%m%d"))
        end_int = int(end_date.strftime("%Y%m%d"))
        # 多取前一天：start_date 的信号需要 start_date-1 的数据
        data_start_int = start_int
        if sessions:
            before_start = [d for d in sessions if d < start_int]
            if before_start:
                data_start_int = before_start[-1]

        data_days = [d for d in sessions if data_start_int <= d <= end_int]
        total = len(data_days)

        if total == 0:
            return {
                "total_days": 0,
                "signals_by_date": {},
                "errors": ["区间内无交易日"],
            }

        # 2. 读特征数据：优先直读模型目录 pred.parquet（训练产出 + 每日推理回写的全量历史分数，
        #    与生产个股分数曲线同源，无需重新推理）；无 pred.parquet 时回退：QuantDB 直读模型走特征
        #    reader 重推理，旧模型读年度快照。
        pred_file = _find_pred_parquet(model_dir)
        df_pred: pd.DataFrame | None = None
        df_all: pd.DataFrame | None = None
        if pred_file is not None:
            df_pred = _load_pred_parquet(
                pred_file, _dt_int_to_date(data_start_int), end_date
            )
            if df_pred is not None and not df_pred.empty:
                logger.info(
                    "回放信号: 直读 pred.parquet（%s，%d 行）",
                    pred_file,
                    len(df_pred),
                )

        if df_pred is None or df_pred.empty:
            model = _load_model(model_dir, meta)
            if str(meta.get("data_source") or "") == "quantdb_factors":
                df_all = _load_quantdb_features(
                    meta, feature_cols, _dt_int_to_date(data_start_int), end_date
                )
                if df_all is None or df_all.empty:
                    return {
                        "total_days": total,
                        "signals_by_date": {},
                        "errors": ["QuantDB 因子数据读取失败或区间内无已发布分区"],
                    }
            else:
                data_dir = _resolve_data_dir()
                parquet_paths = _parquet_paths_for_range(
                    data_dir, _dt_int_to_date(data_start_int), end_date
                )
                if not parquet_paths:
                    return {
                        "total_days": total,
                        "signals_by_date": {},
                        "errors": ["无 parquet 数据文件"],
                    }

                needed_cols = {"trade_date", "symbol"}
                needed_cols.update(feature_cols)
                optional_cols = {"is_st", "volume"}
                all_cols = needed_cols | optional_cols

                dfs: list[pd.DataFrame] = []
                for p in parquet_paths:
                    try:
                        available = set(
                            pd.read_parquet(p, engine="pyarrow").columns
                        )
                        read_cols = [c for c in all_cols if c in available]
                        chunk = pd.read_parquet(
                            p, columns=read_cols, engine="pyarrow"
                        )
                        dfs.append(chunk)
                    except Exception as exc:
                        logger.error("读取 parquet 失败 %s: %s", p, exc)

                if not dfs:
                    return {
                        "total_days": total,
                        "signals_by_date": {},
                        "errors": ["parquet 读取全部失败"],
                    }

                df_all = pd.concat(dfs, ignore_index=True)

        use_pred_scores = df_pred is not None and not df_pred.empty
        if use_pred_scores:
            df_all = df_pred
        df_all["trade_date"] = pd.to_datetime(df_all["trade_date"]).dt.strftime(
            "%Y-%m-%d"
        )

        # 3. 逐日 predict
        signals_by_date: dict[str, list[tuple[str, float]]] = {}
        errors: list[str] = []

        for _idx, dt_int in enumerate(data_days):
            # dt_int = 20240304 → parquet 里 trade_date = "2024-03-04"
            dt_str = (
                f"{dt_int // 10000}-{(dt_int % 10000) // 100:02d}-{dt_int % 100:02d}"
            )
            day_df = df_all[df_all["trade_date"] == dt_str].copy()

            if day_df.empty:
                logger.debug("回放信号: %s 无数据，跳过", dt_str)
                continue

            if use_pred_scores:
                # pred.parquet 直读：分数即列，无行情列可过滤停牌，
                # 不可交易标的由 day_runner 撮合层兜底拒绝。
                scores = day_df["score"].to_numpy(dtype=np.float64)
            else:
                day_df = _filter_untradable(day_df)
                if day_df.empty:
                    continue

                # 补缺失列 + 统一数值类型：历史年份 parquet 特征列可能是
                # Int64 掩码数组，fillna(浮点) 会抛 TypeError（见 _coerce_feature_columns）
                day_df = _coerce_feature_columns(day_df, feature_cols)
                for c, val in fill_values.items():
                    if c in day_df.columns:
                        day_df[c] = day_df[c].fillna(val)
                X = day_df[feature_cols].fillna(0.0).values.astype(np.float32)

                try:
                    scores = _predict(model, X, meta)
                except Exception as exc:
                    msg = f"{dt_str} predict 失败: {exc}"
                    logger.error(msg)
                    errors.append(msg)
                    continue

            # T+1 偏移：数据日 D → 信号生效日 = next_session(D)
            try:
                pos = sessions.index(dt_int)
            except ValueError:
                pos = -1

            if pos + 1 >= len(sessions):
                continue

            signal_date_int = sessions[pos + 1]

            # 只保留 signal_date 在 [start_date, end_date] 范围内的
            if signal_date_int < start_int or signal_date_int > end_int:
                continue

            signal_date_str = _dt_int_to_date(signal_date_int).isoformat()

            # 收集 (symbol, score) 对
            pairs: list[tuple[str, float]] = []
            for sym, sc in zip(day_df["symbol"].tolist(), scores, strict=True):
                sc_val = float(sc)
                if not np.isfinite(sc_val):
                    continue
                pairs.append((StockCodeUtil.to_suffix(str(sym).upper()), sc_val))

            if pairs:
                signals_by_date.setdefault(signal_date_str, []).extend(pairs)

        return {
            "total_days": total,
            "signals_by_date": signals_by_date,
            "errors": errors,
        }

    async def persist_all(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        predict_result: dict[str, Any],
    ) -> dict[str, Any]:
        """将 predict_all() 的结果写入 replay_signals 表。"""
        signals_by_date = predict_result.get("signals_by_date", {})
        total_signals = 0
        errors: list[str] = list(predict_result.get("errors", []))

        for date_str, pairs in signals_by_date.items():
            trade_date = date.fromisoformat(date_str)
            rows: list[ReplaySignal] = []
            for symbol, score in pairs:
                rows.append(
                    ReplaySignal(
                        session_id=session_id,
                        trade_date=trade_date,
                        symbol=symbol,
                        score=score,
                    )
                )

            if not rows:
                continue

            try:
                # 幂等：先删后插
                await db.execute(
                    text(
                        "DELETE FROM replay_signals "
                        "WHERE session_id = :sid AND trade_date = :td"
                    ),
                    {"sid": session_id, "td": trade_date},
                )
                db.add_all(rows)
                await db.flush()
                total_signals += len(rows)
            except Exception as exc:
                await db.rollback()
                msg = f"{date_str} 写入 replay_signals 失败: {exc}"
                logger.error(msg)
                errors.append(msg)

        try:
            await db.commit()
        except Exception as exc:
            await db.rollback()
            errors.append(f"最终 commit 失败: {exc}")

        return {
            "total_days": predict_result.get("total_days", 0),
            "total_signals": total_signals,
            "errors": errors,
        }


# ---------------------------------------------------------------------------
# ReplaySignalLoader: day_runner 用它替代 SignalLoader
# ---------------------------------------------------------------------------


class ReplaySignalLoader:
    """从 replay_signals 表加载指定会话、指定交易日的信号。

    接口与 SignalLoader.load_signals_for_date 兼容，返回 list[SignalScore]。
    """

    async def load_signals_for_date(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        trade_date: date,
        min_score: float | None = None,
        limit: int | None = None,
    ) -> list:
        """加载指定会话、指定交易日的回放信号。

        min_score 默认 None（不过滤），因为 LightGBM 回归输出可正可负，
        过滤策略由 RebalanceCalculator 的 topk/min_score 参数决定。
        """
        from backend.services.trade.simulation.services.signal_loader import SignalScore

        conditions = ["session_id = :sid", "trade_date = :td"]
        params: dict[str, Any] = {
            "sid": session_id,
            "td": trade_date,
            "lim": limit or 1000,
        }
        if min_score is not None:
            conditions.append("score >= :min_score")
            params["min_score"] = min_score

        query = text(f"""
            SELECT symbol, score, trade_date
            FROM replay_signals
            WHERE {" AND ".join(conditions)}
            ORDER BY score DESC
            LIMIT :lim
        """)
        try:
            rows = (await db.execute(query, params)).fetchall()
            return [
                SignalScore(
                    symbol=str(row[0]).upper(),
                    score=float(row[1]),
                    trade_date=row[2],
                    run_id="replay",
                    tenant_id="replay",
                    user_id="replay",
                )
                for row in rows
            ]
        except Exception as exc:
            logger.error("ReplaySignalLoader: 加载信号失败 %s", exc)
            return []


replay_signal_loader = ReplaySignalLoader()
