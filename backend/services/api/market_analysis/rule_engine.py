"""异动告警规则引擎

基于规则自动检测市场异动并生成告警，支持:
    - 成交量激增检测 (超过5日均量2倍)
    - 资金流向反转检测 (连续流入后突然流出，或反之)
    - 涨跌家数背离检测 (指数上涨但下跌家数 > 上涨家数)

规则引擎设计为无状态函数集合，可独立测试，也可组合运行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .domain import AnomalySeverity, AnomalyType


@dataclass
class Anomaly:
    """异动告警数据结构

    Attributes:
        type: 异动类型 (volume_surge / flow_reversal / breadth_divergence)
        sector_id: 关联板块 ID (可为 None 表示全市场异动)
        title: 告警标题
        description: 详细描述
        severity: 严重等级 (info / warning / critical)
        details: 附加数据 (用于前端展示和审计)
    """

    type: str
    sector_id: str | None
    title: str
    description: str
    severity: str = AnomalySeverity.INFO.value
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转为字典"""
        return {
            "type": self.type,
            "sector_id": self.sector_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "details": self.details,
        }


class AnomalyRuleEngine:
    """异动告警规则引擎

    通过一系列规则函数检测市场异动，每条规则接收板块指标数据，
    返回 ``Anomaly`` 或 ``None``。

    使用示例::

        engine = AnomalyRuleEngine()
        anomalies = engine.run_all_rules(metrics_list)
        for a in anomalies:
            print(f"[{a.severity}] {a.title}: {a.description}")
    """

    # ---- 规则参数 ----

    # 成交量激增: 当日成交量 / 5日均量 > 此阈值
    VOLUME_SURGE_RATIO = 2.0

    # 资金流向反转: 前 N 天净流向方向与当日方向相反
    FLOW_REVERSAL_LOOKBACK = 3

    # 涨跌家数背离: 下跌家数 / 上涨家数 > 此阈值 且 avg_pct_change > 0
    BREADTH_DIVERGENCE_RATIO = 1.5

    # ---- 规则 1: 成交量激增 ----

    def detect_volume_surge(self, metrics: dict[str, Any]) -> Anomaly | None:
        """检测成交量激增

        判定条件: 当日成交量超过5日均量的 2 倍。

        Args:
            metrics: 板块指标字典，需包含:
                - volume: 当日成交量
                - avg_volume_5d: 5日均量 (可选，无则跳过)
                - sector_id: 板块 ID
                - trade_date: 交易日期

        Returns:
            检测到异动时返回 Anomaly，否则 None
        """
        volume = metrics.get("volume")
        avg_volume = metrics.get("avg_volume_5d")
        sector_id = metrics.get("sector_id")
        trade_date = metrics.get("trade_date", "")

        if volume is None or avg_volume is None or avg_volume <= 0:
            return None

        ratio = volume / avg_volume
        if ratio < self.VOLUME_SURGE_RATIO:
            return None

        # 根据倍数确定严重等级
        if ratio >= 3.0:
            severity = AnomalySeverity.CRITICAL.value
        elif ratio >= 2.5:
            severity = AnomalySeverity.WARNING.value
        else:
            severity = AnomalySeverity.INFO.value

        sector_name = metrics.get("sector_name", sector_id or "未知板块")
        return Anomaly(
            type=AnomalyType.VOLUME_SURGE.value,
            sector_id=sector_id,
            title=f"{sector_name}成交量激增",
            description=(
                f"成交量({volume:.0f})达到5日均量({avg_volume:.0f})的"
                f"{ratio:.1f}倍，存在异动"
            ),
            severity=severity,
            details={
                "volume": volume,
                "avg_volume_5d": avg_volume,
                "ratio": round(ratio, 2),
                "trade_date": str(trade_date),
            },
        )

    # ---- 规则 2: 资金流向反转 ----

    def detect_flow_reversal(self, metrics: dict[str, Any]) -> Anomaly | None:
        """检测资金流向反转

        判定条件: 前 N 天持续净流入(流出)，当日突然转为净流出(流入)。

        Args:
            metrics: 板块指标字典，需包含:
                - net_flow: 当日净流向
                - prev_flows: 前 N 天的净流向列表 (按时间正序)
                - sector_id: 板块 ID
                - trade_date: 交易日期

        Returns:
            检测到异动时返回 Anomaly，否则 None
        """
        net_flow = metrics.get("net_flow")
        prev_flows = metrics.get("prev_flows")
        sector_id = metrics.get("sector_id")
        trade_date = metrics.get("trade_date", "")

        if net_flow is None or not prev_flows:
            return None

        # 取最近 N 天的数据
        lookback = prev_flows[-self.FLOW_REVERSAL_LOOKBACK:]
        if len(lookback) < 2:
            return None

        # 前几天是否持续同一方向
        prev_positive = all(f > 0 for f in lookback if f is not None)
        prev_negative = all(f < 0 for f in lookback if f is not None)

        if not (prev_positive or prev_negative):
            return None

        # 当日方向反转
        if prev_positive and net_flow < 0:
            direction = "由净流入反转为净流出"
            severity = AnomalySeverity.WARNING.value
        elif prev_negative and net_flow > 0:
            direction = "由净流出反转为净流入"
            severity = AnomalySeverity.INFO.value
        else:
            return None

        sector_name = metrics.get("sector_name", sector_id or "未知板块")
        avg_prev = sum(lookback) / len(lookback)
        return Anomaly(
            type=AnomalyType.FLOW_REVERSAL.value,
            sector_id=sector_id,
            title=f"{sector_name}资金流向反转",
            description=(
                f"前{len(lookback)}天平均净流向{avg_prev:+.2f}，"
                f"当日净流向{net_flow:+.2f}，{direction}"
            ),
            severity=severity,
            details={
                "net_flow": net_flow,
                "prev_avg_flow": round(avg_prev, 2),
                "prev_flows": lookback,
                "reversal_direction": "inflow_to_outflow" if prev_positive else "outflow_to_inflow",
                "trade_date": str(trade_date),
            },
        )

    # ---- 规则 3: 涨跌家数背离 ----

    def detect_breadth_divergence(self, metrics: dict[str, Any]) -> Anomaly | None:
        """检测涨跌家数背离

        判定条件:
            - 板块均价上涨(avg_pct_change > 0) 但下跌家数 > 上涨家数 × 1.5
            - 或板块均价下跌(avg_pct_change < 0) 但上涨家数 > 下跌家数 × 1.5

        Args:
            metrics: 板块指标字典，需包含:
                - avg_pct_change: 板块平均涨跌幅
                - advance_count: 上涨家数
                - decline_count: 下跌家数
                - sector_id: 板块 ID
                - trade_date: 交易日期

        Returns:
            检测到异动时返回 Anomaly，否则 None
        """
        avg_pct_change = metrics.get("avg_pct_change")
        advance = metrics.get("advance_count", 0) or 0
        decline = metrics.get("decline_count", 0) or 0
        sector_id = metrics.get("sector_id")
        trade_date = metrics.get("trade_date", "")

        if avg_pct_change is None:
            return None
        if advance == 0 and decline == 0:
            return None

        is_divergent = False
        divergence_desc = ""

        if avg_pct_change > 0 and decline > 0 and advance > 0:
            # 价升但跌多涨少
            if decline / advance > self.BREADTH_DIVERGENCE_RATIO:
                is_divergent = True
                divergence_desc = (
                    f"板块均价上涨{avg_pct_change:+.2f}%，"
                    f"但下跌家数({decline})远超上涨家数({advance})"
                )
        elif avg_pct_change < 0 and advance > 0 and decline > 0:
            # 价跌但涨多跌少
            if advance / decline > self.BREADTH_DIVERGENCE_RATIO:
                is_divergent = True
                divergence_desc = (
                    f"板块均价下跌{avg_pct_change:+.2f}%，"
                    f"但上涨家数({advance})远超下跌家数({decline})"
                )

        if not is_divergent:
            return None

        sector_name = metrics.get("sector_name", sector_id or "未知板块")
        return Anomaly(
            type=AnomalyType.BREADTH_DIVERGENCE.value,
            sector_id=sector_id,
            title=f"{sector_name}涨跌家数背离",
            description=divergence_desc,
            severity=AnomalySeverity.WARNING.value,
            details={
                "avg_pct_change": avg_pct_change,
                "advance_count": advance,
                "decline_count": decline,
                "trade_date": str(trade_date),
            },
        )

    # ---- 运行所有规则 ----

    def run_all_rules(self, metrics_list: list[dict[str, Any]]) -> list[Anomaly]:
        """对一组板块指标运行所有规则

        Args:
            metrics_list: 板块指标字典列表，每个字典代表一个板块的当日指标

        Returns:
            检测到的所有异动列表
        """
        anomalies: list[Anomaly] = []
        for metrics in metrics_list:
            for rule in (
                self.detect_volume_surge,
                self.detect_flow_reversal,
                self.detect_breadth_divergence,
            ):
                result = rule(metrics)
                if result is not None:
                    anomalies.append(result)
        return anomalies
