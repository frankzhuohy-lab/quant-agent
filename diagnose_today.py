#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：今天(最新bar)为何没开仓 —— 打印门控各分量与候选得分"""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paper_trade as pt

eng = pt.run_engine(live=True, finalized=True)
stocks, codes, common = eng["stocks"], eng["codes"], eng["common"]
feat, state = eng["feat"], eng["state"]
spec = eng["spec"]
t = eng["data_end"]
print("data_end =", common[t], "| idx =", t)

# --- 门控分量 ---
b = state["breadth"][t]
flr5 = state["flr5"][t]
print("广度 breadth =", b, "(要求>=0.4)")
print("炸板率 flr5 =", flr5, "(要求<=0.3)")
upvar = feat["upvar20"][t] if "upvar20" in feat else None
print("upvar20 =", upvar, "(要求>=0.8)")

gate_ok = spec["filt"](state, t)
print(">>> 市场门控:", "通过" if gate_ok else "未通过")

if not gate_ok:
    sys.exit(0)

# --- 门控通过：看个股得分 ---
scores = spec["score"](feat, codes, t)
ranked = sorted(scores.items(), key=lambda x: -x[1])[:10]
print(">>> Top10 候选:")
for c, s in ranked:
    band = 0.195 if c.startswith(("688", "300", "301")) else 0.095
    px = stocks[c]["close"][t]
    lim = px * (1 + band - 0.005)
    at_limit = px >= lim
    print("   %s %s score=%.4f close=%.3f 涨停线=%.3f %s" % (
        c, pt.NAMES.get(c, c), s, px, lim, "【触涨停-跳过】" if at_limit else ""))

sig = pt.tomorrow_signal(stocks, codes, feat, state, common, spec, t)
print(">>> tomorrow_signal: filter_pass=%s picks=%d" % (
    sig["filter_pass"], len(sig["picks"])))

# --- 昨日信号（今早开盘执行的依据）---
sig_y = pt.tomorrow_signal(stocks, codes, feat, state, common, spec, t - 1)
print(">>> 昨日(%s)信号: filter_pass=%s picks=%d" % (
    common[t - 1], sig_y["filter_pass"], len(sig_y["picks"])))
for p in sig_y["picks"]:
    print("   %s %s score=%.4f" % (p["code"], p["name"], p["score"]))
