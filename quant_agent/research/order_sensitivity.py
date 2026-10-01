#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2/E3 重排序近零效应的机理诊断（批次九补足）。

问题：评分重排序后 DEV 仅 -2.0pp / -0.3pp。假设：执行器按意图顺序
依次申领 room/cash，若多数日子 capacity 不 binding（全部意图成交），
顺序无关紧要。本诊断统计：
  1. 各窗口有拒单的日子占比（capacity binding 频率）；
  2. E2/E3 与基线成交集合的差异（多少 (code,entry) 对不同）；
  3. 差异日的拒单构成是否改变。
产物: var/exp_round3/order_sensitivity.md
"""
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
import quant_agent  # noqa: E402

OUT = os.path.join(quant_agent.VAR, "exp_round3")


def fill_set(tag, label):
    fp = os.path.join(OUT, "fills_%s_%s.csv" % (tag, label))
    with open(fp) as f:
        return set((r["证券"], r["入场日"]) for r in csv.DictReader(f))


def skip_days(tag, label):
    fp = os.path.join(OUT, "account_%s_%s.csv" % (tag, label))
    n = 0
    days = 0
    with open(fp) as f:
        for r in csv.DictReader(f):
            days += 1
            if int(r["n_skips"]) > 0:
                n += 1
    return n, days


def main():
    L = ["# E2/E3 重排序效应机理诊断", ""]
    for label in ("IS", "DEV"):
        b = fill_set("baseline", label)
        n_skip_b, days = skip_days("baseline", label)
        L.append("## %s" % label)
        L.append("")
        L.append("- 窗口交易日 %d，其中有拒单日 %d（%.1f%%）——capacity "
                 "binding 频率。" % (days, n_skip_b,
                                    100.0 * n_skip_b / days))
        for tag in ("E2_ridge_rank", "E3_cart_rank"):
            v = fill_set(tag, label)
            diff_in = len(v - b)
            diff_out = len(b - v)
            n_skip_v, _ = skip_days(tag, label)
            L.append("- %s: 成交集合 新增 %d 笔 / 丢失 %d 笔（基线 %d 笔）；"
                     "有拒单日 %d。" % (tag, diff_in, diff_out, len(b),
                                       n_skip_v))
        L.append("")
    L.append("结论：若 binding 日占比低且成交集合几乎不变，则重排序"
             "效应天然近零——排序只在'有拒单的日子'改变谁成交。"
             "评分要产生效应，需与 capacity 约束耦合（分档仓位）或"
             "改变选股集合本身。")
    open(os.path.join(OUT, "order_sensitivity.md"), "w",
         encoding="utf-8").write("\n".join(L))
    print("OK")


if __name__ == "__main__":
    main()
