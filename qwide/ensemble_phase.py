#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E-R5 相位分散组合合并：多个子组合（不同相位、各自 10 万本金）等权合成。

各子组合净值先各自归一（起始=1，未启动前视为持有现金=1），再按日对齐等权平均。
输出 exp/<tag>/ensemble_n<N>_f<F>/：daily.csv、metrics.json
"""
import argparse
import glob
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="baseline_v2")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--freq", type=int, default=10)
    ap.add_argument("--capital", type=float, default=100000)
    args = ap.parse_args()
    suffix = "_c%dk" % (args.capital / 1000)
    outdir = os.path.join(HERE, "exp", args.tag,
                          "ensemble_n%d_f%d" % (args.n, args.freq))
    os.makedirs(outdir, exist_ok=True)
    fps = sorted(glob.glob(os.path.join(
        HERE, "exp", args.tag,
        "cost_n%d_f%d_p*%s" % (args.n, args.freq, suffix), "daily.csv")))
    if not fps:
        raise SystemExit("no sleeve daily.csv found")
    series = []
    for fp in fps:
        d = pd.read_csv(fp, parse_dates=["date"]).set_index("date")["equity"]
        d = d / d.iloc[0]
        series.append(d)
    df = pd.concat(series, axis=1).sort_index()
    df = df.ffill().fillna(1.0)   # 未启动的子组合视为现金
    eq = df.mean(axis=1) * args.capital * len(series)
    daily = pd.DataFrame({"date": eq.index, "equity": eq.values})
    daily["ret"] = daily["equity"].pct_change().fillna(0.0)
    daily.to_csv(os.path.join(outdir, "daily.csv"), index=False)
    rep = {"n": args.n, "freq": args.freq, "sleeves": len(series),
           "sleeve_capital": args.capital,
           "total_capital": args.capital * len(series)}
    for pname, lo, hi in (("ALL", None, None), ("IS", None, "2025-06-30"),
                          ("DEV", "2025-07-01", "2025-12-31"),
                          ("degrade", "2026-01-01", "2026-08-31")):
        s = daily
        if lo:
            s = s[s["date"] >= lo]
        if hi:
            s = s[s["date"] <= hi]
        if len(s) < 10:
            continue
        r = s["ret"]
        ann = (1 + r).prod() ** (244 / len(r)) - 1
        sharpe = float(r.mean() / r.std() * np.sqrt(244)) if r.std() > 0 else None
        e = s["equity"] / s["equity"].iloc[0]
        rep[pname] = {"days": int(len(s)), "net_ann": round(float(ann), 4),
                      "sharpe": round(sharpe, 3) if sharpe else None,
                      "maxdd": round(float((e / e.cummax() - 1).min()), 4),
                      "total_ret": round(float(e.iloc[-1] - 1), 4)}
    with open(os.path.join(outdir, "metrics.json"), "w") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
