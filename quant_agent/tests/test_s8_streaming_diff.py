#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S8d/S8e 扩展（评审第 4 轮）：预扫描引擎 vs 独立逐日流式参考实现。

S8d: 多截断日期下，两者的交易、日收益、敞口逐字段一致；并把交易
     分别送现金执行层，比较逐日现金、持仓批次（含未平仓）、费用；
     参考实现任何决策不索引未来数据（独立验证，非同一扫描逻辑）。
S8e: 延迟成交场景——停牌日入场被丢弃（非顺延），其后下一交易日
     入场为独立批次、持仓期限从实际成交日起算；执行层涨停复核
     跳过的意图不产生幻影仓位。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "qsys"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import qengine  # noqa: E402
from quant_agent.backtest import streaming_ref  # noqa: E402
from quant_agent.backtest.executor import execute_cash  # noqa: E402

DAYS = 60


def _mk_panel():
    """三只股票的合成面板：散布 Keltner 触发、停牌日、跌停开盘。"""
    common = ["2026-03-%02d" % (i + 1) for i in range(DAYS)]
    stocks, feat = {}, {}
    # A: day12/34 触发; B: day20 触发; C: 不触发（纯到时）
    touch = {"A": (12, 34), "B": (20,), "C": ()}
    for code in ("A", "B", "C"):
        o = [10.0 + 0.01 * i for i in range(DAYS)]
        c = [10.0 + 0.01 * i for i in range(DAYS)]
        for t in touch[code]:
            c[t] = 9.2
        if code == "C":
            o[28] = c[27] * 0.90   # 跌停开盘（相对前收 -10%）
        stocks[code] = {"open": o, "close": c}
        feat[code] = {"ma20": [10.0] * DAYS, "atr20": [0.5] * DAYS}
    susp = {"B": frozenset([common[22]])}   # B 在 day22 停牌
    return stocks, feat, common, susp


SPEC = {"K": 3, "params": {"keltner_mult": 1.5, "max_hold": 10},
        "score": lambda feat, codes, t: {c: 1.0 for c in codes},
        "filt": lambda state, t: True}


def _key(tr):
    return (tr["code"], tr["e"], tr["xe"])


def main():
    stocks, feat, common, susp = _mk_panel()
    E_START, ENTRY_END = 3, 40

    print("=== S8d: 多截断点预扫描 vs 流式参考 ===")
    for T in (30, 40, 50, 59):
        # 截断语义: 入场截止与窗口末端同步收缩（生产窗口 entry_end<window_end
        # 恒成立；截断运行保持该不变式）
        ee = min(ENTRY_END, T)
        r_eng = qengine.run_r443(stocks, ["A", "B", "C"], feat, {}, common,
                                 susp, E_START, ee, T, SPEC)
        r_str = streaming_ref.run_streaming(stocks, ["A", "B", "C"], feat,
                                            {}, common, susp,
                                            E_START, ee, T, SPEC)
        k_eng = sorted(_key(t) for t in r_eng["trades"])
        k_str = sorted(_key(t) for t in r_str["trades"])
        assert k_eng == k_str, \
            "T=%d 交易不一致: 仅引擎=%s 仅流式=%s" % (
                T, sorted(set(k_eng) - set(k_str))[:5],
                sorted(set(k_str) - set(k_eng))[:5])
        assert len(r_eng["daily"]) == len(r_str["daily"])
        md = max(abs(a - b) for a, b in zip(r_eng["daily"], r_str["daily"]))
        assert md < 1e-12, "T=%d 日收益不一致 maxdiff=%g" % (T, md)
        assert abs(r_eng["avg_exposure"] - r_str["avg_exposure"]) < 1e-12
        # 现金执行层对账（含未平仓批次、费用、逐日现金）。
        # 同日订单撮合顺序 = 输入列表顺序（引擎输出按入场日+选股序）；
        # 流式实现的 trades 按出场日排列，故两侧统一按 (e, code) 排序后送入，
        # 消除排序歧义再比对账户状态。
        tin_eng = sorted(r_eng["trades"],
                         key=lambda t: (t["e"], t["code"]))
        tin_str = sorted(r_str["trades"],
                         key=lambda t: (t["e"], t["code"]))
        x_eng = execute_cash(tin_eng, stocks, common, susp,
                             0.002, 0.0025, cap=1.0, cash0=1_000_000,
                             end_day=T)
        x_str = execute_cash(tin_str, stocks, common, susp,
                             0.002, 0.0025, cap=1.0, cash0=1_000_000,
                             end_day=T)
        assert abs(x_eng["cash"] - x_str["cash"]) < 1e-6
        assert abs(x_eng["total_cost"] - x_str["total_cost"]) < 1e-9
        assert len(x_eng["trades"]) == len(x_str["trades"])
        for d in range(E_START, T + 1):
            ce = x_eng["daily_cash"].get(d)
            cs = x_str["daily_cash"].get(d)
            assert (ce is None) == (cs is None), "T=%d day %d 现金键缺失" % (T, d)
            if ce is not None:
                assert abs(ce - cs) < 1e-6, "T=%d day %d 现金不一致" % (T, d)
        print("  T=%d: 交易 %d 笔一致, 日收益 maxdiff=%.2e, 终现金 %.2f, "
              "费用 %.2f" % (T, len(k_eng), md, x_eng["cash"],
                             x_eng["total_cost"]))

    print("=== S8e: 延迟成交与独立批次 ===")
    # 停牌日丢弃入场: B 在 day22 停牌 → 该日无 B 入场; day23 入场的 B
    # 是独立批次, 持仓期限从 day23 实际成交日起算 (time_exit=33)。
    r = qengine.run_r443(stocks, ["A", "B", "C"], feat, {}, common, susp,
                         22, 23, 45, SPEC)
    b22 = [t for t in r["trades"] if t["code"] == "B" and t["e"] == 22]
    b23 = [t for t in r["trades"] if t["code"] == "B" and t["e"] == 23]
    assert not b22, "停牌日产生了入场（应为丢弃）"
    assert b23 and b23[0]["xe"] == 33, \
        "次日入场期限未从实际成交日起算: xe=%s" % (b23[0]["xe"] if b23 else None)
    print("  停牌日入场丢弃 ✓；次日入场独立批次 xe=33=23+10 ✓")
    # 执行层涨停复核跳过 → 无幻影仓位
    stocks2, common2 = {}, ["2026-04-%02d" % (i + 1) for i in range(10)]
    o = [10.0] * 10
    c = [10.0] * 10
    c[2] = 9.0            # day3 前收 9 → 涨停线 9*1.09=9.81
    o[3] = 9.9            # day3 开盘 9.9 ≥ 9.81 → 复核跳过
    stocks2["A"] = {"open": o, "close": c}
    intended = [{"code": "A", "e": 3, "xe": 8, "w": 0.5}]
    ex = execute_cash(intended, stocks2, common2, {}, 0.002, 0.0025,
                      cap=1.0, cash0=100_000)
    assert not ex["trades"], "涨停复核后仍产生成交"
    assert ex["cash"] == 100_000, "跳过意图改动了现金"
    assert any(s.get("why") == "limit_up_at_fill" for s in ex["skipped_entries"])
    print("  涨停复核跳过: 无成交、现金不动、跳过原因留痕 ✓")
    print("S8d/S8e 全部通过")


if __name__ == "__main__":
    main()
