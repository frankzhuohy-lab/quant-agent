#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""研究循环 CLI。

用法:
  # 人工提案跑一轮（提案 JSON 见方案文档第 3 章协议）
  python3 -m quant_agent.run_research --proposal proposal.json

  # LLM 提案跑一轮（本地 xiaoji；不可用时自动转人工提示）
  python3 -m quant_agent.run_research --llm

  # 恢复检查（重启后把非终态实验标 ERROR）
  python3 -m quant_agent.run_research --recover

提案 JSON 示例:
{
  "hypothesis": "K=2 降低持仓分散度",
  "change_scope": "K",
  "allowed_patch": {"K": 2},
  "expected_effect": "集中度↑，震荡市损耗↓",
  "failure_conditions": ["OOS回撤劣于基线>2pp"],
  "required_tests": ["rolling_validation", "cost_stress"]
}
"""
from __future__ import print_function
import os, sys, json, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import quant_agent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposal", help="人工提案 JSON 路径")
    ap.add_argument("--llm", action="store_true", help="LLM 提案")
    ap.add_argument("--recover", action="store_true")
    args = ap.parse_args()
    from quant_agent.orchestrator.loop import Orchestrator
    from quant_agent.experiments.store import Store
    orch = Orchestrator()
    if args.recover:
        fixed = orch.recover()
        print("recovered %d interrupted experiments" % len(fixed))
        for eid, st in fixed:
            print("  %s (%s -> ERROR)" % (eid, st))
        orch.close()
        return
    store = orch.store
    snap = store.get_snapshot.__self__  # noqa
    from quant_agent.data import snapshot as snapmod
    from quant_agent.seed import register_snapshot
    m = register_snapshot(store)
    orch.snapshot_manifest = m
    reason = orch.budget_exhausted()
    if reason:
        print("BUDGET STOP:", reason)
        orch.close()
        return
    proposal = None
    if args.proposal:
        proposal = json.load(open(args.proposal))
    elif args.llm:
        from quant_agent.agents.researcher import propose
        hist = _history_summary(store)
        diag = _diagnosis()
        try:
            proposal = propose(diag, hist)
        except Exception as e:
            print("LLM proposal failed (%s)." % e)
            print("请改用 --proposal 提供人工提案；不生成虚构实验。")
            orch.close()
            return
    else:
        ap.error("需要 --proposal 或 --llm")
    print("proposal:", json.dumps(proposal, ensure_ascii=False)[:200])
    out = orch.run_experiment(proposal, snapshot_id=m["snapshot_id"])
    print("=" * 60)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    ch = store.current_champion()
    print("champion:", ch)
    orch.close()


def _history_summary(store):
    rows = store.conn.execute(
        "SELECT e.id, e.hypothesis, d.decision FROM experiments e "
        "LEFT JOIN decisions d ON d.experiment_id=e.id ORDER BY e.id").fetchall()
    return [{"id": r[0], "hypothesis": r[1], "decision": r[2]}
            for r in rows]


def _diagnosis():
    rp = os.path.join(quant_agent.QSYS, "REPORT.md")
    if os.path.exists(rp):
        with open(rp) as f:
            return f.read()[:6000]
    return "REPORT.md missing"


if __name__ == "__main__":
    main()
