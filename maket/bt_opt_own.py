# -*- coding: utf-8 -*-
"""用历史 5 分钟线优化本系统自己的做T模型，再在样本外重跑。

只搜现行模型的三个旋钮，不引入新因子：
  d     开盘锚半宽  0.4% / 0.6% / 0.8% / 1.0%
  stop  入场价止损  1.0% / 1.5% / 2.0%
  screen  收盘后硬筛选开/关（阈值与 screen_candidates.analyze 相同，不调权重）

现行对照：d=0.6%，stop=1.5%，screen=开。成本 0.22%（0.12% + 0.1% 滑点）。
重放对齐 ~/.maket/maket.py 的每日必做路径：9:45 后 5 分钟收盘价触及开盘*(1-d) 才买，
目标开盘*(1+d)，止损优先序与引擎相同（同一根先看目标再看止损），14:55 后强平，无 14:30 兜底。
5 分钟收盘价不是 60 秒轮询，bar 内先触目标还是先触止损无法分辨。

样本外纪律：
  两个扩展窗。折1 训练前 50 个交易日、空 5 日、检验随后 35 日；
  折2 训练到折1 检验末、再空 5 日、检验剩余交易日。
  每个训练窗里，只在笔数≥30 且笔均收益>0 的参数里取日夏普最高者。
  没有正的训练窗时，该折维持现行对照。
  两折选出同一组、且不同于对照、且 publish_gate 对拼接样本外给出 eligible、
  并且样本外日差（优化组−对照）也是 eligible，才允许改引擎。
  本脚本只写结果，不改 maket.py。

用法: python3 bt_opt_own.py
"""
from __future__ import print_function

import json
import os
from collections import defaultdict

import publish_gate as PG

HERE = os.path.dirname(os.path.abspath(__file__))
END = "20260911"
COST = 0.0022
MIN_BARS = 40
MIN_IS_TRADES = 30
PURGE = 5
TRAIN1 = 50
TEST1 = 35

D_GRID = (0.004, 0.006, 0.008, 0.010)
STOP_GRID = (0.010, 0.015, 0.020)
BASELINE = (0.006, 0.015, True)


def load():
    pooled = json.load(open(os.path.join(HERE, "bt_cache_pooled.json")))["data"]
    daily = json.load(open(os.path.join(HERE, "bt_cache_90d.json")))["daily"]
    out = {}
    for sym, v in pooled.items():
        if sym not in daily:
            continue
        days = defaultdict(list)
        for b in v["bars"]:
            days[b[0][:8]].append(b)
        days = {d: sorted(bs, key=lambda x: x[0]) for d, bs in days.items()
                if d <= END and len(bs) >= MIN_BARS}
        if not days:
            continue
        out[sym] = {"name": v.get("name"), "code": v.get("code"),
                    "days": days, "daily": daily[sym]}
    return out


def screen_pass(daily, d8):
    rows = [b for b in daily if b[0].replace("-", "") < d8]
    if len(rows) < 21:
        return False
    closes = [float(b[2]) for b in rows]
    c, o = closes[-1], float(rows[-1][1])
    if c <= 0 or o <= 0:
        return False
    ma20 = sum(closes[-20:]) / 20.0
    ret20 = c / closes[-21] - 1.0
    chg = c / closes[-2] - 1.0
    trs = []
    for i in range(len(rows) - 14, len(rows)):
        h, lo = float(rows[i][3]), float(rows[i][4])
        pc = float(rows[i - 1][2])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    atr = (sum(trs) / 14.0) / c
    dist = c / max(float(b[3]) for b in rows[-20:]) - 1.0
    if not (c > ma20 and ret20 > 0 and chg < 0.05 and 0.012 <= atr <= 0.065):
        return False
    return (c < o) or (-0.10 <= dist <= -0.02)


def replay(day_bars, d, stop):
    o = float(day_bars[0][1])
    if o <= 0:
        return None
    buy, tgt = o * (1.0 - d), o * (1.0 + d)
    start = next((i for i, b in enumerate(day_bars) if b[0][8:] >= "0945"), None)
    if start is None:
        return None
    entry = None
    for i in range(start, len(day_bars)):
        b = day_bars[i]
        hm, px = b[0][8:], float(b[2])
        if entry is not None:
            if px >= tgt:
                return entry, px, "目标"
            if px <= entry * (1.0 - stop):
                return entry, px, "止损"
            if hm >= "1455":
                return entry, px, "强平"
            continue
        if px <= buy:
            entry = px
    if entry is not None:
        return entry, float(day_bars[-1][2]), "强平"
    return None


def collect(universe, dates, d, stop, screen):
    """返回 (按日的回合收益列表, 笔数, 原因计数)。收益已扣成本。"""
    by_day = defaultdict(list)
    reasons = defaultdict(int)
    for uu in universe.values():
        for date in dates:
            bars = uu["days"].get(date)
            if not bars:
                continue
            if screen and not screen_pass(uu["daily"], date):
                continue
            hit = replay(bars, d, stop)
            if not hit:
                continue
            entry, exit_px, reason = hit
            if entry <= 0:
                continue
            net = exit_px / entry - 1.0 - COST
            by_day[date].append(net)
            reasons[reason] += 1
    ordered = [by_day[d8] for d8 in dates if by_day.get(d8)]
    n = sum(len(xs) for xs in ordered)
    return ordered, n, dict(reasons)


def daily_means(ordered):
    return [sum(xs) / float(len(xs)) for xs in ordered]


def is_rank(universe, dates, grid):
    ranked = []
    for cfg in grid:
        ordered, n, reasons = collect(universe, dates, *cfg)
        means = daily_means(ordered)
        sharpe = PG.ann_sharpe(means) if len(means) >= 2 else None
        mean = (sum(r for xs in ordered for r in xs) / float(n)) if n else None
        selectable = n >= MIN_IS_TRADES and mean is not None and mean > 0.0
        ranked.append({
            "d": cfg[0], "stop": cfg[1], "screen": cfg[2],
            "n": n, "mean": mean,
            "sharpe": sharpe if sharpe is not None and sharpe != float("inf")
            and sharpe != float("-inf") else None,
            "sharpe_infinite": bool(sharpe is not None and sharpe == float("inf")),
            "reasons": reasons, "selectable": selectable,
        })
    pool = [r for r in ranked if r["selectable"]]
    pool.sort(key=lambda r: (
        1 if r["sharpe_infinite"] else 0,
        r["sharpe"] if r["sharpe"] is not None else -1e9,
        r["mean"],
    ), reverse=True)
    return ranked, (pool[0] if pool else None)


def cfg_of(row):
    return (row["d"], row["stop"], row["screen"])


def fold_result(universe, train, test, grid):
    ranked, winner = is_rank(universe, train, grid)
    chosen = cfg_of(winner) if winner else BASELINE
    used_baseline = winner is None
    ordered, n, reasons = collect(universe, test, *chosen)
    return {
        "train_n_days": len(train),
        "test_n_days": len(test),
        "is_table": ranked,
        "selected": {"d": chosen[0], "stop": chosen[1], "screen": chosen[2],
                     "fallback_baseline": used_baseline},
        "oos": {"returns": daily_means(ordered), "n_trades": n, "reasons": reasons,
                "mean": (sum(r for xs in ordered for r in xs) / float(n)) if n else None},
    }


def baseline_oos(universe, test):
    ordered, n, reasons = collect(universe, test, *BASELINE)
    return {"returns": daily_means(ordered), "n_trades": n, "reasons": reasons,
            "mean": (sum(r for xs in ordered for r in xs) / float(n)) if n else None}


def main():
    universe = load()
    dates = sorted(set(d for uu in universe.values() for d in uu["days"]))
    need = TRAIN1 + PURGE + TEST1 + PURGE + 15
    if len(dates) < need:
        raise SystemExit("交易日 %d 不足 %d" % (len(dates), need))
    a, b = TRAIN1, TRAIN1 + PURGE
    c = b + TEST1
    d, e = c, c + PURGE
    folds_idx = [(dates[:a], dates[b:c]), (dates[:c], dates[e:])]
    grid = [(d_, s, scr) for d_ in D_GRID for s in STOP_GRID for scr in (False, True)]
    print("股票 %d，交易日 %d (%s ~ %s)，参数 %d 组，成本 %.2f%%"
          % (len(universe), len(dates), dates[0], dates[-1], len(grid), COST * 100))

    folds = []
    base_parts = []
    selections = []
    for i, (train, test) in enumerate(folds_idx, 1):
        fr = fold_result(universe, train, test, grid)
        bo = baseline_oos(universe, test)
        fr["baseline_oos"] = bo
        folds.append(fr)
        selections.append((fr["selected"]["d"], fr["selected"]["stop"], fr["selected"]["screen"]))
        sel = fr["selected"]
        print("\n折%d 训练 %s~%s (%d日) → d=%.1f%% stop=%.1f%% screen=%s%s"
              % (i, train[0], train[-1], len(train), sel["d"] * 100, sel["stop"] * 100,
                 "开" if sel["screen"] else "关",
                 "（训练窗无正期望，退回对照）" if sel["fallback_baseline"] else ""))
        print("  样本外 %s~%s  优化组 %d 笔 笔均 %s | 对照 %d 笔 笔均 %s"
              % (test[0], test[-1], fr["oos"]["n_trades"],
                 ("%.3f%%" % (100 * fr["oos"]["mean"])) if fr["oos"]["mean"] is not None else "NA",
                 bo["n_trades"],
                 ("%.3f%%" % (100 * bo["mean"])) if bo["mean"] is not None else "NA"))
        base_parts.append(bo)

    same = len(set(selections)) == 1
    chosen = selections[0]
    strategy = PG.judge_strategy([
        {"returns": fr["oos"]["returns"], "n_trades": fr["oos"]["n_trades"]} for fr in folds
    ])
    base_judge = PG.judge_strategy([
        {"returns": bo["returns"], "n_trades": bo["n_trades"]} for bo in base_parts
    ])
    cal_diffs = []

    def calendar_means(dates_, cfg):
        by = {}
        for uu in universe.values():
            for date in dates_:
                bars = uu["days"].get(date)
                if not bars:
                    continue
                if cfg[2] and not screen_pass(uu["daily"], date):
                    continue
                hit = replay(bars, cfg[0], cfg[1])
                if not hit or hit[0] <= 0:
                    continue
                by.setdefault(date, []).append(hit[1] / hit[0] - 1.0 - COST)
        return [((sum(by[d8]) / float(len(by[d8]))) if d8 in by else 0.0) for d8 in dates_]

    for (train, test), fr in zip(folds_idx, folds):
        cfg = (fr["selected"]["d"], fr["selected"]["stop"], fr["selected"]["screen"])
        opt = calendar_means(test, cfg)
        base = calendar_means(test, BASELINE)
        cal_diffs.extend(o - b for o, b in zip(opt, base))
    diff = PG.judge_diff_family([
        {"name": "oos_opt_minus_baseline", "diffs": cal_diffs, "expected_sign": 1}
    ]) if cal_diffs else {"status": "pending", "reasons": ["无样本外日期"], "published": False}

    adopt = (same and chosen != BASELINE and strategy["status"] == "eligible"
             and diff["status"] == "eligible")
    print("\n两折选中同一参数: %s %s" % (same, chosen))
    print("优化组样本外发布: %s  成交 %s  夏普 %s  回撤 %s"
          % (strategy["status"], strategy["n_trades"], strategy["sharpe_ann"],
             strategy["max_drawdown"]))
    for reason in strategy["reasons"]:
        print("  - %s" % reason)
    print("对照组样本外发布: %s  成交 %s  夏普 %s"
          % (base_judge["status"], base_judge["n_trades"], base_judge["sharpe_ann"]))
    print("优化组−对照 日差: %s" % diff["status"])
    for reason in diff.get("reasons") or []:
        print("  - %s" % reason)
    print("改引擎: %s" % ("允许" if adopt else "不允许"))

    payload = {
        "generated": "2026-09-23",
        "cost": COST,
        "end": END,
        "baseline": {"d": BASELINE[0], "stop": BASELINE[1], "screen": BASELINE[2]},
        "grid": {"d": list(D_GRID), "stop": list(STOP_GRID), "screen": [False, True]},
        "folds": [{
            "train_n_days": fr["train_n_days"],
            "test_n_days": fr["test_n_days"],
            "selected": fr["selected"],
            "oos": fr["oos"],
            "baseline_oos": fr["baseline_oos"],
            "is_selectable": [r for r in fr["is_table"] if r["selectable"]],
        } for fr in folds],
        "strategy_gate": strategy,
        "baseline_gate": base_judge,
        "diff_gate": diff,
        "same_selection": same,
        "adopt_engine": adopt,
    }
    fp = os.path.join(HERE, "bt_opt_own_result.json")
    json.dump(PG._jsonable(payload), open(fp, "w"), ensure_ascii=False, indent=1)
    print("已写 %s" % fp)


if __name__ == "__main__":
    main()
