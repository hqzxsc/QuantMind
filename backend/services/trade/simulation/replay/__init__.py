"""时光回放模块。

组件：
- account.py    — ReplayAccountManager（Redis key 隔离）
- day_runner.py — ReplayDayRunner（单日推演引擎）
- signal_generator.py — ReplaySignalLoader（直读模型 pred.parquet，无预生成）
- router.py     — FastAPI 端点
"""
