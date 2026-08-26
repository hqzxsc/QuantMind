"""板块轮动分析模块

计算板块轮动信号，判断板块当前处于资金 accumulation(积累) /
distribution(派发) / neutral(中性) 阶段。

轮动信号基于以下维度的综合评分:
    1. 价格动量: 近 N 日涨跌幅
    2. 资金流向: 近 N 日净流入额
    3. 成交量趋势: 近 N 日成交量变化
    4. 相对强度: 板块相对于大盘的超额收益

信号类型:
    - accumulation: 资金持续流入 + 价格上涨 → 建仓阶段
    - distribution: 资金持续流出 + 价格下跌 → 减仓阶段
    - neutral: 信号不明显或方向矛盾
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Any

from .repository import MarketAnalysisRepository


class RotationSignalType(str, Enum):
    """轮动信号类型"""
    ACCUMULATION = "accumulation"    # 积累/建仓
    DISTRIBUTION = "distribution"  # 派发/减仓
    NEUTRAL = "neutral"             # 中性


@dataclass
class RotationSignal:
    """板块轮动信号

    Attributes:
        sector_id: 板块 ID
        signal_type: 信号类型 (accumulation / distribution / neutral)
        strength: 信号强度 [0, 1]，1 为最强
        period_days: 计算周期天数
        details: 详细评分数据
    """

    sector_id: str
    signal_type: str
    strength: float
    period_days: int
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sector_id": self.sector_id,
            "signal_type": self.signal_type,
            "strength": round(self.strength, 4),
            "period_days": self.period_days,
            "details": self.details,
        }


class SectorRotationAnalyzer:
    """板块轮动分析器

    通过分析板块近 N 日的指标数据，计算轮动信号。

    使用示例::

        analyzer = SectorRotationAnalyzer(repo)
        signal = await analyzer.compute_rotation_signal("banking", days=20)
        if signal.signal_type == "accumulation":
            print(f"板块处于建仓阶段，强度 {signal.strength}")
    """

    # 信号强度阈值: 综合评分绝对值超过此值才判定为 accumulation / distribution
    SIGNAL_THRESHOLD = 0.2

    def __init__(self, repository: MarketAnalysisRepository):
        self.repository = repository

    async def compute_rotation_signal(
        self,
        sector_id: str,
        days: int = 20,
    ) -> RotationSignal:
        """计算板块轮动信号

        Args:
            sector_id: 板块 ID
            days: 回看周期天数 (默认 20 个交易日)

        Returns:
            RotationSignal 对象
        """
        # 获取近 N 天的指标数据
        end_date = date.today()
        start_date = end_date - timedelta(days=days * 2)  # 留余量覆盖非交易日

        history = await self.repository.get_metrics_history(
            sector_id, start_date=start_date, end_date=end_date
        )

        # 取最近 days 条记录
        recent = history[-days:] if len(history) >= days else history

        if len(recent) < 3:
            # 数据不足，返回中性信号
            return RotationSignal(
                sector_id=sector_id,
                signal_type=RotationSignalType.NEUTRAL.value,
                strength=0.0,
                period_days=days,
                details={"reason": "数据不足", "data_points": len(recent)},
            )

        # 计算各维度评分
        momentum_score = self._compute_momentum(recent)
        flow_score = self._compute_flow_score(recent)
        volume_score = self._compute_volume_trend(recent)
        relative_score = self._compute_relative_strength(recent)

        # 综合评分 (加权平均)
        # 价格动量 40% + 资金流向 30% + 成交量趋势 15% + 相对强度 15%
        composite = (
            momentum_score * 0.40
            + flow_score * 0.30
            + volume_score * 0.15
            + relative_score * 0.15
        )

        # 判定信号类型
        if composite > self.SIGNAL_THRESHOLD:
            signal_type = RotationSignalType.ACCUMULATION.value
        elif composite < -self.SIGNAL_THRESHOLD:
            signal_type = RotationSignalType.DISTRIBUTION.value
        else:
            signal_type = RotationSignalType.NEUTRAL.value

        # 信号强度: 归一化到 [0, 1]
        strength = min(abs(composite), 1.0)

        return RotationSignal(
            sector_id=sector_id,
            signal_type=signal_type,
            strength=strength,
            period_days=days,
            details={
                "composite_score": round(composite, 4),
                "momentum_score": round(momentum_score, 4),
                "flow_score": round(flow_score, 4),
                "volume_score": round(volume_score, 4),
                "relative_score": round(relative_score, 4),
                "data_points": len(recent),
                "date_range": {
                    "start": str(recent[0].trade_date) if recent else None,
                    "end": str(recent[-1].trade_date) if recent else None,
                },
            },
        )

    # ---- 评分维度 ----

    @staticmethod
    def _compute_momentum(history: list[Any]) -> float:
        """计算价格动量评分

        基于首尾涨跌幅差值，归一化到 [-1, 1]。

        Returns:
            正值表示上涨趋势，负值表示下跌趋势
        """
        if len(history) < 2:
            return 0.0

        first = history[0].avg_pct_change
        last = history[-1].avg_pct_change
        if first is None or last is None:
            return 0.0

        # 总涨跌幅
        total_change = last - first
        # 归一化: ±20% 对应 ±1
        return max(-1.0, min(1.0, total_change / 20.0))

    @staticmethod
    def _compute_flow_score(history: list[Any]) -> float:
        """计算资金流向评分

        基于近 N 日净流入额的均值，归一化到 [-1, 1]。
        """
        flows = [m.net_inflow for m in history if m.net_inflow is not None]
        if not flows:
            return 0.0

        avg_flow = sum(flows) / len(flows)
        if avg_flow == 0:
            return 0.0

        # 归一化: 根据正负方向和量级
        # 使用 tanh 压缩，避免极端值
        import math
        # 以 10亿 为参考量级
        scale = 1e9
        return max(-1.0, min(1.0, math.tanh(avg_flow / scale)))

    @staticmethod
    def _compute_volume_trend(history: list[Any]) -> float:
        """计算成交量趋势评分

        比较后半段与前半段的平均成交量。
        """
        if len(history) < 4:
            return 0.0

        mid = len(history) // 2
        # 从 details 中提取成交量
        first_half_vols = []
        second_half_vols = []
        for i, m in enumerate(history):
            vol = None
            if m.details and isinstance(m.details, dict):
                vol = m.details.get("volume")
            if vol is not None:
                if i < mid:
                    first_half_vols.append(vol)
                else:
                    second_half_vols.append(vol)

        if not first_half_vols or not second_half_vols:
            return 0.0

        avg_first = sum(first_half_vols) / len(first_half_vols)
        avg_second = sum(second_half_vols) / len(second_half_vols)

        if avg_first <= 0:
            return 0.0

        # 量比变化
        ratio = (avg_second - avg_first) / avg_first
        # 归一化: ±50% 对应 ±1
        return max(-1.0, min(1.0, ratio / 0.5))

    @staticmethod
    def _compute_relative_strength(history: list[Any]) -> float:
        """计算相对强度评分

        基于板块涨跌幅与零基准的比较（简化版，无大盘数据时使用涨跌幅均值）。
        """
        pct_changes = [m.avg_pct_change for m in history if m.avg_pct_change is not None]
        if not pct_changes:
            return 0.0

        avg_pct = sum(pct_changes) / len(pct_changes)
        # 归一化: ±5% 对应 ±1
        return max(-1.0, min(1.0, avg_pct / 5.0))
