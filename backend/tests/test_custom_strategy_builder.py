from unittest.mock import MagicMock

import pytest

from backend.services.engine.qlib_app.schemas.backtest import QlibBacktestRequest
from backend.services.engine.qlib_app.services.strategy_builder import (
    CustomStrategyBuilder,
    LongShortTopkBuilder,
    StrategyFactory,
    extract_backtest_dates,
)


class MockStrategy:
    def __init__(self, pool_file_key, pool_file_url, signal="<PRED>"):
        self.pool_file_key = pool_file_key
        self.pool_file_url = pool_file_url
        self.signal = signal


def test_custom_strategy_builder_proactive_repair():
    builder = CustomStrategyBuilder()

    # Mock request
    request = MagicMock(spec=QlibBacktestRequest)
    request.strategy_content = """
class MockStrategy(BaseStrategy):
    def __init__(self, pool_file_key, pool_file_url, signal="<PRED>"):
        pass

STRATEGY_CONFIG = {
    "class": "MockStrategy",
    "kwargs": {
        "signal": "test_signal"
    }
}
"""
    request.strategy_params = MagicMock()
    # Mock attributes that builder looks for in request.strategy_params
    for key in ["topk", "n_drop", "min_score", "max_weight", "stop_loss", "take_profit"]:
        setattr(request.strategy_params, key, None)

    market_state_kwargs = {"market": "cn"}
    signal_data = "some_df"
    backtest_id = "test_id"

    # We need to mock _build_strategy_from_content to return our dict and namespace
    builder._build_strategy_from_content = MagicMock(
        return_value=({"class": "MockStrategy", "kwargs": {"signal": "test_signal"}}, {"MockStrategy": MockStrategy})
    )

    # We also need to bypass _validate_strategy_content which might fail on magicmock
    builder._validate_strategy_content = MagicMock()

    result = builder.build(request, market_state_kwargs, signal_data, backtest_id)

    # result should be a dict if it hasn't been instantiated, or we can check the kwargs in the returned dict
    # In the current implementation, if module_path is empty, it attempts instantiation
    # Let's check the result
    assert isinstance(result, dict) or hasattr(result, "pool_file_key")

    if isinstance(result, dict):
        kwargs = result["kwargs"]
        assert kwargs["pool_file_key"] == ""
        assert kwargs["pool_file_url"] == ""
        assert kwargs["signal"] == "test_signal"  # preserved
    else:
        assert result.pool_file_key == ""
        assert result.pool_file_url == ""


def test_custom_strategy_builder_strict_filtering_with_kwargs():
    builder = CustomStrategyBuilder()

    # Mock class that accepts **kwargs but shouldn't receive 'topk' from system
    class KwargsStrategy:
        def __init__(self, mandatory_param, **kwargs):
            self.mandatory_param = mandatory_param
            self.extra = kwargs

    # Mock request with topk slider value
    request = MagicMock(spec=QlibBacktestRequest)
    request.strategy_content = "class KwargsStrategy(BaseStrategy): ..."
    request.strategy_params = MagicMock()
    request.strategy_params.topk = 100
    for key in ["n_drop", "min_score", "max_weight", "stop_loss", "take_profit"]:
        setattr(request.strategy_params, key, None)

    market_state_kwargs = {}
    signal_data = None
    backtest_id = "test_id"

    # Mock return values for support methods
    builder._build_strategy_from_content = MagicMock(
        return_value=(
            {"class": "KwargsStrategy", "kwargs": {"mandatory_param": "val"}},
            {"KwargsStrategy": KwargsStrategy},
        )
    )
    builder._validate_strategy_content = MagicMock()

    result = builder.build(request, market_state_kwargs, signal_data, backtest_id)

    # Check that 'topk' was NOT merged because it's not in signature or original kwargs
    if isinstance(result, dict):
        kwargs = result["kwargs"]
        assert "topk" not in kwargs
        assert kwargs["mandatory_param"] == "val"
    else:
        assert not hasattr(result, "topk")
        assert result.mandatory_param == "val"


def test_custom_strategy_builder_code_first_keeps_explicit_topk():
    """专家模式代码优先：代码里写 topk=10 时，前端/UI 默认 50 不得覆盖。"""
    builder = CustomStrategyBuilder()

    class TopkStrategy:
        def __init__(self, topk, n_drop=5, signal="<PRED>", **kwargs):
            self.topk = topk

    request = MagicMock(spec=QlibBacktestRequest)
    request.strategy_content = "STRATEGY_CONFIG = {... topk: 10 ...}"
    request.strategy_params = MagicMock()
    request.strategy_params.topk = 50
    request.strategy_params.n_drop = 5
    for key in ["min_score", "max_weight", "stop_loss", "take_profit"]:
        setattr(request.strategy_params, key, None)

    builder._build_strategy_from_content = MagicMock(
        return_value=(
            {"class": "TopkStrategy", "kwargs": {"topk": 10, "signal": "<PRED>"}},
            {"TopkStrategy": TopkStrategy},
        )
    )
    builder._validate_strategy_content = MagicMock()

    result = builder.build(request, {}, None, "bt_test")

    if isinstance(result, dict):
        assert result["kwargs"]["topk"] == 10
    else:
        assert result.topk == 10


def test_custom_strategy_builder_backfills_missing_topk():
    """代码缺失 topk 时，类支持则用 UI/默认值回填。"""
    builder = CustomStrategyBuilder()

    class TopkStrategy:
        def __init__(self, topk, signal="<PRED>", **kwargs):
            self.topk = topk

    request = MagicMock(spec=QlibBacktestRequest)
    request.strategy_content = "STRATEGY_CONFIG without topk"
    request.strategy_params = MagicMock()
    request.strategy_params.topk = 50
    for key in ["n_drop", "min_score", "max_weight", "stop_loss", "take_profit"]:
        setattr(request.strategy_params, key, None)

    builder._build_strategy_from_content = MagicMock(
        return_value=(
            {"class": "TopkStrategy", "kwargs": {"signal": "<PRED>"}},
            {"TopkStrategy": TopkStrategy},
        )
    )
    builder._validate_strategy_content = MagicMock()

    result = builder.build(request, {}, None, "bt_test")

    if isinstance(result, dict):
        assert result["kwargs"]["topk"] == 50
    else:
        assert result.topk == 50


@pytest.mark.parametrize("strategy_type", ["custom", "CustomStrategy", "custom_strategy"])
def test_strategy_factory_maps_custom_aliases(strategy_type):
    builder = StrategyFactory.get_builder(strategy_type)
    assert isinstance(builder, CustomStrategyBuilder)


def test_extract_backtest_dates_from_backtest_config():
    code = 'BACKTEST_CONFIG = {"start_date": "2024-01-01", "end_date": "2024-12-31"}\n'
    assert extract_backtest_dates(code) == {
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
    }


def test_extract_backtest_dates_from_constants():
    code = 'START_DATE = "2023-06-01"\nEND_DATE = "2024-06-01"\n'
    assert extract_backtest_dates(code) == {
        "start_date": "2023-06-01",
        "end_date": "2024-06-01",
    }


def test_extract_backtest_dates_from_getter():
    code = (
        "def get_backtest_config():\n"
        '    return {"start": "2024-01-01", "end": "2024-12-31"}\n'
    )
    assert extract_backtest_dates(code) == {
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
    }


def test_extract_backtest_dates_none_when_absent():
    code = 'STRATEGY_CONFIG = {"class": "X", "kwargs": {"topk": 10}}\n'
    assert extract_backtest_dates(code) is None


def test_extract_backtest_dates_rejects_bad_format():
    with pytest.raises(ValueError):
        extract_backtest_dates('START_DATE = "2024-01-01"\nEND_DATE = "2024/12/31"\n')


def test_extract_backtest_dates_rejects_inverted_range():
    with pytest.raises(ValueError):
        extract_backtest_dates(
            'BACKTEST_CONFIG = {"start_date": "2024-12-31", "end_date": "2024-01-01"}\n'
        )


def test_extract_backtest_dates_config_beats_constants():
    code = (
        'BACKTEST_CONFIG = {"start_date": "2024-01-01", "end_date": "2024-12-31"}\n'
        'START_DATE = "2020-01-01"\nEND_DATE = "2020-12-31"\n'
    )
    assert extract_backtest_dates(code) == {
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
    }


def test_strategy_factory_maps_long_short_topk_template():
    builder = StrategyFactory.get_builder("long_short_topk")
    assert isinstance(builder, LongShortTopkBuilder)


def test_custom_strategy_builder_sets_dynamic_module_path_for_local_class():
    builder = CustomStrategyBuilder()

    request = MagicMock(spec=QlibBacktestRequest)
    request.strategy_content = "class MarginTopKLongShortStrategy(BaseStrategy): ..."
    request.strategy_params = MagicMock()
    for key in [
        "topk",
        "n_drop",
        "min_score",
        "max_weight",
        "stop_loss",
        "take_profit",
        "rebalance_days",
        "enable_short_selling",
        "margin_stock_pool",
        "financing_rate",
        "borrow_rate",
        "max_short_exposure",
        "max_leverage",
    ]:
        setattr(request.strategy_params, key, None)

    builder._build_strategy_from_content = MagicMock(
        return_value=(
            {
                "class": "MarginTopKLongShortStrategy",
                "module_path": "",
                "kwargs": {"signal": "<PRED>"},
            },
            {
                "__strategy_module_name__": "custom_strategy_test_module",
                "MarginTopKLongShortStrategy": type("MarginTopKLongShortStrategy", (), {}),
            },
        )
    )
    builder._validate_strategy_content = MagicMock()

    result = builder.build(request, {}, None, "bt_test")

    assert isinstance(result, dict)
    assert result["module_path"] == "custom_strategy_test_module"
