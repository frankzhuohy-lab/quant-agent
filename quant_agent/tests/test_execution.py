#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""执行测试（方案文档第 7 章）：A股真实性规则的小型可手算场景验证。

合成单股票 20 日面板，人工构造每种限制场景，断言引擎行为:
  E1 T日信号 → T+1 开盘成交（成交日=信号日+1）
  E2 开盘近似涨停 → 跳过买入
  E3 停牌日 → 禁止买入
  E4 跌停开盘 → 卖出顺延（reason+deferred）
  E5 敞口上限 → 现金不足跳过新入场
  E6 费用精确：net = gross - cost_buy - cost_sell
  E7 无当日回转（hold_days >= 1，A股 T+1）
"""
from __future__ import print_function
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "..", "qsys"))
from qengine import run_r443  # noqa: E402

N = 20
COMMON = ["2026-01-%02d" % i for i in range(1, N + 1)]


def panel(open_px, close_px, vol=None):
    vol = vol or [1000.0] * N
    rec = {"name": "X", "code": "AAA", "dates": list(COMMON),
           "open": list(open_px), "high": list(open_px),
           "low": list(open_px), "close": list(close_px), "vol": list(vol)}
    return {"AAA": rec}, ["AAA"], COMMON, {"AAA": set()}


def flat(px=10.0):
    return [px] * N


def spec_for(score_t):
    return {"score": lambda feat, codes, t: {"AAA": 1.0},
            "filt": lambda state, t: t == score_t,
            "K": 1, "allow_fewer": True, "weight_mode": "equal",
            "params": {"keltner_mult": 1.5, "max_hold": 3}}


def feat_none():
    return {"AAA": {"ma20": [None] * N, "atr20": [None] * N}}


def run(scenario_open, scenario_close, score_t=5, e=(6, 6, 19), **kw):
    stocks, codes, common, susp = panel(scenario_open, scenario_close)
    if "susp_days" in kw:
        susp = {"AAA": set(COMMON[i] for i in kw.pop("susp_days"))}
    return run_r443(stocks, codes, feat_none(), {}, common, susp,
                    e[0], e[1], e[2], spec_for(score_t),
                    ld_block=True, **kw)


def main():
    # E1/E6/E7: 全平 10 元，第6日开盘买入，time出场(max_hold=3)→计划第9日
    o, c = flat(), flat()
    r = run(o, c)
    assert len(r["trades"]) == 1, r["trades"]
    t = r["trades"][0]
    assert t["e"] == 6 and t["entry_date"] == COMMON[6], t
    assert t["xe"] == 9 and t["reason"] == "time", t
    assert t["hold_days"] == 3, t
    assert abs(t["net"] - (0.0 - 0.002 - 0.002)) < 1e-12, t
    print("PASS E1 T+1 exec / E6 costs / E7 no same-day roundtrip")

    # E2: 第6日开盘 11.0 >= 10*1.09 → 涨停跳过
    o = flat(); o[6] = 11.0
    r = run(o, flat())
    assert len(r["trades"]) == 0
    print("PASS E2 limit-up open blocked")

    # E3: 第6日停牌（vol=0 且 susp 标记）
    vol = [1000.0] * N; vol[6] = 0.0
    stocks, codes, common, susp = panel(flat(), flat(), vol)
    susp = {"AAA": {COMMON[6]}}
    r = run_r443(stocks, codes, feat_none(), {}, common, susp,
                 6, 6, 19, spec_for(5), ld_block=True)
    assert len(r["trades"]) == 0
    print("PASS E3 suspension blocked")

    # E4: 计划出场日(第9日)开盘跌停 → 顺延至第10日
    o, c = flat(), flat()
    o[9] = 9.00   # <= close[8]=10 的 90.5% → 跌停
    r = run(o, c)
    t = r["trades"][0]
    assert t["xe"] == 10 and t["deferred_days"] == 1, t
    assert t["reason"] == "time+deferred", t
    print("PASS E4 limit-down sell deferred")

    # E5: 敞口上限 0.1 < w=0.2 → 跳过
    r = run(flat(), flat(), exposure_cap=0.1)
    assert len(r["trades"]) == 0
    print("PASS E5 exposure cap blocks entry")

    # E4b: 跌停顺延到窗口末尾 → window_cut 标记（不伪造成交）
    o, c = flat(), flat()
    o[9] = 9.0; o[10] = 9.0; o[11] = 9.0
    r = run(o, c, e=(6, 6, 11))
    t = r["trades"][0]
    assert t["reason"] == "time+window_cut" and t["xe"] == 11, t
    print("PASS E4b window-cut marked (no fabricated fill)")
    print("EXECUTION TESTS: ALL PASS")


if __name__ == "__main__":
    main()
