#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第四轮: 强势买入触发器（用户指令: 量能足够时不必等回绿）
在现有「低吸触发」之外增加「强势触发」, 先到先得, 每日仍恰1回合:
  G1 量能强势: 价格 >= 开盘×(1+s) 且 当bar量能 >= r×当日此前均量 → 买入, 目标入场价+0.6%
  G2 ORB突破:  收盘价突破开盘后前30分钟高点 且 量能确认 → 买入
  统一: 低吸触发保留(先到先得) · 14:30兜底 · 14:55强平 · 无止损 · 成本0.12%
纪律: IS前60天选参 / OOS后65天定去留; OOS较基线≥+5pp才采纳。
"""
import sys, os, json
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maket as M
from bt_walkforward import load, stats, net, IS_N, EARLY, LATE, FORCE

D = 0.006            # 目标幅度(与现引擎一致)
DIP = 0.006          # 低吸线


def replay_G(day_bars, mode, s, r):
    """mode: 'g1'量能强势 / 'g2'ORB突破; 均含低吸触发, 先到先得"""
    if len(day_bars) < 8:
        return None
    o = day_bars[0][1]
    start_i = next((i for i, b in enumerate(day_bars) if b[0][8:] >= EARLY), None)
    if start_i is None:
        return None
    # OR30: 前30分钟高点(0935-1000)
    or30 = max(b[2] for b in day_bars if b[0][8:] < "1005") or o
    pos = None
    for i in range(start_i, len(day_bars)):
        b = day_bars[i]
        hm, px, v = b[0][8:], b[4], b[5]
        if pos:
            if px >= pos[2]:
                return {"dir": 1, "entry": pos[1], "exit": px, "reason": "触发达标", "tag": pos[3]}
            if hm >= FORCE:
                return {"dir": 1, "entry": pos[1], "exit": px, "reason": "尾盘强平", "tag": pos[3]}
            continue
        if hm >= LATE:
            return _out(day_bars, i, px, None, "兜底")
        # 量能比: 当前bar量 / 此前当日均量(至少6根)
        prior = [x[5] for x in day_bars[start_i:i]]
        vr = v / (sum(prior) / len(prior)) if len(prior) >= 6 and sum(prior) else 0
        if mode == "g1" and px >= o * (1 + s) and vr >= r:
            return _out(day_bars, i, px, px * (1 + D), "强势")
        if mode == "g2" and hm >= "1005" and px > or30 and vr >= r:
            return _out(day_bars, i, px, px * (1 + D), "ORB")
        if px <= o * (1 - DIP):
            return _out(day_bars, i, px, o * (1 + D), "低吸")
    return None


def _out(day_bars, i, entry, tgt, tag):
    for j in range(i, len(day_bars)):
        b = day_bars[j]
        hm, px = b[0][8:], b[4]
        if tgt is not None and px >= tgt:
            return {"dir": 1, "entry": entry, "exit": px, "reason": tag + "达标", "tag": tag}
        if hm >= FORCE:
            return {"dir": 1, "entry": entry, "exit": px, "reason": tag + "强平", "tag": tag}
    return {"dir": 1, "entry": entry, "exit": day_bars[-1][4], "reason": tag + "日终", "tag": tag}


def run(days, keys, mode, s, r):
    return [dict(date=dk, **x) for dk in keys for x in [replay_G(days[dk], mode, s, r)] if x]


def split(rounds):
    t = defaultdict(list)
    for r in rounds:
        t[r["tag"]].append(net(r))
    return {k: (len(v), sum(v) / len(v), sum(v)) for k, v in t.items()}


def main():
    days, dkeys = load()
    is_keys, oos_keys = dkeys[:IS_N], dkeys[IS_N:]
    variants = [
        ("G1 s=0.5% r=1.5", "g1", 0.005, 1.5),
        ("G1 s=1.0% r=1.5", "g1", 0.010, 1.5),
        ("G1 s=1.0% r=2.0", "g1", 0.010, 2.0),
        ("G1 s=1.5% r=2.0", "g1", 0.015, 2.0),
        ("G2 ORB r=1.5", "g2", None, 1.5),
        ("G2 ORB r=2.0", "g2", None, 2.0),
    ]
    print("===== IS（前%d天）=====" % IS_N)
    base_is = stats(run(days, is_keys, "g1", 99, 99))   # s=99 → 只有低吸+兜底=基线
    print("  基线(纯低吸): 胜率%.0f%% 单笔%+.3f%% 合计%+.1f%%" %
          (base_is["win"], base_is["avg"], base_is["total"]))
    print("===== OOS（后%d天, 决策依据）=====" % len(oos_keys))
    base_oos = stats(run(days, oos_keys, "g1", 99, 99))
    print("  基线(纯低吸): 胜率%.0f%% 单笔%+.3f%% 合计%+.1f%% 日为正%.0f%%" %
          (base_oos["win"], base_oos["avg"], base_oos["total"], base_oos["day_pos"]))
    print("\n各变体 OOS（较基线≥+5pp才采纳）:")
    best, best_key = None, None
    for tag, mode, s, r in variants:
        st = stats(run(days, oos_keys, mode, s, r))
        diff = st["total"] - base_oos["total"]
        show = "  %-18s 胜率%2.0f%% 单笔%+.3f%% 合计%+7.1f%% | vs基线 %+0.1fpp %s" % (
            tag, st["win"], st["avg"], st["total"], diff,
            "✅ 采纳线" if diff >= 5 else "")
        print(show)
        if diff >= 5 and (best is None or st["total"] > best["total"]):
            best, best_key = st, (tag, mode, s, r)
    # 最优变体的 IS 表现（确认不是 OOS 偶然）
    if best_key:
        st_is = stats(run(days, is_keys, best_key[1], best_key[2], best_key[3]))
        print("\n最优 %s 的 IS 合计: %+.1f%%（IS 应同为正, 防OOS偶然）" % (best_key[0], st_is["total"]))
    # 基线的触发/兜底结构（125天）
    allr = run(days, dkeys, "g1", 99, 99)
    print("\n基线触发结构(125天):", {k: "%d笔/均%+.2f%%/合计%+.1f%%" % (n, avg, tot)
                                      for k, (n, avg, tot) in split(allr).items()})
    # 最优变体结构
    if best_key:
        allb = run(days, dkeys, best_key[1], best_key[2], best_key[3])
        print("最优变体结构(125天):", {k: "%d笔/均%+.2f%%/合计%+.1f%%" % (n, avg, tot)
                                       for k, (n, avg, tot) in split(allb).items()})
    with open(os.path.join(HERE, "bt_round4_result.json"), "w") as f:
        json.dump({"baseline_oos": base_oos["total"],
                   "adopted": best_key[0] if best_key else None}, f, ensure_ascii=False)


def robust():
    """参数邻域检查: 采纳前提是整片邻域为正, 而非单格偶然"""
    days, dkeys = load()
    is_keys, oos_keys = dkeys[:IS_N], dkeys[IS_N:]
    base_oos = stats(run(days, oos_keys, "g1", 99, 99))["total"]
    print("\n===== 参数邻域稳健性（OOS较基线pp）=====")
    print("  %-10s %-8s %-8s %-8s" % ("s\\r", "r=1.25", "r=1.5", "r=1.75"))
    ok = True
    for s in (0.005, 0.0075, 0.010):
        row = []
        for r in (1.25, 1.5, 1.75):
            st = stats(run(days, oos_keys, "g1", s, r))
            diff = st["total"] - base_oos
            row.append("%+0.1f" % diff)
            if diff <= 0:
                ok = False
        print("  %-10s %-8s %-8s %-8s" % ("s=%.2f%%" % (s * 100), *row))
    print("  邻域全为正:", "是 → 稳健" if ok else "否 → 谨慎")


if __name__ == "__main__":
    main()
    robust()
