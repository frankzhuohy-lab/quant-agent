#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""做T回测 v2：低频极端条件版（v1 两族全灭后的最后尝试）
原则: 只在"已实现"的剧烈偏离时下重手，目标毛利 >1.5% 才够 0.4% 成本
无未来函数: 只用当前bar及之前的数据
  V1 深跌反抽: 相对开盘跌幅 > -d%, 且连续2根m5回升 → 买入, 止盈+tp / 止损-sl / 14:55平
  V2 冲高回落: 相对开盘涨幅 > +d%, 且连续2根m5回落 → 卖出反T(买回同V1镜像)
  V3 双向: V1+V2
"""
import json, os, glob
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
COST = 0.002
RT = COST * 2

def load():
    fp = sorted(glob.glob(os.path.join(HERE, "mindata", "m5_*.json")))[-1]
    return json.load(open(fp))["data"]

def day_index(bars):
    days = defaultdict(list)
    for b in bars:
        days[b[0][:8]].append(b)
    for k in days:
        days[k].sort(key=lambda x: x[0])
    return days

def run(days, d, tp, sl):
    """返回回合列表 [(date, dir, ret_pct, eod_forced)]"""
    trades = []
    for dk in sorted(days):
        bl = days[dk]
        if len(bl) < 30:
            continue
        o = bl[0][1]  # 当日开盘价
        pos = None
        up2 = dn2 = 0
        for i in range(1, len(bl)):
            c = bl[i][4]; pc = bl[i-1][4]
            t = bl[i][0]
            up2 = up2 + 1 if c > pc else 0
            dn2 = dn2 + 1 if c < pc else 0
            stop = t >= (dk + "1455")
            if pos:
                dr, e = pos
                r = (c/e - 1.0) if dr == 1 else (e/c - 1.0)
                if r >= tp or r <= -sl or stop:
                    trades.append((dk, dr, r - RT, stop)); pos = None
            if pos is None and not stop and i >= 6:
                dev = c / o - 1.0
                if dev <= -d and up2 >= 2:
                    pos = (1, c)
                elif dev >= d and dn2 >= 2:
                    pos = (-1, c)
        if pos:
            dr, e = pos
            r = (bl[-1][4]/e - 1.0) if dr == 1 else (e/bl[-1][4] - 1.0)
            trades.append((dk, dr, r - RT, True))
    return trades

def report(name, trs):
    if len(trs) < 3:
        print("%-30s 回合=%d (太少，无统计意义)" % (name, len(trs))); return
    rets = [t[2]*100 for t in trs]
    n = len(rets); w = sum(1 for r in rets if r > 0)
    tot = sum(rets)
    print("%-30s 回合=%3d 胜率=%5.1f%% 平均=%+5.2f%% 合计=%+6.1f%% 中位=%+5.2f"
          % (name, n, 100.0*w/n, tot/n, tot, sorted(rets)[n//2]))

def main():
    data = load()
    all_days = {sym: day_index(v["bars"]) for sym, v in data.items()}
    print("== V1/V2/V3 深偏离条件反抽 (成本回合%.1f%%) ==" % (RT*100))
    grid = [(0.03, 0.015, 0.010), (0.04, 0.020, 0.012), (0.05, 0.025, 0.015),
            (0.03, 0.020, 0.008), (0.04, 0.030, 0.010)]
    for d, tp, sl in grid:
        trs = []
        for sym, dd in all_days.items():
            trs += run(dd, d, tp, sl)
        report("d=%.0f%% tp=%.1f%% sl=%.1f%%" % (d*100, tp*100, sl*100), trs)
    # 基准：如果完全不做T，持有底仓的日波动是多少（做T的机会成本参照）
    # 逐票外推：按每回合>=0.5%平均 且 回合/票/周>=2 为达标线

if __name__ == "__main__":
    main()
