#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次六消融重跑（评审第 4 轮修正版）：2×2 独立开关对照。

batch6.py 的指标来自 runner.run 摘要（费用/跳过数缺失），本脚本直接
调 run_window 取全量产物: 净值指标、交易数、总费用(元)、跳过明细、
涨停复核触发数——满足"分别开关两项修改，报告成交数、费用、被拒订单
及净值差异"的要求。
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import quant_agent  # noqa: E402
from quant_agent import seed  # noqa: E402
from quant_agent.backtest.runner import Context, run_window  # noqa: E402
import qengine  # noqa: E402

ABLATIONS = [
    ("A_费0_涨0", 0.0, 0.0, False),
    ("B_费1_涨0", 5.0, 5.0, False),
    ("C_费0_涨1", 0.0, 0.0, True),
    ("D_费1_涨1", 5.0, 5.0, True),
]


def main():
    const = json.load(open(quant_agent.CONFIG))
    assert const["version"] == "constitution_006"
    ctx = Context(constitution=const)
    spec_m, kw, _canon = seed.materialize(
        seed.normalize({}, const), ctx.stocks, ctx.codes, ctx.common,
        universe=ctx.universe)

    table = []
    for name, mf_b, mf_s, lim in ABLATIONS:
        er = copy.deepcopy(const["execution_rules"])
        er["min_fee_buy"] = mf_b
        er["min_fee_sell"] = mf_s
        er["limit_check_at_fill"] = lim
        for label in ctx.dev_labels():
            e0, e1, w2 = ctx.windows[label]
            out = run_window(ctx, spec_m, kw, e0, e1, w2, er)
            m = qengine.metrics(out)
            sk = out.get("skipped_entries", [])
            n_lim = sum(1 for s in sk if s.get("why") == "limit_up_at_fill")
            n_cash = sum(1 for s in sk if s.get("why") == "cash_or_cap")
            table.append({
                "cfg": name, "window": label,
                "ann": m["annual_return"], "sharpe": m["sharpe"],
                "mdd": m["max_drawdown"], "trades": m["n_trades"],
                "cost_yuan": out.get("total_cost", 0.0),
                "skipped": len(sk), "lim": n_lim, "cash_skip": n_cash,
            })
            print("%s %s: ann=%.3f sharpe=%.3f mdd=%.3f trades=%d "
                  "cost=%.0f元 skipped=%d(涨停%d/资金%d)" %
                  (name, label, m["annual_return"], m["sharpe"],
                   m["max_drawdown"], m["n_trades"],
                   out.get("total_cost", 0.0), len(sk), n_lim, n_cash))

    lines = ["# 批次六消融：最低佣金 × 涨停复核 2×2（评审第 4 轮修正版）",
             "",
             "快照 v5 `fe312f298f159972`；constitution_006（引擎末段双计已修复）。",
             "",
             "| 配置 | 窗口 | 年化 | Sharpe | 回撤 | 交易数 | 总费用(元) | "
             "跳过（涨停/资金） |", "|---|---|---|---|---|---|---|---|"]
    for r in table:
        lines.append("| %s | %s | %.2f%% | %.3f | %.2f%% | %d | %.0f | %d(%d/%d) |" %
                     (r["cfg"], r["window"], r["ann"] * 100, r["sharpe"],
                      r["mdd"] * 100, r["trades"], r["cost_yuan"],
                      r["skipped"], r["lim"], r["cash_skip"]))
    lines += ["",
              "## 归因读法",
              "",
              "- B−A = 最低佣金净效应；C−A = 涨停复核净效应；D−(B+C)+A = 交互项。",
              "- 样本中涨停复核触发次数见上表（意图生成器已用同一开盘/前收"
              "规则事前过滤，执行层复核为防御性重检）。",
              "- IS 年化自 baseline_003 的 -5.0% 变动主因是引擎勘误"
              "（末段双计修复），非费用或涨停复核——batch6 四配置均在"
              "勘误后引擎上运行。"]
    out_fp = os.path.join(quant_agent.VAR, "batch6_ablation.md")
    open(out_fp, "w").write("\n".join(lines))
    print("OK ->", out_fp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
