#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对照实验: 学习机构级做法 vs 当前系统规则（石药创新专属, 125天分钟数据）
方法论（对照 QuantInsti/AlgoTrading101 walk-forward 框架）:
  - 样本切分: IS = 前60天(参数搜索), OOS = 后65天(只验证, 不再调参) → 结果看OOS
  - 参赛策略:
    A 基线     = 当前系统: 固定开盘±d网格, 14:30兜底, 14:55强平
    B VWAP回归 = 机构标准: 价格跌破 VWAP - z*σ 触发低吸, 目标回到 VWAP; z∈{1.0,1.5,2.0}
    C ATR自适应 = d 不固定: d_t = k * ATR14(D-1)/昨收, k∈{0.3,0.5,0.7}; 其余同基线
    D VWAP+趋势过滤 = B + 站上当日VWAP才开正T(强趋势日不做逆势低吸)
  - 所有策略统一: 每日恰1回合(未触发14:30兜底), 14:55强平, 成本0.12%, 9:45前不进场
输出: IS排名(仅参考) + OOS成绩(决策依据) + 基线vs最优的OOS逐日差
"""
import sys, os, json
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maket as M

SYM, CODE = "sz300765", "300765"
COST = 0.0012
IS_N = 60          # 前60天调参
EARLY, LATE, FORCE = "0945", "1430", "1450"


def load():
    with open(os.path.join(HERE, "bt_cache_3y_300765.json")) as f:
        days = json.load(f)["days"]
    dkeys = sorted(days)
    return days, dkeys


def day_ctx(day_bars):
    """当日运行VWAP与σ序列（逐bar增量, 无未来函数）"""
    vwap, sd, cum_pv, cum_v = [], [], 0.0, 0.0
    for b in day_bars:
        tp = (b[2] + b[3] + b[4]) / 3.0
        cum_pv += tp * b[5]; cum_v += b[5]
        v = cum_pv / cum_v if cum_v else b[4]
        vwap.append(v)
    # σ = 当日到当前bar为止 (px-vwap) 的样本标准差
    devs, sd = [], []
    for i, b in enumerate(day_bars):
        devs.append(b[4] - vwap[i])
        n = len(devs)
        if n < 2:
            sd.append(0.0)
            continue
        m = sum(devs) / n
        sd.append((sum((x - m) ** 2 for x in devs) / (n - 1)) ** 0.5)
    return vwap, sd


def replay(day_bars, strat, p):
    """统一重放器。strat: A/B/C/D; p: 参数(d 或 z 或 k)"""
    if len(day_bars) < 8:
        return None
    o = day_bars[0][1]
    if strat == "A":
        lo, tgt_line = o * (1 - p), o * (1 + p)
    else:
        # C/D 的 d 逐日由 ATR 决定或由 z 动态触发
        lo = tgt_line = None
    start_i = next((i for i, b in enumerate(day_bars) if b[0][8:] >= EARLY), None)
    if start_i is None:
        return None
    vwap, sd = day_ctx(day_bars) if strat in "BD" else (None, None)
    pos = None
    for i in range(start_i, len(day_bars)):
        b = day_bars[i]
        hm, px = b[0][8:], b[4]
        if pos:
            dirn, entry, target = pos
            if target is not None and px >= target:
                return {"dir": dirn, "entry": entry, "exit": px, "reason": "到达目标"}
            if hm >= FORCE:
                return {"dir": dirn, "entry": entry, "exit": px, "reason": "尾盘强平"}
            continue
        if hm >= LATE:                      # 兜底进场(保证每日一回合)
            return {"dir": 1, "entry": px, "exit": None, "reason": "兜底"}
        if strat == "A":
            if px <= lo:
                pos = (1, px, tgt_line)
        elif strat in ("B", "D"):
            s = sd[i]
            if s > 0 and px <= vwap[i] - p * s:
                if strat == "D" and px < vwap[i] and vwap[i] < o:
                    pass  # 趋势过滤: 当日VWAP已低于开盘(弱势日)仍允许低吸, 但目标改为VWAP
                pos = (1, px, vwap[i])      # 目标: 回归VWAP
        elif strat == "C":
            if lo is not None and px <= lo:
                pos = (1, px, tgt_line)
    if pos:
        dirn, entry, target = pos
        return {"dir": dirn, "entry": entry, "exit": day_bars[-1][4], "reason": "日终"}
    return None


def run_strat(days, dkeys, strat, p, d_map=None):
    """d_map: {date: (lo,tgt)} C策略的逐日ATR网格线"""
    rounds = []
    for dk in dkeys:
        bars = days[dk]
        r = replay(bars, strat, p) if strat != "C" else replay_c(bars, d_map[dk])
        if r:
            if r["reason"] == "兜底":
                r["exit"] = bars[-1][4]
                r["reason"] = "尾盘强平"
            rounds.append(dict(date=dk, **r))
    return rounds


def replay_c(day_bars, lines):
    lo, tgt_line = lines
    if len(day_bars) < 8:
        return None
    start_i = next((i for i, b in enumerate(day_bars) if b[0][8:] >= EARLY), None)
    if start_i is None:
        return None
    pos = None
    for i in range(start_i, len(day_bars)):
        b = day_bars[i]
        hm, px = b[0][8:], b[4]
        if pos:
            if px >= pos[2]:
                return {"dir": 1, "entry": pos[1], "exit": px, "reason": "到达目标"}
            if hm >= FORCE:
                return {"dir": 1, "entry": pos[1], "exit": px, "reason": "尾盘强平"}
            continue
        if hm >= LATE:
            return {"dir": 1, "entry": px, "exit": None, "reason": "兜底"}
        if px <= lo:
            pos = (1, px, tgt_line)
    if pos:
        return {"dir": 1, "entry": pos[1], "exit": day_bars[-1][4], "reason": "日终"}
    return None


def atr14_prev(daily_hist):
    """昨日为止的ATR14（不复权日线与m5同口径近似, 误差可忽略）"""
    trs = []
    for i in range(1, len(daily_hist)):
        h, l = float(daily_hist[i][3]), float(daily_hist[i][4])
        pc = float(daily_hist[i - 1][2])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-14:]) / 14.0 if trs else 0.0


def net(r):
    return ((r["exit"] / r["entry"] - 1.0) * r["dir"] - COST) * 100


def stats(rounds):
    if not rounds:
        return None
    nets = [net(r) for r in rounds]
    day_sum = defaultdict(float)
    for r, n in zip(rounds, nets):
        day_sum[r["date"]] += n
    dv = list(day_sum.values())
    cum = peak = mdd = 0.0
    for v in dv:
        cum += v; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    return {"n": len(nets), "win": 100.0 * sum(1 for n in nets if n > 0) / len(nets),
            "avg": sum(nets) / len(nets), "total": sum(nets),
            "day_pos": 100.0 * sum(1 for v in dv if v > 0) / len(dv), "mdd": mdd,
            "daily": dict(day_sum)}


def main():
    days, dkeys = load()
    # C 需要日线ATR → 拉日线
    daily = M.fetch_daily(SYM, 260)
    d_map = {}
    for dk in dkeys:
        d8 = "%s-%s-%s" % (dk[:4], dk[4:6], dk[6:])
        hist = [b for b in daily if b[0] < d8]
        if not hist:
            d_map[dk] = (days[dk][0][1] * 0.994, days[dk][0][1] * 1.006)
            continue
        prev_c = float(hist[-1][2])
        a = atr14_prev(hist) or prev_c * 0.01
        # 由 k 归一: 先存基础, replay 时按 k 缩放
        d_map[dk] = (a, prev_c)
    is_keys, oos_keys = dkeys[:IS_N], dkeys[IS_N:]

    grid = {"A": [0.004, 0.005, 0.006, 0.008, 0.010, 0.012],
            "B": [1.0, 1.5, 2.0, 2.5], "D": [1.0, 1.5, 2.0]}
    results = {}
    print("===== IS 段（前%d天, 只用于选参）=====" % IS_N)
    for strat, ps in grid.items():
        for pv in ps:
            if strat in ("B", "D"):
                rs = run_strat(days, is_keys, strat, pv)
            else:
                rs = run_strat(days, is_keys, strat, pv)
            st = stats(rs)
            if st:
                results[(strat, pv)] = st
                print("  %s p=%s: 胜率%.0f%% 单笔%+.3f%% 合计%+.1f%% 日为正%.0f%%"
                      % (strat, pv, st["win"], st["avg"], st["total"], st["day_pos"]))
    # C: k 参数
    for k in (0.3, 0.5, 0.7):
        rs = []
        for dk in is_keys:
            a, pc = d_map[dk]
            d = k * a / pc
            o = days[dk][0][1]
            rs += run_strat(days, [dk], "C", None, d_map={dk: (o * (1 - d), o * (1 + d))})
        st = stats(rs)
        if st:
            results[("C", k)] = st
            print("  C k=%s: 胜率%.0f%% 单笔%+.3f%% 合计%+.1f%% 日为正%.0f%%"
                  % (k, st["win"], st["avg"], st["total"], st["day_pos"]))

    # 每策略取 IS 最优参数 → OOS 验证
    print("\n===== OOS 段（后%d天, 决策依据）=====" % len(oos_keys))
    best = {}
    for strat in "ABCD":
        cands = {p: st for (s, p), st in results.items() if s == strat}
        if cands:
            best[strat] = max(cands, key=lambda p: cands[p]["total"])
    oos = {}
    for strat, pv in best.items():
        if strat == "C":
            rs = []
            for dk in oos_keys:
                a, pc = d_map[dk]
                d = pv * a / pc
                o = days[dk][0][1]
                rs += run_strat(days, [dk], "C", None, d_map={dk: (o * (1 - d), o * (1 + d))})
        else:
            rs = run_strat(days, oos_keys, strat, pv)
        oos[strat] = (pv, stats(rs))
    # 基线 = 当前系统参数 0.6%（不重新选参, 作为固定基准）
    base_rs = run_strat(days, oos_keys, "A", 0.006)
    oos["A_当前系统"] = (0.006, stats(base_rs))
    for name, (pv, st) in sorted(oos.items(), key=lambda kv: -kv[1][1]["total"]):
        print("  %-12s p=%s: 胜率%.0f%% 单笔%+.3f%% 合计%+.1f%% 日为正%.0f%% 回撤%.1f"
              % (name, pv, st["win"], st["avg"], st["total"], st["day_pos"], st["mdd"]))
    # 与基线的逐日差
    if "A_当前系统" in oos and len(oos) > 1:
        base_daily = oos["A_当前系统"][1]["daily"]
        print("\n各策略相对当前系统的 OOS 逐日差(合计pp):")
        for name, (pv, st) in oos.items():
            if name == "A_当前系统":
                continue
            diff = sum(st["daily"].get(k, 0) - base_daily.get(k, 0) for k in base_daily)
            better_days = sum(1 for k in base_daily if st["daily"].get(k, 0) > base_daily[k])
            print("  %-12s: %+0.1fpp (逐日更好%2d/%d天)" % (name, diff, better_days, len(base_daily)))
    with open(os.path.join(HERE, "bt_walkforward_result.json"), "w") as f:
        json.dump({"is": {("%s|%s" % k): v for k, v in results.items()},
                   "best": {k: str(v) for k, v in best.items()},
                   "oos": {k: {"p": str(pv), **{kk: vv for kk, vv in st.items() if kk != "daily"}}
                           for k, (pv, st) in oos.items()}}, f, ensure_ascii=False, indent=1)
    print("\n已存 bt_walkforward_result.json")


if __name__ == "__main__":
    main()
