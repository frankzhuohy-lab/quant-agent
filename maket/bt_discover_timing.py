# -*- coding: utf-8 -*-
"""从开盘前可见的数据里自己找做 T 时机。不使用系统里已有的四条规则。

网格固定：开盘 −0.6% 买入、+0.6% 卖出、止损 1.5%、成本 0.22%。
每笔交易只附带 9:45 之前就知道的 10 个量：

  prev_ret    昨日涨跌
  ret5        5 日涨跌
  ret20       20 日涨跌
  gap         今开 / 昨收 − 1
  dist_ma     昨收 / MA20 − 1
  atr         ATR14 / 昨收
  yday_range  昨日振幅
  close_loc   昨收在昨日高低点中的位置
  vol_ratio   昨日量 / 前 20 日均量
  down_streak 截至昨日的连跌天数

训练段对每个量取中位数，分成「低于等于中位数才做」和「高于中位数才做」。
20 条一起做 BH-FDR（q=0.10），只保留笔数≥40、笔均>0、而且 q 通过的。
其中日夏普最高的一条冻结，中位数用训练段的数，检验段不再重算。
两折必须选中同一个量、同一侧，样本外发布 eligible，且相对「有信号就做」的日差也 eligible，才改引擎。
本脚本不改 maket.py。
用法: python3 bt_discover_timing.py
"""
from __future__ import print_function

import json
import os
import statistics
from collections import defaultdict

import bt_opt_own as OWN
import publish_gate as PG

FEATURES = (
    "prev_ret", "ret5", "ret20", "gap", "dist_ma",
    "atr", "yday_range", "close_loc", "vol_ratio", "down_streak",
)
MIN_N = 40
MIN_DAYS = 20


def features(uu, d8):
    rows = [b for b in uu["daily"] if b[0].replace("-", "") < d8]
    bars = uu["days"].get(d8)
    if len(rows) < 22 or not bars:
        return None
    o = [float(b[1]) for b in rows]
    c = [float(b[2]) for b in rows]
    h = [float(b[3]) for b in rows]
    lo = [float(b[4]) for b in rows]
    v = [float(b[5]) for b in rows]
    if c[-1] <= 0 or o[-1] <= 0:
        return None
    trs = []
    for i in range(len(rows) - 14, len(rows)):
        pc = c[i - 1]
        trs.append(max(h[i] - lo[i], abs(h[i] - pc), abs(lo[i] - pc)))
    span = h[-1] - lo[-1]
    streak = 0
    for i in range(len(c) - 1, 0, -1):
        if c[i] < c[i - 1]:
            streak += 1
        else:
            break
    vol_base = sum(v[-21:-1]) / 20.0
    op = float(bars[0][1])
    if op <= 0 or vol_base <= 0:
        return None
    return {
        "prev_ret": c[-1] / c[-2] - 1.0,
        "ret5": c[-1] / c[-6] - 1.0,
        "ret20": c[-1] / c[-21] - 1.0,
        "gap": op / c[-1] - 1.0,
        "dist_ma": c[-1] / (sum(c[-20:]) / 20.0) - 1.0,
        "atr": (sum(trs) / 14.0) / c[-1],
        "yday_range": span / c[-1],
        "close_loc": ((c[-1] - lo[-1]) / span) if span > 0 else 0.5,
        "vol_ratio": v[-1] / vol_base,
        "down_streak": float(min(streak, 5)),
    }


def build_panel(universe, dates):
    rows = []
    for date in dates:
        for uu in universe.values():
            bars = uu["days"].get(date)
            if not bars:
                continue
            feat = features(uu, date)
            if feat is None:
                continue
            hit = OWN.replay(bars, 0.006, 0.015)
            if not hit or hit[0] <= 0:
                continue
            feat["date"] = date
            feat["net"] = hit[1] / hit[0] - 1.0 - OWN.COST
            rows.append(feat)
    return rows


def day_means(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["date"]].append(r["net"])
    days = sorted(by)
    return [sum(by[d]) / float(len(by[d])) for d in days], len(rows)


def one_sided_pos(nw):
    """H1: 日均收益 > 0。做不到的检验 p 记为 1。"""
    if nw["p"] is None or nw["mean"] is None or nw["n"] < MIN_DAYS:
        return 1.0
    if nw["mean"] <= 0:
        return 1.0
    return min(1.0, nw["p"] / 2.0)


def evaluate_rule(panel, feat, side, cutoff):
    picked = [r for r in panel
              if (r[feat] <= cutoff if side == "low" else r[feat] > cutoff)]
    means, n = day_means(picked)
    nw = PG.newey_west_mean(means)
    return picked, n, nw


def discover(train):
    tests = []
    pvals = []
    for feat in FEATURES:
        vals = [r[feat] for r in train]
        cutoff = statistics.median(vals)
        for side in ("low", "high"):
            picked, n, nw = evaluate_rule(train, feat, side, cutoff)
            tests.append({
                "feat": feat, "side": side, "cutoff": cutoff,
                "n": n, "n_days": nw["n"], "mean": nw["mean"],
                "t": nw["t"], "p_two": nw["p"],
            })
            pvals.append(one_sided_pos(nw) if n >= MIN_N else 1.0)
    fdr = PG.bh_fdr(pvals, q=PG.FDR_Q)
    survivors = []
    for row, adj, p in zip(tests, fdr, pvals):
        row["p"] = p
        row["q"] = adj["q_adj"]
        row["pass"] = bool(adj["reject"] and row["mean"] is not None and row["mean"] > 0
                           and row["n"] >= MIN_N)
        if row["pass"]:
            cal = calendar(train, row["feat"], row["side"], row["cutoff"])
            row["sharpe"] = PG.ann_sharpe(cal)
            survivors.append(row)
    survivors.sort(key=lambda r: (
        r["sharpe"] if r["sharpe"] is not None and r["sharpe"] != float("inf") else -1e9
    ), reverse=True)
    return tests, (survivors[0] if survivors else None)


def calendar(panel, feat, side, cutoff):
    dates = sorted(set(r["date"] for r in panel))
    picked = [r for r in panel
              if (r[feat] <= cutoff if side == "low" else r[feat] > cutoff)]
    by = defaultdict(list)
    for r in picked:
        by[r["date"]].append(r["net"])
    return [(sum(by[d]) / float(len(by[d]))) if d in by else 0.0 for d in dates]


def always_calendar(panel):
    dates = sorted(set(r["date"] for r in panel))
    by = defaultdict(list)
    for r in panel:
        by[r["date"]].append(r["net"])
    return [(sum(by[d]) / float(len(by[d]))) if d in by else 0.0 for d in dates]


def main():
    universe = OWN.load()
    dates = sorted(set(d for uu in universe.values() for d in uu["days"]))
    panel = build_panel(universe, dates)
    print("有特征且成交的回合 %d，交易日 %d (%s~%s)" % (
        len(panel), len(dates), dates[0], dates[-1]))
    a, b = OWN.TRAIN1, OWN.TRAIN1 + OWN.PURGE
    c = b + OWN.TEST1
    e = c + OWN.PURGE
    windows = [(dates[:a], dates[b:c]), (dates[:c], dates[e:])]
    chosen = []
    fold_out = []
    oos_parts = []
    diffs = []
    for i, (train_d, test_d) in enumerate(windows, 1):
        train = [r for r in panel if r["date"] in set(train_d)]
        test = [r for r in panel if r["date"] in set(test_d)]
        table, winner = discover(train)
        print("\n折%d 训练回合 %d" % (i, len(train)))
        ranked = sorted(table, key=lambda r: (r["mean"] is not None, r["mean"] or -9), reverse=True)
        for row in ranked[:6]:
            print("  %s %-5s 切点 %+.4f  n=%4d 笔均 %s  q=%s %s" % (
                row["feat"], row["side"], row["cutoff"], row["n"],
                ("%.3f%%" % (100 * row["mean"])) if row["mean"] is not None else "NA",
                ("%.3f" % row["q"]) if row["q"] is not None else "NA",
                "通过" if row["pass"] else ""))
        if winner is None:
            print("  没有通过 FDR 的时机")
            chosen.append(None)
            fold_out.append({"winner": None, "top": ranked[:6]})
            continue
        print("  冻结 %s %s，切点 %.6f" % (winner["feat"], winner["side"], winner["cutoff"]))
        chosen.append((winner["feat"], winner["side"]))
        cal = calendar(test, winner["feat"], winner["side"], winner["cutoff"])
        base = always_calendar(test)
        picked, n, nw = evaluate_rule(test, winner["feat"], winner["side"], winner["cutoff"])
        print("  样本外 %d 笔 笔均 %s | 有信号就做 %d 笔 笔均 %s" % (
            n, ("%.3f%%" % (100 * nw["mean"])) if nw["mean"] is not None else "NA",
            len(test),
            ("%.3f%%" % (100 * sum(r["net"] for r in test) / len(test))) if test else "NA"))
        oos_parts.append({"returns": cal, "n_trades": n})
        # 日差按检验段日历。calendar() 只用了有成交的日期，两边日期集可能不同。
        # 用检验段全部交易日对齐。
        diffs.extend(aligned_diff(test, test_d, winner))
        fold_out.append({
            "winner": {k: winner[k] for k in ("feat", "side", "cutoff", "n", "mean", "q", "sharpe")},
            "oos_n": n, "oos_mean": nw["mean"],
        })

    same = len(set(chosen)) == 1 and chosen[0] is not None
    strategy = PG.judge_strategy(oos_parts) if len(oos_parts) == 2 and same else {
        "status": "pending", "reasons": ["两折没有选中同一侧的同一个量"],
        "n_trades": 0, "sharpe_ann": None, "max_drawdown": None, "published": False}
    diff = PG.judge_diff_family([
        {"name": "discovered_minus_always", "diffs": diffs, "expected_sign": 1}
    ]) if same and diffs else {"status": "pending", "reasons": ["无可用日差"], "published": False}
    adopt = same and strategy["status"] == "eligible" and diff["status"] == "eligible"
    print("\n两折同一时机: %s %s" % (same, chosen))
    print("样本外: %s 成交 %s 夏普 %s 回撤 %s" % (
        strategy["status"], strategy.get("n_trades"), strategy.get("sharpe_ann"),
        strategy.get("max_drawdown")))
    for reason in strategy.get("reasons") or []:
        print("  - %s" % reason)
    print("相对有信号就做: %s" % diff["status"])
    for reason in diff.get("reasons") or []:
        print("  - %s" % reason)
    print("改引擎: %s" % ("允许" if adopt else "不允许"))
    json.dump(PG._jsonable({
        "generated": "2026-09-26",
        "features": list(FEATURES),
        "folds": fold_out,
        "chosen": list(chosen[0]) if same else None,
        "strategy_gate": strategy,
        "diff_gate": diff,
        "adopt": adopt,
    }), open(os.path.join(OWN.HERE, "bt_discover_timing_result.json"), "w"),
        ensure_ascii=False, indent=1)


def aligned_diff(test, test_dates, winner):
    picked = [r for r in test if (
        r[winner["feat"]] <= winner["cutoff"] if winner["side"] == "low"
        else r[winner["feat"]] > winner["cutoff"])]
    by_p, by_a = defaultdict(list), defaultdict(list)
    for r in picked:
        by_p[r["date"]].append(r["net"])
    for r in test:
        by_a[r["date"]].append(r["net"])
    out = []
    for d in test_dates:
        p = (sum(by_p[d]) / float(len(by_p[d]))) if d in by_p else 0.0
        a = (sum(by_a[d]) / float(len(by_a[d]))) if d in by_a else 0.0
        out.append(p - a)
    return out


if __name__ == "__main__":
    main()
