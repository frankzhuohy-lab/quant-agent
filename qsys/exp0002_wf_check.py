#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EXP_0002 附加验证：H2 regime-gate vs baseline 的 Walk-Forward 折稳定性对比。"""
import sys, json
sys.path.insert(0, '/Volumes/项目空间/projects/quant-paper/qsys')
from qdata import load_panel, build_all, r443_spec
from qengine import run_r443
from qstress import REAL_COST, m_of
import qanalyze

stocks, codes, common, susp = load_panel()
feat, state = build_all(stocks, codes, common)
n = len(common)
WARMUP = 80
labels = qanalyze.regime_labels(stocks, codes, common)
bull = set(t for t, (tr, vo) in enumerate(labels) if tr == "上涨")

spec_base = r443_spec()
spec_h2 = r443_spec()
orig = spec_h2["filt"]
spec_h2["filt"] = lambda state, t, _o=orig: _o(state, t) and t in bull


def wf(spec):
    folds = []
    start = WARMUP
    while start + 160 + 60 <= n - 1:
        oos_s, oos_e = start + 160, start + 219
        r = run_r443(stocks, codes, feat, state, common, susp,
                     oos_s, oos_e - 5, oos_e, spec,
                     cost_buy=0.002, cost_sell=0.0025, ld_block=True)
        m = m_of(r)
        folds.append({"window": [common[oos_s], common[oos_e]],
                      "n": m["n_trades"],
                      "ann": m["annual_return"], "sharpe": m["sharpe"],
                      "dd": m["max_drawdown"]})
        start += 60
    return folds


out = {"baseline": wf(spec_base), "h2": wf(spec_h2)}
for tag, folds in out.items():
    negs = sum(1 for f in folds if f["ann"] < 0)
    print(tag, "负收益折:", negs, "/", len(folds))
    for f in folds:
        print("  ", f["window"][0][:7], "n=%d ann=%.2f sharpe=%.2f dd=%.3f"
              % (f["n"], f["ann"], f["sharpe"], f["dd"]))
with open("/Volumes/项目空间/projects/quant-paper/qsys/experiments/EXP_0002_wf.json", "w") as fp:
    json.dump(out, fp, ensure_ascii=False, indent=1)
