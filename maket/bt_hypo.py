# -*- coding: utf-8 -*-
"""路径B第一步：假设检验（不是调参数）。
H1 日内均值回归是否存在（5分钟收益自相关 / 方差比 / 偏离VWAP后的回归概率）
H2 开盘锚网格结构（触线概率 / 目标完成率 / 分月PnL——直接看regime依赖）
H3 开盘前(9:45)可观测变量能否预测"回归日 vs 趋势日"（IS选特征→OOS验证）
H4 目标完成的日内时间分布
重放口径与 bt_always.py 完全一致: bar close 触价、14:50起强平、兜底14:30、双边成本0.12%。
用法: python3 bt_hypo.py [--pooled]
"""
import sys
import os
import json
import math
import datetime
import statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
COST = 0.0012
D_GRID = 0.006
IS_DAYS = 60          # IS=前60个交易日(与walk-forward一致), 其余OOS
END_DATE = "20260911"  # 研究样本截止（当天盘中数据不完整不入样）


def in_sample_date(d8):
    return d8 <= END_DATE


# ---------- 数据 ----------
def load_universe():
    """返回 {sym: {"name","code","days":{date:bars},"daily":[...]}}"""
    out = {}
    fp3y = os.path.join(HERE, "bt_cache_3y_300765.json")
    if os.path.exists(fp3y):
        d = json.load(open(fp3y))["days"]
        out["sz300765"] = {"name": "石药创新", "code": "300765",
                           "days": {k: v for k, v in d.items() if len(v) >= 8}}
    fp = os.path.join(HERE, "bt_cache_pooled.json")
    if os.path.exists(fp):
        d = json.load(open(fp))["data"]
        for sym, v in d.items():
            days = {}
            for b in v["bars"]:
                days.setdefault(b[0][:8], []).append(b)
            days = {k: v for k, v in days.items() if len(v) >= 8}
            if sym in out:
                out[sym]["days"].update(days)
            else:
                out[sym] = {"name": v["name"], "code": v["code"], "days": days}
    fpd = os.path.join(HERE, "bt_cache_90d.json")
    if os.path.exists(fpd):
        daily = json.load(open(fpd))["daily"]
        for sym, bars in daily.items():
            if sym in out:
                out[sym]["daily"] = bars
    for sym in list(out):
        if "daily" not in out[sym]:
            print("WARN 无日线特征数据, 剔除", sym)
            del out[sym]
    return out


# ---------- 重放（与bt_always.py口径一致） ----------
def replay_always(day_bars, d=D_GRID):
    """返回 dict(entry, exit, reason, entry_hm, grid_hit) 或 None"""
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
        px = b[2]          # m5字段序 [t,open,close,high,low,vol]
        if pos:
            entry, tgt, entry_hm = pos[0], pos[1], pos[2]
            if tgt is not None and px >= tgt:
                return {"entry": entry, "exit": px, "reason": "目标",
                        "entry_hm": entry_hm, "grid_hit": True}
            if hm >= "1450":
                return {"entry": entry, "exit": px, "reason": "强平",
                        "entry_hm": pos[2], "grid_hit": pos[3]}
            continue
        if px <= lo:
            pos = (px, hi, hm, True)
            continue
        if hm >= "1430":
            pos = (px, None, hm, False)
    if pos:
        return {"entry": pos[0], "exit": day_bars[-1][4], "reason": "日终",
                "entry_hm": pos[2], "grid_hit": pos[3]}
    return None


def net_pnl(r):
    return (r["exit"] / r["entry"] - 1.0) - COST


# ---------- 特征（全部9:45前可观测） ----------
def day_features(sym, date, day_bars, daily):
    o = day_bars[0][1]
    if not o:
        return None
    closes = [float(b[2]) for b in daily]
    dates = [b[0] for b in daily]
    d8 = date.replace("-", "")
    idx = next((i for i, x in enumerate(dates) if x.replace("-", "") == d8), None)
    if idx is None or idx < 21:
        return None
    prev_c = closes[idx - 1]
    gap = o / prev_c - 1.0
    prev_ret = prev_c / closes[idx - 2] - 1.0
    ret5 = closes[idx - 1] / closes[idx - 6] - 1.0
    ret20 = closes[idx - 1] / closes[idx - 21] - 1.0
    trs = [max(float(daily[i][3]) - float(daily[i][4]),
               abs(float(daily[i][3]) - float(daily[i - 1][2])),
               abs(float(daily[i][4]) - float(daily[i - 1][2])))
           for i in range(idx - 13, idx)]
    atr = sum(trs) / len(trs) / prev_c
    early = day_bars[:3]                     # 0935/0940/0945 三根
    early_move = early[-1][2] / o - 1.0
    early_range = (max(b[3] for b in early) - min(b[4] for b in early)) / o
    dt = datetime.date(int(d8[:4]), int(d8[4:6]), int(d8[6:]))
    return {"gap": gap, "prev_ret": prev_ret, "ret5": ret5, "ret20": ret20,
            "atr_pct": atr, "early_move": early_move, "early_range": early_range,
            "dow": dt.weekday()}


def relative_early_vol(sym_days, daily, date):
    """当日头3根量 / 过去20个同股交易日头3根量中位数（9:45前可观测）"""
    d8 = date.replace("-", "")
    prior = sorted(k for k in sym_days if k < d8)[-20:]
    if not prior or d8 not in sym_days:
        return None
    cur = sum(b[5] for b in sym_days[d8][:3])
    meds = sorted(sum(b[5] for b in sym_days[k][:3]) for k in prior)
    med = meds[len(meds) // 2] if meds else 0
    return cur / med if med else None


# ---------- H1: 日内收益结构 ----------
def h1_tests(days):
    """days: {date: bars}"""
    rets_by_day, autocorrs = [], []
    all_r1, blocks2, blocks4 = [], [], []
    for date in sorted(days):
        bars = sorted(days[date], key=lambda b: b[0])
        if len(bars) < 20:
            continue
        px = [b[2] for b in bars]
        rs = [math.log(px[i] / px[i - 1]) for i in range(1, len(px))
              if bars[i - 1][0][8:] != "1130"]        # 跳过午休边界
        if len(rs) < 20:
            continue
        rets_by_day.append(rs)
        m, v = sum(rs) / len(rs), st.variance(rs)
        if v > 0:
            cov = sum((rs[i] - m) * (rs[i - 1] - m) for i in range(1, len(rs)))
            autocorrs.append(cov / (len(rs) - 1) / v)
        all_r1.extend(rs)
        for i in range(1, len(rs) - 1):
            blocks2.append(rs[i] + rs[i + 1])
        for i in range(1, len(rs) - 3):
            blocks4.append(sum(rs[i:i + 4]))
    v1 = st.variance(all_r1)
    vr2 = st.variance(blocks2) / (2 * v1) if v1 else None
    vr4 = st.variance(blocks4) / (4 * v1) if v1 else None
    n = len(autocorrs)
    mu = sum(autocorrs) / n
    se = st.stdev(autocorrs) / math.sqrt(n) if n > 2 else None
    t = mu / se if se else None
    return {"n_days": n, "lag1_ac_mean": mu, "lag1_ac_t": t,
            "vr2": vr2, "vr4": vr4,
            "ac_by_half": [sum(autocorrs[:n // 2]) / (n // 2),
                           sum(autocorrs[n // 2:]) / (n - n // 2)] if n > 10 else None}


# ---------- H2/H3: 结果变量与按月结构 ----------
def outcome_table(sym, u):
    days, daily = u["days"], u["daily"]
    rows = []
    for date in sorted(days):
        if not in_sample_date(date):
            continue
        bars = sorted(days[date], key=lambda b: b[0])
        r = replay_always(bars)
        if not r:
            continue
        f = day_features(sym, date, bars, daily)
        if f is None:
            continue
        f["rel_early_vol"] = relative_early_vol(days, daily, date)
        f["date"] = date
        f["pnl"] = net_pnl(r) * 100
        f["reason"] = r["reason"]
        f["entry_hm"] = r["entry_hm"]
        f["grid_hit"] = r["grid_hit"]
        rows.append(f)
    return rows


def monthly_structure(rows):
    by_m = defaultdict(list)
    for r in rows:
        by_m[r["date"][:6]].append(r)
    out = {}
    for m in sorted(by_m):
        rs = by_m[m]
        grid = [r for r in rs if r["grid_hit"]]
        out[m] = {"n": len(rs), "grid_n": len(grid),
                  "pnl_mean": sum(r["pnl"] for r in rs) / len(rs),
                  "win": sum(1 for r in rs if r["pnl"] > 0) / len(rs),
                  "grid_completion": (sum(1 for r in grid if r["reason"] == "目标")
                                      / len(grid)) if grid else None}
    return out


# ---------- H3: 特征→结果的IS/OOS检验 ----------
FEATURES = ["gap", "prev_ret", "ret5", "ret20", "atr_pct", "early_move",
            "early_range", "rel_early_vol"]


def tercile_contrast(rows, feat):
    """按特征三分位分组，返回 (高-低)组均值差 + 按日块自助t"""
    vals = sorted(r[feat] for r in rows if r.get(feat) is not None)
    if len(vals) < 30:
        return None
    q1, q2 = vals[len(vals) // 3], vals[2 * len(vals) // 3]
    lo = [r for r in rows if r.get(feat) is not None and r[feat] <= q1]
    hi = [r for r in rows if r.get(feat) is not None and r[feat] > q2]
    if not lo or not hi:
        return None
    m_lo, m_hi = sum(r["pnl"] for r in lo) / len(lo), sum(r["pnl"] for r in hi) / len(hi)
    # 日块自助（同日各票相关 → 按整日重采样）
    def day_map(rs):
        d = defaultdict(list)
        for r in rs:
            d[r["date"]].append(r["pnl"])
        return {k: sum(v) / len(v) for k, v in d.items()}
    dl, dh = day_map(lo), day_map(hi)
    keys = sorted(set(dl) | set(dh))
    diffs = [dh.get(k, 0.0) - dl.get(k, 0.0) for k in keys]
    mu = sum(diffs) / len(diffs)
    se = st.stdev(diffs) / math.sqrt(len(diffs)) if len(diffs) > 2 else None
    return {"m_lo": m_lo, "m_hi": m_hi, "diff": m_hi - m_lo,
            "t": mu / se if se else None, "n_lo": len(lo), "n_hi": len(hi)}


# ---------- H4: 完成时间分布 ----------
def timing_hist(rows):
    """触线入场的日内时间分布（判断交易集中在哪个时段）"""
    buckets = defaultdict(int)
    for r in rows:
        if not r["grid_hit"]:
            continue
        hm = r["entry_hm"]
        if hm < "1000":
            buckets["0945-1000进场"] += 1
        elif hm < "1030":
            buckets["1000-1030"] += 1
        elif hm < "1100":
            buckets["1030-1100"] += 1
        elif hm < "1130":
            buckets["1100-1130"] += 1
        elif hm < "1400":
            buckets["1300-1400"] += 1
        else:
            buckets["1400-1430"] += 1
    return dict(sorted(buckets.items()))


# ---------- 主流程 ----------
def run_stock(sym, u, all_rows):
    rows = all_rows[sym]
    if len(rows) < 30:
        print("样本不足", sym, len(rows))
        return
    h1 = h1_tests(u["days"])
    mon = monthly_structure(rows)
    print("\n===== %s %s（%d个stock-day, %s ~ %s）====="
          % (sym, u["name"], len(rows), rows[0]["date"], rows[-1]["date"]))
    print("H1 日内收益结构: lag1自相关=%.4f (t=%.2f, n=%d日)  VR2=%.3f VR4=%.3f"
          % (h1["lag1_ac_mean"], h1["lag1_ac_t"] or 0, h1["n_days"],
             h1["vr2"] or 0, h1["vr4"] or 0))
    if h1["ac_by_half"]:
        print("    前后半段自相关: %.4f / %.4f" % tuple(h1["ac_by_half"]))
    print("H2 分月结构 (d=0.6%%, 成本0.12%%):")
    for m, v in mon.items():
        print("    %s n=%2d 触线%d 笔均%+.3f%% 胜率%2.0f%% 目标完成率%s"
              % (m, v["n"], v["grid_n"], v["pnl_mean"], v["win"] * 100,
                 ("%.0f%%" % (v["grid_completion"] * 100))
                 if v["grid_completion"] is not None else "-"))
    is_rows, oos_rows = rows[:IS_DAYS], rows[IS_DAYS:]
    print("H3 IS前%d天特征检验 (t=高-低三分位日块自助):" % IS_DAYS)
    sig = {}
    for ft in FEATURES:
        c = tercile_contrast(is_rows, ft)
        if c and c["t"] is not None:
            flag = " ★" if abs(c["t"]) >= 2.5 else ""
            print("    %-13s 低%.3f%% 高%.3f%% diff=%+.3f%% t=%.2f%s"
                  % (ft, c["m_lo"], c["m_hi"], c["diff"], c["t"], flag))
            if abs(c["t"]) >= 2.5:
                sig[ft] = c
    if sig:
        print("    —— OOS验证（同一特征、IS分位点）——")
        for ft, c_is in sig.items():
            c = tercile_contrast(oos_rows, ft)
            if c:
                same = (c["diff"] > 0) == (c_is["diff"] > 0)
                print("    %-13s OOS diff=%+.3f%% t=%.2f 方向%s"
                      % (ft, c["diff"], c["t"] or 0, "一致✓" if same else "相反✗"))
    print("H4 触线入场时间分布:", timing_hist(rows))


def main():
    pooled = "--pooled" in sys.argv
    u = load_universe()
    all_rows = {sym: outcome_table(sym, uu) for sym, uu in u.items()}
    if pooled:
        # 池化: 每个stock-day一个观测, t推断用日块自助
        for sym in sorted(all_rows):
            run_stock(sym, u[sym], all_rows)
        # 市场日层面: 同日全部票的平均结果（检验"日层面regime"）
        day_rows = defaultdict(list)
        for sym, rows in all_rows.items():
            for r in rows:
                r2 = dict(r)
                r2["_sym"] = sym
                day_rows[r["date"]].append(r2)
        pooled_rows = []
        for date in sorted(day_rows):
            rs = day_rows[date]
            f = dict(rs[0])
            f["pnl"] = sum(r["pnl"] for r in rs) / len(rs)
            f["date"] = date
            pooled_rows.append(f)
        print("\n===== 市场日层面（%d天, 当日14票均PnL）=====" % len(pooled_rows))
        is_r, oos_r = pooled_rows[:IS_DAYS], pooled_rows[IS_DAYS:]
        for ft in FEATURES:
            c = tercile_contrast(is_r, ft)
            if c and c["t"] is not None:
                flag = " ★" if abs(c["t"]) >= 2.5 else ""
                print("    IS %-13s 低%.3f%% 高%.3f%% diff=%+.3f%% t=%.2f%s"
                      % (ft, c["m_lo"], c["m_hi"], c["diff"], c["t"], flag))
                if abs(c["t"]) >= 2.5:
                    c2 = tercile_contrast(oos_r, ft)
                    if c2:
                        same = (c2["diff"] > 0) == (c["diff"] > 0)
                        print("        OOS %-11s diff=%+.3f%% t=%.2f 方向%s"
                              % (ft, c2["diff"], c2["t"] or 0,
                                 "一致✓" if same else "相反✗"))
    else:
        for sym in sorted(all_rows):
            run_stock(sym, u[sym], all_rows)


if __name__ == "__main__":
    main()
