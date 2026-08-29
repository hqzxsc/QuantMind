#!/usr/bin/env python3
"""每日推理补跑触发脚本（容器内执行，10-30 秒）。

复盘发现当日无推理 run 时调用：补跑 data_trade_date 的推理并落库
（engine_signal_scores + qm_model_inference_runs），供复盘验证/明日信号。

用法:
  docker cp scripts/trigger_inference.py quantmind:/tmp/
  docker exec quantmind python3 /tmp/trigger_inference.py --date 20260827
  # --date 为特征日（data_trade_date）；prediction 自动 = 下一交易日
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/app")


def main() -> int:
    ap = argparse.ArgumentParser(description="每日推理补跑")
    ap.add_argument("--date", required=True, help="特征日 YYYYMMDD 或 YYYY-MM-DD")
    ap.add_argument("--tenant", default="default")
    ap.add_argument("--user", default="system")
    args = ap.parse_args()

    date_str = args.date.replace("-", "")
    iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    from backend.services.engine.inference.router_service import InferenceRouterService

    svc = InferenceRouterService()
    result = svc.run_daily_inference_script(
        date=iso,
        tenant_id=args.tenant,
        user_id=args.user,
    )
    if not result.success:
        print(f"推理失败: {getattr(result, 'error', '') or result.stdout or ''}", flush=True)
        return 1
    print(
        f"推理完成: run_id={result.run_id} data={iso} "
        f"signals={result.signals_count} fallback={result.fallback_used}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
