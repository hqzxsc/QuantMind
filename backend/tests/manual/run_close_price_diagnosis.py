"""诊断前瞻收益量级异常：mean=2.84 (284%) 明显不合理。

怀疑 close 列存在复权口径混用或单位问题。
"""
import sys

import pandas as pd

sys.path.insert(0, "/app")

# 旧 model_features_*.parquet 已废弃，统一读 QuantDB l1_factors 分区
from backend.shared.feature_source import read_factor_source

df = read_factor_source(
    "l1_factors", "CN", columns=["symbol", "trade_date", "close", "volume"]
)
cols = list(df.columns)
print("总列数:", len(cols))
print("close 相关列:", [c for c in cols if "close" in c.lower() or "adj" in c.lower()])
print("price 相关列:", [c for c in cols if "price" in c.lower()])

df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
print()
print("close 全局统计:")
print(df["close"].describe())

print()
print("=== 单只股票的 close 时间序列（600519 贵州茅台）===")
for sym in ("SH600519", "600519.SH", "600519"):
    sub = df[df["symbol"] == sym].sort_values("trade_date")
    if not sub.empty:
        print("symbol 格式命中:", sym, " 行数:", len(sub))
        print(sub.tail(15)[["trade_date", "close"]].to_string(index=False))
        break
else:
    print("未命中，实际 symbol 样例:", df["symbol"].drop_duplicates().head(5).tolist())

print()
print("=== 逐日 close 中位数（看是否稳定）===")
med = df.groupby("trade_date")["close"].median()
print(med.tail(12).to_string())

print()
print("=== 同一股票相邻日 close 变化率分布（应集中在 ±10% 内）===")
d = df.sort_values(["symbol", "trade_date"]).copy()
d["r"] = d.groupby("symbol")["close"].pct_change()
r = d["r"].dropna()
print(r.describe())
print("超过 +50% 的比例: %.4f%%" % (100.0 * (r > 0.5).mean()))
print("超过 +100% 的比例: %.4f%%" % (100.0 * (r > 1.0).mean()))
