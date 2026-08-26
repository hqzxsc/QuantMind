"""Market analysis domain."""

from enum import Enum


class SectorType(str, Enum):
    INDUSTRY = "industry"
    CONCEPT = "concept"
    INDEX = "index"


class AnomalyType(str, Enum):
    VOLUME_SURGE = "volume_surge"
    PRICE_LIMIT_UP = "price_limit_up"
    PRICE_LIMIT_DOWN = "price_limit_down"
    SECTOR_ROTATION = "sector_rotation"
    FLOW_REVERSAL = "flow_reversal"
    BREADTH_DIVERGENCE = "breadth_divergence"


class AnomalySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SectorNotFoundError(LookupError):
    pass


class SectorConflictError(RuntimeError):
    pass


def classify_sentiment(score: float) -> str:
    """根据情绪分数返回标签。"""
    if score is None:
        return "neutral"
    if score >= 0.6:
        return "greedy"
    if score >= 0.2:
        return "optimistic"
    if score > -0.2:
        return "neutral"
    if score > -0.6:
        return "pessimistic"
    return "fearful"
