#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次六（评审第 4 轮）：引擎勘误后的重建与消融。

背景: S8 对账发现引擎出场日末段收益双计（daily 累乘 ≠ 交易 gross
之和），qengine 已修复（持仓段循环 d+1 < xd）。本批次:

  1. 消融实验 2×2（最低佣金开关 × 成交时点涨停复核开关），解释
     baseline 数字变化的归因（评审第 4 轮要求，不写"量级符合预期"）；
  2. 生产配置（双开）连跑两遍复现 → baseline_005 接任 champion；
     baseline_004 标记 SUPERSEDED_ENGINE_FIX；
  3. 修正事件: 引擎勘误使全部历史指标绝对水平作废（比较方向仍有效）；
  4. 输出 var/batch6_ablation.md。

EXP_0003 重跑（constitution_006）:
  python3 -m quant_agent.run_research --proposal \\
      quant_agent/var/proposals/exp_batch3_exp0003.json
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import quant_agent  # noqa: E402
from quant_agent import seed  # noqa: E402
from quant_agent.backtest.runner import Context, BacktestRunner  # noqa: E402
from quant_agent.experiments.store import Store  # noqa: E402

ABLATIONS = [
    ("A_fee0_limit0", 0.0, 0.0, False),
    ("B_fee1_limit0", 5.0, 5.0, False),
    ("C_fee0_limit1", 0.0, 0.0, True),
    ("D_fee1_limit1", 5.0, 5.0, True),
]


def main():
    const = json.load(open(quant_agent.CONFIG))
    assert const["version"] == "constitution_006", \
        "本脚本要求 constitution_006（引擎勘误后）"
    store = Store(os.path.join(quant_agent.VAR, "qa_store.sqlite3"))
    snap_id = "fe312f298f159972"   # 数据未变（v5 组合快照），仅引擎修复

    ctx = Context(constitution=const)
    canonical = seed.materialize(
        seed.normalize({}, const), ctx.stocks, ctx.codes, ctx.common,
        universe=ctx.universe)[2]

    rows = {}
    for name, mf_b, mf_s, lim in ABLATIONS:
        c2 = copy.deepcopy(const)
        c2["execution_rules"]["min_fee_buy"] = mf_b
        c2["execution_rules"]["min_fee_sell"] = mf_s
        c2["execution_rules"]["limit_check_at_fill"] = lim
        runner = BacktestRunner(ctx, c2)
        out = runner.run(canonical, os.path.join(
            quant_agent.VAR, "artifacts", "batch6_" + name))
        rows[name] = out
        print("%s:" % name)
        for label in ctx.dev_labels():
            m = out[label]["metrics"]
            sk = out[label].get("skipped_entries", [])
            n_lim = sum(1 for s in sk if s.get("why") == "limit_up_at_fill")
            print("  %s ann=%.3f sharpe=%.3f mdd=%.3f trades=%d "
                  "cost=%.0f skipped=%d(limit=%d)" %
                  (label, m["annual_return"], m["sharpe"], m["max_drawdown"],
                   m["n_trades"], out[label].get("total_cost", 0) * 1e6
                   if out[label].get("total_cost", 0) < 1 else
                   out[label].get("total_cost", 0),
                   len(sk), n_lim))

    # 复现验收 + 登记（D = 生产配置）
    a = rows["D_fee1_limit1"]
    c2 = copy.deepcopy(const)
    runner = BacktestRunner(ctx, c2)
    b = runner.run(canonical, os.path.join(quant_agent.VAR, "artifacts",
                                           "batch6_repro2"))
    for label in ctx.dev_labels():
        assert json.dumps(a[label]["metrics"], sort_keys=True) == \
            json.dumps(b[label]["metrics"], sort_keys=True), \
            "%s 两遍不一致" % label
    print("复现验收通过（勘误后引擎两遍一致）")

    seed.register_baseline(store, ctx, const, snap_id,
                           version="baseline_005", promote=True)
    store.conn.execute(
        "UPDATE strategies SET status=? WHERE version=?",
        ("SUPERSEDED_ENGINE_FIX", "baseline_004"))
    store.conn.commit()
    ch = store.conn.execute(
        "SELECT version FROM champion_history ORDER BY promoted_at DESC "
        "LIMIT 1").fetchone()
    print("champion:", ch[0])

    store.add_correction(
        "ENGINE_R443", "出场日末段收益双计（历史全部实验指标）",
        "qengine 持仓段循环条件 d+1<=xd 致末段重复计入 daily；修复为 "
        "d+1<xd 后 daily 累乘与交易 gross 一致（S8d 对账验证）",
        "评审第 4 轮 S8 全链路对账发现",
        "历史实验绝对收益水平全部作废（高估约每笔一段隔夜）；"
        "同一引擎版本内候选 vs 基线的相对比较方向仍有效；"
        "champion 已由 baseline_005 重建")
    print("修正事件入库: 引擎勘误")

    # ---- 消融报告 ----
    lines = ["# 批次六：引擎勘误与 2×2 消融（评审第 4 轮）", "",
             "快照 v5 `%s`；constitution_006；引擎末段双计已修复。" % snap_id,
             "",
             "| 配置 | 窗口 | 年化 | Sharpe | 回撤 | 交易数 | 总费用 | "
             "跳过（涨停复核） |", "|---|---|---|---|---|---|---|---|"]
    for name, _a, _b2, _c in ABLATIONS:
        for label in ctx.dev_labels():
            m = rows[name][label]["metrics"]
            sk = rows[name][label].get("skipped_entries", [])
            n_lim = sum(1 for s in sk if s.get("why") == "limit_up_at_fill")
            tc = rows[name][label].get("total_cost", 0.0)
            if tc > 1:   # 权重口径费用是小数；现金口径是元
                tc_s = "%.0f元" % tc
            else:
                tc_s = "%.4f(权重口径)" % tc
            lines.append("| %s | %s | %.2f%% | %.3f | %.2f%% | %d | %s | %d |" %
                         (name, label, m["annual_return"] * 100, m["sharpe"],
                          m["max_drawdown"] * 100, m["n_trades"], tc_s,
                          n_lim))
    lines += ["",
              "归因读法: B-A = 最低佣金净效应; C-A = 涨停复核净效应; "
              "D-(B+C)+A = 交互项。",
              "引擎勘误（末段不再双计）同时作用于四配置，"
              "不改变四者之间的相对比较。"]
    out = os.path.join(quant_agent.VAR, "batch6_ablation.md")
    open(out, "w").write("\n".join(lines))
    print("OK ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
