#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""石药创新(300765) 专属每日做T · 三年分钟线回测 + 参数搜索
规则族（与引擎 bt_always 一致）:
  UB 只正T: 开盘(1-d)低吸买入 → 开盘(1+d)高抛卖出; 14:30兜底进场; 14:55强平; 无止损
  UA 双向  : 先触及哪条线做哪个方向
注意: 3年窗口用【不复权】日线口径对齐 m5（均不复权），除权日附近有噪声属可接受
输出: 全参数对照 + 最优参数的月度一致性/日胜率/最大回撤 + 成本敏感性
"""
import sys, os, json, time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maket as M

SYM, CODE, NAME = "sz300765", "300765", "石药创新"
START = "202309130000"   # 三年
CACHE = os.path.join(HERE, "bt_cache_3y_300765.json")
COST = 0.0012


def fetch_m5_long(sym, start_ts):
    allb = {}
    anchor = ""
    for page in range(150):
        url = ("https://ifzq.gtimg.cn/appstock/app/kline/mkline"
               "?param=%s,m5,%s,320" % (sym, anchor))
        try:
            d = json.loads(M.http_get(url))
        except Exception:
            time.sleep(2)
            d = json.loads(M.http_get(url))
        bars = d.get("data", {}).get(sym, {}).get("m5") or []
        if not bars:
            break
        for b in bars:
            allb[b[0]] = [b[0], float(b[1]), float(b[2]), float(b[3]),
                          float(b[4]), float(b[5])]
        oldest = min(b[0] for b in bars)
        if oldest <= start_ts:
            break
        anchor = oldest
        time.sleep(0.15)
    return [allb[k] for k in sorted(allb) if k >= start_ts]


def load_or_fetch():
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            c = json.load(f)
        if len(c.get("days", {})) > 500:
            print("使用缓存:", CACHE)
            return c
    print("拉取 %s 三年 m5 分钟线（约110页, 2-3分钟）..." % NAME)
    m5 = fetch_m5_long(SYM, START)
    days = defaultdict(list)
    for b in m5:
        days[b[0][:8]].append(b)
    print("共 %d 个交易日, %d 根bar (%s ~ %s)"
          % (len(days), len(m5), m5[0][0], m5[-1][0]))
    c = {"days": {k: v for k, v in days.items()}}
    with open(CACHE, "w") as f:
        json.dump(c, f)
    return c


def replay_always(day_bars, d, mode):
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
        hm, px = b[0][8:], b[4]
        if pos:
            dirn, entry, tgt = pos
            if tgt is not None and ((dirn == 1 and px >= tgt) or (dirn == -1 and px <= tgt)):
                return {"dir": dirn, "entry": entry, "exit": px, "reason": "到达网格目标"}
            if hm >= "1450":
                return {"dir": dirn, "entry": entry, "exit": px, "reason": "尾盘强平"}
            continue
        if mode == "both":
            if px <= lo:
                pos = (1, px, hi)
                continue
            if px >= hi:
                pos = (-1, px, lo)
                continue
        else:
            if px <= lo:
                pos = (1, px, hi)
                continue
        if hm >= "1430":
            pos = (1, px, None)
            continue
    if pos:
        dirn, entry, _ = pos
        return {"dir": dirn, "entry": entry, "exit": day_bars[-1][4], "reason": "日终未平"}
    return None


def net(r, cost):
    return ((r["exit"] / r["entry"] - 1.0) * r["dir"] - cost) * 100


def run_all_days(cache, d, mode, cost=COST):
    rounds = []
    for dkey in sorted(cache["days"]):
        r = replay_always(cache["days"][dkey], d, mode)
        if r:
            rounds.append(dict(date=dkey, **r))
    return rounds


def summarize(rounds, cost=COST):
    nets = [net(r, cost) for r in rounds]
    if not nets:
        return None
    day_sum = defaultdict(float)
    for r, n in zip(rounds, nets):
        day_sum[r["date"]] += n
    days = sorted(day_sum)
    day_vals = [day_sum[k] for k in days]
    pos_days = sum(1 for v in day_vals if v > 0)
    cum = peak = mdd = 0.0
    for v in day_vals:
        cum += v
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return {"n": len(nets), "win": 100.0 * sum(1 for n in nets if n > 0) / len(nets),
            "avg": sum(nets) / len(nets), "total": sum(nets),
            "day_pos": 100.0 * pos_days / len(day_vals), "days": len(day_vals),
            "worst_day": min(day_vals), "best_day": max(day_vals), "mdd": mdd,
            "day_sum": {k: round(v, 3) for k, v in day_sum.items()}}


def main():
    cache = load_or_fetch()
    print("\n===== %s 三年每日做T 参数搜索（成本%.2f%%）=====" % (NAME, COST * 100))
    print("%-12s %4s %6s %9s %10s %8s %9s %9s" %
          ("模式", "回合", "胜率", "单笔均值", "90d式合计", "日为正", "最差日", "累计回撤"))
    results = {}
    for mode, tag in (("long", "UB只正T"), ("both", "UA双向")):
        for d in (0.004, 0.005, 0.006, 0.008, 0.010, 0.012):
            rs = run_all_days(cache, d, mode)
            st = summarize(rs)
            results["%s_%.3f" % (mode, d)] = st
            print("%-12s %4d %5.1f%% %+8.3f%% %+9.1f%% %7.1f%% %+8.2f%% %+8.1f"
                  % ("%s %.1f%%" % (tag, d * 100), st["n"], st["win"], st["avg"],
                     st["total"], st["day_pos"], st["worst_day"], st["mdd"]))
    # 最优 = 合计最高且日为正比例最高
    best_key = max(results, key=lambda k: (results[k]["total"], results[k]["day_pos"]))
    st = results[best_key]
    print("\n>>> 最优参数: %s" % best_key)
    # 成本敏感性
    for c in (0.0012, 0.002, 0.003):
        mode, d = best_key.split("_")
        rs = run_all_days(cache, float(d), mode)
        s2 = summarize(rs, c)
        print("  成本%.2f%%: 单笔%+.3f%% 合计%+.1f%% 日为正%.0f%%"
              % (c * 100, s2["avg"], s2["total"], s2["day_pos"]))
    # 月度一致性
    print("\n最优参数月度拆分:")
    mon = defaultdict(list)
    for k, v in st["day_sum"].items():
        mon[k[:6]].append(v)
    for m in sorted(mon):
        v = mon[m]
        print("  %s-%s: %2d天 当月合计%+7.1f%% 日为正%2.0f%%"
              % (m[:4], m[4:], len(v), sum(v), 100.0 * sum(1 for x in v if x > 0) / len(v)))
    with open(os.path.join(HERE, "bt_3y_result.json"), "w") as f:
        json.dump({"best": best_key, "summary": results}, f, ensure_ascii=False)
    print("\n已存 bt_3y_result.json")


if __name__ == "__main__":
    main()
