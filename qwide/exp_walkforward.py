#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qwide 通用 walk-forward ridge（基线与扩展共用同一执行器）。

用法：
  python3 exp_walkforward.py --tag baseline_v2
  python3 exp_walkforward.py --tag volman_ext --extra z_pressure,z_struct,z_false_break20

与旧 exp_ridge_baseline 的差异：仅工程优化（numpy 切片代替逐日 DataFrame 过滤）
+ 保存逐日预测供组合回测；模型/超参/窗口完全一致（λ=10，504 日，标准化只用训练段）。
输出 exp/<tag>/：pred.parquet、metrics.json、report.md
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FEATS = os.path.join(HERE, "panel", "features.parquet")
BASE_Z = ["z_mom5", "z_mom10", "z_mom20", "z_mom60", "z_rev1", "z_maxret5",
          "z_vol20", "z_vol60", "z_skew20", "z_bias20", "z_hl20",
          "z_amihud20", "z_turn20", "z_liq", "z_lp"]
TRAIN_WIN = 504
EVAL_START = np.datetime64("2025-01-01")
SEALED_START = np.datetime64("2026-09-01")


def spearman(a, b):
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 30:
        return np.nan
    ra = pd.Series(a[m]).rank().values
    rb = pd.Series(b[m]).rank().values
    ra -= ra.mean()
    rb -= rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d > 0 else np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--extra", default="")
    ap.add_argument("--max-days", type=int, default=0, help="冒烟测试：只跑前 N 个评估日")
    args = ap.parse_args()
    zcols = BASE_Z + [c for c in args.extra.split(",") if c]
    outdir = os.path.join(HERE, "exp", args.tag)
    os.makedirs(outdir, exist_ok=True)

    df = pd.read_parquet(FEATS)
    df = df.sort_values(["date", "code"]).reset_index(drop=True)
    dates = np.sort(df["date"].unique())
    row_date = df["date"].values
    day_start = np.searchsorted(row_date, dates)  # 每日在排序后行号中的起点
    didx = {d: i for i, d in enumerate(dates)}
    X = df[zcols].values.astype(np.float32)
    y1 = df["fwd1"].values.astype(np.float32)
    y5 = df["fwd5"].values.astype(np.float32)
    codes = df["code"].values
    limit_up = df["limit_up"].values
    suspended = df["suspended"].values
    print("rows=%d days=%d zcols=%d" % (len(df), len(dates), len(zcols)), flush=True)

    preds, ics1, ics5, ls1, ls5 = [], [], [], [], []
    n_eval = 0
    for t in dates:
        if args.max_days and n_eval >= args.max_days:
            break
        if t < EVAL_START or t >= SEALED_START:
            continue
        i = didx[t]
        if i < TRAIN_WIN:
            continue
        tr0, tr1 = day_start[i - TRAIN_WIN], day_start[i]
        te0, te1 = day_start[i], day_start[i + 1] if i + 1 < len(dates) else len(df)
        Xtr = X[tr0:tr1]
        ytr = y1[tr0:tr1]
        mtr = ~np.isnan(Xtr).any(1) & ~np.isnan(ytr)
        if mtr.sum() < 5000:
            continue
        Xtr = Xtr[mtr].astype(np.float64)
        ytr = ytr[mtr].astype(np.float64)
        mu = Xtr.mean(0)
        sd = Xtr.std(0)
        sd[sd == 0] = 1.0
        Xs = (Xtr - mu) / sd
        yc = ytr - ytr.mean()
        A = Xs.T @ Xs + 10.0 * np.eye(len(zcols))
        w = np.linalg.solve(A, Xs.T @ yc)
        Xte = X[te0:te1]
        mte = ~np.isnan(Xte).any(1)
        if mte.sum() < 500:
            continue
        Xe = (Xte[mte].astype(np.float64) - mu) / sd
        pred = Xe @ w
        te_codes = codes[te0:te1][mte]
        te_y1, te_y5 = y1[te0:te1][mte], y5[te0:te1][mte]
        buy = ~(limit_up[te0:te1][mte] | suspended[te0:te1][mte])
        for dd, cc, pp in zip(np.repeat(t, len(pred)), te_codes, pred):
            preds.append((dd, cc, pp))
        ics1.append((t, spearman(pred, te_y1)))
        ics5.append((t, spearman(pred, te_y5)))
        for store, fwd in ((ls1, te_y1), (ls5, te_y5)):
            ok = buy & ~np.isnan(fwd)
            if ok.sum() > 200:
                q = pd.qcut(pd.Series(pred[ok]).rank(), 10, labels=False).values
                store.append((t, fwd[ok][q == 9].mean() - fwd[ok][q == 0].mean()))
        print("%s ic1=%.4f" % (str(t)[:10], ics1[-1][1]), flush=True)
        n_eval += 1

    pd.DataFrame(preds, columns=["date", "code", "pred"]).to_parquet(
        os.path.join(outdir, "pred.parquet"), index=False)
    rep = summarize(ics1, ics5, ls1, ls5)
    rep["zcols"] = zcols
    with open(os.path.join(outdir, "metrics.json"), "w") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1), flush=True)


def stats(pairs):
    s = pd.Series([v for _, v in pairs]).dropna()
    return {"n_days": int(len(s)), "ic_mean": round(float(s.mean()), 4),
            "ic_std": round(float(s.std()), 4),
            "ic_ir": round(float(s.mean() / s.std()), 3) if s.std() else None,
            "ic_gt0_pct": round(float((s > 0).mean()), 3)}


def by_period(pairs, scale=1.0):
    df = pd.DataFrame(pairs, columns=["date", "v"])
    df["period"] = np.where(df["date"] <= np.datetime64("2025-06-30"), "IS",
                            np.where(df["date"] < np.datetime64("2026-01-01"),
                                     "DEV", "degrade"))
    return {p: round(float(g["v"].mean()) * scale, 4)
            for p, g in df.groupby("period")}


def summarize(ics1, ics5, ls1, ls5):
    return {"fwd1": stats(ics1), "fwd5": stats(ics5),
            "ls1_daily": stats(ls1), "ls5_daily": stats(ls5),
            "ic1_by_period": by_period(ics1),
            "ic5_by_period": by_period(ics5),
            "ls1_ann_by_period": by_period(ls1, 244.0)}


if __name__ == "__main__":
    main()
