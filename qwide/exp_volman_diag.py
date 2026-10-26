#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E-V1/V2/V3 独立诊断：事件研究 + 单因子 RankIC（不进模型，先过独立检验）。

判定门槛见 EXPERIMENTS.md（2026-10-26 预注册）：
- V1：事件数≥500，fwd5 超额均值≤-0.5pp 且按日聚合 t≥2
- V2/V3：DEV 段单因子 RankIC(fwd5) 均值>0.01 才允许进入增量检验
输出 exp/volman_diag/metrics.json + 控制台摘要
"""
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FEATS = os.path.join(HERE, "panel", "features.parquet")
OUTDIR = os.path.join(HERE, "exp", "volman_diag")
SEALED = np.datetime64("2026-09-01")


def period(d):
    return np.where(d <= np.datetime64("2025-06-30"), "IS",
                    np.where(d < np.datetime64("2026-01-01"), "DEV", "degrade"))


def rank_ic(df, col):
    out = []
    for t, g in df.groupby("date"):
        a, b = g[col].values, g["fwd5"].values
        m = ~(np.isnan(a) | np.isnan(b))
        if m.sum() < 200 or len(np.unique(a[m])) < 5:
            continue
        ra = pd.Series(a[m]).rank().values
        rb = pd.Series(b[m]).rank().values
        ra -= ra.mean()
        rb -= rb.mean()
        d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
        if d > 0:
            out.append((t, float((ra * rb).sum() / d)))
    r = pd.DataFrame(out, columns=["date", "ic"])
    r["period"] = period(r["date"].values)
    return {"overall": round(float(r["ic"].mean()), 4),
            "by_period": {p: round(float(g["ic"].mean()), 4)
                          for p, g in r.groupby("period")},
            "n_days": int(len(r))}


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    df = pd.read_parquet(FEATS)
    for col in ("pressure", "struct", "fwd5"):
        df[col] = df[col].replace([np.inf, -np.inf], np.nan)
    df = df[(df["date"] >= np.datetime64("2025-01-01")) & (df["date"] < SEALED)]
    rep = {}

    # V1 假突破事件研究（按日聚合超额，t 检验用日期为样本）
    ev = df[(df["false_break20"] == 1) & ~df["fwd5"].isna()]
    uni = df.groupby("date")["fwd5"].mean().rename("uni")
    ex = ev.groupby("date")["fwd5"].mean().rename("ev").to_frame().join(uni)
    ex["excess"] = ex["ev"] - ex["uni"]
    n_days = len(ex)
    t_stat = float(ex["excess"].mean() / ex["excess"].std() * np.sqrt(n_days)) \
        if n_days > 2 and ex["excess"].std() > 0 else None
    rep["V1_false_break"] = {
        "n_events": int(len(ev)), "n_dates": n_days,
        "excess_fwd5_mean": round(float(ex["excess"].mean()), 5),
        "t_stat": round(t_stat, 2) if t_stat is not None else None,
        "verdict": "PASS" if (len(ev) >= 500 and ex["excess"].mean() <= -0.005
                              and t_stat is not None and t_stat >= 2) else "FAIL"}

    for tag, col in (("V2_pressure", "pressure"), ("V3_struct", "struct")):
        r = rank_ic(df[["date", col, "fwd5"]].dropna(subset=[col]), col)
        r["verdict"] = "PASS" if r["by_period"].get("DEV", 0) > 0.01 else "FAIL"
        rep[tag] = r

    with open(os.path.join(OUTDIR, "metrics.json"), "w") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
