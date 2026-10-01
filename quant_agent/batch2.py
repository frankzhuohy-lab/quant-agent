#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次二登记 + 幸存者偏差量化。

  1. 快照 v2: ucache 全量哈希 + 宇宙日历哈希 + 规则哈希。
  2. baseline_002: 同 R443 参数 + PIT 宇宙（REFERENCE，不抢 champion 指针）。
  3. 幸存者偏差量化: 同一窗口/同一规格，定型22池 vs PIT宇宙，IS/DEV 对比。

用法: python3 -m quant_agent.batch2 [--skip-verify]
"""
from __future__ import print_function
import os, sys, json, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import quant_agent
from quant_agent.experiments.store import Store
from quant_agent.orchestrator.loop import load_constitution
from quant_agent.backtest.runner import Context, BacktestRunner
from quant_agent.strategies.adapter import normalize, spec_hash, code_hash
from quant_agent.seed import register_baseline
from quant_agent.data import snapshot as snap
from quant_agent.data import universe as U


def register_snapshot_v2(store):
    m = snap.build_manifest(U.UCACHE)
    umeta = json.load(open(os.path.join(U.UINDEX, "universe_meta.json")))
    m["universe_rule"] = umeta["rule_id"]
    m["universe_rule_hash"] = umeta["rule_hash"]
    m["universe_calendar_hash"] = umeta["calendar_hash"]
    m["universe_avg_members"] = umeta["avg_members"]
    blob = json.dumps(m, sort_keys=True, ensure_ascii=False)
    import hashlib
    m["snapshot_id"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    store.register_snapshot(m["snapshot_id"], m)
    return m


def pct(x):
    return "%+.2f%%" % (x * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-verify", action="store_true")
    args = ap.parse_args()
    const = load_constitution()
    store = Store(os.path.join(quant_agent.VAR, "qa_store.sqlite3"))
    snap2 = register_snapshot_v2(store)
    print("snapshot_v2=%s files=%d universe_avg=%.0f" % (
        snap2["snapshot_id"], len(snap2["universe_files"]),
        snap2["universe_avg_members"]))

    # PIT 宇宙上下文（板块特征冻结22只）
    ctx_u = Context(constitution=const, use_universe=True)
    # 定型池上下文（同窗口定义，便于同口径对比）
    ctx_c = Context(constitution=const, use_universe=False)
    print("ctx_u: %d codes %s..%s windows=%s" % (
        len(ctx_u.codes), ctx_u.common[0], ctx_u.common[-1],
        {k: ctx_u.window_dates(k) for k in ctx_u.windows}))
    sh2, ch2 = register_baseline(store, ctx_u, const, snap2["snapshot_id"],
                                 version="baseline_002", promote=False)
    print("baseline_002 spec_hash=%s code_hash=%s" % (sh2, ch2))

    ru = BacktestRunner(ctx_u, const)
    rc = BacktestRunner(ctx_c, const)
    base = normalize({}, const)
    out_md = ["# 幸存者偏差量化（batch_002）", "",
              "同一冻结规格（R443 基线参数）、同一窗口、同一成本口径；",
              "唯一差异 = 可交易宇宙（定型22池 vs PIT 规则宇宙）。", ""]
    rows = {}
    for label in ctx_u.dev_labels():
        mu = ru.run(base, os.path.join(quant_agent.VAR, "batch2_u", label),
                    windows=(label,))[label]["metrics"]
        mc = rc.run(base, os.path.join(quant_agent.VAR, "batch2_c", label),
                    windows=(label,))[label]["metrics"]
        rows[label] = (mu, mc)
    hdr = "| 窗口 | 宇宙 | 年化 | Sharpe | 最大回撤 | 笔数 | 平均敞口 |"
    out_md += [hdr, "|---|---|---|---|---|---|---|"]
    for label, (mu, mc) in rows.items():
        out_md.append("| %s | PIT宇宙 | %s | %.2f | %s | %d | %.0f%% |" % (
            label, pct(mu["annual_return"]), mu["sharpe"],
            pct(mu["max_drawdown"]), mu["n_trades"],
            mu["avg_exposure"] * 100))
        out_md.append("| %s | 定型22池 | %s | %.2f | %s | %d | %.0f%% |" % (
            label, pct(mc["annual_return"]), mc["sharpe"],
            pct(mc["max_drawdown"]), mc["n_trades"],
            mc["avg_exposure"] * 100))
    out_md += ["", "## 解读", "",
               "- 差异 = 幸存者偏差+宇宙宽度效应的合计，不可分拆；",
               "- PIT 宇宙版是唯一可作未来预期参考的口径；",
               "- 规则与数据局限见 config/universe_rule.json 注记。", ""]
    fp = os.path.join(quant_agent.VAR, "batch2_survivorship.md")
    with open(fp, "w") as f:
        f.write("\n".join(out_md))
    print("\n".join(out_md))
    if not args.skip_verify:
        a = ru.run(base, os.path.join(quant_agent.VAR, "b2v_a"))
        b = ru.run(base, os.path.join(quant_agent.VAR, "b2v_b"))
        ok = all(a[l]["metrics"] == b[l]["metrics"] for l in ctx_u.dev_labels())
        print("REPRODUCIBILITY(batch2):", "PASS" if ok else "FAIL")
    store.close()


if __name__ == "__main__":
    main()
