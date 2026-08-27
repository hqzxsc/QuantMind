#!/usr/bin/env python3
"""更新 model_features_{year}.parquet，从 QuantDB 本地 parquet 读取数据。

数据源: QuantDB 本地 parquet (daily_forward 前复权日线 + 估值 + 技术指标 + 行业/概念)
口径:   前复权 (daily_forward) — 因子计算需要连续价格序列，消除除权除息缺口。
        撮合/行情层使用 daily_unadjusted (不复权)，两者口径有意区分。

用法:
    python update_feature_parquet.py                    # 自动补充所有缺失日期
    python update_feature_parquet.py --since 2026-05-23  # 从指定日期开始
    python update_feature_parquet.py --rebuild           # 重建全部日期
    python update_feature_parquet.py --dry-run           # 仅检查，不写入
"""

import argparse
import os
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)

# ── 路径配置 ──
if os.path.exists("/app") and not os.environ.get("QUANTMIND_HOST_MODE"):
    FEATURE_SNAPSHOT_DIR = Path("/app/db/feature_snapshots")
    QDB_DATA_DIR = Path(os.environ.get("QM_QUANTDB_DATA_DIR", "/data/quantdb"))
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    FEATURE_SNAPSHOT_DIR = PROJECT_ROOT / "db" / "feature_snapshots"
    QDB_DATA_DIR = Path(os.environ.get("QM_QUANTDB_DATA_DIR", str(PROJECT_ROOT / "data" / "quantdb")))

# QuantDB 子目录
QDB_KLINE_DIR = QDB_DATA_DIR / "1_kline_data"
QDB_SECTOR_DIR = QDB_DATA_DIR / "2_base_sector"
QDB_FIN_DIR = QDB_DATA_DIR / "3_financial_data"
QDB_TECH_DIR = QDB_DATA_DIR / "5_technical_derived"

# 默认 lookback: 250 交易日 ≈ 1 年，确保 mom_ret_120d / ma120 / ma60 等有足够窗口
DEFAULT_LOOKBACK_DAYS = 250


def _parquet_path_for_year(year: int) -> Path:
    return FEATURE_SNAPSHOT_DIR / f"model_features_{year}.parquet"


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ═══════════════════════════════════════════════════════════════════════════
# 数据读取 — QuantDB 本地 parquet
# ═══════════════════════════════════════════════════════════════════════════

# 从 QuantDB 读取的列（用于特征计算 + 辅助列）
DB_OHLCV_COLS = [
    "symbol", "trade_date", "open", "high", "low", "close", "volume", "amount", "adj_factor",
]
DB_FUNDAMENTAL_COLS = [
    "pe_ttm", "pb", "roe", "bp", "ep_ttm", "ln_mv_total", "float_mv", "total_mv",
    "industry", "is_st", "listing_market",
]
DB_INDEX_COLS = [
    "idx_all", "idx_hs300", "idx_zz1000", "idx_chinext", "idx_margin",
]
DB_CONCEPT_COLS = [
    "concept_ai", "concept_chip", "concept_new_energy", "concept_pv",
    "concept_military", "concept_medical", "concept_fintech",
    "concept_consumption", "concept_state_owned", "concept_lithium",
]
DB_TECHNICAL_COLS = [
    "return_1d", "return_5d", "return_20d", "ma5", "ma20", "ma60",
    "rsi_14", "kdj_k", "macd_hist", "vol_std_20", "vol_atr_14",
    "turnover_rate", "beta_20",
    "flow_net_amount", "volume_ma_5", "amount_ma_5",
]

ALL_DB_COLS = list(dict.fromkeys(
    DB_OHLCV_COLS + DB_FUNDAMENTAL_COLS + DB_INDEX_COLS + DB_CONCEPT_COLS + DB_TECHNICAL_COLS
))


def _read_kline_forward(since: date, until: date) -> pd.DataFrame:
    """从 QuantDB daily_forward parquet 读取前复权 OHLCV。

    目录结构: 1_kline_data/daily_forward/dt=YYYYMMDD/*.parquet
    每个文件列: symbol, time, open, high, low, close, volume, amount, ...
    """
    kline_dir = QDB_KLINE_DIR / "daily_forward"
    if not kline_dir.exists():
        _log(f"  daily_forward 目录不存在: {kline_dir}")
        return pd.DataFrame()

    # 扫描日期分区目录
    parts = []
    for dt_dir in sorted(kline_dir.iterdir()):
        if not dt_dir.is_dir() or not dt_dir.name.startswith("dt="):
            continue
        dt_str = dt_dir.name[3:]  # "dt=20240304" → "20240304"
        try:
            dt = date(int(dt_str[:4]), int(dt_str[4:6]), int(dt_str[6:8]))
        except ValueError:
            continue
        if dt < since or dt > until:
            continue
        for pf in dt_dir.glob("*.parquet"):
            parts.append(pf)

    if not parts:
        return pd.DataFrame()

    _log(f"  读取 daily_forward: {len(parts)} 个分区文件")
    dfs = [pd.read_parquet(p, columns=["symbol", "time", "open", "high", "low", "close", "volume", "amount"])
           for p in parts]
    df = pd.concat(dfs, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["time"]).dt.date
    df = df.drop(columns=["time"])
    # 确保数值类型
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _read_valuation(since: date, until: date) -> pd.DataFrame:
    """从 QuantDB valuation parquet 读取估值数据 (pe_ttm, pb, total_mv, float_mv 等).

    目录结构: 5_technical_derived/valuation/dt=YYYYMMDD/data.parquet
    """
    val_dir = QDB_TECH_DIR / "valuation"
    if not val_dir.exists():
        return pd.DataFrame()

    parts = []
    for dt_dir in sorted(val_dir.iterdir()):
        if not dt_dir.is_dir() or not dt_dir.name.startswith("dt="):
            continue
        dt_str = dt_dir.name[3:]
        try:
            dt = date(int(dt_str[:4]), int(dt_str[4:6]), int(dt_str[6:8]))
        except ValueError:
            continue
        if dt < since or dt > until:
            continue
        pf = dt_dir / "data.parquet"
        if pf.exists():
            parts.append(pf)

    if not parts:
        return pd.DataFrame()

    _log(f"  读取 valuation: {len(parts)} 个分区文件")
    cols = ["symbol", "time", "pe_ttm", "pb", "total_mv", "float_mv",
            "net_profit_ttm", "equity", "circulating_capital", "total_capital"]
    dfs = []
    for p in parts:
        try:
            available = pd.read_parquet(p).columns.tolist()
            use_cols = [c for c in cols if c in available]
            dfs.append(pd.read_parquet(p, columns=use_cols))
        except Exception:
            continue
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["time"]).dt.date
    df = df.drop(columns=["time"], errors="ignore")
    # 计算衍生列
    if "pe_ttm" in df.columns:
        df["ep_ttm"] = 1.0 / df["pe_ttm"].replace(0, np.nan)
    if "pb" in df.columns:
        df["bp"] = 1.0 / df["pb"].replace(0, np.nan)
    if "total_mv" in df.columns:
        df["ln_mv_total"] = np.log(df["total_mv"].clip(lower=1))
    if "equity" in df.columns and "net_profit_ttm" in df.columns:
        df["roe"] = (df["net_profit_ttm"] / df["equity"].clip(lower=1)).clip(-5, 5)
    return df


def _read_technical_indicators(since: date, until: date) -> pd.DataFrame:
    """从 QuantDB technical_indicators parquet 读取技术指标.

    目录结构: 5_technical_derived/technical_indicators/dt=YYYYMMDD/data.parquet
    """
    ti_dir = QDB_TECH_DIR / "technical_indicators"
    if not ti_dir.exists():
        return pd.DataFrame()

    parts = []
    for dt_dir in sorted(ti_dir.iterdir()):
        if not dt_dir.is_dir() or not dt_dir.name.startswith("dt="):
            continue
        dt_str = dt_dir.name[3:]
        try:
            dt = date(int(dt_str[:4]), int(dt_str[4:6]), int(dt_str[6:8]))
        except ValueError:
            continue
        if dt < since or dt > until:
            continue
        pf = dt_dir / "data.parquet"
        if pf.exists():
            parts.append(pf)

    if not parts:
        return pd.DataFrame()

    _log(f"  读取 technical_indicators: {len(parts)} 个分区文件")
    cols = ["symbol", "time", "return_1d", "return_5d", "return_20d",
            "ma5", "ma10", "ma20", "ma60", "rsi_6", "rsi_14",
            "kdj_k", "kdj_d", "kdj_j", "macd_dif", "macd_dea", "macd_hist",
            "vol_std_5", "vol_std_20", "vol_std_60", "vol_atr_14",
            "vol_to_ma5", "vol_to_ma20", "volume_ma_3", "amount_ma_5",
            "beta_20", "pct_change"]
    dfs = []
    for p in parts:
        try:
            available = pd.read_parquet(p).columns.tolist()
            use_cols = [c for c in cols if c in available]
            dfs.append(pd.read_parquet(p, columns=use_cols))
        except Exception:
            continue
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["time"]).dt.date
    df = df.drop(columns=["time"], errors="ignore")
    # 重命名 vol_to_ma5 → volume_ratio_5, vol_to_ma20 → volume_ratio_20
    if "vol_to_ma5" in df.columns:
        df["volume_ratio_5"] = df.pop("vol_to_ma5")
    if "vol_to_ma20" in df.columns:
        df["volume_ratio_20"] = df.pop("vol_to_ma20")
    return df


def _read_instrument_detail() -> pd.DataFrame:
    """从 instrument_detail.parquet 读取行业编码、ST 标识、复权因子等静态信息。

    返回: DataFrame[symbol, is_st, industry, adj_factor, ind_code_l1, listing_market]
    """
    ind_path = QDB_SECTOR_DIR / "instrument_detail" / "instrument_list.parquet"
    if not ind_path.exists():
        ind_path = QDB_SECTOR_DIR / "instrument_detail" / "instrument_detail.parquet"
    if not ind_path.exists():
        _log(f"  instrument_detail.parquet 不存在: {ind_path}")
        return pd.DataFrame()

    _log("  读取 instrument_detail")
    use_cols = ["Symbol", "IsSTGP", "rs_hycode_sim", "rs_hyname",
                "ZAF", "tdx_dycode", "tdx_dyname", "BelongHS300", "BelongRZRQ"]
    available = pd.read_parquet(ind_path).columns.tolist()
    read_cols = [c for c in use_cols if c in available]
    df = pd.read_parquet(ind_path, columns=read_cols)

    # 统一 symbol 格式: "000001.SZ" (已是后缀格式)
    df = df.rename(columns={"Symbol": "symbol"})
    df["symbol"] = df["symbol"].astype(str).str.strip()

    # is_st: IsSTGP 是字符串 "0"/"1"
    if "IsSTGP" in df.columns:
        df["is_st"] = pd.to_numeric(df["IsSTGP"], errors="coerce").fillna(0).astype(int)
    else:
        df["is_st"] = 0

    # industry: 使用 rs_hyname (行业名称)
    if "rs_hyname" in df.columns:
        df["industry"] = df["rs_hyname"].astype(str).str.strip()
        df.loc[df["industry"].isin(["", "nan", "None"]), "industry"] = np.nan
    else:
        df["industry"] = np.nan

    # adj_factor: daily_forward 已是前复权，factor=1.0
    # (instrument_detail.ZAF 是涨跌幅不是复权因子，不可用作 adj_factor)
    if "adj_factor" not in df.columns:
        df["adj_factor"] = 1.0
    else:
        df["adj_factor"] = 1.0  # 前复权数据 factor 恒 1.0

    # listing_market: 从 tdx_dycode 推断
    if "tdx_dycode" in df.columns:
        df["listing_market"] = df["tdx_dycode"].astype(str).apply(
            lambda x: "SH" if x in ("1", "7") else ("SZ" if x in ("2", "8") else "BJ")
        )
    else:
        df["listing_market"] = "Unknown"

    # 指数成分标记
    if "BelongHS300" in df.columns:
        df["idx_hs300"] = pd.to_numeric(df["BelongHS300"], errors="coerce").fillna(0).astype(int)
    else:
        df["idx_hs300"] = 0
    if "BelongRZRQ" in df.columns:
        df["idx_margin"] = pd.to_numeric(df["BelongRZRQ"], errors="coerce").fillna(0).astype(int)
    else:
        df["idx_margin"] = 0

    # ind_code_l1: rs_hycode_sim → CatBoost 整数编码
    if "rs_hycode_sim" in df.columns:
        df["ind_code_l1"] = pd.Categorical(df["rs_hycode_sim"]).codes.astype(np.float32)
        df.loc[df["rs_hycode_sim"].isna() | (df["rs_hycode_sim"] == ""), "ind_code_l1"] = -1
    else:
        df["ind_code_l1"] = -1.0

    df["ind_code_l2"] = -1.0
    df["idx_all"] = 1  # 所有 A 股
    df["idx_zz1000"] = 0
    df["idx_chinext"] = 0

    return df[["symbol", "is_st", "industry", "adj_factor", "listing_market",
               "idx_all", "idx_hs300", "idx_zz1000", "idx_chinext", "idx_margin",
               "ind_code_l1", "ind_code_l2"]]


def _read_sector_concepts() -> pd.DataFrame:
    """从 sector_members.parquet 读取概念标签，转为 0/1 列。

    返回: DataFrame[symbol, concept_ai, concept_chip, ...]
    """
    sm_path = QDB_SECTOR_DIR / "sector_concept" / "sector_members.parquet"
    if not sm_path.exists():
        _log(f"  sector_members.parquet 不存在: {sm_path}")
        return pd.DataFrame()

    _log("  读取 sector_concept")
    df = pd.read_parquet(sm_path)

    # 概念名称 → 列名映射
    CONCEPT_MAP = {
        "人工智能": "concept_ai", "AI": "concept_ai",
        "芯片": "concept_chip", "半导体": "concept_chip",
        "新能源": "concept_new_energy",
        "光伏": "concept_pv",
        "军工": "concept_military", "国防": "concept_military",
        "医药": "concept_medical", "医疗": "concept_medical",
        "金融科技": "concept_fintech", "互金": "concept_fintech",
        "消费": "concept_consumption",
        "国企": "concept_state_owned", "央企": "concept_state_owned",
        "锂电": "concept_lithium", "锂电池": "concept_lithium",
    }

    # 过滤概念类型
    concept_df = df[df.get("SectorType", "").astype(str).str.contains("概念", na=False)] if "SectorType" in df.columns else df

    # 构建映射
    sym_col = "Symbol" if "Symbol" in concept_df.columns else "symbol"
    concept_df = concept_df.rename(columns={sym_col: "symbol", "SectorName": "concept_name"})
    concept_df["symbol"] = concept_df["symbol"].astype(str).str.strip()
    concept_df["col_name"] = concept_df["concept_name"].map(CONCEPT_MAP)

    valid = concept_df[concept_df["col_name"].notna()]
    if valid.empty:
        # 返回空 DataFrame 带所有 concept 列
        return pd.DataFrame(columns=["symbol"] + list(CONCEPT_MAP.values()))

    # pivot: symbol × concept_col → 0/1
    pivot = valid.groupby(["symbol", "col_name"]).size().reset_index(name="_cnt")
    pivot = pivot.pivot(index="symbol", columns="col_name", values="_cnt").fillna(0).astype(int)
    pivot.columns = list(pivot.columns)  # flatten

    # 确保所有 concept 列都存在
    for col in CONCEPT_MAP.values():
        if col not in pivot.columns:
            pivot[col] = 0

    pivot = pivot.reset_index()
    return pivot


def fetch_data_from_quantdb(since: date, until: date, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> pd.DataFrame:
    """从 QuantDB 本地 parquet 读取全部数据（含 lookback 窗口）。

    合并 daily_forward (OHLCV) + valuation (估值) + technical_indicators (技术指标)
         + instrument_detail (行业/ST/复权因子) + sector_concepts (概念标签)
    """
    data_since = since - timedelta(days=lookback_days)

    # 1. 前复权 OHLCV
    kline = _read_kline_forward(data_since, until)
    if kline.empty:
        _log("  daily_forward 无数据")
        return pd.DataFrame()
    _log(f"  OHLCV: {len(kline):,} 行, {kline['symbol'].nunique()} 只股票")

    # 2. 估值数据
    val = _read_valuation(data_since, until)
    if not val.empty:
        _log(f"  估值: {len(val):,} 行")

    # 3. 技术指标
    ti = _read_technical_indicators(data_since, until)
    if not ti.empty:
        _log(f"  技术指标: {len(ti):,} 行")

    # 4. 静态信息 (行业/ST/复权因子/指数成分)
    inst = _read_instrument_detail()
    if not inst.empty:
        _log(f"  instrument_detail: {len(inst)} 只股票")

    # 5. 概念标签
    concepts = _read_sector_concepts()
    if not concepts.empty:
        _log(f"  概念标签: {len(concepts)} 只股票")

    # ── 合并 ──
    df = kline

    # 合并估值 (按 symbol + trade_date)
    if not val.empty:
        val_cols = [c for c in val.columns if c not in df.columns]
        if val_cols:
            df = df.merge(val[["symbol", "trade_date"] + val_cols],
                          on=["symbol", "trade_date"], how="left")

    # 合并技术指标 (按 symbol + trade_date)
    if not ti.empty:
        ti_cols = [c for c in ti.columns if c not in df.columns]
        if ti_cols:
            df = df.merge(ti[["symbol", "trade_date"] + ti_cols],
                          on=["symbol", "trade_date"], how="left")

    # 合并静态信息 (按 symbol, 广播到所有日期)
    if not inst.empty:
        inst_cols = [c for c in inst.columns if c not in df.columns]
        if inst_cols:
            df = df.merge(inst[["symbol"] + inst_cols], on="symbol", how="left")

    # 合并概念标签 (按 symbol, 广播到所有日期)
    if not concepts.empty:
        concept_cols = [c for c in concepts.columns if c not in df.columns]
        if concept_cols:
            df = df.merge(concepts[["symbol"] + concept_cols], on="symbol", how="left")

    # 填充缺失的 concept / index 列为 0
    for col in DB_CONCEPT_COLS + DB_INDEX_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        else:
            df[col] = 0

    # 填充缺失的 is_st
    if "is_st" in df.columns:
        df["is_st"] = pd.to_numeric(df["is_st"], errors="coerce").fillna(0).astype(int)
    else:
        df["is_st"] = 0

    # 填充缺失的 adj_factor
    if "adj_factor" not in df.columns:
        df["adj_factor"] = 1.0
    else:
        df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce").fillna(1.0)

    # 排序
    df = df.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    return df


# ═══════════════════════════════════════════════════════════════════════════
# 特征计算
# ═══════════════════════════════════════════════════════════════════════════

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9) -> tuple:
    low_n = low.rolling(n, min_periods=1).min()
    high_n = high.rolling(n, min_periods=1).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def _macd(close: pd.Series) -> tuple:
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)
    hist = dif - dea
    return dif, dea, hist


# All feature columns added by _compute_features_core.
# Used to add NaN columns when all rows are suspended.
FEATURE_COLS = [
    # 动量
    "mom_ret_1d", "mom_ret_5d", "mom_ret_10d", "mom_ret_20d",
    "mom_ma_gap_5", "mom_ma_gap_20", "mom_macd_hist", "mom_rsi_14", "mom_kdj_k",
    "mom_breakout_20d",
    # 波动率
    "vol_std_20", "vol_atr_14", "vol_parkinson_20", "vol_gk_20", "vol_rs_20",
    "vol_downside_20", "vol_realized_rv", "vol_jump_zadj",
    # 流动性
    "liq_volume", "liq_amount", "liq_turnover_os", "liq_volume_ma_20",
    "liq_volume_ratio_5", "liq_amount_ma_20", "liq_amount_ratio_5",
    "liq_mfi_14", "liq_amihud_20", "liq_amihud_60", "liq_accdist_20",
    # 资金流
    "flow_net_amount", "flow_net_amount_ratio", "flow_large_net_amount",
    "flow_vpin", "flow_vpin_ma_5", "flow_vpin_ma_20",
    # 风格
    "style_ln_mv_total", "style_ln_mv_float", "style_beta_20", "style_beta_60",
    "style_idio_vol_20", "style_residual_ret_20",
    # 行业
    "ind_ret_1d", "ind_ret_5d", "ind_ret_10d", "ind_ret_20d",
    "ind_strength_20", "ind_strength_60", "ind_momentum_rank_20",
    "ind_vol_20", "ind_turnover_20", "ind_amount_20",
    "ind_dispersion_20", "ind_up_breadth_20", "ind_down_breadth_20",
    "ind_relative_volume_20", "ind_relative_volatility_20", "ind_relative_flow_20",
    "ind_value_rank", "ind_size_rank",
    # 新增动量
    "mom_ret_3d", "mom_ret_60d", "mom_ret_120d",
    "mom_ma_gap_10", "mom_ma_gap_60", "mom_ma_gap_120",
    "mom_ema_gap_12", "mom_ema_gap_26", "mom_roc_12",
    # 新增波动率
    "vol_std_10", "vol_atr_20", "vol_true_range", "vol_parkinson_10",
    "vol_gk_10", "vol_rs_10", "vol_upside_20", "vol_realized_rrv",
    "vol_realized_rskew", "vol_realized_rkurt", "vol_jump_rjv_ratio", "vol_jump_sjv_ratio",
    # 新增流动性
    "liq_turnover_tl", "liq_volume_ma_5", "liq_volume_ma_10",
    "liq_volume_ratio_20", "liq_amount_ma_5", "liq_amount_ma_10",
    "liq_amount_ratio_20", "liq_obv_20", "liq_obv_60",
    # 新增资金流
    "flow_vpin_delta_5", "flow_net_order_count", "flow_net_order_ratio", "flow_pressure_index",
    # 新增风格
    "style_beta_120", "style_idio_vol_60", "style_bp", "style_ep_ttm",
    # 辅助
    "factor", "pctchange",
    # DB 基本面 (numeric only)
    "pe_ttm", "pb", "roe", "bp", "ep_ttm", "ln_mv_total", "float_mv", "total_mv",
    # 指数成分 / 概念
    "idx_all", "idx_hs300", "idx_zz1000", "idx_chinext", "idx_margin",
    "concept_ai", "concept_chip", "concept_new_energy", "concept_pv",
    "concept_military", "concept_medical", "concept_fintech",
    "concept_consumption", "concept_state_owned", "concept_lithium",
    # DB 技术指标
    "return_1d", "return_5d", "return_20d", "ma5", "ma20", "ma60",
    "rsi_14", "rsi_6", "kdj_k", "kdj_d", "kdj_j",
    "macd_hist", "macd_dif", "macd_dea",
    "ma_gap_5", "ma_gap_20",
    "vol_std_5", "vol_std_60", "vol_atr_14",
    "volume_ratio_5", "volume_ratio_20", "volume_ma_5", "volume_ma_3", "amount_ma_5",
    "turnover_rate", "beta_20",
    "mom_macd_dif", "mom_macd_dea", "mom_rsi_6", "mom_kdj_d", "mom_kdj_j",
    # Alpha158 K线
    "kline_kmid", "kline_klen", "kline_kmid2", "kline_kup", "kline_kup2",
    "kline_klow", "kline_klow2", "kline_ksft", "kline_ksft2",
    # Alpha158 价格相对
    "prel_open0", "prel_high0", "prel_low0", "prel_vwap0",
    # 价格位置
    "price_position_20", "price_position_60",
    "dist_to_high_20", "dist_to_low_20", "ret_rank_20",
    # 波动率调整动量
    "mom_sharpe_5", "mom_sharpe_20", "mom_sharpe_60", "mom_risk_adj_20",
    # 量价配合
    "pv_corr_20", "pv_corr_10", "up_volume_ratio_20", "pv_divergence_20",
    # 趋势质量
    "trend_r2_20", "trend_slope_20", "consecutive_updown_5",
    # 时序滞后
    "ret_1d_lag1", "ret_1d_lag2",
    # ═══ 第六梯队新增因子 ═══
    # 波动率曲面
    "vol_smile_20", "vol_term_structure", "vol_of_vol",
    # 技术形态
    "tech_bollinger_position", "tech_williams_r_14", "tech_cci_20",
    # Alpha101 风格
    "alpha_decay_ret_10", "alpha_corr_cv_20", "alpha_tsrank_ret_20", "alpha_tsrank_volume_20",
    # Alpha360 补充
    "alpha_high_20d_ratio", "alpha_low_20d_ratio", "alpha_close_open_gap",
    # 基本面补充
    "fund_pe_percentile", "fund_pb_percentile",
    # 行业编码 (CatBoost cat_features, 从 instrument_detail 填充)
    "ind_code_l1", "ind_code_l2",
    # 分类列 (keep as-is, no NaN fill needed)
    # "industry", "is_st", "listing_market",
]


def _add_nan_features(g: pd.DataFrame) -> pd.DataFrame:
    """Add all feature columns as NaN (for fully-suspended stocks)."""
    missing = [c for c in FEATURE_COLS if c not in g.columns]
    if missing:
        nan_df = pd.DataFrame(np.nan, index=g.index, columns=missing)
        g = pd.concat([g, nan_df], axis=1)
    # Ensure is_st is int (not string) to avoid mixed-type parquet errors
    if "is_st" in g.columns:
        g["is_st"] = pd.to_numeric(g["is_st"], errors="coerce").fillna(0).astype(int)
    return g


def compute_features_for_group(g: pd.DataFrame) -> pd.DataFrame:
    """为单只股票计算全部特征。输入需按 trade_date 排序。"""
    g = g.sort_values("trade_date").copy()

    # 过滤停牌/零价格行：close<=0 或 volume=0 视为停牌，不参与特征计算
    suspended = (g["close"] <= 0) | (g["volume"] == 0)
    if suspended.any():
        # 停牌行的特征保持 NaN，只对有效行计算
        valid = g[~suspended].copy()
        if len(valid) < 2:
            return _add_nan_features(g)  # 数据太少，特征全部填 NaN
        feat = _compute_features_core(valid)
        result = g.copy()
        # 特征回写前的类型对齐：部分市场以空串填充 is_st/listing_market
        # （str 列），而特征计算会输出数值列，直接回写会触发 arrow-string
        # 类型错误。仅当 feat 侧为数值且 result 侧为字符串时规整。
        for col in set(feat.columns) & set(result.columns):
            if (
                col in ("is_st", "listing_market")
                and pd.api.types.is_numeric_dtype(feat[col])
                and not pd.api.types.is_numeric_dtype(result[col])
            ):
                result[col] = (
                    pd.to_numeric(result[col], errors="coerce").fillna(0).astype(int)
                )
        # Add missing feature columns in one batch to avoid fragmentation
        missing = [c for c in feat.columns if c not in result.columns]
        if missing:
            nan_df = pd.DataFrame(np.nan, index=result.index, columns=missing)
            result = pd.concat([result, nan_df], axis=1)
        for col in feat.columns:
            result.loc[feat.index, col] = feat[col].values
        return result

    return _compute_features_core(g)


def _compute_features_core(g: pd.DataFrame) -> pd.DataFrame:
    """实际特征计算逻辑（假设输入数据无停牌）。"""
    c = g["close"]
    h = g["high"]
    lo = g["low"]
    v = g["volume"]
    amt = g["amount"]
    ret = c.pct_change().clip(lower=-1.0, upper=10.0)  # 限制收益率范围，防止 inf
    ln_c = np.log(c.clip(lower=1e-8))
    log_ret = ln_c.diff()
    # 替换 inf 为 NaN
    ret = ret.replace([np.inf, -np.inf], np.nan)
    log_ret = log_ret.replace([np.inf, -np.inf], np.nan)

    # ═══ 原有 51 个特征 ═══

    # ── 动量 ──
    g["mom_ret_1d"] = ret
    g["mom_ret_5d"] = c.pct_change(5)
    g["mom_ret_10d"] = c.pct_change(10)
    g["mom_ret_20d"] = c.pct_change(20)
    g["mom_ma_gap_5"] = (c / c.rolling(5, min_periods=1).mean()) - 1
    g["mom_ma_gap_20"] = (c / c.rolling(20, min_periods=1).mean()) - 1
    g["mom_macd_hist"] = _macd(c)[2]
    g["mom_rsi_14"] = _rsi(c, 14)
    g["mom_kdj_k"] = _kdj(h, lo, c)[0]
    g["mom_breakout_20d"] = (c / c.rolling(20, min_periods=1).max()) - 1

    # ── 波动率 ──
    g["vol_std_20"] = log_ret.rolling(20, min_periods=5).std()
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    g["vol_atr_14"] = tr.rolling(14, min_periods=1).mean()
    hl_ratio = np.log(h / lo.clip(lower=1e-8))
    g["vol_parkinson_20"] = np.sqrt((hl_ratio ** 2).rolling(20, min_periods=5).mean() / (4 * np.log(2)))
    g["vol_gk_20"] = np.sqrt(
        (0.5 * (ln_c.diff() ** 2) - (2 * np.log(2) - 1) * (log_ret ** 2))
        .rolling(20, min_periods=5).mean().clip(lower=0)
    )
    g["vol_rs_20"] = np.sqrt((log_ret.clip(lower=0) ** 2).rolling(20, min_periods=5).mean())
    neg_ret = log_ret.clip(upper=0)
    g["vol_downside_20"] = neg_ret.rolling(20, min_periods=5).std()
    g["vol_realized_rv"] = np.sqrt((log_ret ** 2).rolling(20, min_periods=5).mean() * 252)
    rv = log_ret.rolling(20, min_periods=5).std()
    bv = (log_ret.abs() * log_ret.shift().abs()).rolling(20, min_periods=5).mean()
    bv = bv.clip(lower=1e-12)
    g["vol_jump_zadj"] = ((rv ** 2) / bv).clip(upper=10).fillna(0)

    # ── 流动性 ──
    g["liq_volume"] = v
    g["liq_amount"] = amt
    g["liq_turnover_os"] = v / v.rolling(250, min_periods=20).mean().clip(lower=1)
    g["liq_volume_ma_20"] = v.rolling(20, min_periods=1).mean()
    g["liq_volume_ratio_5"] = v / v.rolling(5, min_periods=1).mean().clip(lower=1) - 1
    g["liq_amount_ma_20"] = amt.rolling(20, min_periods=1).mean()
    g["liq_amount_ratio_5"] = amt / amt.rolling(5, min_periods=1).mean().clip(lower=1) - 1
    tp = (h + lo + c) / 3
    mf = tp * v
    pos_mf = mf * (tp > tp.shift()).astype(float)
    neg_mf = mf * (tp <= tp.shift()).astype(float)
    mfr = pos_mf.rolling(14, min_periods=1).sum() / neg_mf.rolling(14, min_periods=1).sum().replace(0, np.nan)
    g["liq_mfi_14"] = 100 - (100 / (1 + mfr))
    abs_ret = ret.abs()
    g["liq_amihud_20"] = (abs_ret / amt.clip(lower=1)).rolling(20, min_periods=1).mean()
    g["liq_amihud_60"] = (abs_ret / amt.clip(lower=1)).rolling(60, min_periods=5).mean()
    clv = ((c - lo) - (h - c)) / (h - lo).replace(0, np.nan)
    clv = clv.fillna(0)
    g["liq_accdist_20"] = (clv * v).rolling(20, min_periods=1).sum()

    # ── 资金流 ──
    direction = np.sign(c.diff())
    g["flow_net_amount"] = (amt * direction).rolling(5, min_periods=1).sum()
    g["flow_net_amount_ratio"] = g["flow_net_amount"] / amt.rolling(20, min_periods=1).sum().clip(lower=1)
    # 大单净流入: 使用 DB 的 main_flow 列，或 fallback 用高金额交易日近似
    if "main_flow" in g.columns:
        g["flow_large_net_amount"] = pd.to_numeric(g["main_flow"], errors="coerce").fillna(0)
    else:
        # Fallback: 大单定义 - 单笔成交额 > 20日均值的 3 倍
        amt_threshold = amt.rolling(20, min_periods=5).mean() * 3
        is_large = amt > amt_threshold
        g["flow_large_net_amount"] = (amt * direction * is_large).rolling(5, min_periods=1).sum()
    buy_vol = v * (c > c.shift()).astype(float)
    sell_vol = v * (c <= c.shift()).astype(float)
    g["flow_vpin"] = (buy_vol - sell_vol).abs().rolling(20, min_periods=5).sum() / v.rolling(20, min_periods=5).sum().clip(lower=1)
    g["flow_vpin_ma_5"] = g["flow_vpin"].rolling(5, min_periods=1).mean()
    g["flow_vpin_ma_20"] = g["flow_vpin"].rolling(20, min_periods=1).mean()

    # ── 风格因子 ──
    # 总市值: 使用 DB 的 total_mv，fallback 用成交额近似
    if "total_mv" in g.columns:
        total_mv = pd.to_numeric(g["total_mv"], errors="coerce")
        g["style_ln_mv_total"] = np.where(
            total_mv.notna() & (total_mv > 0),
            np.log(total_mv.clip(lower=1)),
            np.log(amt.clip(lower=1))
        )
    else:
        g["style_ln_mv_total"] = np.log(amt.clip(lower=1))
    # 流通市值: 使用 DB 的 float_mv，fallback 用 total_mv * 0.9
    if "float_mv" in g.columns:
        float_mv = pd.to_numeric(g["float_mv"], errors="coerce")
        g["style_ln_mv_float"] = np.where(
            float_mv.notna() & (float_mv > 0),
            np.log(float_mv.clip(lower=1)),
            g["style_ln_mv_total"] * 0.9
        )
    else:
        g["style_ln_mv_float"] = g["style_ln_mv_total"] * 0.9
    # beta = cov(ret, market) / var(market); proxy: rolling mean / rolling std
    ret_ma20 = ret.rolling(20, min_periods=5).mean()
    ret_std20 = ret.rolling(20, min_periods=5).std().clip(lower=1e-12)
    g["style_beta_20"] = ret_ma20 / ret_std20
    ret_ma60 = ret.rolling(60, min_periods=10).mean()
    ret_std60 = ret.rolling(60, min_periods=10).std().clip(lower=1e-12)
    g["style_beta_60"] = ret_ma60 / ret_std60
    g["style_idio_vol_20"] = log_ret.rolling(20, min_periods=5).std()
    g["style_residual_ret_20"] = ret.rolling(20, min_periods=5).mean()

    # ── 行业因子（占位，实际计算在 compute_all_features 中跨股票聚合）──
    g["ind_ret_1d"] = np.nan
    g["ind_ret_20d"] = np.nan
    g["ind_strength_20"] = np.nan
    g["ind_momentum_rank_20"] = np.nan

    # ═══ 新增动量特征（纯 OHLCV 计算，不依赖后续变量） ═══
    g["mom_ret_3d"] = c.pct_change(3)
    g["mom_ret_60d"] = c.pct_change(60)
    g["mom_ret_120d"] = c.pct_change(120)
    g["mom_ma_gap_10"] = (c / c.rolling(10, min_periods=1).mean()) - 1
    g["mom_ma_gap_60"] = (c / c.rolling(60, min_periods=1).mean()) - 1
    g["mom_ma_gap_120"] = (c / c.rolling(120, min_periods=1).mean()) - 1
    ema12 = _ema(c, 12)
    ema26 = _ema(c, 26)
    g["mom_ema_gap_12"] = (c / ema12) - 1
    g["mom_ema_gap_26"] = (c / ema26) - 1
    g["mom_roc_12"] = c.pct_change(12)

    # ═══ 新增波动率特征 ═══
    g["vol_std_10"] = log_ret.rolling(10, min_periods=3).std()
    g["vol_atr_20"] = tr.rolling(20, min_periods=1).mean()
    g["vol_true_range"] = tr  # raw true range
    g["vol_parkinson_10"] = np.sqrt((hl_ratio ** 2).rolling(10, min_periods=3).mean() / (4 * np.log(2)))
    g["vol_gk_10"] = np.sqrt(
        (0.5 * (ln_c.diff() ** 2) - (2 * np.log(2) - 1) * (log_ret ** 2))
        .rolling(10, min_periods=3).mean().clip(lower=0)
    )
    g["vol_rs_10"] = np.sqrt((log_ret.clip(lower=0) ** 2).rolling(10, min_periods=3).mean())
    pos_ret = log_ret.clip(lower=0)
    g["vol_upside_20"] = pos_ret.rolling(20, min_periods=5).std()
    # realized relative vol (rv / bv ratio)
    rv_20 = log_ret.rolling(20, min_periods=5).std()
    bv_20 = (log_ret.abs() * log_ret.shift().abs()).rolling(20, min_periods=5).mean().clip(lower=1e-12)
    g["vol_realized_rrv"] = (rv_20 / bv_20).clip(upper=10).fillna(0)
    # realized skewness & kurtosis (vectorized approximation)
    lr_mean = log_ret.rolling(20, min_periods=10).mean()
    lr_std = log_ret.rolling(20, min_periods=10).std().clip(lower=1e-12)
    lr_z = (log_ret - lr_mean) / lr_std
    g["vol_realized_rskew"] = (lr_z ** 3).rolling(20, min_periods=10).mean()
    g["vol_realized_rkurt"] = (lr_z ** 4).rolling(20, min_periods=10).mean() - 3
    # jump ratios
    rv_sq = rv_20 ** 2
    bv_val = bv_20
    g["vol_jump_rjv_ratio"] = (rv_sq / bv_val).clip(upper=10).fillna(0)
    bipower_var = (log_ret.abs() * log_ret.shift().abs()).rolling(20, min_periods=5).mean()
    jump_var = (rv_sq - bipower_var).clip(lower=0)
    g["vol_jump_sjv_ratio"] = (jump_var / rv_sq.replace(0, np.nan)).fillna(0).clip(upper=1)

    # ═══ 新增流动性特征 ═══
    # turnover_rate: 换手率 = volume / circulating_capital，QuantDB 不直接提供
    if "turnover_rate" in g.columns and g["turnover_rate"].notna().any():
        g["turnover_rate"] = pd.to_numeric(g["turnover_rate"], errors="coerce").fillna(
            v / v.rolling(250, min_periods=20).mean().clip(lower=1)
        )
    else:
        # Fallback: 用 volume / 250日均量 近似换手率
        g["turnover_rate"] = v / v.rolling(250, min_periods=20).mean().clip(lower=1)
    g["liq_turnover_tl"] = g["turnover_rate"]  # alias
    g["liq_volume_ma_5"] = v.rolling(5, min_periods=1).mean()
    g["liq_volume_ma_10"] = v.rolling(10, min_periods=1).mean()
    g["liq_volume_ratio_20"] = v / v.rolling(20, min_periods=1).mean().clip(lower=1) - 1
    g["liq_amount_ma_5"] = amt.rolling(5, min_periods=1).mean()
    g["liq_amount_ma_10"] = amt.rolling(10, min_periods=1).mean()
    g["liq_amount_ratio_20"] = amt / amt.rolling(20, min_periods=1).mean().clip(lower=1) - 1
    # OBV (On-Balance Volume)
    obv_direction = np.sign(c.diff())
    obv_raw = (v * obv_direction).cumsum()
    g["liq_obv_20"] = obv_raw - obv_raw.rolling(20, min_periods=1).mean()
    g["liq_obv_60"] = obv_raw - obv_raw.rolling(60, min_periods=1).mean()

    # ═══ 新增资金流特征 ═══
    g["flow_vpin_delta_5"] = g["flow_vpin"].diff(5)
    # approximate order count from volume pattern
    g["flow_net_order_count"] = (v * direction).rolling(5, min_periods=1).sum()
    g["flow_net_order_ratio"] = g["flow_net_order_count"] / v.rolling(20, min_periods=1).sum().clip(lower=1)
    # pressure index: cumulative money flow direction
    g["flow_pressure_index"] = (amt * direction).rolling(20, min_periods=1).sum() / amt.rolling(20, min_periods=1).sum().clip(lower=1)

    # ═══ 新增风格因子 ═══
    ret_ma120 = ret.rolling(120, min_periods=20).mean()
    ret_std120 = ret.rolling(120, min_periods=20).std().clip(lower=1e-12)
    g["style_beta_120"] = ret_ma120 / ret_std120
    g["style_idio_vol_60"] = log_ret.rolling(60, min_periods=10).std()
    # style_bp: 账面市值比，优先用 bp，fallback 用 1/pb
    if "bp" in g.columns:
        bp_val = pd.to_numeric(g["bp"], errors="coerce")
        pb_val = pd.to_numeric(g["pb"], errors="coerce") if "pb" in g.columns else pd.Series(np.nan, index=g.index)
        g["style_bp"] = bp_val.fillna(1.0 / pb_val.replace(0, np.nan))
    else:
        pb_val = pd.to_numeric(g["pb"], errors="coerce") if "pb" in g.columns else pd.Series(np.nan, index=g.index)
        g["style_bp"] = 1.0 / pb_val.replace(0, np.nan)
    # style_ep_ttm: 盈利收益率，优先用 ep_ttm，fallback 用 1/pe_ttm
    if "ep_ttm" in g.columns:
        ep_val = pd.to_numeric(g["ep_ttm"], errors="coerce")
        pe_val = pd.to_numeric(g["pe_ttm"], errors="coerce") if "pe_ttm" in g.columns else pd.Series(np.nan, index=g.index)
        g["style_ep_ttm"] = ep_val.fillna(1.0 / pe_val.replace(0, np.nan))
    else:
        pe_val = pd.to_numeric(g["pe_ttm"], errors="coerce") if "pe_ttm" in g.columns else pd.Series(np.nan, index=g.index)
        g["style_ep_ttm"] = 1.0 / pe_val.replace(0, np.nan)

    # ── 辅助列 ──
    g["factor"] = g["adj_factor"]
    g["pctchange"] = ret

    # ═══ 新增特征：从 DB 直接使用（已有值保留，NULL 填 0） ═══

    # 基本面
    for col in DB_FUNDAMENTAL_COLS:
        if col in g.columns:
            if col in ("industry", "is_st", "listing_market"):
                # 分类列保持原样
                pass
            else:
                g[col] = pd.to_numeric(g[col], errors="coerce").fillna(0)
        else:
            g[col] = 0

    # 指数成分 / 概念标签（0/1 标记）
    for col in DB_INDEX_COLS + DB_CONCEPT_COLS:
        if col in g.columns:
            g[col] = pd.to_numeric(g[col], errors="coerce").fillna(0).astype(int)
        else:
            g[col] = 0

    # 技术指标（DB 已计算的，优先用 DB 值，NULL 用 OHLCV 重算）
    if "return_1d" in g.columns:
        g["return_1d"] = pd.to_numeric(g["return_1d"], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(ret)
    else:
        g["return_1d"] = ret

    if "return_5d" in g.columns:
        g["return_5d"] = pd.to_numeric(g["return_5d"], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(c.pct_change(5).clip(lower=-1.0, upper=10.0).replace([np.inf, -np.inf], np.nan))
    else:
        g["return_5d"] = c.pct_change(5).clip(lower=-1.0, upper=10.0).replace([np.inf, -np.inf], np.nan)

    if "return_20d" in g.columns:
        g["return_20d"] = pd.to_numeric(g["return_20d"], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(c.pct_change(20).clip(lower=-1.0, upper=10.0).replace([np.inf, -np.inf], np.nan))
    else:
        g["return_20d"] = c.pct_change(20).clip(lower=-1.0, upper=10.0).replace([np.inf, -np.inf], np.nan)

    # 移动平均
    if "ma5" not in g.columns or g["ma5"].isna().all():
        g["ma5"] = c.rolling(5, min_periods=1).mean()
    else:
        g["ma5"] = pd.to_numeric(g["ma5"], errors="coerce").fillna(c.rolling(5, min_periods=1).mean())

    if "ma20" not in g.columns or g["ma20"].isna().all():
        g["ma20"] = c.rolling(20, min_periods=1).mean()
    else:
        g["ma20"] = pd.to_numeric(g["ma20"], errors="coerce").fillna(c.rolling(20, min_periods=1).mean())

    if "ma60" not in g.columns or g["ma60"].isna().all():
        g["ma60"] = c.rolling(60, min_periods=1).mean()
    else:
        g["ma60"] = pd.to_numeric(g["ma60"], errors="coerce").fillna(c.rolling(60, min_periods=1).mean())

    # 均线偏离
    g["ma_gap_5"] = (c / g["ma5"]) - 1
    g["ma_gap_20"] = (c / g["ma20"]) - 1

    # RSI
    if "rsi_14" not in g.columns or g["rsi_14"].isna().all():
        g["rsi_14"] = _rsi(c, 14)
    else:
        g["rsi_14"] = pd.to_numeric(g["rsi_14"], errors="coerce").fillna(_rsi(c, 14))

    g["rsi_6"] = _rsi(c, 6)

    # KDJ
    kdj_k, kdj_d, kdj_j = _kdj(h, lo, c)
    if "kdj_k" not in g.columns or g["kdj_k"].isna().all():
        g["kdj_k"] = kdj_k
    else:
        g["kdj_k"] = pd.to_numeric(g["kdj_k"], errors="coerce").fillna(kdj_k)
    g["kdj_d"] = kdj_d
    g["kdj_j"] = kdj_j

    # MACD
    macd_dif, macd_dea, macd_hist = _macd(c)
    if "macd_hist" not in g.columns or g["macd_hist"].isna().all():
        g["macd_hist"] = macd_hist
    else:
        g["macd_hist"] = pd.to_numeric(g["macd_hist"], errors="coerce").fillna(macd_hist)
    g["macd_dif"] = macd_dif
    g["macd_dea"] = macd_dea

    # 动量别名（catalog key 匹配）
    g["mom_macd_dif"] = macd_dif
    g["mom_macd_dea"] = macd_dea
    g["mom_rsi_6"] = g["rsi_6"]
    g["mom_kdj_d"] = kdj_d
    g["mom_kdj_j"] = kdj_j

    # 波动率补充
    g["vol_std_5"] = log_ret.rolling(5, min_periods=2).std()
    g["vol_std_60"] = log_ret.rolling(60, min_periods=10).std()
    if "vol_std_20" in g.columns:
        g["vol_std_20"] = pd.to_numeric(g["vol_std_20"], errors="coerce").fillna(log_ret.rolling(20, min_periods=5).std())
    if "vol_atr_14" in g.columns:
        g["vol_atr_14"] = pd.to_numeric(g["vol_atr_14"], errors="coerce").fillna(tr.rolling(14, min_periods=1).mean())

    # 成交量比率
    g["volume_ratio_5"] = v / v.rolling(5, min_periods=1).mean().clip(lower=1)
    g["volume_ratio_20"] = v / v.rolling(20, min_periods=1).mean().clip(lower=1)
    g["volume_ma_5"] = v.rolling(5, min_periods=1).mean()
    g["volume_ma_3"] = v.rolling(3, min_periods=1).mean()
    g["amount_ma_5"] = amt.rolling(5, min_periods=1).mean()

    # 换手率
    if "turnover_rate" in g.columns:
        g["turnover_rate"] = pd.to_numeric(g["turnover_rate"], errors="coerce").fillna(
            v / v.rolling(250, min_periods=20).mean().clip(lower=1)
        )
    else:
        g["turnover_rate"] = v / v.rolling(250, min_periods=20).mean().clip(lower=1)

    # Beta
    if "beta_20" in g.columns:
        g["beta_20"] = pd.to_numeric(g["beta_20"], errors="coerce").fillna(
            ret_ma20 / ret_std20
        )

    # 市值相关
    if "ln_mv_total" in g.columns:
        g["ln_mv_total"] = pd.to_numeric(g["ln_mv_total"], errors="coerce").fillna(np.log(amt.clip(lower=1)))

    # bp / ep_ttm
    if "bp" in g.columns:
        g["bp"] = pd.to_numeric(g["bp"], errors="coerce").fillna(0)
    if "ep_ttm" in g.columns:
        g["ep_ttm"] = pd.to_numeric(g["ep_ttm"], errors="coerce").fillna(0)

    # is_st
    if "is_st" in g.columns:
        g["is_st"] = pd.to_numeric(g["is_st"], errors="coerce").fillna(0).astype(int)

    # ═══ Alpha158 K 线形态因子 (9 个) ═══
    # 来源: Qlib Alpha158 — 仅需 OHLCV，无窗口依赖
    o = g["open"]
    denom = (h - lo).replace(0, np.nan)
    g["kline_kmid"] = (c - o) / o.clip(lower=1e-8)                              # 实体比
    g["kline_klen"] = (h - lo) / o.clip(lower=1e-8)                             # 振幅比
    g["kline_kmid2"] = (c - o) / denom                                          # 实体占振幅比
    g["kline_kup"] = (h - pd.concat([o, c], axis=1).max(axis=1)) / o.clip(lower=1e-8)   # 上影线比
    g["kline_kup2"] = (h - pd.concat([o, c], axis=1).max(axis=1)) / denom              # 上影线占振幅比
    g["kline_klow"] = (pd.concat([o, c], axis=1).min(axis=1) - lo) / o.clip(lower=1e-8) # 下影线比
    g["kline_klow2"] = (pd.concat([o, c], axis=1).min(axis=1) - lo) / denom             # 下影线占振幅比
    g["kline_ksft"] = (2 * c - h - lo) / o.clip(lower=1e-8)                     # 重心偏移
    g["kline_ksft2"] = (2 * c - h - lo) / denom                                  # 重心偏移归一化

    # ═══ Alpha158 价格相对因子 (4 个) ═══
    g["prel_open0"] = o / c.clip(lower=1e-8)                                    # 开盘/收盘
    g["prel_high0"] = h / c.clip(lower=1e-8)                                    # 最高/收盘
    g["prel_low0"] = lo / c.clip(lower=1e-8)                                    # 最低/收盘
    vwap = amt / v.clip(lower=1)                                                 # VWAP = 成交额/成交量
    vwap = vwap.replace([np.inf, -np.inf], np.nan).fillna((h + lo + c) / 3)     # fallback: 典型价格
    g["prel_vwap0"] = vwap / c.clip(lower=1e-8)                                 # VWAP/收盘

    # ═══ 第一梯队: 价格位置因子 (5 个) ═══
    # 价格在N日区间的位置 (0=最低, 1=最高)
    low_20 = lo.rolling(20, min_periods=1).min()
    high_20 = h.rolling(20, min_periods=1).max()
    g["price_position_20"] = (c - low_20) / (high_20 - low_20).clip(lower=1e-8)

    low_60 = lo.rolling(60, min_periods=1).min()
    high_60 = h.rolling(60, min_periods=1).max()
    g["price_position_60"] = (c - low_60) / (high_60 - low_60).clip(lower=1e-8)

    # 距离N日新高的回撤幅度
    g["dist_to_high_20"] = c / high_20.clip(lower=1e-8) - 1
    g["dist_to_low_20"] = c / low_20.clip(lower=1e-8) - 1

    # 20日内收益率排名 (0~1) — 向量化近似
    ret_1d = c.pct_change()
    ret_min_20 = ret_1d.rolling(20, min_periods=5).min()
    ret_max_20 = ret_1d.rolling(20, min_periods=5).max()
    g["ret_rank_20"] = ((ret_1d - ret_min_20) / (ret_max_20 - ret_min_20).clip(lower=1e-8)).replace([np.inf, -np.inf], np.nan)

    # ═══ 第二梯队: 波动率调整动量 (4 个) ═══
    # Sharpe型动量 = 收益 / 波动率
    ret_std_5 = ret_1d.rolling(5, min_periods=2).std()
    ret_std_20 = ret_1d.rolling(20, min_periods=5).std()
    ret_std_60 = ret_1d.rolling(60, min_periods=10).std()
    g["mom_sharpe_5"] = c.pct_change(5) / ret_std_5.clip(lower=1e-6)
    g["mom_sharpe_20"] = c.pct_change(20) / ret_std_20.clip(lower=1e-6)
    g["mom_sharpe_60"] = c.pct_change(60) / ret_std_60.clip(lower=1e-6)

    # 风险调整后的相对强度
    ret_20 = c.pct_change(20)
    g["mom_risk_adj_20"] = ((ret_20 - ret_20.rolling(20, min_periods=5).mean()) / ret_std_20.clip(lower=1e-6)).replace([np.inf, -np.inf], np.nan)

    # ═══ 第三梯队: 量价配合度 (4 个) ═══
    log_vol = np.log(v.clip(lower=1))

    # 量价相关性 (正=量价齐升)
    g["pv_corr_20"] = ret_1d.rolling(20, min_periods=10).corr(log_vol).clip(-1, 1).fillna(0)
    g["pv_corr_10"] = ret_1d.rolling(10, min_periods=5).corr(log_vol).clip(-1, 1).fillna(0)

    # 放量上涨占比 (20日里上涨日成交量占总量比)
    up_vol = pd.Series(np.where(ret_1d > 0, v, 0), index=g.index)
    g["up_volume_ratio_20"] = up_vol.rolling(20, min_periods=5).sum() / v.rolling(20, min_periods=5).sum().clip(lower=1e-6)

    # 量价背离 (价格排名 - 成交量排名) — 向量化近似
    c_min_20 = c.rolling(20, min_periods=5).min()
    c_max_20 = c.rolling(20, min_periods=5).max()
    v_min_20 = v.rolling(20, min_periods=5).min()
    v_max_20 = v.rolling(20, min_periods=5).max()
    c_rank = (c - c_min_20) / (c_max_20 - c_min_20).clip(lower=1e-8)
    v_rank = (v - v_min_20) / (v_max_20 - v_min_20).clip(lower=1e-8)
    g["pv_divergence_20"] = c_rank - v_rank

    # ═══ 第四梯队: 趋势质量因子 (3 个) ═══
    # 20日趋势R² — 向量化: R² = corr(price, time_index)²
    time_idx = pd.Series(np.arange(len(c), dtype=float), index=c.index)
    g["trend_r2_20"] = (c.rolling(20, min_periods=10).corr(time_idx) ** 2).clip(upper=1).fillna(0)

    # 20日趋势斜率 — 向量化: slope = corr * std(price) / std(t) / mean(price)
    c_std_20 = c.rolling(20, min_periods=10).std()
    t_std = np.sqrt((np.arange(20) - np.arange(20).mean()) ** 2).sum() / 20
    corr_ct = c.rolling(20, min_periods=10).corr(time_idx)
    g["trend_slope_20"] = corr_ct * c_std_20 / (t_std + 1e-6) / c.rolling(20, min_periods=10).mean().clip(lower=1e-6)

    # 连续上涨/下跌强度 (5日涨跌天数差)
    up_down = pd.Series(np.where(ret_1d > 0, 1, np.where(ret_1d < 0, -1, 0)), index=g.index)
    g["consecutive_updown_5"] = up_down.rolling(5, min_periods=1).sum()

    # ═══ 第五梯队: 时序滞后特征 (2 个) ═══
    g["ret_1d_lag1"] = ret_1d.shift(1)
    g["ret_1d_lag2"] = ret_1d.shift(2)

    # ═══ 第六梯队: 新增高价值因子 ═══

    # -- 波动率曲面 --
    # vol_smile_20: 波动率微笑 (上行波动 / 下行波动)
    g["vol_smile_20"] = pos_ret.rolling(20, min_periods=5).std() / neg_ret.rolling(20, min_periods=5).std().replace(0, np.nan)
    g["vol_smile_20"] = g["vol_smile_20"].fillna(1.0)  # 对称时 = 1

    # vol_term_structure: 波动率期限结构 (60日波动 / 20日波动)
    vol_60 = log_ret.rolling(60, min_periods=10).std()
    vol_20 = log_ret.rolling(20, min_periods=5).std()
    g["vol_term_structure"] = vol_60 / vol_20.replace(0, np.nan)
    g["vol_term_structure"] = g["vol_term_structure"].fillna(1.0)

    # vol_of_vol: 波动率的波动率
    daily_vol = log_ret.rolling(5, min_periods=2).std()
    g["vol_of_vol"] = daily_vol.rolling(20, min_periods=5).std()

    # -- 技术形态因子 --
    # bollinger_position: 布林带位置 (close - mid) / (upper - lower)
    bb_mid = c.rolling(20, min_periods=5).mean()
    bb_std = c.rolling(20, min_periods=5).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    g["tech_bollinger_position"] = (c - bb_mid) / (bb_upper - bb_lower).replace(0, np.nan)

    # williams_r_14: Williams %R = (High14 - Close) / (High14 - Low14) * -100
    high_14 = h.rolling(14, min_periods=1).max()
    low_14 = lo.rolling(14, min_periods=1).min()
    g["tech_williams_r_14"] = (high_14 - c) / (high_14 - low_14).replace(0, np.nan) * -100

    # cci_20: 商品通道指标 = (TP - SMA(TP,20)) / (0.015 * MeanDev(TP,20))
    tp_cci = (h + lo + c) / 3
    tp_sma = tp_cci.rolling(20, min_periods=5).mean()
    tp_mad = tp_cci.rolling(20, min_periods=5).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    g["tech_cci_20"] = (tp_cci - tp_sma) / (0.015 * tp_mad).replace(0, np.nan)

    # -- Alpha101 风格因子 --
    # alpha_decay_ret_10: 10日收益率线性衰减加权 (近期权重更大)
    weights_10 = np.arange(10, 0, -1, dtype=float)
    weights_10 = weights_10 / weights_10.sum()
    g["alpha_decay_ret_10"] = ret_1d.rolling(10, min_periods=5).apply(
        lambda x: np.dot(x, weights_10[:len(x)]), raw=True
    )

    # alpha_corr_cv_20: 收盘价与成交量的20日相关性
    log_vol = np.log(v.clip(lower=1))
    g["alpha_corr_cv_20"] = c.rolling(20, min_periods=10).corr(log_vol).clip(-1, 1).fillna(0)

    # alpha_tsrank_ret_20: 20日收益率时序排名 (当前收益在过去20天中的位置)
    g["alpha_tsrank_ret_20"] = ret_1d.rolling(20, min_periods=10).apply(
        lambda x: (x[-1] >= x).mean() if len(x) > 0 else 0.5, raw=True
    )

    # alpha_tsrank_volume_20: 20日成交量时序排名
    g["alpha_tsrank_volume_20"] = v.rolling(20, min_periods=10).apply(
        lambda x: (x[-1] >= x).mean() if len(x) > 0 else 0.5, raw=True
    )

    # -- Alpha360 补充因子 --
    # alpha_high_20d_ratio: 20日内创新高天数占比
    high_20_max = h.rolling(20, min_periods=1).max()
    is_new_high = (h >= high_20_max * 0.995).astype(float)  # 0.5% 容差
    g["alpha_high_20d_ratio"] = is_new_high.rolling(20, min_periods=5).mean()

    # alpha_low_20d_ratio: 20日内创新低天数占比
    low_20_min = lo.rolling(20, min_periods=1).min()
    is_new_low = (lo <= low_20_min * 1.005).astype(float)
    g["alpha_low_20d_ratio"] = is_new_low.rolling(20, min_periods=5).mean()

    # alpha_close_open_gap: 跳空缺口 (前日收盘 vs 今日开盘)
    g["alpha_close_open_gap"] = (o - c.shift()) / c.shift().clip(lower=1e-8)

    # -- 基本面因子补充 --
    # pe_percentile: PE 历史分位数
    if "pe_ttm" in g.columns:
        pe_val = pd.to_numeric(g["pe_ttm"], errors="coerce")
        g["fund_pe_percentile"] = pe_val.rolling(120, min_periods=20).apply(
            lambda x: (x[-1] >= x).mean() if len(x) > 0 else 0.5, raw=True
        )

    # pb_percentile: PB 历史分位数
    if "pb" in g.columns:
        pb_val = pd.to_numeric(g["pb"], errors="coerce")
        g["fund_pb_percentile"] = pb_val.rolling(120, min_periods=20).apply(
            lambda x: (x[-1] >= x).mean() if len(x) > 0 else 0.5, raw=True
        )

    return g


def _normalize_industry(series: pd.Series) -> pd.Series:
    """标准化行业名称，去除 CSRC 代码前缀等变体。"""
    s = series.astype(str).str.strip()
    # 去掉 CSRC 代码前缀 (C39计算机 → 计算机, A01农业 → 农业)
    s = s.str.replace(r'^[A-Z]?\d{1,3}', '', regex=True)
    # 去掉尾部罗马数字
    s = s.str.replace(r'[ⅠⅡⅢIV]+$', '', regex=True)
    # 去掉多余空格
    s = s.str.strip()
    # 映射空字符串回 NaN
    s = s.replace({'': np.nan, 'nan': np.nan, 'None': np.nan, 'NoneType': np.nan})
    return s


def _compute_industry_codes(df: pd.DataFrame) -> pd.DataFrame:
    """从 instrument_detail.parquet 填充 ind_code_l1 / ind_code_l2 行业编码。

    instrument_detail.parquet 包含 rs_hycode_sim (CSRC 行业编码)，
    将其映射为 CatBoost 可用的整数类别编码，缺失行业填 -1。

    注意: instrument_detail 的 Symbol 已是后缀格式 (如 "600036.SH")，
    无需 zfill + 加后缀，直接 merge 即可。
    """
    from pathlib import Path as _Path

    ind_path = QDB_SECTOR_DIR / "instrument_detail" / "instrument_list.parquet"
    if not ind_path.exists():
        ind_path = QDB_SECTOR_DIR / "instrument_detail" / "instrument_detail.parquet"
    if not ind_path.exists():
        _log("    instrument_detail.parquet 不存在，行业编码填充为 -1")
        df["ind_code_l1"] = -1.0
        df["ind_code_l2"] = -1.0
        return df

    ind_df = pd.read_parquet(ind_path, columns=["Symbol", "rs_hycode_sim"])
    sym_col = "Symbol" if "Symbol" in ind_df.columns else "symbol"
    ind_map = ind_df.rename(columns={sym_col: "symbol", "rs_hycode_sim": "ind_code_l1"})
    # Symbol 已是 "600036.SH" 格式，直接用，不要 zfill(6)
    ind_map["symbol"] = ind_map["symbol"].astype(str).str.strip()
    ind_map["ind_code_l1"] = pd.Categorical(ind_map["ind_code_l1"]).codes.astype(np.float32)
    ind_map["ind_code_l2"] = -1.0

    # 删除 df 中已有的 ind_code_l1/l2 (可能来自 fetch_data_from_quantdb 的静态信息)
    for col in ["ind_code_l1", "ind_code_l2"]:
        if col in df.columns:
            df = df.drop(columns=[col])

    df = df.merge(ind_map[["symbol", "ind_code_l1", "ind_code_l2"]], on="symbol", how="left")
    df["ind_code_l1"] = df["ind_code_l1"].fillna(-1)
    df["ind_code_l2"] = df["ind_code_l2"].fillna(-1)
    _log(f"    行业编码填充完成: {ind_map['ind_code_l1'].nunique()} 个 L1 行业")
    return df


def _compute_industry_features(all_feat: pd.DataFrame) -> pd.DataFrame:
    """跨股票计算行业因子（需要在全部股票特征计算完成后执行）。"""
    # 过滤无行业的行
    valid_ind = all_feat["industry"].notna() & (all_feat["industry"] != "")
    if valid_ind.sum() < 100:
        _log("    行业数据不足，跳过行业因子计算")
        return all_feat

    # 1. 行业日聚合指标
    ind_daily = all_feat[valid_ind].groupby(["industry", "trade_date"]).agg(
        ind_close=("close", "median"),
        ind_flow=("flow_net_amount", "mean"),
        ind_volume=("volume", "sum"),
        ind_amount=("amount", "sum"),
        ind_turnover=("liq_turnover_os", "mean"),
        ind_mv=("ln_mv_total", "mean"),
        ind_ret_1d_stock=("mom_ret_1d", "median"),
    ).reset_index()

    ind_daily = ind_daily.sort_values(["industry", "trade_date"])

    # 行业收益率 (1d, 5d, 10d, 20d)
    ind_daily["ind_ret_1d"] = ind_daily.groupby("industry")["ind_close"].pct_change()
    ind_daily["ind_ret_5d"] = ind_daily.groupby("industry")["ind_close"].pct_change(5)
    ind_daily["ind_ret_10d"] = ind_daily.groupby("industry")["ind_close"].pct_change(10)
    ind_daily["ind_ret_20d"] = ind_daily.groupby("industry")["ind_close"].pct_change(20)

    # 行业波动率 (20日)
    ind_ret = ind_daily.groupby("industry")["ind_close"].pct_change()
    ind_daily["ind_vol_20"] = ind_ret.rolling(20, min_periods=5).std()

    # 行业强度 (20日/60日)
    ind_std_20 = ind_ret.rolling(20, min_periods=5).std().clip(lower=1e-8)
    ind_daily["ind_strength_20"] = ind_daily["ind_ret_20d"] / ind_std_20
    ind_std_60 = ind_ret.rolling(60, min_periods=10).std().clip(lower=1e-8)
    ind_daily["ind_strength_60"] = ind_daily.groupby("industry")["ind_close"].pct_change(60) / ind_std_60

    # 行业换手率/成交额 (20日均值)
    ind_daily["ind_turnover_20"] = ind_daily.groupby("industry")["ind_turnover"].rolling(20, min_periods=5).mean().values
    ind_daily["ind_amount_20"] = ind_daily.groupby("industry")["ind_amount"].rolling(20, min_periods=5).mean().values

    # 行业内动量排名（截面排名）
    ind_daily["ind_momentum_rank_20"] = ind_daily.groupby("trade_date")["ind_ret_20d"].rank(pct=True)

    # 行业离散度 (20日个股收益标准差)
    stock_disp = all_feat[valid_ind].groupby(["industry", "trade_date"])["mom_ret_1d"].std().reset_index()
    stock_disp.columns = ["industry", "trade_date", "ind_dispersion_20"]
    ind_daily = ind_daily.merge(stock_disp, on=["industry", "trade_date"], how="left")

    # 行业涨跌家数
    stock_breadth = all_feat[valid_ind].groupby(["industry", "trade_date"]).agg(
        ind_up_breadth_20=("mom_ret_1d", lambda x: (x > 0).sum()),
        ind_down_breadth_20=("mom_ret_1d", lambda x: (x < 0).sum()),
    ).reset_index()
    ind_daily = ind_daily.merge(stock_breadth, on=["industry", "trade_date"], how="left")

    # 行业相对指标 (行业/全市场)
    mkt_daily = all_feat.groupby("trade_date").agg(
        mkt_volume=("volume", "sum"),
        mkt_amount=("amount", "sum"),
    ).reset_index()
    ind_daily = ind_daily.merge(mkt_daily, on="trade_date", how="left")
    ind_daily["ind_relative_volume_20"] = (ind_daily["ind_volume"] / ind_daily["mkt_volume"].clip(lower=1)).clip(0, 1)
    ind_daily["ind_relative_volatility_20"] = (ind_daily["ind_vol_20"] / ind_daily.groupby("trade_date")["ind_vol_20"].transform("median").clip(lower=1e-8)).clip(0, 5)
    ind_daily["ind_relative_flow_20"] = (ind_daily["ind_flow"] / ind_daily["mkt_amount"].clip(lower=1)).clip(0, 1)

    # 行业市值/价值排名
    ind_daily["ind_value_rank"] = ind_daily.groupby("trade_date")["ind_mv"].rank(pct=True)
    ind_daily["ind_size_rank"] = ind_daily.groupby("trade_date")["ind_volume"].rank(pct=True)

    # 清理临时列
    drop_cols = [c for c in ["ind_volume", "ind_amount", "ind_turnover", "ind_mv",
                              "ind_ret_1d_stock", "mkt_volume", "mkt_amount"] if c in ind_daily.columns]
    ind_daily = ind_daily.drop(columns=drop_cols)

    # Merge 回主表
    merge_cols = [c for c in ind_daily.columns if c not in ["industry", "trade_date"]]
    # 先删除主表中已有的占位列
    for col in merge_cols:
        if col in all_feat.columns:
            all_feat = all_feat.drop(columns=[col])

    all_feat = all_feat.merge(
        ind_daily,
        on=["industry", "trade_date"],
        how="left",
    )

    _log(f"    行业因子计算完成: {ind_daily['industry'].nunique()} 个行业, {len(merge_cols)} 个因子")
    return all_feat


def compute_all_features(df: pd.DataFrame, target_dates: set) -> pd.DataFrame:
    """为所有股票计算特征，只返回 target_dates 中的数据。"""
    # 标准化行业名称
    if "industry" in df.columns:
        df["industry"] = _normalize_industry(df["industry"])

    _log(f"  计算特征（{df['symbol'].nunique()} 只股票）...")
    results = []
    total = df["symbol"].nunique()
    done = 0

    for _sym, group in df.groupby("symbol"):
        feat = compute_features_for_group(group)
        results.append(feat)
        done += 1
        if done % 1000 == 0:
            _log(f"    进度: {done}/{total}")

    all_feat = pd.concat(results, ignore_index=True)

    # 跨股票计算行业因子
    _log("  计算行业因子...")
    all_feat = _compute_industry_features(all_feat)

    # 填充行业编码 (CatBoost cat_features)
    _log("  填充行业编码...")
    all_feat = _compute_industry_codes(all_feat)

    all_feat = all_feat[all_feat["trade_date"].isin(target_dates)].copy()
    return all_feat


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="更新 feature parquet")
    parser.add_argument("--since", default="", help="起始日期 (默认: parquet 最后日期+1)")
    parser.add_argument("--until", default="", help="截止日期 (默认: 今天)")
    parser.add_argument("--dry-run", action="store_true", help="仅检查不写入")
    parser.add_argument("--rebuild", action="store_true", help="重建全部特征")
    parser.add_argument("--year", type=int, default=0, help="指定年份 (默认: 当前年份)")
    args = parser.parse_args()

    year = args.year or date.today().year
    PARQUET_PATH = _parquet_path_for_year(year)

    if not PARQUET_PATH.exists():
        _log(f"parquet 文件不存在，将创建: {PARQUET_PATH}")
        # 创建空 parquet 以便后续逻辑正常工作
        empty_df = pd.DataFrame({"trade_date": pd.Series(dtype="object"), "symbol": pd.Series(dtype="str")})
        empty_df.to_parquet(str(PARQUET_PATH), index=False, engine="pyarrow")

    # 读取现有 parquet
    _log(f"读取现有 parquet: {PARQUET_PATH}")
    existing = pd.read_parquet(PARQUET_PATH, engine="pyarrow")
    existing["trade_date"] = pd.to_datetime(existing["trade_date"]).dt.date
    max_date = existing["trade_date"].max() if len(existing) else date(year, 1, 1) - timedelta(days=1)
    _log(f"  现有数据: {len(existing):,} 行, {existing['symbol'].nunique() if len(existing) else 0} 只股票")
    if len(existing):
        _log(f"  日期范围: {existing['trade_date'].min()} ~ {max_date}")

    # 确定日期范围
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    since = date.fromisoformat(args.since) if args.since else (max_date + timedelta(days=1) if len(existing) else year_start)
    until = date.fromisoformat(args.until) if args.until else min(date.today(), year_end)

    if args.rebuild:
        since = year_start
        _log(f"  REBUILD 模式: 重建 {year} 年 {since} ~ {until}")

    _log(f"  需要补充: {since} ~ {until}")

    if since > until and not args.rebuild:
        _log("无需更新（parquet 已是最新）")
        return

    if args.dry_run:
        _log("DRY RUN 模式，不写入")
        return

    # 从 QuantDB 读取数据
    _log(f"从 QuantDB 本地 parquet 读取数据（含 {DEFAULT_LOOKBACK_DAYS} 天 lookback）...")
    db_df = fetch_data_from_quantdb(since, until, lookback_days=DEFAULT_LOOKBACK_DAYS)

    if db_df.empty:
        _log("DB 中没有新数据")
        return

    _log(f"  读取到 {len(db_df):,} 行, {db_df['symbol'].nunique()} 只股票")

    # 计算特征
    target_dates = set()
    d = since
    while d <= until:
        target_dates.add(d)
        d += timedelta(days=1)

    new_data = compute_all_features(db_df, target_dates)
    _log(f"  计算完成: {len(new_data):,} 行")

    if new_data.empty:
        _log("没有有效数据")
        return

    # 确定输出列（parquet 已有列 + 新增列，去重）
    existing_cols = set(existing.columns)
    _new_cols = set(new_data.columns)
    all_cols = list(dict.fromkeys(list(existing.columns) + [c for c in new_data.columns if c not in existing_cols]))

    # 类型感知的填充：string/object 列用 None/空字符串，数值列用 NaN（不是 0）
    # 防止 industry 等字符串列被 fill_value=0 污染导致 pyarrow 写 parquet 失败
    import numpy as np
    for col in all_cols:
        if col in new_data.columns:
            continue
        # new_data 缺这一列，需要补
        if col in existing.columns:
            dtype = existing[col].dtype
            if dtype is np.dtype('O') or pd.api.types.is_string_dtype(dtype):
                new_data[col] = None
            elif pd.api.types.is_integer_dtype(dtype):
                new_data[col] = pd.NA  # 用 nullable Int
            else:
                new_data[col] = np.nan
        else:
            new_data[col] = np.nan
    new_data = new_data[all_cols]

    # 合并
    if args.rebuild:
        combined = new_data
    else:
        overlap_dates = set(new_data["trade_date"].unique()) & set(existing["trade_date"].unique())
        if overlap_dates:
            _log(f"  发现重叠日期 {len(overlap_dates)} 天，将覆盖")
            existing = existing[~existing["trade_date"].isin(overlap_dates)]
        # 对齐列（已有数据缺少的新列：同样按类型填充）
        for c in all_cols:
            if c not in existing.columns:
                if c in new_data.columns:
                    dtype = new_data[c].dtype
                    if dtype is np.dtype('O') or pd.api.types.is_string_dtype(dtype):
                        existing[c] = None
                    elif pd.api.types.is_integer_dtype(dtype):
                        existing[c] = pd.NA
                    else:
                        existing[c] = np.nan
                else:
                    existing[c] = np.nan
        existing = existing[all_cols]
        combined = pd.concat([existing, new_data], ignore_index=True)

    combined = combined.sort_values(["trade_date", "symbol"]).reset_index(drop=True)

    _log(f"合并后: {len(combined):,} 行, {len(combined.columns)} 列")
    _log(f"日期: {combined['trade_date'].min()} ~ {combined['trade_date'].max()}")

    # 写入 parquet
    combined.to_parquet(str(PARQUET_PATH), index=False, engine="pyarrow")
    _log(f"已写入: {PARQUET_PATH} ({PARQUET_PATH.stat().st_size / 1024 / 1024:.1f}MB)")

    # 验证
    verify = pd.read_parquet(PARQUET_PATH, engine="pyarrow")
    verify["trade_date"] = pd.to_datetime(verify["trade_date"]).dt.date
    _log(f"验证: {len(verify):,} 行, {len(verify.columns)} 列, 最新日期 {verify['trade_date'].max()}")

    # 检查覆盖率
    latest = verify[verify["trade_date"] == verify["trade_date"].max()]
    _log(f"最新日期 {len(latest)} 只股票:")
    for col_group, cols in [
        ("OHLCV", ["open", "high", "low", "close", "volume"]),
        ("动量", ["mom_ret_1d", "mom_ret_5d", "mom_rsi_14"]),
        ("波动率", ["vol_std_20", "vol_atr_14"]),
        ("流动性", ["liq_volume", "liq_amihud_20"]),
        ("资金流", ["flow_net_amount", "flow_vpin"]),
        ("基本面", ["pe_ttm", "pb", "ln_mv_total"]),
        ("指数", ["idx_hs300", "idx_zz1000", "idx_chinext"]),
        ("概念", ["concept_ai", "concept_chip", "concept_new_energy"]),
    ]:
        coverage = []
        for col in cols:
            if col in latest.columns:
                non_null = latest[col].notna().sum()
                coverage.append(f"{col}={non_null}")
        _log(f"  [{col_group}] {', '.join(coverage)}")

    _log("完成!")

    # 生成 metadata.json
    try:
        import json as _json
        meta_path = PARQUET_PATH.with_suffix(".metadata.json")
        feature_cols = [c for c in combined.columns if c not in ("trade_date", "symbol", "instrument")]
        meta = {
            "year": year,
            "calc_start_date": str(since - timedelta(days=DEFAULT_LOOKBACK_DAYS)),
            "output_start_date": str(combined["trade_date"].min()),
            "output_end_date": str(combined["trade_date"].max()),
            "lookback_days": DEFAULT_LOOKBACK_DAYS,
            "trading_days": int(combined["trade_date"].nunique()),
            "row_count": len(combined),
            "symbol_count": int(combined["symbol"].nunique()),
            "implemented_feature_count": len(feature_cols),
            "feature_columns": feature_cols,
            "source": "quantdb",
            "data_source": "quantdb_local_parquet",
            "adjust": "qfq",
        }
        meta_path.write_text(_json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(f"已写入 metadata: {meta_path}")
    except Exception as exc:
        _log(f"metadata.json 写入失败: {exc}")


if __name__ == "__main__":
    main()
