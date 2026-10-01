#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S8 前视（lookahead）因果性测试（评审第 3 轮 P0-1）。

背景: 引擎在入场日 e 预扫描未来 K 线确定计划出场日 xe（取首个触发日+1）。
数学上与"逐日流式触发首个满足"等价，但预扫描形式必须证明无前视泄漏。
验收三条:
  S8-1 截断: 窗口截到第 T 日后，出场日<=T 的交易与全窗口运行完全一致；
  S8-2 扰动: 把第 T 日之后的行情整体打乱/放大，T 日之前产生的
        全部信号与订单意图必须逐字段不变；
  S8-3 响应: 改变触发条件出现的日期，出场日随之移动（证明退出
        确实由后续行情驱动，而非入场日写死）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "qsys"))
import qengine

DAYS = 36


def _mk(touch_a=8, touch_b=None, pert_from=None, pert_val=None):
    common = ["2026-01-%02d" % (i + 1) for i in range(DAYS)]
    stocks, feat = {}, {}
    for code in ("A", "B"):
        o = [10.0] * DAYS
        c = [10.0] * DAYS
        if code == "A" and touch_a is not None:
            c[touch_a] = 9.2    # 跌破 ma20 - 1.5*atr = 9.25 → 触发；且
                                # 次日 open=10 < 9.2*1.09=10.03 → 不触涨停守卫
        if code == "B" and touch_b is not None:
            c[touch_b] = 9.2
        if pert_from is not None:
            for d in range(pert_from, DAYS):
                c[d] = pert_val
        stocks[code] = {"open": o, "close": c}
        feat[code] = {"ma20": [10.0] * DAYS, "atr20": [0.5] * DAYS}
    return stocks, feat, common


SPEC = {"K": 2, "params": {"keltner_mult": 1.5, "max_hold": 10},
        "score": lambda feat, codes, t: {c: 1.0 for c in codes},
        "filt": lambda state, t: True}


def _run(stocks, feat, common, entry_end=15, window_end=35):
    r = qengine.run_r443(stocks, ["A", "B"], feat, {}, common, {},
                           2, entry_end, window_end, SPEC)
    return [(t["code"], t["e"], t["xe"], t["planned_reason"]) for t in r["trades"]]


def main():
    base = _mk(touch_a=8)
    trades_full = _run(*base)

    # S8-1: 截断到 day25（window_end=25），出场<=25 的子集必须一致
    cut = _run(*base, window_end=25)
    expect = [t for t in trades_full if t[2] <= 25]
    assert cut == expect, "S8-1 FAIL: 截断运行与全窗口不一致"
    print("S8-1 截断一致性通过（%d 笔出场<=25 全等）" % len(expect))

    # S8-2: 自 day21 起行情改为 50（含收盘价与开盘）。出场日 xd 的决策
    # 只使用 close[xd-1] 与 open[xd] —— 故 xd <= 21 的全部订单必须不变；
    # xd >= 22 的订单允许变化（属于因果窗口内的正常响应，S8-3 另验）。
    P = 21
    pert = _mk(touch_a=8, pert_from=P, pert_val=50.0)
    trades_pert = _run(*pert)
    sub_full = [t for t in trades_full if t[2] <= P]
    sub_pert = [t for t in trades_pert if t[2] <= P]
    assert sub_pert == sub_full, "S8-2 FAIL: 边界外扰动泄漏进历史订单"
    print("S8-2 未来扰动不变性通过（day%d 后行情 x5，%d 笔 xd<=%d 订单逐字段不变）"
          % (P, len(sub_full), P))

    # S8-2b: 极端情形——删除未来（截尾为 NaN 语义不适用，用暴跌模拟），
    # 验证晚出场订单确实随未来行情变化（非恒定写死）。
    late_changed = [t for t in trades_pert if t[2] > P] != \
                   [t for t in trades_full if t[2] > P]
    assert late_changed, "S8-2b FAIL: 未来行情变化未影响因果窗口内订单"
    print("S8-2b 因果窗口响应确认（%d 笔 xd>%d 订单随未来行情变化）"
          % (len(trades_full) - len(sub_full), P))

    # S8-3: 触发日从 day8 移到 day12。各笔 A 的可扫描区间 [e, e+9] 不同，
    # 晚移幅度不一致属正常；验收 = A 每笔出场不早于原值且至少一笔严格变晚，
    # B（无触发）出场全部不变。
    moved = _mk(touch_a=12)
    trades_moved = _run(*moved)
    mf = {(c, e): x for (c, e, x, _) in trades_full}
    mm = {(c, e): x for (c, e, x, _) in trades_moved}
    assert set(mf) == set(mm), "S8-3 FAIL: 触发日移动改变了入场集合"
    n_changed = 0
    for k, x0 in mf.items():
        x1 = mm[k]
        if k[0] == "A":
            # 解析期望: 扫描区间 [e, min(e+9,35)] 首个触发日+1；无触发则 e+10。
            e = k[1]
            touch = next((d for d in range(e, min(e + 9, DAYS - 1) + 1)
                          if d == 12), None)
            expect = (touch + 1) if touch is not None else e + 10
            assert x1 == expect, \
                "S8-3 FAIL: A e=%d 出场=%d 与解析期望 %d 不符" % (e, x1, expect)
            if x1 != x0:
                n_changed += 1
        else:
            assert x1 == x0, "S8-3 FAIL: B 无触发却变化"
    assert n_changed > 0, "S8-3 FAIL: A 出场日未随触发日移动"
    print("S8-3 退出响应后续行情通过（%d/%d 笔 A 出场按解析期望变动，B 全不变）"
          % (n_changed, len(mf) // 2))
    print("S8 前视因果性测试全部通过")


if __name__ == "__main__":
    main()
