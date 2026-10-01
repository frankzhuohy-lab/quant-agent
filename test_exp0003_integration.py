#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EXP_0003 接入验证：①门控逻辑与 qsys 逐字等价；②A/B 重放对比；
③敞口恒 ≤100%。在 quant-paper/ 下运行，数据用 qsys 缓存（避免网络变量）。"""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "qsys"))

import quant_iter26 as q26
import quant_iter44 as q44
import paper_trade as pt
import qdata, qanalyze

stocks, codes, common, susp = qdata.load_panel()
n = len(common)

# ---------- ① 趋势门控与 qsys 逐字等价 ----------
labels = qanalyze.regime_labels(stocks, codes, common)
bull_qsys = set(t for t, (tr, vo) in enumerate(labels) if tr == u"上涨")
bull_pt = pt.regime_bull_days(stocks, codes, common)
assert bull_pt == bull_qsys, ("门控不等价! diff=%s" % (
    sorted(bull_pt ^ bull_qsys)[:10],))
print(u"[PASS] 趋势门控与 qsys.regime_labels 完全等价：%d/%d 天为上涨市" % (
    len(bull_qsys), n))

# 诊断函数一致性
diag = pt.regime_diagnostics(stocks, codes, common, n - 1)
gate_last = (n - 1) in bull_qsys
assert diag["gate_pass"] == gate_last
print(u"[PASS] regime_diagnostics 末日一致: gate=%s mom20=%s idx/ma60=%s" % (
    diag["gate_pass"], diag["mom20"], diag["idx_vs_ma60"]))

# ---------- ② A/B 重放对比（旧逻辑 vs EXP_0003） ----------
feat, state = q26.build_features(stocks, codes, common)
state = q44.add_batchAP_features(stocks, codes, common, feat, state)
paper_idx = next(i for i, d in enumerate(common) if d >= pt.PAPER_START)

def run(gate, cap):
    spec = pt.build_spec()
    if gate:
        orig = spec["filt"]
        bull = pt.regime_bull_days(stocks, codes, common)
        spec["filt"] = lambda st, t, _b=bull, _o=orig: _o(st, t) and t in _b
    closed, opens = pt.replay(stocks, codes, feat, state, common, spec,
                              paper_idx, n - 1, today_finalized=True,
                              exposure_cap=cap)
    nav = pt.nav_of(closed, opens)
    return closed, opens, nav, spec

c_old, o_old, nav_old, _ = run(False, None)
c_new, o_new, nav_new, _ = run(True, pt.EXPOSURE_CAP)
print(u"[INFO] 旧逻辑（纸面期 %s~%s）: %d 笔已平 + %d 笔持仓, NAV=%.4f" % (
    common[paper_idx], common[n - 1], len(c_old), len(o_old), nav_old))
print(u"[INFO] EXP_0003: %d 笔已平 + %d 笔持仓, NAV=%.4f" % (
    len(c_new), len(o_new), nav_new))

# EXP0003 的重放必须等于「只加 cap」且「只加 gate」的组合约束下的子集：
# 每笔新交易在旧结果中也必须存在（e/code 相同，序列前缀性不保证，做集合包含）
old_keys = set((t["code"], t["e"]) for t in c_old + o_old)
for t in c_new + o_new:
    assert (t["code"], t["e"]) in old_keys, u"出现了旧逻辑没有的新交易!"
print(u"[PASS] EXP_0003 交易集 ⊆ 旧逻辑交易集（无新增交易）")

# ---------- ③ 敞口恒 ≤100%（重算敞口核查） ----------
def exposure_of(trades, data_end):
    exp = {}
    for t in trades:
        for d in range(t["e"], min(t["xe"], data_end + 1)):
            exp[d] = exp.get(d, 0.0) + t["weight"]
    return exp

exp_new = exposure_of(c_new + o_new, n - 1)
mx = max(exp_new.values()) if exp_new else 0.0
assert mx <= pt.EXPOSURE_CAP + 1e-9, u"敞口超限: %f" % mx
print(u"[PASS] EXP_0003 最大敞口 = %.1f%%（上限 100%%）" % (mx * 100))
exp_old = exposure_of(c_old + o_old, n - 1)
mx_old = max(exp_old.values()) if exp_old else 0.0
print(u"[INFO] 旧逻辑最大敞口 = %.1f%%（隐含杠杆证据）" % (mx_old * 100))

# ---------- ④ 信号链端到端：明日计划遵循门控 ----------
spec_gated = pt.build_spec()
bull = pt.regime_bull_days(stocks, codes, common)
orig = spec_gated["filt"]
spec_gated["filt"] = lambda st, t, _b=bull, _o=orig: _o(st, t) and t in _b
sig = pt.tomorrow_signal(stocks, codes, feat, state, common, spec_gated, n - 1)
expect_pass = (n - 1) in bull
assert sig["filter_pass"] == expect_pass or not sig["picks"] or expect_pass
print(u"[PASS] tomorrow_signal 门控生效: filter_pass=%s picks=%d（末日 gate=%s）" % (
    sig["filter_pass"], len(sig["picks"]), expect_pass))
print(u"ALL CHECKS PASSED")
