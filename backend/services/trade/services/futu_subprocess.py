#!/usr/bin/env python3
"""FutuBroker 子进程执行器。

futu SDK 的连接/等待模型与 asyncio 事件循环混用会死锁，故由
overseas_brokers.FutuBroker 以独立子进程方式调用本脚本。

用法:
  python futu_subprocess.py <host> <port> <rsa_key_path> <op> <payload>

op:
  account — 查询账户（资产/持仓）
  place   — 下单（payload.order: code/price/quantity/order_type/trd_side/is_hk）
  cancel  — 撤单（payload.order_id）
"""
import json
import sys
import warnings

warnings.filterwarnings("ignore")


def main() -> int:
    host, port, rsa_key, op, payload, output_path = (
        sys.argv[1],
        int(sys.argv[2]),
        sys.argv[3],
        sys.argv[4],
        json.loads(sys.argv[5]),
        sys.argv[6],
    )

    from futu.common.sys_config import SysConfig

    SysConfig.set_init_rsa_file(rsa_key)

    from futu import (
        ModifyOrderOp,
        OrderType,
        OpenSecTradeContext,
        TrdEnv,
        TrdMarket,
        TrdSide,
    )

    ctx = OpenSecTradeContext(
        filter_trdmarket=TrdMarket.HK,
        host=host,
        port=port,
        security_firm="FUTUSECURITIES",
        is_encrypt=True,
    )
    try:
        env = TrdEnv.REAL if payload.get("env") == "REAL" else TrdEnv.SIMULATE
        out: dict = {}

        if op == "account":
            ret, data = ctx.accinfo_query(trd_env=env)
            if ret == 0 and len(data):
                row = data.iloc[0]
                out = {
                    "total_asset": float(row.get("total_assets") or 0),
                    "cash": float(row.get("cash") or 0),
                    "market_value": float(row.get("market_val") or 0),
                }
            ret2, plist = ctx.position_list_query(trd_env=env)
            positions = {}
            if ret2 == 0 and len(plist):
                for _, p in plist.iterrows():
                    positions[str(p.get("code", ""))] = {
                        "volume": float(p.get("qty") or 0),
                        "available_volume": float(p.get("can_sell_qty") or 0),
                        "price": float(p.get("current_price") or 0),
                        "market_value": float(p.get("market_val") or 0),
                        "cost": float(p.get("cost_price") or 0),
                    }
            out["positions"] = positions

        elif op == "place":
            order = payload["order"]
            order_type = {
                "MARKET": OrderType.MARKET,
                "NORMAL": OrderType.NORMAL,
            }.get(order["order_type"], OrderType.NORMAL)
            trd_side = {
                "BUY": TrdSide.BUY,
                "SELL": TrdSide.SELL,
            }.get(order["trd_side"], TrdSide.BUY)
            ret, data = ctx.place_order(
                code=order["code"],
                price=float(order["price"]),
                quantity=float(order["quantity"]),
                order_type=order_type,
                trd_side=trd_side,
                trd_env=env,
                adjust_limit=0.0 if order.get("is_hk") else None,
            )
            if ret != 0:
                out = {"success": False, "message": str(data)}
            else:
                out = {
                    "success": True,
                    "order_id": str(data.get("order_id", "")),
                    "message": "SUBMITTED",
                }

        elif op == "cancel":
            ret, data = ctx.modify_order(
                ModifyOrderOp.CANCEL,
                order_id=payload["order_id"],
                qty=0,
                price=0,
                trd_env=env,
            )
            out = {"success": ret == 0, "message": str(data) if ret != 0 else "CANCELLED"}

        else:
            out = {"success": False, "message": f"unknown op: {op}"}

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False))
        return 0
    finally:
        ctx.close()


if __name__ == "__main__":
    sys.exit(main())
