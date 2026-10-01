# -*- coding: utf-8 -*-
"""做 T 只在开盘前已经能判断的时机做。网格参数不动（0.6% / 止损 1.5% / 成本 0.22%）。

四条时机都来自本系统已经写过的规则，阈值不新估：
  not_strong  昨日不是 STRONG（原引擎：强势只持有不做 T；每日必做把它盖掉了）
  yday_down   昨日收跌（H3：昨日上涨时低吸更差；分界就是涨跌本身）
  gap_down    今日开盘不高于昨收（9:30 已知，顺均值回归方向）
  tailwind    此前最近 20 笔已完成的做 T，胜率≥50% 且合计收益>0
              （引擎里已有的顺风/逆风监控，这里改成硬门槛；不足 20 笔不做）

训练段只在笔数≥30 且笔均>0 的时机里取日夏普最高的一条。
两折选中同一条，样本外发布为 eligible，并且比「每天都做」的日差也是 eligible，才改引擎。
本脚本不改 maket.py。
用法: python3 bt_opt_timing.py
"""
from __future__ import print_function

import json
import os
from collections import defaultdict

import bt_opt_own as OWN
import publish_gate as PG

D = 0.006
STOP = 0.015
RULES = ("not_strong", "yday_down", "gap_down", "tailwind")


def limit_pct(code):
    return 0.20 if str(code).startswith(("300", "301", "688")) else 0.10


def classify(code, bars):
    c = [float(b[2]) for b in bars]
    n = len(c)
    ma20 = sum(c[-20:]) / 20.0 if n >= 20 else c[-1]
    ret5 = c[-1] / c[-6] - 1 if n >= 6 else 0.0
    ret20 = c[-1] / c[-21] - 1 if n >= 21 else 0.0
    up3 = n >= 4 and all(c[-i] > c[-i - 1] for i in (1, 2, 3))
    ret3 = c[-1] / c[-4] - 1 if n >= 4 else 0.0
    lp = limit_pct(code)
    prev_c = c[-2] if n >= 2 else c[-1]
    hit_limit = abs(c[-1] - round(prev_c * (1 + lp), 2)) < 0.005 * prev_c or c[-1] / prev_c - 1 >= lp - 0.002
    if hit_limit or ret5 >= 0.12 or (up3 and ret3 >= 0.10):
        return "STRONG"
    if c[-1] < ma20 and ret20 < -0.15:
        return "WEAK"
    return "NORMAL"


def day_ctx(uu, d8):
    rows = [b for b in uu["daily"] if b[0].replace("-", "") < d8]
    bars = uu["days"].get(d8)
    if len(rows) < 6 or not bars:
        return None
    prev_c = float(rows[-1][2])
    if prev_c <= 0:
        return None
    return {
        "regime": classify(uu["code"], rows),
        "prev_ret": prev_c / float(rows[-2][2]) - 1.0,
        "gap": float(bars[0][1]) / prev_c - 1.0,
    }


def always_rounds(universe, dates):
    """按日期排序的已完成回合，供 tailwind 只用更早的日期。"""
    out = []
    for date in dates:
        for uu in universe.values():
            bars = uu["days"].get(date)
            if not bars:
                continue
            hit = OWN.replay(bars, D, STOP)
            if not hit or hit[0] <= 0:
                continue
            out.append((date, hit[1] / hit[0] - 1.0 - OWN.COST))
    return out


def tailwind_dates(rounds):
    ok = set()
    prior = []
    by_day = defaultdict(list)
    for date, net in rounds:
        by_day[date].append(net)
    for date in sorted(by_day):
        if len(prior) >= 20:
            window = prior[-20:]
            if sum(1 for x in window if x > 0) / 20.0 >= 0.5 and sum(window) > 0:
                ok.add(date)
        prior.extend(by_day[date])
    return ok


def allowed(rule, ctx, date, good_days):
    if rule == "not_strong":
        return ctx is not None and ctx["regime"] != "STRONG"
    if rule == "yday_down":
        return ctx is not None and ctx["prev_ret"] < 0
    if rule == "gap_down":
        return ctx is not None and ctx["gap"] <= 0
    if rule == "tailwind":
        return date in good_days
    raise KeyError(rule)


def collect(universe, dates, rule, ctxs, good_days):
    by_day = defaultdict(list)
    n = 0
    for date in dates:
        for uu in universe.values():
            bars = uu["days"].get(date)
            if not bars:
                continue
            if not allowed(rule, ctxs.get((uu["code"], date)), date, good_days):
                continue
            hit = OWN.replay(bars, D, STOP)
            if not hit or hit[0] <= 0:
                continue
            by_day[date].append(hit[1] / hit[0] - 1.0 - OWN.COST)
            n += 1
    calendar = [(sum(by_day[d]) / float(len(by_day[d]))) if by_day.get(d) else 0.0
                for d in dates]
    mean = (sum(v for xs in by_day.values() for v in xs) / float(n)) if n else None
    return calendar, n, mean


def rank_rules(universe, dates, ctxs, good_days):
    rows = []
    for rule in RULES:
        cal, n, mean = collect(universe, dates, rule, ctxs, good_days)
        sharpe = PG.ann_sharpe(cal) if len(cal) >= 2 else None
        rows.append({
            "rule": rule, "n": n, "mean": mean,
            "sharpe": sharpe if sharpe is not None and abs(sharpe) != float("inf") else None,
            "selectable": n >= OWN.MIN_IS_TRADES and mean is not None and mean > 0,
        })
    pool = [r for r in rows if r["selectable"]]
    pool.sort(key=lambda r: (r["sharpe"] if r["sharpe"] is not None else -1e9, r["mean"]),
              reverse=True)
    return rows, (pool[0]["rule"] if pool else None)


def main():
    universe = OWN.load()
    dates = sorted(set(d for uu in universe.values() for d in uu["days"]))
    ctxs = {}
    for uu in universe.values():
        for date in uu["days"]:
            ctxs[(uu["code"], date)] = day_ctx(uu, date)
    good_days = tailwind_dates(always_rounds(universe, dates))
    a = OWN.TRAIN1
    b = a + OWN.PURGE
    c = b + OWN.TEST1
    e = c + OWN.PURGE
    folds_idx = [(dates[:a], dates[b:c]), (dates[:c], dates[e:])]
    print("股票 %d，交易日 %d (%s~%s)" % (len(universe), len(dates), dates[0], dates[-1]))

    chosen = []
    fold_rows = []
    for i, (train, test) in enumerate(folds_idx, 1):
        table, winner = rank_rules(universe, train, ctxs, good_days)
        print("\n折%d 训练 %s~%s" % (i, train[0], train[-1]))
        for row in table:
            print("  %-12s n=%4d 笔均 %s  夏普 %s  %s" % (
                row["rule"], row["n"],
                ("%.3f%%" % (100 * row["mean"])) if row["mean"] is not None else "NA",
                ("%.2f" % row["sharpe"]) if row["sharpe"] is not None else "NA",
                "可选" if row["selectable"] else "不选"))
        rule = winner
        chosen.append(rule)
        if rule is None:
            print("  没有笔均>0 的时机，该折不启用择时")
            fold_rows.append({"rule": None, "train": table, "oos": None, "base": None,
                              "test": [test[0], test[-1]]})
            continue
        cal, n, mean = collect(universe, test, rule, ctxs, good_days)
        base_cal, bn, bmean = collect(universe, test, "not_strong", ctxs, good_days)
        # 每天都做：用一条恒真规则重放。直接不算 filter。
        always_cal, an, amean = always_on(universe, test)
        print("  选中 %s → 样本外 %d 笔 笔均 %s | 每天都做 %d 笔 笔均 %s" % (
            rule, n, ("%.3f%%" % (100 * mean)) if mean is not None else "NA",
            an, ("%.3f%%" % (100 * amean)) if amean is not None else "NA"))
        fold_rows.append({
            "rule": rule, "train": table,
            "oos": {"returns": cal, "n_trades": n, "mean": mean},
            "base": {"returns": always_cal, "n_trades": an, "mean": amean},
            "test": [test[0], test[-1]],
        })

    same = len(set(chosen)) == 1 and chosen[0] is not None
    parts = [r["oos"] for r in fold_rows if r["oos"]]
    bases = [r["base"] for r in fold_rows if r["base"]]
    strategy = PG.judge_strategy(parts) if len(parts) == 2 else {
        "status": "pending", "reasons": ["没有两折都选中的时机"], "n_trades": 0,
        "sharpe_ann": None, "max_drawdown": None, "published": False}
    diffs = []
    if same:
        for row in fold_rows:
            diffs.extend(s - b for s, b in zip(row["oos"]["returns"], row["base"]["returns"]))
    diff = PG.judge_diff_family([
        {"name": "timed_minus_always", "diffs": diffs, "expected_sign": 1}
    ]) if diffs else {"status": "pending", "reasons": ["无日差"], "published": False}
    adopt = same and strategy["status"] == "eligible" and diff["status"] == "eligible"
    print("\n两折同一时机: %s %s" % (same, chosen))
    print("择时样本外: %s 成交 %s 夏普 %s 回撤 %s" % (
        strategy["status"], strategy.get("n_trades"), strategy.get("sharpe_ann"),
        strategy.get("max_drawdown")))
    for reason in strategy.get("reasons") or []:
        print("  - %s" % reason)
    print("相对每天都做: %s" % diff["status"])
    for reason in diff.get("reasons") or []:
        print("  - %s" % reason)
    print("改引擎: %s" % ("允许，时机=%s" % chosen[0] if adopt else "不允许"))
    json.dump(PG._jsonable({
        "generated": "2026-09-25",
        "rules": list(RULES),
        "folds": [{
            "rule": r["rule"], "test": r["test"], "train": r["train"],
            "oos_n": None if not r["oos"] else r["oos"]["n_trades"],
            "oos_mean": None if not r["oos"] else r["oos"]["mean"],
            "always_n": None if not r["base"] else r["base"]["n_trades"],
            "always_mean": None if not r["base"] else r["base"]["mean"],
        } for r in fold_rows],
        "strategy_gate": strategy,
        "diff_gate": diff,
        "adopt": adopt,
        "chosen": chosen[0] if adopt else None,
    }), open(os.path.join(OWN.HERE, "bt_opt_timing_result.json"), "w"),
        ensure_ascii=False, indent=1)


def always_on(universe, dates):
    by_day = defaultdict(list)
    n = 0
    for date in dates:
        for uu in universe.values():
            bars = uu["days"].get(date)
            if not bars:
                continue
            hit = OWN.replay(bars, D, STOP)
            if not hit or hit[0] <= 0:
                continue
            by_day[date].append(hit[1] / hit[0] - 1.0 - OWN.COST)
            n += 1
    calendar = [(sum(by_day[d]) / float(len(by_day[d]))) if by_day.get(d) else 0.0
                for d in dates]
    mean = (sum(v for xs in by_day.values() for v in xs) / float(n)) if n else None
    return calendar, n, mean


if __name__ == "__main__":
    main()
