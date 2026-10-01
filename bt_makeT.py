#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""做T策略回测 v1（纸面验证前置门槛，未达标不自动化）
数据: mindata/m5_*.json（腾讯5分钟线，2026-06-01~08-28）
口径: 成本单边 0.2%（与 R443 冻结口径一致），每回合合计 0.4%
      T 仓大小 = 1 单位（约底仓的1/3），收益按占该单位本金的百分比计
策略族:
  S1 VWAP偏离回归: 跌破 VWAP-k·sigma 买入(正T)回到VWAP卖 / 冲高卖出(反T)回归买回
  S2 开盘区间突破: 前30分钟高低点，突破做多 / 跌破先卖后买回(反T)
纪律: 收盘前 14:55 一律平T仓，不留过夜增量；每日每票每方向最多1次
"""
import json, os, sys, glob
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
COST = 0.002          # 单边 0.2%
RT = COST * 2         # 回合 0.4%

def load():
    fp = sorted(glob.glob(os.path.join(HERE, "mindata", "m5_*.json")))[-1]
    d = json.load(open(fp))
    print("数据文件: %s  抓取: %s" % (os.path.basename(fp), d["fetched_at"][:19]))
    return d["data"]

def day_index(bars):
    days = defaultdict(list)
    for b in bars:
        days[b[0][:8]].append(b)   # [t, o, h, l, c, v]
    for k in days:
        days[k].sort(key=lambda x: x[0])
    return days

def run_s1(days, k):
    """VWAP 偏离回归，k=入场阈值(倍数sigma)"""
    trades = []
    for dk in sorted(days):
        bl = days[dk]
        if len(bl) < 30:
            continue
        cum_pv = cum_v = 0.0
        dev_hist = []
        long_open = short_open = None
        for i, b in enumerate(bl):
            t, o, h, l, c, v = b[0], b[1], b[2], b[3], b[4], float(b[5])
            tp = (h + l + c) / 3.0
            cum_pv += tp * v; cum_v += v
            vwap = cum_pv / cum_v if cum_v else c
            dev = c / vwap - 1.0
            stop = t >= (dk + "1455")
            if long_open:
                if c >= vwap or stop:
                    r = c / long_open - 1.0 - RT
                    trades.append((dk, "S1多", r, stop)); long_open = None
            if short_open:
                if c <= vwap or stop:
                    r = short_open / c - 1.0 - RT
                    trades.append((dk, "S1空", r, stop)); short_open = None
            if len(dev_hist) >= 20:
                mu = sum(dev_hist[-20:]) / 20.0
                sg = (sum((x - mu) ** 2 for x in dev_hist[-20:]) / 20.0) ** 0.5
                if sg > 1e-4:
                    if not long_open and dev < mu - k * sg and not stop:
                        long_open = c
                    if not short_open and dev > mu + k * sg and not stop:
                        short_open = c
            dev_hist.append(dev)
        if long_open: trades.append((dk, "S1多", 1.0 / long_open - 1.0 - RT, True))
        if short_open: trades.append((dk, "S1空", short_open / bl[-1][4] - 1.0 - RT, True))
    return trades

def run_s2(days, k):
    """开盘区间突破（前30分钟=6根m5），止盈=2R 止损=回到区间中值"""
    trades = []
    for dk in sorted(days):
        bl = days[dk]
        if len(bl) < 30:
            continue
        orh = max(b[2] for b in bl[:6]); orl = min(b[3] for b in bl[:6])
        mid = (orh + orl) / 2.0
        rng = orh - orl
        if rng <= 0 or orh == 0:
            continue
        pos = None  # (dir, entry)
        for b in bl[6:]:
            t, c = b[0], b[4]
            stop = t >= (dk + "1455")
            if pos:
                d, e = pos
                r = (c / e - 1.0) if d == "L" else (e / c - 1.0)
                tgt = r * 2.0 if d == "L" else r
                hit_tp = (d == "L" and c >= e + k * rng) or (d == "S" and c <= e - k * rng)
                hit_sl = (d == "L" and c <= mid) or (d == "S" and c >= mid)
                if hit_tp or hit_sl or stop:
                    trades.append((dk, "S2" + d, r - RT, stop))
                    pos = None
            if not pos and not stop:
                if c > orh: pos = ("L", c)
                elif c < orl: pos = ("S", c)
        if pos:
            d, e = pos
            r = (bl[-1][4] / e - 1.0) if d == "L" else (e / bl[-1][4] - 1.0)
            trades.append((dk, "S2" + d, r - RT, True))
    return trades

def report(name, trades):
    if not trades:
        print("%-22s 无成交" % name); return
    rets = [t[2] * 100 for t in trades]
    n = len(rets); wins = sum(1 for r in rets if r > 0)
    avg = sum(rets) / n; tot = sum(rets)
    print("%-22s 回合=%3d  胜率=%5.1f%%  平均=%+5.2f%%  合计=%+6.1f%%  最好=%+.2f 最差=%+.2f"
          % (name, n, 100.0 * wins / n, avg, tot, max(rets), min(rets)))

def amplitude(days):
    amps = []
    for dk, bl in days.items():
        if len(bl) < 30: continue
        hi = max(b[2] for b in bl); lo = min(b[3] for b in bl)
        if lo > 0: amps.append((hi / lo - 1.0) * 100)
    amps.sort()
    if amps:
        print("日内振幅%%: 中位=%.2f  P25=%.2f  P75=%.2f  均值=%.2f (n=%d)"
              % (amps[len(amps)//2], amps[len(amps)//4], amps[3*len(amps)//4],
                 sum(amps)/len(amps), len(amps)))

def main():
    data = load()
    all_by_sym = {sym: day_index(v["bars"]) for sym, v in data.items()}
    # 全池振幅
    merged = {}
    for sym, dd in all_by_sym.items():
        for dk, bl in dd.items():
            merged.setdefault(sym + dk, bl)
    print("== 做T毛料：日内振幅 ==")
    amplitude(merged)
    print("\n== S1 VWAP回归（每回合成本 %.1f%%）==" % (RT * 100))
    for k in (1.0, 1.5, 2.0):
        trs = []
        for sym, dd in all_by_sym.items():
            trs += run_s1(dd, k)
        report("k=%.1f 全池" % k, trs)
    print("\n== S2 开盘区间突破 ==")
    for k in (0.5, 1.0):
        trs = []
        for sym, dd in all_by_sym.items():
            trs += run_s2(dd, k)
        report("止盈%.1fR 全池" % k, trs)

if __name__ == "__main__":
    main()
