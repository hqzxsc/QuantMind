from enum import Enum


class _CaseInsensitiveEnum(str, Enum):
    @classmethod
    def _missing_(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None

        upper = text.upper()
        if upper in cls.__members__:
            return cls.__members__[upper]

        lower = text.lower()
        for member in cls:
            if str(member.value).lower() == lower:
                return member
        return None

class Exchange(str, Enum):
    SSE = "SSE"
    SZSE = "SZSE"
    BSE = "BSE"
    SHFE = "SHFE"
    DCE = "DCE"
    CZCE = "CZCE"
    CFFEX = "CFFEX"
    INE = "INE"

class OrderSide(_CaseInsensitiveEnum):
    BUY = "buy"
    SELL = "sell"


class PositionSide(_CaseInsensitiveEnum):
    # 值与 db_init.sql 的 PG positionside enum 一致（LONG/SHORT）
    LONG = "LONG"
    SHORT = "SHORT"


class TradeAction(_CaseInsensitiveEnum):
    # 值与 db_init.sql 的 PG tradeaction enum 一致（OPEN/CLOSE/OPEN_REVERSE/CLOSE_REVERSE）
    BUY_TO_OPEN = "OPEN"
    SELL_TO_CLOSE = "CLOSE"
    SELL_TO_OPEN = "OPEN_REVERSE"
    BUY_TO_CLOSE = "CLOSE_REVERSE"

class OrderType(_CaseInsensitiveEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"

class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"

class OrderStatus(_CaseInsensitiveEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    OPEN = "submitted"
    PARTIAL = "partially_filled"

class TradingMode(_CaseInsensitiveEnum):
    SIMULATION = "SIMULATION"
    REAL = "REAL"
    SHADOW = "SHADOW"
    BACKTEST = "SIMULATION"
