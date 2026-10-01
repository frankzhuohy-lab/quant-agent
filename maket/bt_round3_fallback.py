#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三轮: 兜底回合优化（约束不变: 每天必交易, 只正T, 触发逻辑不动）
基线兜底: 14:30市价进场, 无目标, 14:55强平 → 125天单笔-0.737%
变体:
  F1a 兜底带目标+0.3%   F1b 兜底带目标+0.5%
  F3a 兜底提前14:00+目标0.3%   F3b 提前14:00+目标0.5%
  F4  兜底方向按VWAP: 14:30价格在VWAP上方才做多, 下方则做多但目标更近(+0.2%)
统一: 触发回合(开盘-0.6%)逻辑完全不动; IS/OOS各半验证; 只有OOS≥+5pp才采纳。
另: 修复上轮入场时段分桶bug(基线回合补记entry时间)。
"""
import sys, os, json
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maket as M
from bt_walkforward import load, day_ctx, stats, net, IS_N, EARLY, FORCE

LATE = "1430"


def replay_A(day_bars, fb_time=LATE, fb_tgt=0.0):
    """基线A(开盘-0.6%触发,目标+0.6%) + 可配兜底(时间/目标), 回合带entry时间"""
    if len(day_bars) < 8:
        return None
    o = day_bars[0][1]
    start_i = next((i for i, b in enumerate(day_bars) if b[0][8:] >= EARLY), None)
    if start_i is None:
        return None
    pos = None
    for i in range(start_i, len(day_bars)):
        b = day_bars[i]
        hm, px = b[0][8:], b[4]
        if pos:
            if px >= pos[2]:
                return {"dir": 1, "entry": pos[1], "exit": px, "reason": "触发达标", "et": pos[3]}
            if hm >= FORCE:
                return {"dir": 1, "entry": pos[1], "exit": px, "reason": "尾盘强平", "et": pos[3]}
            continue
        if hm >= fb_time:
            tgt = px * (1 + fb_tgt) if fb_tgt > 0 else None
            return _walk_out(day_bars, i, px, tgt, fb_tgt)
        if px <= o * (1 - 0.006):
            return _walk_out(day_bars, i, px, o * (1 + 0.006), None)
    return None


def _walk_out(day_bars, i, entry, tgt, fb_tgt):
    hm_in = day_bars[i][0][8:]
    reason0 = "触发" if fb_tgt is None else "兜底"
    for j in range(i, len(day_bars)):
        b = day_bars[j]
        hm, px = b[0][8:], b[4]
        if tgt is not None and px >= tgt:
            return {"dir": 1, "entry": entry, "exit": px, "reason": reason0 + "达标", "et": hm_in}
        if hm >= FORCE:
            return {"dir": 1, "entry": entry, "exit": px, "reason": reason0 + "强平", "et": hm_in}
    return {"dir": 1, "entry": entry, "exit": day_bars[-1][4], "reason": reason0 + "日终", "et": hm_in}


def run(days, keys, fb_time, fb_tgt):
    return [dict(date=dk, **r) for dk in keys
            for r in [replay_A(days[dk], fb_time, fb_tgt)] if r]


def show(tag, st, base_total=None):
    line = "  %-28s 胜率%2.0f%% 单笔%+.3f%% 合计%+7.1f%% 日为正%.0f%%" % (
        tag, st["win"], st["avg"], st["total"], st["day_pos"])
    if base_total is not None:
        line += " | vs基线 %+0.1fpp" % (st["total"] - base_total)
    print(line)


def main():
    days, dkeys = load()
    is_keys, oos_keys = dkeys[:IS_N], dkeys[IS_N:]
    variants = [
        ("基线 兜底14:30无目标", LATE, 0.0),
        ("F1a 兜底14:30目标+0.3%", LATE, 0.003),
        ("F1b 兜底14:30目标+0.5%", LATE, 0.005),
        ("F3a 兜底14:00目标+0.3%", "1400", 0.003),
        ("F3b 兜底14:00目标+0.5%", "1400", 0.005),
        ("F3c 兜底13:30目标+0.3%", "1330", 0.003),
    ]
    print("===== IS（前%d天）=====" % IS_N)
    is_st = {}
    for tag, t, g in variants:
        is_st[tag] = stats(run(days, is_keys, t, g))
        show(tag, is_st[tag])
    print("\n===== OOS（后%d天, 决策依据）=====" % len(oos_keys))
    base_total = stats(run(days, oos_keys, LATE, 0.0))["total"]
    best, best_key = None, None
    for tag, t, g in variants:
        st = stats(run(days, oos_keys, t, g))
        show(tag, st, base_total)
        if tag != variants[0][0] and (best is None or st["total"] > best["total"]):
            best, best_key = st, (tag, t, g)
    if best:
        print("\n最优变体: %s | OOS较基线 %+0.1fpp %s"
              % (best_key[0], best["total"] - base_total,
                 "→ 达到+5pp采纳线" if best["total"] - base_total >= 5 else "→ 未达+5pp, 不改引擎"))
    # 入场时段分析（基线, 带真实entry时间, 全125天）
    print("\n===== 基线触发回合入场时段（125天, 描述性）=====")
    allr = run(days, dkeys, LATE, 0.0)
    buckets = defaultdict(list)
    for r in allr:
        if r["reason"].startswith("兜底"):
            buckets["兜底"].append(net(r))
        else:
            t = r["et"]
            key = ("09:45-10:30" if t < "1030" else "10:30-11:30" if t < "1130"
                   else "13:00-14:00" if t < "1400" else "14:00+")
            buckets[key].append(net(r))
    for k in ["09:45-10:30", "10:30-11:30", "13:00-14:00", "14:00+", "兜底"]:
        v = buckets.get(k, [])
        if v:
            print("  %-12s %3d天 胜率%2.0f%% 单笔%+.3f%% 合计%+7.1f%%"
                  % (k, len(v), 100.0 * sum(1 for x in v if x > 0) / len(v),
                     sum(v) / len(v), sum(v)))
    # 触发vs兜底（基线）
    trig = [net(r) for r in allr if not r["reason"].startswith("兜底")]
    fb = [net(r) for r in allr if r["reason"].startswith("兜底")]
    print("\n触发回合 %d天 单笔%+.3f%% 合计%+.1f%% || 兜底回合 %d天 单笔%+.3f%% 合计%+.1f%%"
          % (len(trig), sum(trig)/len(trig), sum(trig), len(fb), sum(fb)/len(fb), sum(fb)))
    with open(os.path.join(HERE, "bt_round3_result.json"), "w") as f:
        json.dump({"note": "兜底变体OOS对比", "baseline_oos": base_total}, f)


if __name__ == "__main__":
    main()
