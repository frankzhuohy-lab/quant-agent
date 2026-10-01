#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无条件每日必做一回合的机械高抛低吸回测（用户目标: 每天都交易, 不设触发条件）
规则（每票每交易日恰好一买一卖，基于底仓）:
  网格线 = 当日开盘价 ± d
  UA 双向: 先触及 -d 线→正T(买入, 目标+d线); 先触及 +d 线→反T(底仓先卖, 目标-d线)
  UB 只正T: 触及 -d 线→买入, 目标 +d 线（不做反T, 90日回测已证反T是亏损腿）
  兜底: 14:30 仍未进场 → 以现价买入(正T), 保证当天必有交易
  退出: 到目标价 或 14:55 强平（无止损, 纯网格; EOD兜住单日风险）
用法: python3 bt_always.py
"""
import sys, os, json
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maket as M

COST = 0.0012
GRIDS = [0.005, 0.008, 0.012]


def load_cache():
    fp = os.path.join(HERE, "bt_cache_90d.json")
    with open(fp) as f:
        return json.load(f)


def replay_always(day_bars, d, mode):
    """无条件每日一回合。返回 dict(dir, entry, exit, reason) 或 None(数据不足)"""
    if len(day_bars) < 8:
        return None
    o = day_bars[0][1]
    if not o:
        return None
    lo, hi = o * (1 - d), o * (1 + d)
    start_i = next((i for i, b in enumerate(day_bars) if b[0][8:] >= "0945"), None)
    if start_i is None:
        return None
    pos = None
    for i in range(start_i, len(day_bars)):
        b = day_bars[i]
        hm = b[0][8:]
        px = b[4]
        if pos:
            dirn, entry, tgt = pos
            exit_px = None
            if tgt is not None and ((dirn == 1 and px >= tgt) or (dirn == -1 and px <= tgt)):
                exit_px = (px, "到达网格目标")
            elif hm >= "1450":
                exit_px = (px, "尾盘强平")
            if exit_px:
                return {"dir": dirn, "entry": entry, "exit": exit_px[0], "reason": exit_px[1]}
            continue
        if mode == "both":
            if px <= lo:
                pos = (1, px, hi)
                continue
            if px >= hi:
                pos = (-1, px, lo)
                continue
        else:  # long-only
            if px <= lo:
                pos = (1, px, hi)
                continue
        if hm >= "1430":  # 兜底: 保证每天必有交易
            pos = (1, px, None)
            continue
    if pos:
        dirn, entry, _ = pos
        return {"dir": dirn, "entry": entry, "exit": day_bars[-1][4], "reason": "日终未平"}
    return None


def run(cache, d, mode):
    rounds = []
    for date in cache["bt_dates"]:
        dkey = date.replace("-", "")
        for s in cache["pool"]:
            sym = s["symbol"]
            if sym not in cache["daily"] or sym not in cache["m5"]:
                continue
            day_bars = [b for b in cache["m5"][sym] if b[0].startswith(dkey)]
            r = replay_always(day_bars, d, mode)
            if r:
                rounds.append(dict(date=date, code=s["code"], name=s["name"], **r))
    nets = [(r["gross"] if False else (r["exit"] / r["entry"] - 1.0) * r["dir"] - COST) * 100
            for r in rounds]
    if not nets:
        print("无回合")
        return
    wins = sum(1 for n in nets if n > 0)
    day_sum = defaultdict(float)
    for r, n in zip(rounds, nets):
        day_sum[r["date"]] += n
    active = list(day_sum.values())
    pos_days = sum(1 for v in active if v > 0)
    tag = "UA双向" if mode == "both" else "UB只正T"
    print("\n[%s d=%.1f%%] 回合%d 胜率%.1f%% 单笔%+.3f%% 合计%+.1f%% | "
          "股票·日 %d 个 其中当日合计为正 %d (%.0f%%)"
          % (tag, d * 100, len(nets), 100.0 * wins / len(nets),
             sum(nets) / len(nets), sum(nets), len(active), pos_days,
             100.0 * pos_days / len(active)))
    from collections import Counter
    for reason, cnt in Counter(r["reason"] for r in rounds).most_common():
        rn = [(r["exit"] / r["entry"] - 1.0) * r["dir"] * 100 - COST * 100
              for r in rounds if r["reason"] == reason]
        print("    %-12s %4d笔 均值%+.3f%%" % (reason, cnt, sum(rn) / len(rn)))
    per = defaultdict(list)
    for r, n in zip(rounds, nets):
        per[r["name"]].append(n)
    best = sorted(per.items(), key=lambda kv: -sum(kv[1]))[:3]
    worst = sorted(per.items(), key=lambda kv: sum(kv[1]))[:3]
    print("    最好3只: " + " | ".join("%s %+0.1f%%(n=%d)" % (k, sum(v), len(v)) for k, v in best))
    print("    最差3只: " + " | ".join("%s %+0.1f%%(n=%d)" % (k, sum(v), len(v)) for k, v in worst))
    return rounds


def main():
    cache = load_cache()
    print("区间: %s ~ %s（%d个交易日, 双边成本%.2f%%）"
          % (cache["bt_dates"][0], cache["bt_dates"][-1], len(cache["bt_dates"]), COST * 100))
    out = {}
    for d in GRIDS:
        for mode in ("both", "long"):
            out["%s_%.3f" % (mode, d)] = run(cache, d, mode)
    with open(os.path.join(HERE, "bt_always_result.json"), "w") as f:
        json.dump({k: len(v) if v else 0 for k, v in out.items()}, f)


if __name__ == "__main__":
    main()
