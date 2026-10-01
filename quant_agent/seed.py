#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seed：登记数据快照、baseline_001、历史实验 EXP_0001~0003（阶段一验收）。

用法: python3 -m quant_agent.seed [--verify-twice]
  --verify-twice: 基线复现验收 —— IS+OOS 连跑两遍，指标必须完全一致。
"""
from __future__ import print_function
import os, sys, json, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import quant_agent
from quant_agent.data import snapshot as snap
from quant_agent.strategies.adapter import normalize, materialize, \
    spec_hash, code_hash
from quant_agent.backtest.runner import Context, BacktestRunner
from quant_agent.experiments.store import Store


def register_snapshot(store):
    cache = os.path.join(quant_agent.QSYS, "cache")
    m = snap.build_manifest(cache)
    store.register_snapshot(m["snapshot_id"], m)
    return m


def register_baseline(store, ctx, constitution, snapshot_id,
                      version="baseline_001", promote=True):
    norm = normalize({}, constitution)
    canonical = materialize(norm, ctx.stocks, ctx.codes, ctx.common)[2]
    sh = spec_hash(canonical)
    ch = code_hash()
    now = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    d = store.conn.execute(
        "SELECT 1 FROM strategies WHERE version=?", (version,)).fetchone()
    if not d:
        store.conn.execute(
            "INSERT INTO strategies(version,parent,spec_hash,code_hash,"
            "status,created_at,spec_json) VALUES(?,?,?,?,?,?,?)",
            (version, "baseline_001" if version != "baseline_001" else None,
             sh, ch, "CHAMPION_SEED" if promote else "REFERENCE", now,
             json.dumps(canonical, ensure_ascii=False)))
        if promote:
            store.conn.execute(
                "INSERT OR IGNORE INTO champion_history VALUES(?,?,?,?)",
                (version, None, now, None))
        store.conn.commit()
    return sh, ch


HISTORY = {
    "EXP_0001": {"hyp": "H1_exposure_cap: 组合敞口上限100%（消除隐含杠杆）",
                 "patch": {"exposure_cap": 1.0}, "decision": "REJECT",
                 "reasons": ["OOS年化腰斩(220→83%)而回撤不降(-23.7→-23.6%)，"
                             "去杠杆收益-风险比劣化；关键洞察：原回测隐含~200%敞口"]},
    "EXP_0002": {"hyp": "H2_regime_gate: 非上涨趋势不开新仓",
                 "patch": {"regime_gate": True}, "decision": "ACCEPT",
                 "reasons": ["OOS年化不降/Sharpe升/回撤-23.7%→-12.6%腰斩；"
                             "趋势判定全因果无未来函数"]},
    "EXP_0003": {"hyp": "H4_h1h2_combo: 趋势门控+敞口上限（部署形态）",
                 "patch": {"regime_gate": True, "exposure_cap": 1.0},
                 "decision": "ACCEPT",
                 "reasons": ["唯一符合真实资金约束(敞口<=100%)的版本；"
                             "成本x2压力下Sharpe 1.74仍稳健"]},
}


def register_history(store, constitution, snapshot_id):
    exp_dir = os.path.join(quant_agent.QSYS, "experiments")
    ts = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    for eid, meta in HISTORY.items():
        if store.get_experiment(eid):
            continue
        fp = os.path.join(exp_dir, eid + ".json")
        mets = {"IS": None, "OOS": None}
        if os.path.exists(fp):
            d = json.load(open(fp))
            for w in ("IS", "OOS"):
                if isinstance(d.get("variant"), dict) and \
                        isinstance(d["variant"].get(w), dict):
                    mets[w] = {k: d["variant"][w].get(k) for k in
                               ("annual_return", "sharpe", "max_drawdown",
                                "n_trades") if k in d["variant"][w]}
        store.create_experiment(eid, meta["hyp"], "baseline_001",
                                snapshot_id, "pre_qa_manual", meta["patch"],
                                trial_index=int(eid.split("_")[1]))
        for st in ("SPEC_VALIDATED", "AUDITED", "BACKTESTED", "VALIDATED",
                   "CRITIQUED"):
            store.transition(eid, st)
        store.add_decision(eid, "manual_review_2026_09_30",
                           meta["decision"], meta["reasons"],
                           reviewer="human")
        store.transition(eid, "DECIDED")
        for w, mm in mets.items():
            if mm:
                store.add_run("%s_%s" % (eid, w), eid, w, None, None,
                              {"buy": 0.002, "sell": 0.0025}, mm, None)


def verify_twice(ctx, constitution, snapshot_id):
    """阶段一验收: 同输入重跑两遍，全部指标字节级一致。"""
    runner = BacktestRunner(ctx, constitution)
    import tempfile
    a = runner.run(normalize({}, constitution),
                   os.path.join(quant_agent.VAR, "verify_a"))
    b = runner.run(normalize({}, constitution),
                   os.path.join(quant_agent.VAR, "verify_b"))
    ok = True
    for label in ("IS", "OOS"):
        ma, mb = a[label]["metrics"], b[label]["metrics"]
        if ma != mb:
            ok = False
            print("MISMATCH %s:" % label)
            for k in sorted(set(ma) | set(mb)):
                if ma.get(k) != mb.get(k):
                    print("  %s: %r != %r" % (k, ma.get(k), mb.get(k)))
    return ok, a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-twice", action="store_true")
    args = ap.parse_args()
    from quant_agent.orchestrator.loop import load_constitution, config_hash
    constitution = load_constitution()
    store = Store(os.path.join(quant_agent.VAR, "qa_store.sqlite3"))
    ctx = Context()
    m = register_snapshot(store)
    print("snapshot_id=%s files=%d range=%s..%s" % (
        m["snapshot_id"], len(m["universe_files"]),
        min(e["first_date"] for e in m["universe_files"] if e["first_date"]),
        max(e["last_date"] for e in m["universe_files"] if e["last_date"])))
    sh, ch = register_baseline(store, ctx, constitution, m["snapshot_id"])
    print("baseline_001 spec_hash=%s code_hash=%s" % (sh, ch))
    register_history(store, constitution, m["snapshot_id"])
    print("history: EXP_0001(REJECT) EXP_0002(ACCEPT) EXP_0003/ACCEPT)")
    if args.verify_twice:
        ok, a = verify_twice(ctx, constitution, m["snapshot_id"])
        for label in ("IS", "OOS"):
            mm = a[label]["metrics"]
            print("%s: ann=%.2f%% sharpe=%.2f mdd=%.2f%% trades=%d" % (
                label, mm["annual_return"] * 100, mm["sharpe"],
                mm["max_drawdown"] * 100, mm["n_trades"]))
        print("REPRODUCIBILITY:", "PASS" if ok else "FAIL")
    store.close()


if __name__ == "__main__":
    main()
