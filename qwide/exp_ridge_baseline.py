#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qwide Phase 2b：ridge 横截面基线（纯 numpy，纪律同 E2）。

 walk-forward：每个交易日 t，用 [t-504, t-1] 窗口训练（标准化参数只用训练段），
 预测 t 日截面；评估从 2025-01-01 起（2023-06 起数据 ≈ 504 个交易日后）。
 指标：RankIC（pred vs fwd1/fwd5，Spearman）、十分位多空收益、分组单调性。
 成本意识：fwd1 评估排除 limit_up（买不进）与 suspended。

输出 qwide/exp/ridge_baseline/：pred.csv、metrics.json、report.md
"""
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FEATS = os.path.join(HERE, "panel", "features.parquet")
OUTDIR = os.path.join(HERE, "exp", "ridge_baseline")
ZCOLS = ["z_mom5", "z_mom10", "z_mom20", "z_mom60", "z_rev1", "z_maxret5",
         "z_vol20", "z_vol60", "z_skew20", "z_bias20", "z_hl20",
         "z_amihud20", "z_turn20", "z_liq", "z_lp"]
TRAIN_WIN = 504
EVAL_START = "2025-01-01"
SEALED_START = "2026-09-01"  # 封存段：只记录不评估


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
    os.makedirs(OUTDIR, exist_ok=True)
    df = pd.read_parquet(FEATS)
    dates = np.sort(df["date"].unique())
    didx = {d: i for i, d in enumerate(dates)}
    eval_dates = [d for d in dates
                  if d >= np.datetime64(EVAL_START)
                  and d < np.datetime64(SEALED_START)]
    by_date = dict(tuple(df.groupby("date")))
    preds = {}
    ics1, ics5 = [], []
    ls1, ls5 = [], []
    for t in eval_dates:
        i = didx[t]
        if i < TRAIN_WIN:
            continue
        tr = df[(df["date"] >= dates[i - TRAIN_WIN]) & (df["date"] < t)]
        te = by_date[pd.Timestamp(t)]
        tr = tr.dropna(subset=ZCOLS + ["fwd1"])
        te_m = te.dropna(subset=ZCOLS)
        if len(tr) < 5000 or len(te_m) < 500:
            continue
        X = tr[ZCOLS].values
        mu = X.mean(0)
        sd = X.std(0)
        sd[sd == 0] = 1.0
        Xs = (X - mu) / sd
        y = tr["fwd1"].values
        y = y - y.mean()
        lam = 10.0
        A = Xs.T @ Xs + lam * np.eye(len(ZCOLS))
        w = np.linalg.solve(A, Xs.T @ y)
        Xe = (te_m[ZCOLS].values - mu) / sd
        pred = Xe @ w
        preds[t] = (te_m["code"].values, pred)
        ic1 = spearman(pred, te_m["fwd1"].values)
        ic5 = spearman(pred, te_m["fwd5"].values)
        ics1.append((t, ic1))
        ics5.append((t, ic5))
        buy = ~(te_m["limit_up"].values | te_m["suspended"].values)
        for store, fwdcol in ((ls1, "fwd1"), (ls5, "fwd5")):
            r = te_m[fwdcol].values
            ok = buy & ~np.isnan(r)
            if ok.sum() > 200:
                q = pd.qcut(pd.Series(pred[ok]).rank(), 10,
                            labels=False).values
                top = r[ok][q == 9].mean()
                bot = r[ok][q == 0].mean()
                store.append((t, top - bot))
    rep = summarize(ics1, ics5, ls1, ls5)
    with open(os.path.join(OUTDIR, "metrics.json"), "w") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    write_report(rep)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


def summarize(ics1, ics5, ls1, ls5):
    def stats(pairs, name):
        s = pd.Series([v for _, v in pairs]).dropna()
        return {"n_days": int(len(s)), "ic_mean": round(float(s.mean()), 4),
                "ic_std": round(float(s.std()), 4),
                "ic_ir": round(float(s.mean() / s.std()), 3) if s.std() else None,
                "ic_gt0_pct": round(float((s > 0).mean()), 3)}
    r = {"fwd1": stats(ics1, "fwd1"), "fwd5": stats(ics5, "fwd5"),
         "ls1_daily": stats(ls1, "ls1"), "ls5_daily": stats(ls5, "ls5")}
    for key, cut in (("IS_<=2025-06", "2025-06-30"),
                     ("DEV_2025H2", "2026-01-01")):
        pass
    ic = pd.DataFrame(ics1, columns=["date", "ic"])
    ic["period"] = np.where(ic["date"] <= np.datetime64("2025-06-30"),
                            "IS", np.where(
                                ic["date"] < np.datetime64("2026-01-01"),
                                "DEV", "degrade"))
    r["ic1_by_period"] = {p: round(float(g["ic"].mean()), 4)
                          for p, g in ic.groupby("period")}
    ls = pd.DataFrame(ls1, columns=["date", "ls"])
    ls["period"] = np.where(ls["date"] <= np.datetime64("2025-06-30"),
                            "IS", np.where(
                                ls["date"] < np.datetime64("2026-01-01"),
                                "DEV", "degrade"))
    r["ls1_ann_by_period"] = {p: round(float(g["ls"].mean()) * 244, 4)
                              for p, g in ls.groupby("period")}
    return r


def write_report(rep):
    md = ["# qwide ridge 基线（walk-forward， Phase 2b）", "",
          "纯 numpy ridge（λ=10），训练窗 504 交易日，逐日滚动再拟合，"
          "标准化参数只用训练段。评估 2025-01-01~2026-08-31（封存段不评估）。",
          "", "## RankIC（pred vs 前向收益）"]
    for k in ("fwd1", "fwd5"):
        s = rep[k]
        md.append("- %s: mean=%.4f IR=%.3f >0占比=%.1f%% (%d 天)"
                  % (k, s["ic_mean"], s["ic_ir"] or 0,
                     s["ic_gt0_pct"] * 100, s["n_days"]))
    md += ["", "## 十分位多空（日频，未扣成本）"]
    md.append("- fwd1 多空日均: %.4f（年化×244=%.1f%%）"
              % (rep["ls1_daily"]["ic_mean"],
                 rep["ls1_daily"]["ic_mean"] * 244 * 100))
    md += ["", "## 分阶段（IC fwd1 / 多空年化）"]
    for p in rep["ic1_by_period"]:
        md.append("- %s: IC=%.4f 多空=%.1f%%"
                  % (p, rep["ic1_by_period"][p],
                     rep["ls1_ann_by_period"].get(p, 0) * 100))
    md += ["", "## 解读纪律",
           "- 多空未扣成本；日频换手全组合在 100 万本金+5 元最低佣下不可行，"
           "可行性需组合级回测另证。",
           "- 本文件是信号层证据，不是收益承诺。"]
    with open(os.path.join(OUTDIR, "report.md"), "w") as f:
        f.write("\n".join(md))


if __name__ == "__main__":
    main()
