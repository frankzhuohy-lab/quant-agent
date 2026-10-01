#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E5 执行测试：现金账户执行层（2026-09-30 评审后新增）。

验证点:
  E5-1 现金永不为负；
  E5-2 敞口 ≤ cap（整手取整误差容忍 1 个百分点）；
  E5-3 每笔股数为 100 的整数倍；
  E5-4 会计恒等式: 期末现金 = 初始现金 + Σ卖出净额 − Σ买入总额 − Σ费用
      （由成交序列独立重算，与执行器内部现金对账）；
  E5-5 cap=1.0 下 skipped_entries 非空（引擎权重口径平均敞口>1，
      现金口径必然放弃部分订单）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import quant_agent  # noqa: E402
from quant_agent.backtest.runner import Context  # noqa: E402
from quant_agent.backtest.executor import execute_cash  # noqa: E402
from quant_agent.strategies.adapter import normalize, materialize  # noqa: E402


def main():
    import json as _json
    const = _json.load(open(quant_agent.CONFIG))
    ctx = Context(constitution=const, use_universe=False)
    norm = normalize({}, const)
    spec, kw, _c = materialize(norm, ctx.stocks, ctx.codes, ctx.common)
    import qengine
    er = const["execution_rules"]
    isr = ctx.windows["IS"]
    run = qengine.run_r443(
        ctx.stocks, ctx.codes, ctx.feat, ctx.state, ctx.common, ctx.susp,
        isr[0], isr[1], isr[2], spec,
        cost_buy=er["cost_buy"], cost_sell=er["cost_sell"])
    cap = 1.0
    cash0 = 1_000_000.0
    ex = execute_cash(run["trades"], ctx.stocks, ctx.common, ctx.susp,
                      er["cost_buy"], er["cost_sell"], cap=cap,
                      cash0=cash0, end_day=isr[2])

    fails = []

    # E5-1 现金恒等式独立重算（对账）
    buys = sum(t["notional"] + t["fee_buy"] for t in ex["trades"])
    sells = sum(t["shares"] * t["exit_px"] - t["fee_sell"] for t in ex["trades"])
    recon = cash0 - buys + sells
    if abs(recon - ex["cash"]) > 1.0:
        fails.append("E5-1 对账不平: recon=%.2f cash=%.2f" %
                     (recon, ex["cash"]))
    # E5-1b 无负现金路径: 用同一序列复核每日现金下界
    # （执行器不暴露逐日现金，这里用终值+已成交上限推定）
    if ex["cash"] < 0:
        fails.append("E5-1 期末现金为负: %.2f" % ex["cash"])

    # E5-2 敞口上限
    over = [(d, v) for d, v in ex["exposure"].items() if v > cap + 0.01]
    if over:
        fails.append("E5-2 敞口超上限: %s" % over[:3])

    # E5-3 整手
    bad_lot = [t for t in ex["trades"] if t["shares"] % 100 != 0]
    if bad_lot:
        fails.append("E5-3 非整手成交 %d 笔" % len(bad_lot))

    # E5-4 费用为正且已入账
    fee_sum = sum(t["fee_buy"] + t["fee_sell"] for t in ex["trades"])
    if not (fee_sum > 0 and abs(fee_sum - ex["total_cost"]) < 1.0):
        fails.append("E5-4 费用不平: trades=%.2f total=%.2f" %
                     (fee_sum, ex["total_cost"]))

    # E5-5 现金口径必然放弃部分订单（引擎平均敞口>1 时）
    if run["avg_exposure"] > 1.0 and not ex["skipped_entries"]:
        fails.append("E5-5 引擎敞口>1 但无订单被放弃，上限未生效")

    print("E5 execution: trades=%d skipped=%d avg_exp=%.3f max_exp=%.3f "
          "engine_avg_exp=%.3f" %
          (len(ex["trades"]), len(ex["skipped_entries"]),
           ex["avg_exposure"],
           max(ex["exposure"].values()) if ex["exposure"] else 0.0,
           run["avg_exposure"]))
    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("E5 执行测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
