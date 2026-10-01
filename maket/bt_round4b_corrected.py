#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第四轮重赛（修正字段序后）: 强势买入触发器 vs 当前引擎基线
字段序勘误后: 腾讯m5真实序 [time, open, close, high, low, vol] → 收盘=b[2]
基线=当前引擎S3: 低吸(开盘-0.6%买, 目标开盘+0.6%) + 止损-1.5% + 14:50强平 + 无兜底(无信号日不交易)
G1=基线 + 强势触发: 收盘 ≥ 开盘×(1+s) 且 当bar量 ≥ 此前均量×r → 买入(目标入场价+0.6%), 先到先得
纪律: IS前60天选参 → OOS后65天冻结验证; OOS较基线≥+5pp才采纳。
"""
import sys, os, json
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maket as M
from bt_walkforward import load, IS_N

D = 0.006
STOP = 0.015
EARLY, FORCE = "0945", "1450"
COST = 0.0012


def replay(day_bars, s=None, r=None):
    """s=None → 纯基线(只低吸); 否则含强势触发"""
    if len(day_bars) < 8:
        return None
    o = day_bars[0][1]                      # b[1]=开盘（两种字段序一致）
    start_i = next((i for i, b in enumerate(day_bars) if b[0][8:] >= EARLY), None)
    if start_i is None:
        return None
    pos = None
    for i in range(start_i, len(day_bars)):
        b = day_bars[i]
        hm, px = b[0][8:], b[2]             # b[2]=收盘（修正后成交价）
        if pos:
            entry, tgt, tag = pos
            if px >= tgt:
                return {"dir": 1, "entry": entry, "exit": px, "reason": "达标", "tag": tag}
            if px <= entry * (1 - STOP):
                return {"dir": 1, "entry": entry, "exit": px, "reason": "止损", "tag": tag}
            if hm >= FORCE:
                return {"dir": 1, "entry": entry, "exit": px, "reason": "强平", "tag": tag}
            continue
        if hm >= FORCE:
            return None                      # 无信号日不交易
        tag = None
        if px <= o * (1 - D):
            tag = "低吸"
        elif s is not None and px >= o * (1 + s):
            prior = [x[5] for x in day_bars[start_i:i]]
            vr = b[5] / (sum(prior) / len(prior)) if len(prior) >= 6 and sum(prior) else 0
            if vr >= r:
                tag = "强势"
        if tag == "低吸":
            pos = (px, o * (1 + D), tag)
        elif tag == "强势":
            pos = (px, px * (1 + D), tag)
    return None


def fix(r):
    """pos元组里的tag提取修正（replay返回结构统一）"""
    return r


def run(days, keys, s, r):
    out = []
    for dk in keys:
        x = replay(days[dk], s, r)
        if x:
            x = dict(x)
            out.append(dict(date=dk, **x))
    return out


def net(r):
    return ((r["exit"] / r["entry"] - 1.0) * r["dir"] - COST) * 100


def stats(rounds):
    if not rounds:
        return None
    nets = [net(r) for r in rounds]
    dv = [net(r) for r in rounds]
    return {"n": len(nets), "win": 100.0 * sum(1 for n in nets if n > 0) / len(nets),
            "avg": sum(nets) / len(nets), "total": sum(nets),
            "day_pos": 100.0 * sum(1 for n in dv if n > 0) / len(nets),
            "worst": min(nets)}


def main():
    days, dkeys = load()
    is_keys, oos_keys = dkeys[:IS_N], dkeys[IS_N:]
    # 基线校准（对照 bt_model_report: 石药S3 OOS +25.7% / +0.63%每笔, 全窗~+14.7%）
    base_is = stats(run(days, is_keys, None, None))
    base_oos = stats(run(days, oos_keys, None, None))
    base_all = stats(run(days, dkeys, None, None))
    print("基线S3校准: IS %+.1f%% | OOS %+.1f%% (笔均%+.2f%%) | 全窗 %+.1f%%  ← 报告参考: IS-17.7/OOS+25.7/全窗+14.7"
          % (base_is["total"], base_oos["total"], base_oos["avg"], base_all["total"]))
    print("\n===== IS 选参（前%d天）=====" % IS_N)
    cands = {}
    for s in (0.005, 0.0075, 0.010):
        for r in (1.25, 1.5, 1.75):
            st = stats(run(days, is_keys, s, r))
            cands[(s, r)] = st
            print("  s=%.2f%% r=%.2f: 合计%+.1f%% (基线%+.1f%%, 差%+.1fpp)"
                  % (s * 100, r, st["total"], base_is["total"], st["total"] - base_is["total"]))
    (bs, br) = max(cands, key=lambda k: cands[k]["total"])
    print("IS最优: s=%.2f%% r=%.2f" % (bs * 100, br))
    print("\n===== OOS 冻结验证（后%d天）=====" % len(oos_keys))
    st_best = stats(run(days, oos_keys, bs, br))
    diff = st_best["total"] - base_oos["total"]
    print("  基线:      合计%+.1f%% 笔均%+.2f%% 胜率%.0f%% 最差%+.1f%%"
          % (base_oos["total"], base_oos["avg"], base_oos["win"], base_oos["worst"]))
    print("  G1强势:    合计%+.1f%% 笔均%+.2f%% 胜率%.0f%% 最差%+.1f%% | vs基线 %+0.1fpp %s"
          % (st_best["total"], st_best["avg"], st_best["win"], st_best["worst"], diff,
             "✅ 达+5pp采纳线" if diff >= 5 else "❌ 未达+5pp"))
    # OOS 邻域表
    print("\nOOS 邻域（较基线pp）:")
    for s in (0.005, 0.0075, 0.010):
        row = []
        for r in (1.25, 1.5, 1.75):
            st = stats(run(days, oos_keys, s, r))
            row.append("%+0.1f" % (st["total"] - base_oos["total"]))
        print("  s=%.2f%%: r1.25=%s r1.5=%s r1.75=%s" % (s * 100, *row))
    # G1的触发结构（OOS）
    g1 = run(days, oos_keys, bs, br)
    tags = defaultdict(list)
    for x in g1:
        tags[x["tag"]].append(net(x))
    print("\nG1 OOS 触发结构:", {k: "%d笔/均%+.2f%%" % (len(v), sum(v) / len(v)) for k, v in tags.items()})
    with open(os.path.join(HERE, "bt_round4b_result.json"), "w") as f:
        json.dump({"base_oos": base_oos["total"], "g1_oos": st_best["total"],
                   "diff": diff, "params": [bs, br], "adopt": diff >= 5}, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
