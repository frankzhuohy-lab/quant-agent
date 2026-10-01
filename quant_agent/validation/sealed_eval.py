#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封存测试独立评估（方案文档第 4/5 章）。

纪律:
  - 只在候选版本/门槛/预算锁定后由维护者显式调用；不是研究循环的一部分。
  - 结果写入 sealed_evaluations 表，不回传研究 Agent；查看后若改策略，
    该区间即降级为开发数据（constitution 注明）。
  - 封存窗口短（~9个月），WF 不适用；verdict 复用 Gate 的开发验证+
    成本压力门槛（WF 项自动跳过）。

用法:
  python3 -m quant_agent.validation.sealed_eval --challenger exp_20260930_202923
  python3 -m quant_agent.validation.sealed_eval --list
"""
from __future__ import print_function
import os, sys, json, argparse, getpass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import quant_agent
from quant_agent.experiments.store import Store
from quant_agent.orchestrator.loop import load_constitution
from quant_agent.backtest.runner import Context, BacktestRunner
from quant_agent.validation.evaluate import validate
from quant_agent.gate.rules import Gate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--challenger", help="实验 id（默认: 当前 champion 对 parent）")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    const = load_constitution()
    store = Store(os.path.join(quant_agent.VAR, "qa_store.sqlite3"))
    if args.list:
        for e in store.get_sealed_evals():
            print(e["id"], e["target"], "vs", e["challenger"],
                  e["verdict"], e["decided_at"])
        store.close()
        return
    sh = const.get("sealed_holdout")
    if not sh or not sh.get("locked"):
        print("sealed holdout 未锁定，拒绝评估")
        sys.exit(1)
    ctx = Context(constitution=const)
    if "SEALED" not in ctx.windows:
        print("当前数据无封存区间")
        sys.exit(1)
    w0, w1 = ctx.window_dates("SEALED")
    # 全系统唯一显式放行点: 独立封存评估允许计算封存窗口
    const = json.loads(json.dumps(const))
    const["execution_rules"]["_allow_sealed"] = True
    runner = BacktestRunner(ctx, const)

    if args.challenger:
        exp = store.get_experiment(args.challenger)
        if not exp or not exp.get("spec_json"):
            print("实验不存在或无规格: %s" % args.challenger)
            sys.exit(1)
        cand_spec = dict(exp["spec_json"])
        parent = exp["parent_version"]
    else:
        champ = store.current_champion()
        if not champ:
            print("无 champion")
            sys.exit(1)
        row = store.conn.execute(
            "SELECT spec_json FROM strategies WHERE version=?",
            (champ["version"],)).fetchone()
        cand_spec = json.loads(row[0]) if row and row[0] else {}
        parent = champ.get("previous_version") or "baseline_001"
        args.challenger = "champion:%s" % champ["version"]
    prow = store.conn.execute(
        "SELECT spec_json FROM strategies WHERE version=?",
        (parent,)).fetchone()
    base_spec = json.loads(prow[0]) if prow and prow[0] else {}

    out_dir = os.path.join(quant_agent.VAR, "sealed",
                           "%s_vs_%s" % (args.challenger, parent))
    cand = runner.run(cand_spec, os.path.join(out_dir, "cand"),
                      windows=("SEALED",))
    base = runner.run(base_spec, os.path.join(out_dir, "base"),
                      windows=("SEALED",))
    ev = {"windows": {"SEALED": {"candidate": cand["SEALED"]["metrics"],
                                 "baseline": base["SEALED"]["metrics"]}},
          "stress": {}, "dev_label": "SEALED", "consistent": True}
    st = runner.run(cand_spec, os.path.join(out_dir, "stress_x2"),
                    windows=("SEALED",),
                    cost_override=(const["execution_rules"]["cost_buy"] * 2,
                                   const["execution_rules"]["cost_sell"] * 2))
    ev["stress"]["cost_x2_SEALED"] = st["SEALED"]["metrics"]
    gate = Gate(const)
    decision = gate.evaluate("sealed_%s" % args.challenger, ev)
    verdict = ("SEALED_PASS" if decision["decision"] == "PROMOTE"
               else "SEALED_FAIL")
    store.add_sealed_eval(parent, args.challenger, w0, w1,
                          cand["SEALED"]["metrics"],
                          base["SEALED"]["metrics"], verdict,
                          decision["reasons"], getpass.getuser())
    cm = cand["SEALED"]["metrics"]
    bm = base["SEALED"]["metrics"]
    print("SEALED window: %s .. %s" % (w0, w1))
    print("challenger %s: ann=%.1f%% sharpe=%.2f mdd=%.1f%% trades=%d" % (
        args.challenger, cm["annual_return"] * 100, cm["sharpe"],
        cm["max_drawdown"] * 100, cm["n_trades"]))
    print("baseline   %s: ann=%.1f%% sharpe=%.2f mdd=%.1f%% trades=%d" % (
        parent, bm["annual_return"] * 100, bm["sharpe"],
        bm["max_drawdown"] * 100, bm["n_trades"]))
    print("VERDICT:", verdict)
    for r in decision["reasons"]:
        print("  -", r)
    store.close()


if __name__ == "__main__":
    main()
