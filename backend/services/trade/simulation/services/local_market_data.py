"""本地行情数据层 —— 模拟盘的唯一行情来源。

按市场读取对应数据平台的 parquet（经各市场 DataHub 的 DuckDB 视图），
不再依赖实时行情 HTTP 服务。

复权口径选择：
- CN：不复权（daily_unadjusted）。模拟撮合需要"当日实际可成交价"，
  涨跌停也必须在同一口径上计算；前复权序列的价格是被回溯改写过的
  历史价，用它撮合会产生与真实盘面不一致的成交价与涨跌停判定。
- HK/US/FUTURES/CRYPTO：daily_forward（同步落盘的不复权日K）。

市场差异（见 market_rules.MarketTradingRules）：
- 涨跌停仅 CN 计算，其余市场 limit_up=inf / limit_down=0（无限制）
- ST 判定仅 CN（其余市场恒为 False）
"""

from __future__ import annotations

import logging
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal

import pandas as pd

from backend.services.engine.data_platform.quantdb_hub import QuantDBDataHub
from backend.services.trade.simulation.services.market_rules import (
    Market,
    normalize_market,
)
from backend.shared.stock_utils import StockCodeUtil

logger = logging.getLogger(__name__)

# CN 不复权日线视图；其余市场用各自 daily_forward（同步落盘的原始价）
_MARKET_KLINE_VIEWS: dict[Market, str] = {
    Market.CN: "qdb_daily_unadjusted",
    Market.HK: "qhk_daily_forward",
    Market.US: "qus_daily_forward",
    Market.FUTURES: "qfut_daily_forward",
    Market.CRYPTO: "qbc_daily_forward",
}

# 每档涨跌幅（不含 ST 折减）
_PCT_MAIN = Decimal("0.10")
_PCT_GROWTH = Decimal("0.20")  # 创业板 300/301/302 + 科创板 688/689
_PCT_BSE = Decimal("0.30")
_PCT_ST_MAIN = Decimal("0.05")

_GROWTH_PREFIXES = ("300", "301", "302", "688", "689")
_BSE_PREFIXES = ("43", "83", "87", "88", "92")

# ST 主板涨跌幅在本数据集中于 2026-07-06 由 ±5% 变为 ±10%：
# 该日之前 ST 收益率明显聚集在 ±5%，之后聚集在 ±10%，且 instrument_detail
# 快照（HqDate=20260720）的 ZTPrice/DTPrice 对 ST 股同样按 ±10% 给出。
# 创业板/科创板/北交所 ST 股不折减（实测最大涨幅仍为 ±20% / ±30%）。
_ST_LIMIT_RELAXED_FROM = date(2026, 7, 6)

_CENT = Decimal("0.01")

# amount/volume 的单位在 2026-07-21 前后发生切换，因此按日自动识别而非硬编码：
#   旧口径：volume=股, amount=万元  -> close*volume/amount ≈ 1e4
#   新口径：volume=手, amount=元    -> close*volume/amount ≈ 1e-2
# 两者相差 6 个数量级，用中位数判别非常稳健。搞错会让成交额/vwap 偏 1e4 倍。
_SCALE_LEGACY = 1e4  # amount 万元→元；volume 已是股
_SCALE_CURRENT = 1e-2  # volume 手→股需 ×100，amount 已是元
_SCALE_DECISION_BOUNDARY = 1.0

_CACHE_SIZE = 32


@dataclass
class DailyBar:
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float  # 股
    amount: float  # 元
    vwap: float  # 元/股
    pre_close: float
    limit_up: float
    limit_down: float
    is_st: bool
    suspended: bool
    lot_size: int = 100


def _round_cent(value: Decimal, rounding: str) -> float:
    return float(value.quantize(_CENT, rounding=rounding))


def _is_bse(symbol: str) -> bool:
    code, _, suffix = symbol.partition(".")
    if suffix == "BJ":
        return True
    return code[:2] in _BSE_PREFIXES


def _board_pct(symbol: str) -> Decimal:
    if _is_bse(symbol):
        return _PCT_BSE
    code = symbol.partition(".")[0]
    if code[:3] in _GROWTH_PREFIXES:
        return _PCT_GROWTH
    return _PCT_MAIN


def limit_pct(symbol: str, *, is_st: bool, trade_date: date) -> Decimal:
    """该标的当日的涨跌幅限制比例。"""
    pct = _board_pct(symbol)
    st_reduces = is_st and pct == _PCT_MAIN and trade_date < _ST_LIMIT_RELAXED_FROM
    return _PCT_ST_MAIN if st_reduces else pct


def compute_limits(
    symbol: str,
    pre_close: float,
    *,
    is_st: bool,
    trade_date: date,
) -> tuple[float, float]:
    """由昨收 + 板块规则计算涨停/跌停价。

    无昨收（新股首日）视为无涨跌幅限制。
    上交所/深交所四舍五入到分；北交所涨停价截尾、跌停价进位（实测与
    instrument_detail 的 ZTPrice/DTPrice 完全一致）。
    """
    if pre_close is None or not math.isfinite(pre_close) or pre_close <= 0:
        return math.inf, 0.0

    pct = limit_pct(symbol, is_st=is_st, trade_date=trade_date)
    base = Decimal(repr(round(pre_close, 2)))
    if _is_bse(symbol):
        up_rounding, down_rounding = ROUND_DOWN, ROUND_UP
    else:
        up_rounding = down_rounding = ROUND_HALF_UP
    return (
        _round_cent(base * (Decimal(1) + pct), up_rounding),
        _round_cent(base * (Decimal(1) - pct), down_rounding),
    )


def _detect_amount_scale(df: pd.DataFrame) -> float:
    """识别当日 amount/volume 的单位口径，返回 vwap 换算系数。"""
    usable = df[(df["volume"] > 0) & (df["amount"] > 0) & (df["close"] > 0)]
    if usable.empty:
        return _SCALE_CURRENT
    ratio = (usable["close"] * usable["volume"] / usable["amount"]).median()
    if not math.isfinite(ratio) or ratio <= 0:
        return _SCALE_CURRENT
    return _SCALE_LEGACY if ratio > _SCALE_DECISION_BOUNDARY else _SCALE_CURRENT


class LocalMarketData:
    """按日读取本地 parquet 日线，产出带涨跌停/ST/停牌标记的 DailyBar。

    market 决定数据根（hub）与视图：CN 用不复权日线并计算涨跌停/ST；
    其余市场用 daily_forward 原始价，无涨跌停、无 ST。
    """

    def __init__(
        self,
        hub: QuantDBDataHub | None = None,
        market: Market | str | None = None,
    ) -> None:
        self.market = normalize_market(market)
        self._hub = hub or self._resolve_hub(self.market)
        self._kline_view = _MARKET_KLINE_VIEWS[self.market]
        self._lock = threading.RLock()
        self._date_cache: OrderedDict[date, dict[str, DailyBar]] = OrderedDict()
        self._st_symbols: frozenset[str] | None = None
        self._session_dates: list[int] | None = None

    @staticmethod
    def _resolve_hub(market: Market) -> QuantDBDataHub:
        if market is Market.CN:
            return QuantDBDataHub.get_instance()
        if market is Market.HK:
            from backend.services.engine.data_platform.quanthk_hub import QuantHKDataHub

            return QuantHKDataHub.get_instance()
        if market is Market.US:
            from backend.services.engine.data_platform.quantus_hub import QuantUSDataHub

            return QuantUSDataHub.get_instance()
        if market is Market.FUTURES:
            from backend.services.engine.data_platform.quantfutures_hub import (
                QuantFuturesDataHub,
            )

            return QuantFuturesDataHub.get_instance()
        from backend.services.engine.data_platform.quantbc_hub import QuantBCDataHub

        return QuantBCDataHub.get_instance()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def load_date(
        self,
        trade_date: date,
        symbols: list[str] | None = None,
    ) -> dict[str, DailyBar]:
        """加载指定交易日的全市场（或指定标的）日线。"""
        bars = self._load_date_cached(trade_date)
        if symbols is None:
            return bars
        wanted = {StockCodeUtil.to_suffix(s) for s in symbols}
        return {sym: bar for sym, bar in bars.items() if sym in wanted}

    def get_bar(self, symbol: str, trade_date: date) -> DailyBar | None:
        return self._load_date_cached(trade_date).get(StockCodeUtil.to_suffix(symbol))

    def latest_trade_date(self, on_or_before: date | None = None) -> date | None:
        """最近一个有行情数据的交易日。"""
        sessions = self._sessions()
        if not sessions:
            return None
        cutoff = _to_dt_int(on_or_before) if on_or_before else None
        candidates = [d for d in sessions if cutoff is None or d <= cutoff]
        return _from_dt_int(candidates[-1]) if candidates else None

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _load_date_cached(self, trade_date: date) -> dict[str, DailyBar]:
        with self._lock:
            cached = self._date_cache.get(trade_date)
            if cached is not None:
                self._date_cache.move_to_end(trade_date)
                return cached

        bars = self._build_date(trade_date)

        with self._lock:
            self._date_cache[trade_date] = bars
            self._date_cache.move_to_end(trade_date)
            while len(self._date_cache) > _CACHE_SIZE:
                self._date_cache.popitem(last=False)
        return bars

    def _build_date(self, trade_date: date) -> dict[str, DailyBar]:
        dt_int = _to_dt_int(trade_date)
        prev_dt_int = self._previous_session(dt_int)
        if prev_dt_int is None:
            wanted = (dt_int,)
        else:
            wanted = (dt_int, prev_dt_int)

        df = self._scan(wanted)
        if df.empty:
            return {}

        today = df[df["dt"] == dt_int]
        if today.empty:
            return {}

        # 量额单位自动探测仅 CN（历史口径切换）；其余市场 volume=股/枚、
        # amount=计价货币原样，vwap=amount/volume。
        if self.market is Market.CN:
            scale = _detect_amount_scale(today)
            # volume 为"手"时（新口径）需换算为股，amount 已是元。
            volume_to_shares = 100.0 if scale == _SCALE_CURRENT else 1.0
            amount_to_yuan = 1.0 if scale == _SCALE_CURRENT else _SCALE_LEGACY
        else:
            volume_to_shares = 1.0
            amount_to_yuan = 1.0

        if prev_dt_int is None:
            pre_close_map: dict[str, float] = {}
        else:
            prev = df[df["dt"] == prev_dt_int]
            pre_close_map = dict(zip(prev["symbol"], prev["close"].astype(float), strict=True))

        st_symbols = self._st_symbol_set() if self.market is Market.CN else frozenset()

        bars: dict[str, DailyBar] = {}
        for row in today.itertuples(index=False):
            symbol = str(row.symbol)
            close = _as_float(row.close)
            raw_volume = max(_as_float(row.volume), 0.0)
            raw_amount = max(_as_float(row.amount), 0.0)
            volume = raw_volume * volume_to_shares
            amount = raw_amount * amount_to_yuan
            vwap = amount / volume if volume > 0 and amount > 0 else close

            pre_close = pre_close_map.get(symbol, 0.0)
            is_st = symbol in st_symbols
            if self.market is Market.CN and pre_close > 0:
                limit_up, limit_down = compute_limits(
                    symbol, pre_close, is_st=is_st, trade_date=trade_date
                )
            else:
                # 无昨收（新股首日）或非 CN 市场：无涨跌幅限制
                limit_up, limit_down = math.inf, 0.0
            bars[symbol] = DailyBar(
                symbol=symbol,
                trade_date=trade_date,
                open=_as_float(row.open),
                high=_as_float(row.high),
                low=_as_float(row.low),
                close=close,
                volume=volume,
                amount=amount,
                vwap=vwap,
                pre_close=pre_close,
                limit_up=limit_up,
                limit_down=limit_down,
                is_st=is_st,
                suspended=volume <= 0,
            )
        return bars

    def _scan(self, dt_ints: tuple[int, ...]) -> pd.DataFrame:
        """一次 DuckDB 扫描取回目标日与前一交易日的全市场日线。"""
        dt_list = ", ".join(str(d) for d in dt_ints)
        sql = (
            "SELECT symbol, dt, open, high, low, close, volume, amount "
            f"FROM {self._kline_view} WHERE dt IN ({dt_list})"
        )
        try:
            return self._hub.query(sql)
        except Exception as exc:
            logger.error("本地行情扫描失败 dt=%s: %s", dt_list, exc)
            return pd.DataFrame()

    def _sessions(self) -> list[int]:
        """有行情数据的交易日（以实际 parquet 分区为准，交易日历与分区并不总是一致）。"""
        with self._lock:
            if self._session_dates is not None:
                return self._session_dates
        try:
            df = self._hub.query(
                f"SELECT DISTINCT dt FROM {self._kline_view} ORDER BY dt"
            )
            sessions = [int(v) for v in df["dt"].tolist()]
        except Exception as exc:
            # 失败时不写缓存：避免把空的 session 列表永久钉住，
            # 后续数据补齐（如 quantdb 恢复）后下次调用能自动重试。
            logger.error("读取本地行情交易日失败: %s", exc)
            return []
        with self._lock:
            self._session_dates = sessions
        return sessions

    def _previous_session(self, dt_int: int) -> int | None:
        sessions = self._sessions()
        prior = [d for d in sessions if d < dt_int]
        return prior[-1] if prior else None

    def _st_symbol_set(self) -> frozenset[str]:
        """ST 标的集合：IsSTGP 标志 + 证券简称含 ST，双路判定。

        instrument_detail 是单一快照（HqDate 固定），因此 ST 状态只在快照
        日期附近严格准确；历史回放会沿用该快照。
        """
        with self._lock:
            if self._st_symbols is not None:
                return self._st_symbols

        symbols: set[str] = set()
        try:
            detail = self._hub.fetch_stock_list()
        except Exception as exc:
            logger.error("读取 instrument_detail 失败: %s", exc)
            detail = pd.DataFrame()

        if not detail.empty:
            symbols = extract_st_symbols(detail)

        result = frozenset(symbols)
        with self._lock:
            self._st_symbols = result
        return result


def extract_st_symbols(detail: pd.DataFrame) -> set[str]:
    """从 instrument_detail 中提取 ST 标的。

    IsSTGP / 数值列在 parquet 中为 large_string，必须先 to_numeric 强转，
    否则直接比较会抛 ArrowNotImplementedError。
    """
    symbol_col = "Symbol" if "Symbol" in detail.columns else "symbol"
    if symbol_col not in detail.columns:
        return set()

    symbols = detail[symbol_col].astype("string")
    mask = pd.Series(False, index=detail.index)

    if "IsSTGP" in detail.columns:
        flag = pd.to_numeric(detail["IsSTGP"], errors="coerce").fillna(0)
        mask |= flag > 0

    if "Name" in detail.columns:
        mask |= detail["Name"].astype("string").str.contains("ST", na=False)

    return set(symbols[mask.fillna(False)].dropna().tolist())


def _as_float(value) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _to_dt_int(value: date) -> int:
    return value.year * 10000 + value.month * 100 + value.day


def _from_dt_int(value: int) -> date:
    return date(value // 10000, (value // 100) % 100, value % 100)


_default_instances: dict[Market, LocalMarketData] = {}
_default_lock = threading.Lock()


def get_local_market_data(market: Market | str | None = None) -> LocalMarketData:
    """进程内共享实例（DuckDB 连接与按日缓存均可复用），按市场隔离。"""
    market_key = normalize_market(market)
    if market_key is Market.CN and _default_instances.get(Market.CN) is None:
        # 兼容历史：无参调用复用既有 CN 单例
        with _default_lock:
            if _default_instances.get(Market.CN) is None:
                _default_instances[Market.CN] = LocalMarketData(market=Market.CN)
    if market_key not in _default_instances:
        with _default_lock:
            if market_key not in _default_instances:
                _default_instances[market_key] = LocalMarketData(market=market_key)
    return _default_instances[market_key]
