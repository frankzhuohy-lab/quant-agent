# -*- coding: utf-8 -*-
"""路径B第二步：假设→模型（消融式构建 + walk-forward）。

假设来源（bt_hypo.py 检验结论）:
  H1 5分钟收益自相关显著为正(延续)      → 触线后需"企稳回升"确认才进场（confirm）
  H3 昨日大涨次日低吸更差(13/13票同向)  → 昨日涨幅≥c 的日子关闭低吸（gate）
  尾部批判（用户+文档回撤-18.3%）        → 入场价-1.5%日度止损（stop）

策略族（全部: 正T网格 d=0.6%、双边成本0.12%、9:45后、14:50强平）:
  gate    = 昨日涨幅>=5% 当日不做（无兜底）
  confirm = 触线bar要求 低点<=线 且 收阳（跌下去收不回来的bar不接）
  stop    = bar收盘 <= 入价*0.985 止损
  S0      = 全关 = 现行引擎ALWAYS模式（含14:30兜底）
  其余S1..S7 = 三个开关的组合（见CONFIGS），兜底仅在开关全关时存在（模型模式:无信号不交易）

流程: IS前60天选最优配置 → 冻结 → OOS报告全部8配置（选型诚实性）。
用法: python3 bt_model_r1.py
"""
import os
import json
import statistics as st
from collections import defaultdict

import bt_hypo as H

HERE = os.path.dirname(os.path.abspath(__file__))
COST = 0.0012
D = 0.006
GATE_C = 0.05        # 昨日涨幅门控阈值（事前声明: >=5%视为单日过热）
STOP_PCT = 0.015     # 与引擎 STOP_PCT 一致
IS_DAYS = 60

CONFIGS = [
    ("S0_always",        False, False, False),
    ("S1_gate",          True,  False, False),
    ("S2_confirm",       False, True,  False),
    ("S3_stop",          False, False, True),
    ("S4_gate_confirm",  True,  True,  False),
    ("S5_gate_stop",     True,  False, True),
    ("S6_confirm_stop",  False, True,  True),
    ("S7_full",          True,  True,  True),
]


def load_universe():
    import bt_hypo as H
    return H.load_universe()


def replay(day_bars, prev_ret, gate, confirm, stop):
    """返回 dict(entry, exit, reason) 或 None(当日未交易)"""
    if len(day_bars) < 8:
        return None
    o = day_bars[0][1]
    if not o:
        return None
    if gate and prev_ret is not None and prev_ret >= GATE_C:
        return None                       # 门控关闭当日
    lo, hi = o * (1 - D), o * (1 + D)
    start_i = next((i for i, b in enumerate(day_bars) if b[0][8:] >= "0945"), None)
    if start_i is None:
        return None
    legacy = not (gate or confirm or stop)   # S0 = 现行引擎行为(含兜底)
    pos = None
    for i in range(start_i, len(day_bars)):
        b = day_bars[i]
        hm = b[0][8:]
        op, px, hi_, lo_ = b[1], b[2], b[3], b[4]   # m5: [t,open,close,high,low,vol]
        if pos:
            entry, tgt = pos
            if stop and px <= entry * (1 - STOP_PCT):
                return {"entry": entry, "exit": px, "reason": "止损"}
            if tgt is not None and px >= tgt:
                return {"entry": entry, "exit": px, "reason": "目标"}
            if hm >= "1450":
                return {"entry": entry, "exit": px, "reason": "强平"}
            continue
        touched_low = lo_ <= lo           # 当根最低价触及网格线
        if confirm:
            enter = touched_low and px > op    # 触线且当根收阳(企稳回升), 以收盘价成交
        else:
            enter = px <= lo                    # S0口径: 收盘触线(与bt_always一致)
        if enter:
            pos = (px, hi)
            continue
        if legacy and hm >= "1430":      # 兜底仅S0保留
            pos = (px, None)
    if pos:
        return {"entry": pos[0], "exit": day_bars[-1][2], "reason": "日终"}
    return None


def run_config(u, cfg_name, gate, confirm, stop):
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
            d8 = date
            idx = next((i for i, x in enumerate(dates_all)
                        if x.replace("-", "") == d8), None)
            if idx is None or idx < 2:
                continue
            prev_ret = (float(daily[idx - 1][2]) / float(daily[idx - 2][2]) - 1.0)
            r = replay(bars, prev_ret, gate, confirm, stop)
            if r:
                r.update({"date": date, "sym": sym, "name": uu["name"],
                          "pnl": (r["exit"] / r["entry"] - 1) * 100 - COST * 100})
                rounds.append(r)
    return rounds


def summarize(rounds, tag):
    if not rounds:
        return {"tag": tag, "n": 0}
    pn = [r["pnl"] for r in rounds]
    day_sum = defaultdict(float)
    for r in rounds:
        day_sum[r["date"]] += r["pnl"]
    active = list(day_sum.values())
    worst = min(r["pnl"] for r in rounds)
    return {"tag": tag, "n": len(rounds),
            "win": sum(1 for x in pn if x > 0) / len(pn),
            "mean": sum(pn) / len(pn), "total": sum(pn),
            "pos_days": sum(1 for v in active if v > 0) / len(active),
            "worst_round": worst}


def print_sum(s, days_n):
    if s["n"] == 0:
        print("  %-18s 无交易" % s["tag"])
        return
    print("  %-18s 回合%4d 胜率%2.0f%% 笔均%+.3f%% 合计%+7.1f%% 日正比例%2.0f%% 最差单笔%+.2f%%"
          % (s["tag"], s["n"], s["win"] * 100, s["mean"], s["total"],
             s["pos_days"] * 100, s["worst_round"]))


def main():
    u = load_universe()
    all_rounds = {}
    for name, g, c, s in CONFIGS:
        all_rounds[name] = run_config(u, name, g, c, s)
    # 按日期切IS/OOS（用S0的日期集合界定交易历）
    cal = sorted({r["date"] for r in all_rounds["S0_always"]})
    cut = cal[IS_DAYS - 1] if len(cal) >= IS_DAYS else cal[-1]

    print("样本: %d个交易日 (%s ~ %s), IS前%d天(至%s) | d=0.6%% 成本0.12%%"
          % (len(cal), cal[0], cal[-1], IS_DAYS, cut))
    for phase, sel in (("IS(选型)", lambda r: r["date"] <= cut),
                       ("OOS(冻结验证)", lambda r: r["date"] > cut)):
        print("\n== %s ==" % phase)
        for name, *_ in CONFIGS:
            s = summarize([r for r in all_rounds[name] if sel(r)], name)
            print_sum(s, len(cal))
        print("  —— 石药创新(300765)专项 ——")
        for name, *_ in CONFIGS:
            s = summarize([r for r in all_rounds[name]
                           if sel(r) and r["sym"] == "sz300765"], name)
            print_sum(s, len(cal))

    # 离场原因分布（OOS, S7与S0对照）
    for name in ("S0_always", "S7_full"):
        oos = [r for r in all_rounds[name] if r["date"] > cut]
        cnt = defaultdict(list)
        for r in oos:
            cnt[r["reason"]].append(r["pnl"])
        print("\n[%s OOS离场原因]" % name)
        for k, v in sorted(cnt.items(), key=lambda kv: -len(kv[1])):
            print("    %-4s %4d笔 均值%+.3f%%" % (k, len(v), sum(v) / len(v)))

    with open(os.path.join(HERE, "bt_model_r1_result.json"), "w") as f:
        json.dump({k: summarize(v, k) for k, v in all_rounds.items()}, f,
                  ensure_ascii=False, indent=1)




# ---------- R2: 趋势门控（与R443同源假设: 只在上升趋势日低吸） ----------
def run_config_trend(u, cfg_name, gate_trend, gate_prev, confirm, stop):
    import statistics
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
            closes = [float(b[2]) for b in daily]
            ma20 = sum(closes[idx - 20:idx]) / 20.0
            trend_on = closes[idx - 1] > ma20          # 昨收在MA20上(趋势门)
            prev_ret = closes[idx - 1] / closes[idx - 2] - 1.0
            if gate_trend and not trend_on:
                continue
            if gate_prev and prev_ret >= GATE_C:
                continue
            r = replay(bars, prev_ret, False, confirm, stop)
            if r:
                r.update({"date": date, "sym": sym, "name": uu["name"],
                          "pnl": (r["exit"] / r["entry"] - 1) * 100 - COST * 100})
                rounds.append(r)
    return rounds


T_CONFIGS = [
    ("T0_trend",           True,  False, False, False),
    ("T1_trend_stop",      True,  False, False, True),
    ("T2_trend_prev_stop", True,  True,  False, True),
    ("T3_trend_conf_stop", True,  False, True,  True),
    ("T4_all",             True,  True,  True,  True),
]


def main2():
    u = load_universe()
    res = {}
    for name, gt, gp, cf, st_ in T_CONFIGS:
        res[name] = run_config_trend(u, name, gt, gp, cf, st_)
    res["S0_always"] = run_config(u, "S0", False, False, False)
    res["S6_confirm_stop"] = run_config(u, "S6", False, True, True)
    cal = sorted({r["date"] for r in res["S0_always"]})
    cut = cal[IS_DAYS - 1]
    print("R2 趋势门控消融 | 样本 %d天, IS前%d天(至%s)" % (len(cal), IS_DAYS, cut))
    for phase, sel in (("IS", lambda r: r["date"] <= cut),
                       ("OOS", lambda r: r["date"] > cut)):
        print("\n== %s ==" % phase)
        for name in res:
            s = summarize([r for r in res[name] if sel(r)], name)
            print_sum(s, len(cal))
        print("  —— 石药专项 ——")
        for name in res:
            s = summarize([r for r in res[name]
                           if sel(r) and r["sym"] == "sz300765"], name)
            print_sum(s, len(cal))
    with open(os.path.join(HERE, "bt_model_r2_result.json"), "w") as f:
        json.dump({k: summarize(v, k) for k, v in res.items()}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main2()  # R2趋势门控消融; R1的prev_ret消融结果已在bt_model_r1_result.json存档
