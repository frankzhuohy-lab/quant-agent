#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E-Phase3 多头组合成本回测（预注册见 EXPERIMENTS.md）。

执行语义：
- 信号日 t 收盘出分 → 下一交易日 t+1 开盘成交（不用收盘价自我成交）
- top N 等权，调仓只交易差额（非全清全买）；买入 100 股整手，卖出可零股
- 成本：买 0.20‰ / 卖 0.25‰，每笔最低 5 元；本金 100 万
- 不可交易：停牌（无成交/无当日行）；开盘涨停（open≥前收×(1+lim)）不可买
  涨停阈值：688/689/300/301=19.5%，ST=4.75%，其余 9.5%
- T+1：每日最多一次调仓，当日买入不会当日卖出
- 不剔 ST、不挪评估区间（预注册禁区）

用法：python3 exp_portfolio_cost.py --tag baseline_v2 --n 20 --freq 1
输出 exp/<tag>/cost_n<N>_f<F>/：daily.csv、metrics.json、report.md
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BUY_RATE, SELL_RATE, MIN_FEE = 0.0002, 0.00025, 5.0
CAPITAL = 1_000_000.0


def lim_of(code, is_st):
    if is_st:
        return 0.0475
    return 0.195 if code.startswith(("68", "30")) else 0.095


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="baseline_v2")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--freq", type=int, default=1)
    ap.add_argument("--phase", type=int, default=0, help="调仓相位（E-R1）")
    ap.add_argument("--capital", type=float, default=CAPITAL,
                    help="本金（E-R5 子组合用 10 万，使最低佣金按真实规模生效）")
    args = ap.parse_args()
    suffix = "" if args.capital == CAPITAL else "_c%dk" % (args.capital / 1000)
    outdir = os.path.join(HERE, "exp", args.tag,
                          "cost_n%d_f%d_p%d%s" % (args.n, args.freq, args.phase, suffix))
    os.makedirs(outdir, exist_ok=True)

    pred = pd.read_parquet(os.path.join(HERE, "exp", args.tag, "pred.parquet"))
    flags = pd.read_parquet(os.path.join(HERE, "panel", "features.parquet"),
                            columns=["code", "date", "limit_up", "suspended"])
    pred = pred.merge(flags, on=["code", "date"], how="left")
    pred["limit_up"] = pred["limit_up"].fillna(False)
    pred["suspended"] = pred["suspended"].fillna(True)
    panel = pd.read_parquet(os.path.join(HERE, "panel", "daily.parquet"),
                            columns=["code", "date", "open", "close", "vol",
                                     "is_st", "suspended"])
    panel_dates = np.sort(panel["date"].unique())
    by_date = {pd.Timestamp(d): g.set_index("code")
               for d, g in panel.groupby("date")}
    sig_dates = sorted(pred["date"].unique())
    sig_by_date = {d: g for d, g in pred.groupby("date")}
    # 执行日 = 信号日后的下一个交易日
    exec_map = {}
    for k, t in enumerate(sig_dates):
        if k % args.freq != args.phase % args.freq:
            continue
        nxt = panel_dates[panel_dates > t]
        if len(nxt):
            exec_map[nxt[0]] = t

    cash, pos = args.capital, {}   # pos: code -> shares
    last_close = {}
    fee_total, turn_total, n_trades = 0.0, 0.0, 0
    records = []
    start = min(exec_map) if exec_map else None
    for d in panel_dates:
        if start is None or d < start:
            continue
        g = by_date[pd.Timestamp(d)]
        if d in exec_map:
            t = exec_map[d]
            sig = sig_by_date[pd.Timestamp(t)]
            # 信号侧过滤：停牌/涨停（预注册）不纳入买点候选
            cand = sig[~sig["limit_up"] & ~sig["suspended"]] \
                .sort_values("pred", ascending=False)
            target = list(cand["code"].values[:args.n])
            # 开盘估值
            equity_open = cash
            open_px, tradable = {}, {}
            for code, sh in pos.items():
                if code in g.index and not g.at[code, "suspended"]:
                    px = float(g.at[code, "open"])
                    open_px[code] = px
                    tradable[code] = True
                    equity_open += sh * px
                else:
                    equity_open += sh * last_close.get(code, 0.0)
                    tradable[code] = False
            tgt_val = equity_open / args.n
            sig_close = {}
            sg = by_date.get(pd.Timestamp(t))
            if sg is not None:
                sig_close = sg["close"].to_dict()
            # 先卖：不在目标内清仓，在目标内减到目标值
            for code in list(pos):
                if not tradable.get(code):
                    continue
                px = open_px[code]
                pc = sig_close.get(code)
                if pc and px <= pc * (1 - lim_of(code, bool(g.at[code, "is_st"]))) * 1.001:
                    continue  # E-R2：一字跌停，卖不出
                cur_val = pos[code] * px
                want = tgt_val if code in target else 0.0
                if cur_val - want < max(1000.0, tgt_val * 0.05):
                    continue
                sell_sh = pos[code] if want == 0 else int((cur_val - want) / px)
                sell_sh = min(sell_sh, pos[code])
                if sell_sh <= 0:
                    continue
                amt = sell_sh * px
                fee = max(MIN_FEE, amt * SELL_RATE)
                cash += amt - fee
                pos[code] -= sell_sh
                if pos[code] == 0:
                    del pos[code]
                fee_total += fee
                turn_total += amt
                n_trades += 1
            # 后买：按目标缺口，100 股整手
            for code in target:
                if code not in g.index or g.at[code, "suspended"]:
                    continue
                px = float(g.at[code, "open"])
                if px <= 0:
                    continue
                pc = sig_close.get(code)
                if pc and px >= pc * (1 + lim_of(code, bool(g.at[code, "is_st"]))) * 0.999:
                    continue  # 开盘涨停，买不进
                cur_val = pos.get(code, 0) * px
                gap = tgt_val - cur_val
                if gap < max(1000.0, tgt_val * 0.05):
                    continue
                lots = int(gap / px / 100)
                while lots > 0:
                    amt = lots * 100 * px
                    fee = max(MIN_FEE, amt * BUY_RATE)
                    if amt + fee <= cash:
                        break
                    lots -= 1
                if lots <= 0:
                    continue
                amt = lots * 100 * px
                fee = max(MIN_FEE, amt * BUY_RATE)
                cash -= amt + fee
                pos[code] = pos.get(code, 0) + lots * 100
                fee_total += fee
                turn_total += amt
                n_trades += 1
        # 收盘估值
        equity = cash
        for code, sh in pos.items():
            if code in g.index:
                last_close[code] = float(g.at[code, "close"])
            equity += sh * last_close.get(code, 0.0)
        records.append((d, equity, len(pos), fee_total, turn_total))

    daily = pd.DataFrame(records, columns=[
        "date", "equity", "n_pos", "fee_cum", "turnover_cum"])
    daily["ret"] = daily["equity"].pct_change().fillna(0.0)
    daily.to_csv(os.path.join(outdir, "daily.csv"), index=False)
    rep = {"n": args.n, "freq": args.freq, "phase": args.phase,
           "limit_down_sell": True, "trades": n_trades,
           "fee_total": round(fee_total, 0),
           "turnover_total": round(turn_total, 0),
           "final_equity": round(float(daily["equity"].iloc[-1]), 0)}
    for pname, lo, hi in (("ALL", None, None),
                          ("IS", None, "2025-06-30"),
                          ("DEV", "2025-07-01", "2025-12-31"),
                          ("degrade", "2026-01-01", "2026-08-31")):
        s = daily
        if lo:
            s = s[s["date"] >= np.datetime64(lo)]
        if hi:
            s = s[s["date"] <= np.datetime64(hi)]
        if len(s) < 10:
            continue
        r = s["ret"]
        ann = (1 + r).prod() ** (244 / len(r)) - 1
        sharpe = float(r.mean() / r.std() * np.sqrt(244)) if r.std() > 0 else None
        eq = s["equity"] / s["equity"].iloc[0]
        mdd = float((eq / eq.cummax() - 1).min())
        rep[pname] = {"days": int(len(s)), "net_ann": round(float(ann), 4),
                      "sharpe": round(sharpe, 3) if sharpe else None,
                      "maxdd": round(mdd, 4),
                      "total_ret": round(float(eq.iloc[-1] - 1), 4)}
    dev = rep.get("DEV", {})
    if dev.get("net_ann", -1) < 0:
        verdict = "FAIL"
    elif dev.get("net_ann", 0) > 0.08 and (dev.get("sharpe") or 0) > 0.5:
        verdict = "PASS"
    else:
        verdict = "NEEDS_MORE_TESTING"
    rep["verdict"] = verdict
    with open(os.path.join(outdir, "metrics.json"), "w") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
