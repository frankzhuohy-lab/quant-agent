# -*- coding: utf-8 -*-
"""价位架构对决（预注册实验）: 单锚网格 vs 多维因子价位。
动机: 旧walk-forward在m5字段序bug上运行, "简单锚优于多维价位"的结论作废, 需修正口径重赛。
四种"低/高"的定义（全部只用决策时点可得数据）:
  A 开盘锚:   买=今开×0.994,       卖=今开×1.006            （现行引擎）
  B 枢轴区:   昨日OHLC枢轴P/R1/S1/R2与昨收±0.6×ATR对齐 → 低吸区上沿进, 高抛区下沿出（经典多维）
  C VWAP回归: 买=现价较当日VWAP低0.6%, 卖=进场时VWAP（机构标准均值回归）
  D 多因子共振: 下方参照位{S1,昨收,今开-1×ATR,开盘30分VWAP}中≥2个聚集(0.15×ATR内)处才算"低";
              上方{R1,昨收,今开+1×ATR,开盘30分VWAP}同法定"高"; 无共振不交易
统一: 止损入场价-1.5%, 9:45后, 14:50强平, 双边成本0.12%, 每日至多1回合, 无兜底。
C/D 需要 VWAP → 10:00后才能进（开盘30分VWAP ready）。
流程: IS前60天只做记录, OOS统一冻结评估; 石药专项 + 13票池化。
用法: python3 bt_levels.py
"""
import os
import json
import statistics as st
from collections import defaultdict

import bt_hypo as H

HERE = os.path.dirname(os.path.abspath(__file__))
COST = 0.0012
STOP = 0.015
IS_DAYS = 60
D = 0.006


def day_ctx(daily, idx):
    """昨日OHLC → 枢轴/ATR/参照位"""
    o = float(daily[idx - 1][1]); c = float(daily[idx - 1][2])
    h = float(daily[idx - 1][3]); l = float(daily[idx - 1][4])
    p = (h + l + c) / 3.0
    atrs = []
    for i in range(idx - 14, idx):
        ho, cl = float(daily[i][3]), float(daily[i][2])
        lo_, pc = float(daily[i][4]), float(daily[i - 1][2])
        atrs.append(max(ho - lo_, abs(ho - pc), abs(lo_ - pc)))
    atr = sum(atrs) / len(atrs)
    return {"p": p, "r1": 2 * p - l, "s1": 2 * p - h, "r2": p + (h - l),
            "s2": p - (h - l), "atr": atr, "pc": c, "po": o}


def levels_for(ctx, o_today, bars_upto, mode):
    """返回 (entry_line, target_line) 或 None。bars_upto: 到当前的当日bars"""
    s1, r1, pc, atr = ctx["s1"], ctx["r1"], ctx["pc"], ctx["atr"]
    if mode == "A":
        return o_today * (1 - D), o_today * (1 + D)
    if mode == "B":
        # 低吸区上沿: min(S1, 收盘×(1-0.8%)) 与 0.95收盘下托 (同build_zones几何)
        hi_k = max(0.6 * atr / ctx["pc"], 0.008)
        buy_hi = min(s1, pc * (1 - hi_k))
        buy_hi = max(buy_hi, pc * 0.95)
        sell_lo = max(r1, pc * (1 + hi_k))
        sell_lo = min(sell_lo, pc * 1.055)
        return buy_hi, sell_lo
    if mode == "C":
        pv, vv = 0.0, 0.0
        for b in bars_upto:
            pv += b[2] * b[5]; vv += b[5]
        if vv <= 0:
            return None
        vwap = pv / vv
        return vwap * (1 - D), vwap
    if mode == "D":
        pv, vv = 0.0, 0.0
        for b in bars_upto[:7]:          # 开盘30分钟VWAP (0935-1005前7根)
            pv += b[2] * b[5]; vv += b[5]
        vwap30 = pv / vv if vv > 0 else None
        lows = [s1, pc, o_today - atr] + ([vwap30] if vwap30 else [])
        highs = [r1, pc, o_today + atr] + ([vwap30] if vwap30 else [])
        tol = 0.15 * atr * o_today

        def cluster(levels):
            best, best_n = None, -1
            for L in levels:
                n = sum(1 for M in levels if abs(M - L) <= tol)
                if n > best_n or (n == best_n and best is not None and L > best):
                    best, best_n = L, n
            return best, best_n

        lo_ref, n_lo = cluster(lows)
        hi_ref, n_hi = cluster(highs)
        if n_lo < 2 or lo_ref is None:
            return None                   # 无下方共振=没有可信的"低"
        tgt = hi_ref if (n_hi >= 2 and hi_ref > lo_ref * 1.004) \
            else o_today * (1 + D)
        return lo_ref, tgt
    raise ValueError(mode)


def replay(bars, ctx, mode):
    if len(bars) < 20:
        return None
    o_today = bars[0][1]
    if not o_today:
        return None
    start = next((i for i, b in enumerate(bars) if b[0][8:] >= "0945"), None)
    if start is None:
        return None
    if mode in ("C", "D"):
        if start < 7:
            start = 7                     # 开盘30分VWAP ready
    pos = None
    for i in range(start, len(bars)):
        b = bars[i]
        hm, px = b[0][8:], b[2]
        if pos:
            entry, tgt = pos
            if px <= entry * (1 - STOP):
                return {"entry": entry, "exit": px, "reason": "止损"}
            if px >= tgt:
                return {"entry": entry, "exit": px, "reason": "目标"}
            if hm >= "1450":
                return {"entry": entry, "exit": px, "reason": "强平"}
            continue
        lines = levels_for(ctx, o_today, bars[:i + 1], mode)
        if not lines:
            continue
        lo, hi = lines
        if lo <= 0:
            continue
        if px <= lo:
            pos = (px, hi)
    if pos:
        return {"entry": pos[0], "exit": bars[-1][2], "reason": "日终"}
    return None


def run(u, mode):
    rounds = []
    for sym, uu in u.items():
        days, daily = uu["days"], uu["daily"]
        dates_all = [b[0] for b in daily]
        for date in sorted(days):
            if not H.in_sample_date(date):
                continue
            bars = sorted(days[date], key=lambda b: b[0])
            if len(bars) < 20:
                continue
            idx = next((i for i, x in enumerate(dates_all)
                        if x.replace("-", "") == date), None)
            if idx is None or idx < 21:
                continue
            r = replay(bars, day_ctx(daily, idx), mode)
            if r:
                r.update(date=date, sym=sym, pnl=(r["exit"] / r["entry"] - 1) * 100 - COST * 100)
                rounds.append(r)
    return rounds


def show(rounds, sel, tag):
    rs = [r for r in rounds if sel(r)]
    if not rs:
        print("  %-14s 无交易" % tag)
        return
    pn = [r["pnl"] for r in rs]
    ds = defaultdict(float)
    for r in rs:
        ds[r["date"]] += r["pnl"]
    act = list(ds.values())
    print("  %-14s 回合%4d 胜率%2.0f%% 笔均%+.3f%% 合计%+8.1f%% 最差%+.2f%% 日正%2.0f%%"
          % (tag, len(pn), 100 * sum(1 for x in pn if x > 0) / len(pn),
             sum(pn) / len(pn), sum(pn), min(pn),
             100 * sum(1 for v in act if v > 0) / len(act)))


def main():
    u = H.load_universe()
    modes = ["A", "B", "C", "D"]
    res = {m: run(u, m) for m in modes}
    cal = sorted({r["date"] for r in res["A"]})
    cut = cal[IS_DAYS - 1]
    print("价位架构对决 | %d天 (%s~%s), IS前%d天 | 统一止损-1.5%% 成本0.12%%"
          % (len(cal), cal[0], cal[-1], IS_DAYS))
    for phase, sel in (("IS", lambda d: d <= cut), ("OOS", lambda d: d > cut)):
        print("\n== %s 池化(14票) ==" % phase)
        for m in modes:
            show(res[m], lambda r, s=sel: s(r["date"]), m)
        print("  -- 石药专项 --")
        for m in modes:
            show([r for r in res[m] if r["sym"] == "sz300765"],
                 lambda r, s=sel: s(r["date"]), m)
    with open(os.path.join(HERE, "bt_levels_result.json"), "w") as f:
        json.dump({m: {"n": len(v)} for m, v in res.items()}, f)


if __name__ == "__main__":
    main()
