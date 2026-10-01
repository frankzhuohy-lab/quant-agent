#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E6 现金账户与持仓联动测试（评审第 2 轮 P0-3）。

回答评审问题并在合成数据上验收:
  E6-1 买入 sizing 已预留手续费（afford=cash/(1+费)），逐日现金≥0；
  E6-2 卖单未成交（停牌顺延）时资金不释放、仓位继续估值；
  E6-3 买入被截断/跳过后，不存在对应卖出；成交卖出股数=实际持仓股数；
  E6-4 敞口上限为下单时约束：价格漂移可致市值敞口越限，越限天数如实记录；
  E6-5 整手订单仍可能部分成交（未建模）——本测试不声称覆盖，文档声明。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from quant_agent.backtest.executor import execute_cash  # noqa: E402


def _mk_panel(days=12):
    common = ["2026-01-%02d" % (i + 1) for i in range(days)]

    def st(price):
        return {"open": [price] * days, "close": [price] * days,
                "vol": [10000.0] * days}
    stocks = {"A": st(10.0), "B": st(20.0)}
    return stocks, common


def main():
    fails = []

    # E6-1: 大额连续买入，逐日现金不得为负
    stocks, common = _mk_panel()
    intended = [{"code": "A", "e": 2, "xe": 9, "w": 0.6},
                {"code": "B", "e": 2, "xe": 9, "w": 0.6}]
    ex = execute_cash(intended, stocks, common, {},
                      0.0002, 0.00025, cap=1.0, cash0=100_000)
    if min(ex["daily_cash"].values()) < 0:
        fails.append("E6-1 现金为负: min=%.2f" % min(ex["daily_cash"].values()))
    if len(ex["trades"]) != 2:
        fails.append("E6-1 应成交 2 笔(权重和>1,现金口径截断后仍可各买)")
    # 权重和 1.2 > cap 1.0: 第二笔应受 room 约束缩小而非现金透支
    tot = sum(t["notional"] for t in ex["trades"])
    if tot > 100_000 * 1.001:
        fails.append("E6-1 名义额超现金: %.0f" % tot)

    # E6-2: 出场日停牌 → 顺延，当日不释放资金
    stocks, common = _mk_panel()
    susp = {"A": frozenset([common[5]])}   # 计划 xe=5 停牌
    intended = [{"code": "A", "e": 2, "xe": 5, "w": 0.5}]
    ex = execute_cash(intended, stocks, common, susp,
                      0.0002, 0.00025, cap=1.0, cash0=100_000)
    cash_day5 = ex["daily_cash"][5]
    # 未顺延成功前现金仍含仓位（现金=总权益-仓位市值；价格不变故现金不变小）
    t = ex["trades"][0] if ex["trades"] else None
    if t is None:
        fails.append("E6-2 未成交")
    else:
        if t["exit_date"] != common[6]:
            fails.append("E6-2 出场未顺延到 %s: %s" % (common[6],
                                                       t["exit_date"]))
        # 关键: 第5日（停牌日）不得有卖出入账——现金不应因'卖出'跳升
        # （合成面板价格恒定，现金唯一跳变来源就是卖出）
        if ex["daily_cash"][5] > ex["daily_cash"][4] + 1e-6:
            fails.append("E6-2 停牌日资金被释放")

    # E6-3: 截断/跳过的买入无对应卖出；卖出股数=实际股数
    stocks, common = _mk_panel()
    intended = [{"code": "A", "e": 2, "xe": 8, "w": 0.5},
                {"code": "B", "e": 2, "xe": 8, "w": 0.5}]
    ex = execute_cash(intended, stocks, common, {},
                      0.0002, 0.00025, cap=0.05, cash0=100_000)
    # cap=0.05 → 第一只占用全部额度，第二只 room=0 被跳过；
    # 被跳过意图不得产生卖出；成交卖出股数=实际买入股数。
    t = ex["trades"][0]
    if t["code"] != "A" or t["shares"] != 500:
        fails.append("E6-3 截断错误: %s %d" % (t["code"], t["shares"]))
    if len(ex["trades"]) != 1 or not ex["skipped_entries"]:
        fails.append("E6-3 成交/跳过计数异常: trades=%d skipped=%d" %
                     (len(ex["trades"]), len(ex["skipped_entries"])))
    if ex["skipped_entries"][0]["code"] != "B":
        fails.append("E6-3 被跳过的应是 B")

    # E6-4: 敞口上限为下单时约束；cap<1 时价格漂移可致市值敞口越限并记录。
    # （cap=1.0 时现金账户无负债，市值敞口恒≤100%，物理上不可能越限——
    #   该事实本身验证了"无未建模融资"。）
    days = 12
    common = ["2026-02-%02d" % (i + 1) for i in range(days)]
    stocks = {"A": {"open": [10.0] * 5 + [20.0] * (days - 5),
                    "close": [10.0] * 5 + [20.0] * (days - 5),
                    "vol": [10000.0] * days}}
    intended = [{"code": "A", "e": 2, "xe": 10, "w": 0.5}]
    ex = execute_cash(intended, stocks, common, {},
                      0.0002, 0.00025, cap=0.5, cash0=100_000)
    # 买入≈5000股@10=50000（50%）；价格翻倍 → 市值100000/权益150000≈0.667
    if not ex["exposure_breach_days"]:
        fails.append("E6-4 价格漂移越限未被记录")
    if ex["exposure"].get(2, 0) > 0.51:
        fails.append("E6-4 下单日敞口超上限: %.3f" % ex["exposure"][2])

    print("E6 linkage: cash_min=%.0f breach_days=%s trades=%d" % (
        min(ex["daily_cash"].values()), len(ex["exposure_breach_days"]),
        len(ex["trades"])))
    # E6-5: 最低收费边界（评审第 3 轮）
    # 5a) 现金 1004、价 10: 100 股需 1000+5=1005 > 1004 → 必须退到 0 股且不成交
    from quant_agent.backtest.executor import _max_affordable_shares
    s = _max_affordable_shares(10.0, 1004.0, 0.0002, 5.0)
    if s != 0:
        fails.append("E6-5a 最低收费边界失败: shares=%d" % s)
    # 5b) 现金 1050: 100 股需 1005 ≤ 1050 → 成交 100 股
    s = _max_affordable_shares(10.0, 1050.0, 0.0002, 5.0)
    if s != 100:
        fails.append("E6-5b 最低收费可成交边界失败: shares=%d" % s)
    # 5c) 纯比例费闭式解未被最低收费误伤: 大额现金下取比例约束
    s = _max_affordable_shares(10.0, 100_000.0, 0.0002, 5.0)
    # 100000/(10*1.0002)=9998 股 → 9900（整手）
    if s != 9900:
        fails.append("E6-5c 比例费闭式解失败: shares=%d" % s)
    # 5d) 整手回验: 结果必然满足 金额+费 ≤ 现金（逐手回验的终态断言）
    for cash_t in (999.0, 12345.0, 250_000.0):
        s = _max_affordable_shares(99.5, cash_t, 0.0002, 5.0)
        if s > 0:
            n_ = s * 99.5
            fee_ = max(n_ * 0.0002, 5.0)
            if n_ + fee_ > cash_t + 1e-9:
                fails.append("E6-5d 回验失败 cash=%s" % cash_t)
    # 5e) 集成: 现金 1004、目标满仓 → 最低收费使可买股数为 0，不成交、现金不动
    stocks, common = _mk_panel()
    intended = [{"code": "A", "e": 2, "xe": 9, "w": 1.0}]
    ex = execute_cash(intended, stocks, common, {},
                      0.0002, 0.00025, cap=1.0, cash0=1004.0,
                      min_fee_buy=5.0, min_fee_sell=5.0)
    if ex["trades"] or ex["cash"] != 1004.0:
        fails.append("E6-5e 小额意图应被最低收费挡下且不动用现金")
    if not fails:
        print("E6-5 最低收费边界通过（挡下/可成交/比例解/回验/集成）")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("E6 现金-持仓联动测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
