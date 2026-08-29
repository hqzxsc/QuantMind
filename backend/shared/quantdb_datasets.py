"""QuantDB 数据集目录规格（云端 COS 对象与本地落盘的统一描述）。

从 quantdb_console 抽出为共享模块，供管理台路由与本地扫描脚本共用，
避免脚本反向 import 路由模块引入 FastAPI 依赖。

layout 决定落盘形态与扫描/预览读法：
  partition — dt=YYYYMMDD/data.parquet 按交易日分区（含 quarter=YYYYQN 季度分区）
  symbol    — 每标的一个 {SYMBOL}.parquet
  single    — 整个数据集一个 parquet（文件名不定，如 instrument_list.parquet）

云端 object key 与本地落盘相对路径一致（posix 分隔），
例如 ``1_kline_data/daily_forward/dt=20260804/data.parquet``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Layout = Literal["partition", "symbol", "single"]


@dataclass(frozen=True)
class DatasetSpec:
    dataset: str
    name: str
    category_id: str
    group: str
    rel_dir: str
    layout: Layout
    note: str = ""


GROUPS: list[dict[str, str]] = [
    {"id": "kline", "name": "K线行情", "category_id": "1"},
    {"id": "base_sector", "name": "基础板块", "category_id": "2"},
    {"id": "financial", "name": "财务数据", "category_id": "3"},
    {"id": "bond_etf", "name": "债券/ETF", "category_id": "4"},
    {"id": "technical", "name": "技术衍生", "category_id": "5"},
    {"id": "ml", "name": "ML数据集", "category_id": "6"},
]

DATASETS: tuple[DatasetSpec, ...] = (
    # 1 K线行情
    DatasetSpec("daily_forward", "日线前复权", "1", "kline", "1_kline_data/daily_forward", "partition", "训练/回测主用"),
    DatasetSpec("daily_backward", "日线后复权", "1", "kline", "1_kline_data/daily_backward", "partition"),
    DatasetSpec("daily_unadjusted", "日线不复权", "1", "kline", "1_kline_data/daily_unadjusted", "partition", "amount/volume 单位在 20260721 切换"),
    DatasetSpec("index_daily", "指数日线", "1", "kline", "1_kline_data/index_daily", "partition"),
    DatasetSpec("min5_kline", "5分钟线", "1", "kline", "1_kline_data/min5_kline", "symbol"),
    DatasetSpec("min1_kline", "1分钟线", "1", "kline", "1_kline_data/min1_kline", "symbol", "体积大，按需同步"),
    DatasetSpec("tick_data", "Tick逐笔", "1", "kline", "1_kline_data/tick_data", "partition", "流量消耗极高"),
    # 2 基础板块
    DatasetSpec("instrument_detail", "个股详情", "2", "base_sector", "2_base_sector/instrument_detail", "single", "152 列基本面快照"),
    DatasetSpec("sector_concept", "板块概念", "2", "base_sector", "2_base_sector/sector_concept", "single"),
    DatasetSpec("index_weights", "指数权重", "2", "base_sector", "2_base_sector/index_weights", "symbol", "沪深300/中证500/1000 等"),
    DatasetSpec("trading_calendar", "交易日历", "2", "base_sector", "2_base_sector/trading_calendar", "single"),
    DatasetSpec("margin_trading", "融资融券", "2", "base_sector", "2_base_sector/margin_trading", "partition"),
    DatasetSpec("hsgt_north", "北向资金(季度)", "2", "base_sector", "2_base_sector/hsgt_north", "partition", "2024-08 起北向个股改季度披露，每季度末+第5交易日抓取，symbol 6位格式"),
    DatasetSpec("hsgt_north_daily", "北向资金日频(akshare)", "2", "base_sector", "2_base_sector/hsgt_north/daily_freq", "symbol", "2017-03~2024-08 北向持股日频，akshare逐股拉取"),
    # 3 财务数据
    DatasetSpec("balance", "资产负债表", "3", "financial", "3_financial_data/balance", "symbol"),
    DatasetSpec("income", "利润表", "3", "financial", "3_financial_data/income", "symbol"),
    DatasetSpec("cashflow", "现金流量表", "3", "financial", "3_financial_data/cashflow", "symbol"),
    DatasetSpec("capital", "股本结构", "3", "financial", "3_financial_data/capital", "symbol"),
    DatasetSpec("pershare_index", "每股指标", "3", "financial", "3_financial_data/pershare_index", "symbol"),
    DatasetSpec("dividend_factors", "分红因子", "3", "financial", "3_financial_data/dividend_factors", "symbol"),
    DatasetSpec("holder_num", "股东户数", "3", "financial", "3_financial_data/holder_num", "symbol"),
    # 4 债券/ETF
    DatasetSpec("etf_pcf", "ETF申赎清单", "4", "bond_etf", "4_bond_etf/etf_pcf", "symbol"),
    DatasetSpec("convertible_bond", "可转债", "4", "bond_etf", "4_bond_etf/convertible_bond", "symbol"),
    # 5 技术衍生
    DatasetSpec("valuation", "估值", "5", "technical", "5_technical_derived/valuation", "partition", "PE/PB/市值"),
    DatasetSpec("technical_indicators", "技术指标", "5", "technical", "5_technical_derived/technical_indicators", "partition", "本地覆盖不全，优先用 features_daily"),
    DatasetSpec("market_sentiment", "市场情绪", "5", "technical", "5_technical_derived/market_sentiment", "partition"),
    # 6 ML数据集
    DatasetSpec("features_daily", "日频特征", "6", "ml", "6_ml_datasets/features_daily", "partition", "技术指标 + 估值合并，PG 填充主源"),
    DatasetSpec("l1_factors", "L1 因子", "6", "ml", "6_ml_datasets/l1_factors", "partition", "因子挖掘核心"),
    DatasetSpec("l2_factors", "L2 因子", "6", "ml", "6_ml_datasets/l2_factors", "partition", "高频微观因子"),
    DatasetSpec("l1_l2_factors", "L1+L2 合并", "6", "ml", "6_ml_datasets/l1_l2_factors", "partition"),
)

_BY_NAME: dict[str, DatasetSpec] = {ds.dataset: ds for ds in DATASETS}


def get_dataset_spec(dataset: str) -> DatasetSpec:
    spec = _BY_NAME.get(dataset)
    if spec is None:
        raise KeyError(f"未知数据集: {dataset}")
    return spec
