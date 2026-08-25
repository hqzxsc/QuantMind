"""QuantDB parquet 全量特征聚合服务。

为投研平台提供 `stock_daily_latest` 之外的完整因子视图：
- 5_technical_derived/{valuation,technical_indicators,market_sentiment}
- 6_ml_datasets/{l1_factors,l2_factors}

所有读取都复用 QuantDBDataHub 单例的 DuckDB 视图（懒加载 + 线程本地连接），
不额外创建连接。字段按类别（估值/技术/动量/波动/流动性/资金流/风格/行业/
筹码/概念/微观结构/情绪）分组返回，缺失字段返回 null。
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backend.shared.stock_utils import StockCodeUtil

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60
_CACHE_MAX_ENTRIES = 1024
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()

# 特征读取线程池：DuckDB 连接按线程持有（threading.local），并发过大会同时
# 打开多套多 GB parquet 视图扫描，故刻意限制为 2 个常驻线程。
_FEATURE_EXECUTOR: ThreadPoolExecutor | None = None
_FEATURE_EXECUTOR_LOCK = threading.Lock()


def _feature_executor() -> ThreadPoolExecutor:
    global _FEATURE_EXECUTOR
    if _FEATURE_EXECUTOR is None:
        with _FEATURE_EXECUTOR_LOCK:
            if _FEATURE_EXECUTOR is None:
                _FEATURE_EXECUTOR = ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="qm-features"
                )
    return _FEATURE_EXECUTOR


async def _offload(coro_func, *args):
    """在受限线程池中执行同步特征读取，避免阻塞 API 事件循环。"""
    return await asyncio.get_running_loop().run_in_executor(
        _feature_executor(), coro_func, *args
    )

# 批量接口单次上限，避免超大请求拖垮 DuckDB 扫描
MAX_BATCH_SYMBOLS = 200

# 投影模式（只取表格/筛选所需字段）下的上限：响应体小得多，可覆盖整个候选池
MAX_BATCH_SYMBOLS_PROJECTED = 1500

# 只接受规范 suffix 代码，杜绝 SQL 注入（hub.query 不支持参数绑定）
_SYMBOL_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")

# dt 为整数 YYYYMMDD；回看窗口用于裁剪分区扫描范围（约一个月）
_DT_LOOKBACK = 100

# 基础元数据列（不作为因子列参与任何处理）
_META_COLUMNS = frozenset(
    {
        "symbol",
        "wind_code",
        "time",
        "trade_date",
        "dt",
        "release_id",
        "published_at",
        "rn",
        "close",
    }
)

# 排除在全量分类输出之外的列（基础价格与前瞻收益率标签，最新日全为 None，不属于历史技术特征）
_EXCLUDED_FROM_CATEGORIES = frozenset(
    {
        "open",
        "high",
        "low",
        "volume",
        "amount",
        "return_1d",
        "return_3d",
        "return_5d",
        "return_10d",
        "return_20d",
        "return_60d",
    }
)

# DuckDB 视图 → 默认输出类别（视图内未命中前缀规则的列归入此类别）
_VIEW_DEFAULT_CATEGORY: dict[str, str] = {
    "qdb_features_daily": "technical",
    "qdb_valuation": "valuation",
    "qdb_technical_indicators": "technical",
    "qdb_market_sentiment": "sentiment",
    "qdb_l1_factors": "other",
    "qdb_l2_factors": "other",
}

# 显式字段分类映射（用于 features_daily 等混合视图中无前缀列的精确归类）
_COLUMN_EXPLICIT_CATEGORY: dict[str, str] = {
    # 估值与基本面
    "pe_ttm": "valuation",
    "pe_static": "valuation",
    "pb": "valuation",
    "ps_ttm": "valuation",
    "dividend_rate": "valuation",
    "total_mv": "valuation",
    "float_mv": "valuation",
    "total_capital": "valuation",
    "circulating_capital": "valuation",
    "net_profit_ttm": "valuation",
    "revenue_ttm": "valuation",
    "equity": "valuation",
    "annual_net_profit": "valuation",
    # 核心技术指标（均线、乖离率、超买超卖、动能）
    "ma5": "technical",
    "ma10": "technical",
    "ma20": "technical",
    "ma30": "technical",
    "ma60": "technical",
    "ma_gap_5": "technical",
    "ma_gap_10": "technical",
    "ma_gap_20": "technical",
    "ma_gap_60": "technical",
    "rsi_6": "technical",
    "rsi_14": "technical",
    "kdj_k": "technical",
    "kdj_d": "technical",
    "kdj_j": "technical",
    "macd_dif": "technical",
    "macd_dea": "technical",
    "macd_hist": "technical",
    "vol_to_ma5": "technical",
    "vol_to_ma20": "technical",
    "volume_ma_3": "technical",
    "amount_ma_5": "technical",
    "volume_trend_3d": "technical",
    "beta_20": "technical",
    "pct_change": "technical",
    # 波动率
    "vol_std_5": "volatility",
    "vol_std_20": "volatility",
    "vol_std_60": "volatility",
    "vol_atr_14": "volatility",
    # 换手率（l1/l2_factors 的 turn_* 为小数，归入流动性并按百分数展示）
    "turn_1": "liquidity",
    "turn_3": "liquidity",
    "turn_5": "liquidity",
    "turn_10": "liquidity",
    "turn_20": "liquidity",
    "turn_60": "liquidity",
}

# 因子列前缀 → 类别（按前缀长度降序匹配，长前缀优先）
_PREFIX_CATEGORY: tuple[tuple[str, str], ...] = (
    ("mom_", "momentum"),
    ("vol_", "volatility"),
    ("liq_", "liquidity"),
    ("tech_", "technical"),
    ("fun_", "fundamental"),
    ("style_", "style"),
    ("ind_", "industry"),
    ("chip_", "chip"),
    ("concept_", "concept"),
    ("micro_", "microstructure"),
    ("flow_", "fundFlow"),
)

# 输出类别顺序（保证响应结构稳定，即使某类别为空）
CATEGORIES: tuple[str, ...] = (
    "valuation",
    "technical",
    "momentum",
    "volatility",
    "liquidity",
    "fundFlow",
    "fundamental",
    "style",
    "industry",
    "chip",
    "concept",
    "microstructure",
    "sentiment",
    "other",
)


def _get_hub():
    """获取 QuantDBHub 单例（延迟导入，避免 API 服务启动强依赖 engine 模块）。"""
    from backend.services.engine.data_platform.quantdb_hub import QuantDBDataHub

    return QuantDBDataHub.get_instance()


def normalize_symbols(symbols: list[str]) -> list[str]:
    """将任意格式股票代码归一化为规范 suffix 格式并去重（保持入参顺序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for raw in symbols:
        suffix = StockCodeUtil.to_suffix(str(raw or "").strip())
        if not _SYMBOL_RE.match(suffix) or suffix in seen:
            continue
        seen.add(suffix)
        result.append(suffix)
    return result


def _category_for(column: str, default: str) -> str:
    if column in _COLUMN_EXPLICIT_CATEGORY:
        return _COLUMN_EXPLICIT_CATEGORY[column]
    for prefix, category in _PREFIX_CATEGORY:
        if column.startswith(prefix):
            return category
    return default


# ----------------------------------------------------------------------
# camelCase 投影
# ----------------------------------------------------------------------
# 与前端 featureMapper.ts 的 FIELD_ALIASES 保持一致：这些列所在的 QuantDB 前缀
# 与前端列分组不同，必须显式改名，否则投影会漏掉它们。
_CAMEL_ALIASES: dict[str, str] = {
    "fun_mv_rank": "styleMvRank",
    "fun_value_zscore": "styleValueZscore",
    "micro_liquidity_amihud_20": "liqAmihud20",
    # PG `stock_daily_latest` 自 2026-06-18 起不再回填 PE/ROE 等列（近期交易日 100% NULL，
    # 序列化后变成 0，前端显示 “PE 0.0 / ROE 0.0%”）。这些别名让 QuantDB 顶上同名 UI 字段。
    "pe_ttm": "pe",
    "fun_roe": "roe",
}

# market_sentiment 视图的列没有前缀，前端统一加 sentiment 前缀避免与基础字段冲突。
_SENTIMENT_CAMEL_ALIASES: dict[str, str] = {
    "liquidity_score": "sentimentLiquidityScore",
    "buy_pressure": "sentimentBuyPressure",
    "sell_pressure": "sentimentSellPressure",
    "body_ratio": "sentimentBodyRatio",
    "intraday_vol": "sentimentIntradayVol",
    "gap_up_down": "sentimentGapUpDown",
    "am_pm_trend": "sentimentAmPmTrend",
    "volume_concentration": "sentimentVolumeConcentration",
}


def _to_camel(column: str) -> str:
    """mom_ret_1d → momRet1d（与前端 toCamel 同规则）。"""
    parts = [p for p in column.split("_") if p]
    if not parts:
        return column
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _camel_name(column: str, view: str) -> str:
    if view == "qdb_market_sentiment" and column in _SENTIMENT_CAMEL_ALIASES:
        return _SENTIMENT_CAMEL_ALIASES[column]
    return _CAMEL_ALIASES.get(column) or _to_camel(column)


# 投影兜底：目标字段缺失时，用替代列换算填充。
# 注意：return1d/3d/5d 不再兜底到 l1_factors.mom_ret_*d（过去收益）——
# 投研平台的 return 系列是“推理日后 N 日真实收益”，语义上必须来自
# features_daily 按推理日读取的 return_* 标签；mom_ret_*d 是历史动量（过去收益），
# 用它会污染“未来收益”展示。features_daily 的 return_* 在推理日后未满 N 个交易日
# 时为 NaN，投影路径返回空由前端显示“-”，待未来行情生成后自然回填。
_DERIVED_FALLBACKS: dict[str, tuple[str, float]] = {
    # UI 的 rsi / atr 字段在 PG 里分别来自 rsi_6 与 vol_atr_14，
    # QuantDB 同名列是 rsi_6 / vol_atr_14，这里补上映射避免这两列取不到值。
    "rsi": ("rsi6", 1.0),
    "atr": ("volAtr14", 1.0),
}

# 单位对齐：QuantDB parquet 存原始单位（元），而 `/research/universe` 已把同名字段
# 换算过（市值 → 亿元，资金流 → 百万元）。前端在 universe 缺值或填 0 占位时才采用
# QuantDB 值，所以这里必须把会被采用的字段换算到同一量纲。
# 注意：qdb_valuation.total_mv / float_mv 是真正的元，可以线性换算；
# 但 l1_factors 的 fun_mv / liq_amount 是对数值，任何缩放都是错的——
# 它们保留原名（funMv / liqAmount），不参与 UI 的市值字段。
_UNIT_SCALES: dict[str, float] = {
    "totalMv": 1e-8,
    "floatMv": 1e-8,
    "mainFlow": 1e-6,
    "flowNetAmount": 1e-6,
    "flowBuyAmount": 1e-6,
    "flowSellAmount": 1e-6,
    "flowLargeNet": 1e-6,
    "flowMediumNet": 1e-6,
    "flowSmallNet": 1e-6,
    # l2_factors.flow_super_net 单位是元，与其他 flow* 一致（同类别统一 → 百万元）
    "flowSuperNet": 1e-6,
}

# 换手率字段：l1/l2_factors 的 turn_N 为小数（0.016 = 1.6%），统一 ×100 转为百分数，
# 与投影路径现算的 turnoverRate（百分比量纲）及 /research/universe 对齐。
_TURNOVER_PERCENT_FIELDS: frozenset[str] = frozenset(
    {"turn_1", "turn_3", "turn_5", "turn_10", "turn_20", "turn_60"}
)


def _to_jsonable(value: Any) -> Any:
    """转换为 JSON 安全值：NaN/Inf/NaT → None，numpy 标量 → python 原生类型。"""
    if value is None:
        return None
    # numpy / pandas 标量统一走 .item()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except (ValueError, TypeError):
            return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    # date / datetime / Timestamp（pd.NaT.isoformat() 返回字符串 "NaT"，需排除）
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            text = isoformat()
        except (ValueError, TypeError):
            return None
        return None if text == "NaT" else text
    return None


def _latest_rows(view: str, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """读取指定视图中每个 symbol 的最新一行。

    返回 {symbol: row_dict}；视图不存在或无数据时返回空字典（优雅降级）。
    """
    quoted = ", ".join(f"'{s}'" for s in symbols)
    sql = f"""
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY dt DESC) AS rn
            FROM {view}
            WHERE symbol IN ({quoted})
              AND dt >= (SELECT MAX(dt) - {_DT_LOOKBACK} FROM {view})
        ) WHERE rn = 1
    """
    try:
        df = _get_hub().query(sql)
    except Exception as exc:
        logger.debug("QuantDB 视图 %s 查询失败（跳过）: %s", view, exc)
        return {}

    if df.empty:
        return {}
    return {str(row["symbol"]): dict(row) for _, row in df.iterrows()}


def _latest_l1_from_files(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """L1 因子平铺格式兜底：读取最新一个 l1_factors_YYYYMMDD.parquet。

    l1_factors 目录混合了分区目录与平铺文件，hub 的 DuckDB 视图只挂载分区目录，
    分区目录缺失时用此路径读取最新平铺文件（单文件，成本可控）。
    """
    import pandas as pd

    try:
        l1_dir = _get_hub().data_dir / "6_ml_datasets" / "l1_factors"
        files = sorted(l1_dir.glob("l1_factors_*.parquet"))
        if not files:
            return {}
        df = pd.read_parquet(files[-1])
    except Exception as exc:
        logger.warning("读取 L1 平铺文件失败（跳过）: %s", exc)
        return {}

    symbol_col = "symbol" if "symbol" in df.columns else "wind_code"
    if symbol_col not in df.columns:
        return {}

    df = df[df[symbol_col].isin(symbols)]
    return {str(row[symbol_col]): dict(row) for _, row in df.iterrows()}


def _fetch_l1(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """L1 因子：优先分区视图，缺失时回落到平铺文件。"""
    rows = _latest_rows("qdb_l1_factors", symbols)
    if rows:
        return rows
    return _latest_l1_from_files(symbols)


def _resolve_trade_date(rows: dict[str, dict[str, Any]]) -> str | None:
    """从任一数据源行中提取交易日期。"""
    for row in rows.values():
        for key in ("time", "trade_date"):
            value = _to_jsonable(row.get(key))
            if isinstance(value, str) and value:
                return value[:10]
    return None


def _apply_unit_scales(values: dict[str, Any]) -> None:
    """对需要单位换算的字段就地缩放（元→亿元 / 元→百万元）。

    全量路径 `_build_payload` 和投影路径 `_build_projected_payload` 共用此函数，
    确保两者返回的同名字段量纲一致（与 `/research/universe` 已换算后的值对齐）。

    `_UNIT_SCALES` 用 camelCase 键（totalMv / mainFlow），但 `_build_payload`
    的 grouped 字典保留原始 snake_case 列名（total_mv / main_flow），因此
    这里同时尝试两种命名。
    """
    for camel_name, scale in _UNIT_SCALES.items():
        # camelCase 键（投影路径）
        if camel_name in values:
            values[camel_name] = values[camel_name] * scale
        # snake_case 键（全量路径）：totalMv → total_mv
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", camel_name).lower()
        if snake in values:
            values[snake] = values[snake] * scale

    # 换手率：小数 → 百分数（全量路径的 turn_* 保留 snake_case 列名）
    for field in _TURNOVER_PERCENT_FIELDS:
        if field in values:
            values[field] = values[field] * 100


def _build_payload(symbol: str, sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """将各数据源的行按类别合并为单只股票的响应体。"""
    grouped: dict[str, dict[str, Any]] = {name: {} for name in CATEGORIES}
    available: list[str] = []

    for view, rows_by_symbol in sources.items():
        row = rows_by_symbol.get(symbol)
        if not row:
            continue
        available.append(view.removeprefix("qdb_"))
        default_category = _VIEW_DEFAULT_CATEGORY.get(view, "other")
        for column, value in row.items():
            if column in _META_COLUMNS or column in _EXCLUDED_FROM_CATEGORIES:
                continue
            category = _category_for(column, default_category)
            grouped[category][column] = _to_jsonable(value)

    # 对含单位换算的字段统一缩放，与投影路径和 /research/universe 的量纲对齐
    for category_values in grouped.values():
        _apply_unit_scales(category_values)

    payload: dict[str, Any] = {
        "symbol": symbol,
        "tradeDate": _resolve_trade_date(
            {v: r[symbol] for v, r in sources.items() if symbol in r}
        ),
        "sources": sorted(available),
    }
    payload.update(grouped)
    return payload


def _build_projected_payload(
    symbol: str, sources: dict[str, dict[str, Any]], wanted: frozenset[str]
) -> dict[str, Any]:
    """只输出 wanted 中的 camelCase 字段，平铺在 values 下（不分类）。

    表格与筛选只消费数值，因此这里丢弃非数值字段，响应体比全量小两个数量级。
    """
    # 兜底源字段即使未被请求也要收集，用于补齐缺失的目标字段
    fallback_sources = {
        src: (target, scale)
        for target, (src, scale) in _DERIVED_FALLBACKS.items()
        if target in wanted
    }
    # 换手率需要现算，额外收集两个原料列
    extra_spares: set[str] = set()
    if "turnoverRate" in wanted:
        extra_spares |= {"volume", "circulatingCapital"}

    values: dict[str, Any] = {}
    spare: dict[str, float] = {}
    available: list[str] = []

    for view, rows_by_symbol in sources.items():
        row = rows_by_symbol.get(symbol)
        if not row:
            continue
        available.append(view.removeprefix("qdb_"))
        for column, raw in row.items():
            if column in _META_COLUMNS:
                continue
            name = _camel_name(column, view)
            is_wanted = name in wanted and name not in values
            is_spare = (name in fallback_sources or name in extra_spares) and name not in spare
            if not is_wanted and not is_spare:
                continue
            value = _to_jsonable(raw)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if is_wanted:
                values[name] = value
            if is_spare:
                spare[name] = float(value)

    # 目标字段缺失时用兜底源换算填充
    for src, (target, scale) in fallback_sources.items():
        if target not in values and src in spare:
            values[target] = spare[src] * scale

    # 换手率：PG 从 2026-06-26 起完全没有该列，QuantDB 也没有直接可用的百分比字段
    # （fun_turnover_1 是另一套量纲）。按定义现算：成交量(手)×100 / 流通股本 × 100%。
    # 校验：全市场中位 1.86%、p95 8.66%，与 PG 早期有效行（2.02% / 2.84%）一致。
    if "turnoverRate" in wanted and "turnoverRate" not in values:
        volume = spare.get("volume")
        circulating = spare.get("circulatingCapital")
        if volume and circulating and circulating > 0:
            values["turnoverRate"] = volume * 100.0 * 100.0 / circulating

    # 与 universe 行的单位对齐（亿元 / 百万元）
    _apply_unit_scales(values)

    return {
        "symbol": symbol,
        "tradeDate": _resolve_trade_date(
            {v: r[symbol] for v, r in sources.items() if symbol in r}
        ),
        "sources": sorted(available),
        "values": values,
    }


def _cache_get(symbol: str) -> dict[str, Any] | None:
    # 缓存现在被事件循环与特征线程池并发访问，读写均加锁
    with _CACHE_LOCK:
        cached = _CACHE.get(symbol)
        if not cached:
            return None
        if (time.monotonic() - cached[0]) > _CACHE_TTL_SECONDS:
            _CACHE.pop(symbol, None)
            return None
        return cached[1]


def _cache_set(symbol: str, payload: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _CACHE[symbol] = (time.monotonic(), payload)
        if len(_CACHE) > _CACHE_MAX_ENTRIES:
            oldest = min(_CACHE.items(), key=lambda kv: kv[1][0])[0]
            _CACHE.pop(oldest, None)


def _query_sources(symbols: list[str], *, include_daily: bool = False) -> dict[str, dict[str, Any]] | None:
    """查询全部 QuantDB 视图。数据目录不可用时返回 None。

    优先使用 52 维核心宽表 qdb_features_daily 替代旧的 qdb_valuation 与 qdb_technical_indicators，
    未落盘 features_daily 时自动回退至旧分散视图。
    include_daily 额外挂载日线视图（提供 volume，用于现算换手率）。仅投影路径需要。
    """
    hub = _get_hub()
    if not hub.available:
        logger.warning("QuantDB 数据目录不可用: %s", hub.data_dir)
        return None

    sources: dict[str, dict[str, Any]] = {}

    # 1. 50+ 维日频特征宽表（优先主源）
    features_daily_rows = _latest_rows("qdb_features_daily", symbols)
    if features_daily_rows:
        sources["qdb_features_daily"] = features_daily_rows
    else:
        # 回退至旧分散视图
        sources["qdb_valuation"] = _latest_rows("qdb_valuation", symbols)
        sources["qdb_technical_indicators"] = _latest_rows("qdb_technical_indicators", symbols)

    # 2. 情绪面、L1 因子、L2 因子
    sources["qdb_market_sentiment"] = _latest_rows("qdb_market_sentiment", symbols)
    sources["qdb_l1_factors"] = _fetch_l1(symbols)
    sources["qdb_l2_factors"] = _latest_rows("qdb_l2_factors", symbols)

    if include_daily:
        # 日线视图只用于取 volume（现算换手率的原料）。其余列必须丢弃：
        # amount/open/high/low 与 UI 字段同名但量纲不同（amount 是元，UI 期望亿元），
        # 一旦混入就会污染成交额筛选。
        daily = _latest_rows("qdb_daily_unadjusted", symbols)
        sources["qdb_daily_unadjusted"] = {
            sym: {"volume": row.get("volume")} for sym, row in daily.items()
        }
    return sources


def _load_features(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """查询所有 QuantDB 数据源，返回 {symbol: payload}（含缓存读写）。"""
    result: dict[str, dict[str, Any]] = {}
    pending: list[str] = []
    for symbol in symbols:
        cached = _cache_get(symbol)
        if cached is not None:
            result[symbol] = cached
        else:
            pending.append(symbol)

    if not pending:
        return result

    sources = _query_sources(pending)
    if sources is None:
        return result

    for symbol in pending:
        payload = _build_payload(symbol, sources)
        _cache_set(symbol, payload)
        result[symbol] = payload
    return result


def _load_projected_features(
    symbols: list[str], wanted: frozenset[str]
) -> dict[str, dict[str, Any]]:
    """投影模式：不走全量缓存（键随字段集变化），直接查询后按需裁剪。"""
    sources = _query_sources(symbols, include_daily="turnoverRate" in wanted)
    if sources is None:
        return {}
    return {s: _build_projected_payload(s, sources, wanted) for s in symbols}


def get_symbol_full_features_sync(symbol: str) -> dict[str, Any]:
    """单只股票全量特征的同步实现（唯一实现，异步端点经由线程池调用）。

    特征读取链路含 DuckDB/pandas 同步 IO，在 API 单 worker 进程中直接执行
    会阻塞事件循环导致健康检查超时，因此所有调用方都应走 _offload。
    """
    normalized = normalize_symbols([symbol])
    if not normalized:
        return {"code": 400, "message": f"无效股票代码: {symbol}", "data": None}

    target = normalized[0]
    features = _load_features(normalized)
    payload = features.get(target)
    if payload is None or not payload.get("sources"):
        return {"code": 404, "message": f"无 QuantDB 特征数据: {target}", "data": None}
    return {"code": 200, "data": payload}


async def get_symbol_full_features(symbol: str) -> dict[str, Any]:
    """单只股票的全量 QuantDB 特征（线程池卸载版）。"""
    return await _offload(get_symbol_full_features_sync, symbol)


def get_batch_full_features_sync(
    symbols: list[str], fields: list[str] | None = None
) -> dict[str, Any]:
    """批量股票全量/投影特征的同步实现（唯一实现）。

    传入 fields（camelCase）时走投影模式：响应只含这些字段且平铺在 values 下，
    上限提高到 MAX_BATCH_SYMBOLS_PROJECTED，可一次覆盖整个候选池以支持全池筛选。
    """
    normalized = normalize_symbols(symbols or [])
    if not normalized:
        return {"code": 200, "data": {"items": [], "total": 0, "missing": []}}

    wanted = frozenset(f for f in (fields or []) if f)
    cap = MAX_BATCH_SYMBOLS_PROJECTED if wanted else MAX_BATCH_SYMBOLS
    truncated = normalized[:cap]

    if wanted:
        features = _load_projected_features(truncated, wanted)
    else:
        features = _load_features(truncated)

    items = [features[s] for s in truncated if features.get(s, {}).get("sources")]
    missing = [s for s in truncated if not features.get(s, {}).get("sources")]
    return {
        "code": 200,
        "data": {
            "items": items,
            "total": len(items),
            "missing": missing,
            "truncated": len(normalized) > cap,
            "projected": bool(wanted),
        },
    }


async def get_batch_full_features(
    symbols: list[str], fields: list[str] | None = None
) -> dict[str, Any]:
    """批量股票的全量 QuantDB 特征（线程池卸载版，用于表格增强）。"""
    return await _offload(get_batch_full_features_sync, symbols, fields)
