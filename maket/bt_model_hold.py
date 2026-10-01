# -*- coding: utf-8 -*-
"""持有轮换模型。用现行筛选规则选前 4 名，次日开盘换仓，持有到落选。不做日内 T。

依据是纸面账本：做 T 回合合计为负，账户正收益来自底仓轮换。
本文件不搜索参数。筛选硬条件和打分与 screen_candidates.analyze 相同，
名额 K=4 与 MAX_DYN 相同，单边成本 0.11%（来回 0.22%）。
涨停买不进、跌停卖不出（主板 10%，创业板/科创板 20%，留 0.5 个百分点）。

评价：信号日没调过任何参数，所以整段都是样本外。按时间切成前后两折，
交给 publish_gate。对照是同一 200 只股票的等权日收益。
两折拼接后的策略要 eligible，且「策略−等权」的日差也要 eligible，才算更好。
本脚本只写结果，不改引擎。

数据：bt_cache_c6_daily.json。宇宙是 2026-09-22 成交额前 200 的静态快照，
历史逐日没有重放流动性预筛，有轻度幸存者偏差。
用法: python3 bt_model_hold.py
"""
from __future__ import print_function

import json
import os

import publish_gate as PG

HERE = os.path.dirname(os.path.abspath(__file__))
K = 4
SIDE = 0.0011
MIN_HIST = 30


def limit_ratio(sym):
    code = sym[2:]
    if code.startswith(("300", "301", "688", "689")):
        return 0.195
    return 0.095


def load():
    raw = json.load(open(os.path.join(HERE, "bt_cache_c6_daily.json")))
    series = {}
    for sym, bars in raw.items():
        rows = []
        for b in bars:
            o, c, h, lo = float(b[1]), float(b[2]), float(b[3]), float(b[4])
            if o <= 0 or c <= 0 or h <= 0 or lo <= 0:
                continue
            rows.append((b[0].replace("-", ""), o, c, h, lo))
        if len(rows) >= MIN_HIST:
            series[sym] = rows
    return series


def index_maps(series):
    """sym -> {date: (o,c,h,l)}，以及该票的日期序。"""
    book = {}
    dates = set()
    for sym, rows in series.items():
        m = {}
        for d, o, c, h, lo in rows:
            m[d] = (o, c, h, lo)
            dates.add(d)
        book[sym] = m
    return book, sorted(dates)


def score_on(book_sym, ordered_dates, end):
    """用截至 end（含）的日线打分。不合格返回 None。"""
    rows = []
    for d in ordered_dates:
        if d > end:
            break
        bar = book_sym.get(d)
        if bar is not None:
            rows.append(bar)
    if len(rows) < MIN_HIST:
        return None
    opens = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    c, o = closes[-1], opens[-1]
    ma20 = sum(closes[-20:]) / 20.0
    ret20 = c / closes[-21] - 1.0
    chg = c / closes[-2] - 1.0
    trs = []
    for i in range(len(rows) - 14, len(rows)):
        pc = closes[i - 1]
        trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    atr = (sum(trs) / 14.0) / c
    dist = c / max(highs[-20:]) - 1.0
    if not (c > ma20 and ret20 > 0 and chg < 0.05 and 0.012 <= atr <= 0.065):
        return None
    if not (c < o or -0.10 <= dist <= -0.02):
        return None
    return (min(ret20, 0.30) / 0.30 * 40.0
            + (-dist - 0.02) / 0.08 * 30.0
            + (1.0 - abs(atr - 0.025) / 0.025) * 15.0
            + min(chg + 0.06, 0.06) / 0.06 * 15.0)


def targets_for(book, sym_dates, signal_day, trade_day):
    ranked = []
    for sym, mp in book.items():
        sc = score_on(mp, sym_dates[sym], signal_day)
        if sc is None:
            continue
        prev = mp.get(signal_day)
        today = mp.get(trade_day)
        if prev is None or today is None:
            continue
        if today[0] / prev[1] - 1.0 >= limit_ratio(sym):
            continue
        ranked.append((sc, sym))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [sym for _, sym in ranked[:K]]


def simulate(book, sym_dates, calendar, i0, i1):
    """calendar[i] 是收益日。信号用 calendar[i-1]。返回日收益、开仓数、平仓数。"""
    cash = 1.0
    shares = {}
    equity = 1.0
    daily = []
    entries = 0
    exits = 0
    for i in range(i0, i1):
        day = calendar[i]
        prev = calendar[i - 1]
        held_val = 0.0
        for sym, qty in list(shares.items()):
            bar = book[sym].get(day)
            if bar is None:
                continue
            held_val += qty * bar[0]
        eq_open = cash + held_val
        want = targets_for(book, sym_dates, prev, day)
        want_set = set(want)
        for sym in list(shares.keys()):
            if sym in want_set:
                continue
            bar = book[sym].get(day)
            prev_bar = book[sym].get(prev)
            if bar is None or prev_bar is None:
                continue
            if bar[0] / prev_bar[1] - 1.0 <= -limit_ratio(sym):
                continue
            cash += shares[sym] * bar[0] * (1.0 - SIDE)
            del shares[sym]
            exits += 1
        held_val = 0.0
        for sym, qty in shares.items():
            bar = book[sym].get(day)
            if bar is not None:
                held_val += qty * bar[0]
        eq_open = cash + held_val
        slot = eq_open / float(K)
        for sym in want:
            bar = book[sym].get(day)
            if bar is None:
                continue
            o = bar[0]
            cur = shares.get(sym, 0.0) * o
            gap_val = slot - cur
            if gap_val > slot * 0.02 and cash > 1e-8:
                spend = min(gap_val, cash / (1.0 + SIDE))
                if cur <= 0:
                    entries += 1
                shares[sym] = shares.get(sym, 0.0) + spend / o
                cash -= spend * (1.0 + SIDE)
            elif gap_val < -slot * 0.02 and sym in shares:
                sell_qty = min(shares[sym], (-gap_val) / o)
                cash += sell_qty * o * (1.0 - SIDE)
                shares[sym] -= sell_qty
                if shares[sym] * o < slot * 0.02:
                    cash += shares[sym] * o * (1.0 - SIDE)
                    del shares[sym]
        eq_close = cash
        for sym, qty in shares.items():
            bar = book[sym].get(day)
            px = bar[1] if bar is not None else None
            if px is None:
                continue
            eq_close += qty * px
        daily.append(eq_close / equity - 1.0)
        equity = eq_close
    return daily, entries, exits, equity


def baseline(book, calendar, i0, i1):
    """同一宇宙等权收盘到收盘。首尾各扣一次单边成本。"""
    daily = []
    for i in range(i0, i1):
        day, prev = calendar[i], calendar[i - 1]
        rets = []
        for mp in book.values():
            a, b = mp.get(prev), mp.get(day)
            if a is None or b is None or a[1] <= 0:
                continue
            rets.append(b[1] / a[1] - 1.0)
        if not rets:
            daily.append(0.0)
            continue
        r = sum(rets) / float(len(rets))
        if i == i0:
            r -= SIDE
        if i == i1 - 1:
            r -= SIDE
        daily.append(r)
    return daily


def main():
    series = load()
    book, calendar = index_maps(series)
    sym_dates = {sym: sorted(mp) for sym, mp in book.items()}
    # 前 30 个自然日留给均线。收益从 calendar[30] 开始。
    start = 30
    usable = list(range(start, len(calendar)))
    if len(usable) < 40:
        raise SystemExit("可交易日不足")
    mid = start + len(usable) // 2
    folds = [(start, mid), (mid, len(calendar))]
    print("股票 %d，日历 %s ~ %s，可交易 %d 日，K=%d，单边成本 %.2f%%"
          % (len(book), calendar[0], calendar[-1], len(usable), K, SIDE * 100))

    strat_folds = []
    base_folds = []
    diffs = []
    for n, (a, b) in enumerate(folds, 1):
        daily, entries, exits, equity = simulate(book, sym_dates, calendar, a, b)
        base = baseline(book, calendar, a, b)
        strat_folds.append({"returns": daily, "n_trades": entries})
        base_folds.append({"returns": base, "n_trades": max(len(book), 1)})
        diffs.extend(s - r for s, r in zip(daily, base))
        print("折%d %s~%s  开仓 %d 平仓 %d  期末净值 %.3f  日均 %+.3f%%"
              % (n, calendar[a], calendar[b - 1], entries, exits, equity,
                 100.0 * (sum(daily) / len(daily))))
        print("    等权对照日均 %+.3f%%" % (100.0 * (sum(base) / len(base))))

    strategy = PG.judge_strategy(strat_folds)
    bench = PG.judge_strategy(base_folds)
    diff = PG.judge_diff_family([
        {"name": "hold_minus_ew", "diffs": diffs, "expected_sign": 1}
    ])
    better = strategy["status"] == "eligible" and diff["status"] == "eligible"
    print("\n持有轮换: %s  成交 %d  夏普 %s  回撤 %s"
          % (strategy["status"], strategy["n_trades"], strategy["sharpe_ann"],
             strategy["max_drawdown"]))
    for reason in strategy["reasons"]:
        print("  - %s" % reason)
    print("等权宇宙: %s  夏普 %s" % (bench["status"], bench["sharpe_ann"]))
    print("超额日差: %s" % diff["status"])
    for reason in diff.get("reasons") or []:
        print("  - %s" % reason)
    print("更好: %s" % ("是" if better else "否"))

    payload = PG._jsonable({
        "generated": "2026-09-25",
        "model": "hold_rotate_top4",
        "k": K,
        "side_cost": SIDE,
        "n_symbols": len(book),
        "calendar": [calendar[0], calendar[-1]],
        "folds": [{
            "n_days": len(f["returns"]),
            "n_trades": f["n_trades"],
            "mean_daily": sum(f["returns"]) / float(len(f["returns"])),
        } for f in strat_folds],
        "strategy_gate": strategy,
        "baseline_gate": bench,
        "diff_gate": diff,
        "better": better,
        "bias": "宇宙为 2026-09-22 成交额前 200 的静态快照",
    })
    fp = os.path.join(HERE, "bt_model_hold_result.json")
    json.dump(payload, open(fp, "w"), ensure_ascii=False, indent=1)
    print("已写 %s" % fp)


if __name__ == "__main__":
    main()
