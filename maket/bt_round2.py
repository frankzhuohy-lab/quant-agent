#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""优化第二轮: 混合策略 + 入场时段分析 + 兜底成本量化（石药创新125天）
延续 walk-forward 纪律: IS=前60天选参, OOS=后65天定去留。
  E1 VWAP择时+网格目标: 入场=VWAP−z·σ(吃B的高胜率择时), 目标=入场价+0.6%(吃A的利润形态), z∈{1.0,1.5,2.0}
  E2 动态目标: 入场=开盘−0.6%(A), 目标=max(开盘+0.6%, 当时刻VWAP) — 顺势多拿一点
  E3 分析项: 基线回合按入场时段分组成绩; 兜底回合成本量化
注意: 在同一OOS上测试的变体越多, 选择偏差越大——只有 OOS 超基线 ≥5pp 才考虑采纳。
"""
import sys, os, json
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maket as M
from bt_walkforward import load, day_ctx, stats, net, IS_N, EARLY, LATE, FORCE


def replay_e1(day_bars, z):
    """E1: VWAP择时 + 固定0.6%目标"""
    if len(day_bars) < 8:
        return None
    start_i = next((i for i, b in enumerate(day_bars) if b[0][8:] >= EARLY), None)
    if start_i is None:
        return None
    vwap, sd = day_ctx(day_bars)
    pos = None
    for i in range(start_i, len(day_bars)):
        b = day_bars[i]
        hm, px = b[0][8:], b[4]
        if pos:
            if px >= pos[2]:
                return {"dir": 1, "entry": pos[1], "exit": px, "reason": "到达目标", "et": pos[3]}
            if hm >= FORCE:
                return {"dir": 1, "entry": pos[1], "exit": px, "reason": "尾盘强平", "et": pos[3]}
            continue
        if hm >= LATE:
            return {"dir": 1, "entry": px, "exit": day_bars[-1][4], "reason": "兜底", "et": hm}
        if sd[i] > 0 and px <= vwap[i] - z * sd[i]:
            pos = (1, px, px * 1.006, hm)
    if pos:
        return {"dir": 1, "entry": pos[1], "exit": day_bars[-1][4], "reason": "日终", "et": pos[3]}
    return None


def replay_e2(day_bars, d=None):
    """E2: 开盘低吸入场 + max(开盘+0.6%, 当时刻VWAP) 动态目标"""
    d = 0.006 if d is None else d
    if len(day_bars) < 8:
        return None
    o = day_bars[0][1]
    start_i = next((i for i, b in enumerate(day_bars) if b[0][8:] >= EARLY), None)
    if start_i is None:
        return None
    vwap, sd = day_ctx(day_bars)
    pos = None
    for i in range(start_i, len(day_bars)):
        b = day_bars[i]
        hm, px = b[0][8:], b[4]
        if pos:
            if px >= pos[2]:
                return {"dir": 1, "entry": pos[1], "exit": px, "reason": "到达目标", "et": pos[3]}
            if hm >= FORCE:
                return {"dir": 1, "entry": pos[1], "exit": px, "reason": "尾盘强平", "et": pos[3]}
            continue
        if hm >= LATE:
            return {"dir": 1, "entry": px, "exit": day_bars[-1][4], "reason": "兜底", "et": hm}
        if px <= o * (1 - d):
            tgt = max(o * (1 + d), vwap[i])
            pos = (1, px, tgt, hm)
    if pos:
        return {"dir": 1, "entry": pos[1], "exit": day_bars[-1][4], "reason": "日终", "et": pos[3]}
    return None


def run_all(days, dkeys, fn, p):
    rounds = []
    for dk in dkeys:
        r = fn(days[dk], p)
        if r:
            rounds.append(dict(date=dk, **r))
    return rounds


def main():
    days, dkeys = load()
    is_keys, oos_keys = dkeys[:IS_N], dkeys[IS_N:]

    # —— 基线 A d=0.6%（复用 bt_walkforward 的 A 策略）——
    from bt_walkforward import run_strat
    base_is = run_strat(days, is_keys, "A", 0.006)
    base_oos = run_strat(days, oos_keys, "A", 0.006)
    base_is_st, base_oos_st = stats(base_is), stats(base_oos)
    print("基线 A(0.6%%): IS 合计%+.1f%% | OOS 合计%+.1f%% 日为正%.0f%%"
          % (base_is_st["total"], base_oos_st["total"], base_oos_st["day_pos"]))

    # —— E1/E2: IS 选参 → OOS 验证 ——
    print("\n===== IS 选参 =====")
    cands = {}
    for name, fn, ps in [("E1 VWAP择时+网格目标", replay_e1, [1.0, 1.5, 2.0]),
                         ("E2 动态目标", replay_e2, [None])]:
        for pz in ps:
            st = stats(run_all(days, is_keys, fn, pz))
            if st:
                cands[(name, pz)] = st
                print("  %s z=%s: 胜率%.0f%% 单笔%+.3f%% 合计%+.1f%%"
                      % (name, pz, st["win"], st["avg"], st["total"]))
    print("\n===== OOS 验证（vs 基线 %+0.1f%%）=====" % base_oos_st["total"])
    oos_res = {}
    for (name, pz) in cands:
        fn = replay_e1 if name.startswith("E1") else replay_e2
        st = stats(run_all(days, oos_keys, fn, pz))
        oos_res[(name, pz)] = st
        diff = sum(st["daily"].get(k, 0) - base_oos_st["daily"].get(k, 0)
                   for k in base_oos_st["daily"])
        print("  %s z=%s: 胜率%.0f%% 单笔%+.3f%% 合计%+.1f%% | vs基线 %+0.1fpp (逐日更好%d/%d)"
              % (name, pz, st["win"], st["avg"], st["total"], diff,
                 sum(1 for k in base_oos_st["daily"] if st["daily"].get(k, 0) > base_oos_st["daily"][k]),
                 len(base_oos_st["daily"])))

    # —— E3a: 基线入场时段分析（全125天, 描述性）——
    print("\n===== 基线入场时段分析（125天）=====")
    buckets = defaultdict(list)
    for r in base_is + base_oos:
        t = r.get("et") or r.get("entry_time", "0000")[:4] if isinstance(r.get("entry_time"), str) else "0000"
        h = int(t[:2])
        key = "兜底(14:30+)" if r["reason"] in ("兜底",) else (
              "09:45-10:00" if t < "1000" else "10:00-11:00" if t < "1100"
              else "11:00-12:00" if t < "1200" else "13:00-14:00" if t < "1400" else "14:00+")
        buckets[key].append(net(r))
    for k in ["09:45-10:00", "10:00-11:00", "11:00-12:00", "13:00-14:00", "14:00+", "兜底(14:30+)"]:
        v = buckets.get(k, [])
        if v:
            print("  %-12s %3d笔 胜率%2.0f%% 单笔%+.3f%% 合计%+6.1f%%"
                  % (k, len(v), 100.0 * sum(1 for x in v if x > 0) / len(v),
                     sum(v) / len(v), sum(v)))

    # —— E3b: 兜底成本量化 ——
    fb = [r for r in base_is + base_oos if r["reason"] in ("兜底", "尾盘强平") and r.get("et", "9999") >= LATE]
    trig = [r for r in base_is + base_oos if r not in fb]
    if fb:
        print("\n兜底回合: %d天 单笔%+.3f%% 合计%+.1f%%（占总回合%.0f%%）"
              % (len(fb), sum(net(r) for r in fb) / len(fb), sum(net(r) for r in fb),
                 100.0 * len(fb) / (len(fb) + len(trig))))
        print("触发回合: %d天 单笔%+.3f%% 合计%+.1f%%"
              % (len(trig), sum(net(r) for r in trig) / len(trig), sum(net(r) for r in trig)))
    with open(os.path.join(HERE, "bt_round2_result.json"), "w") as f:
        json.dump({"oos": {"%s|%s" % k: v for k, v in oos_res.items()}}, f, ensure_ascii=False, indent=1)
    print("\n已存 bt_round2_result.json")


if __name__ == "__main__":
    main()
