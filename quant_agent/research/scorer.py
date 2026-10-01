#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""二次选股评分（批次八，constitution_008，任务书第三部分）。

在现有策略生成的候选内部预测交易质量（扣费标签 label_net）。
v1 正则化线性（ridge 闭式解）；v2 限深回归树（max_depth=4,
min_samples_leaf=50，确定性分裂）。纯 numpy，无外部模型依赖。

纪律（任务书）:
- 特征只用下单前已知信息（数据集 t=信号日快照）；
- 标准化/缺失值处理只在训练集拟合（median/均值/方差）；
- 滚动时间训练：训练标签不跨入验证期（按月切分）；
- 验证：评分 5 分组的净收益单调性 + Spearman IC，逐折与跨期检查。

阻塞声明: 相对行业强弱特征因数据面无行业分类不可用（constitution_008
scoring.features_blocked），未用任何代理伪造。

产物: var/candidates/scoring_report.md、folds_<model>.csv、
pred_<model>.csv（每候选预测分，供 E2/E3 重排序）。
"""
import csv
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import quant_agent  # noqa: E402

OUT = os.path.join(quant_agent.VAR, "candidates")

FEATURES = ["score", "rank_pct", "atr20_pct", "vol_ratio", "mom5", "mom10",
            "mom20", "rs20", "rs_chg5", "rsi", "dist20", "upvar20",
            "skew20", "breadth", "mkt_mom20", "mkt_ret5"]


def load():
    rows = []
    for w in ("IS", "DEV"):
        with open(os.path.join(OUT, "candidates_%s.csv" % w)) as f:
            for r in csv.DictReader(f):
                if r["label_net"] == "":
                    continue
                rows.append({
                    "window": w,
                    "month": r["signal_date"][:7],
                    "date": r["signal_date"],
                    "code": r["code"],
                    "y": float(r["label_net"]),
                    "x": {k: (float(r[k]) if r[k] not in ("", None)
                              else float("nan")) for k in FEATURES},
                })
    return rows


def fit_preprocess(train):
    """只在训练集拟合: 缺失→中位数，标准化 mu/sigma。"""
    med, mu, sd = {}, {}, {}
    for k in FEATURES:
        vals = np.array([r["x"][k] for r in train], dtype=float)
        finite = np.isfinite(vals)
        m = float(np.median(vals[finite])) if finite.any() else 0.0
        filled = np.where(finite, vals, m)
        med[k] = m
        mu[k] = float(filled.mean())
        s = float(filled.std())
        sd[k] = s if s > 1e-12 else 1.0
    return med, mu, sd


def apply_prep(rows, med, mu, sd):
    X = np.zeros((len(rows), len(FEATURES)))
    for i, r in enumerate(rows):
        for j, k in enumerate(FEATURES):
            v = r["x"][k]
            X[i, j] = (med[k] if not math.isfinite(v) else v)
            X[i, j] = (X[i, j] - mu[k]) / sd[k]
    return X


def fit_ridge(X, y, lam=1.0):
    n_feat = X.shape[1]
    A = X.T @ X + lam * np.eye(n_feat)
    b = X.T @ y
    w = np.linalg.solve(A, b)
    return lambda X2: X2 @ w


def fit_cart(X, y, max_depth=4, min_leaf=50, n_thresh=9):
    """确定性 CART 回归树：每层每特征取训练分位点作候选阈值，
    SSE 最小者分裂；叶取均值。"""
    n_feat = X.shape[1]

    def build(idx, depth):
        node = {"pred": float(y[idx].mean())}
        if depth >= max_depth or len(idx) < 2 * min_leaf:
            return node
        best = None
        for j in range(n_feat):
            col = X[idx, j]
            qs = np.unique(np.quantile(col, np.linspace(0.1, 0.9, n_thresh)))
            for q in qs:
                left = idx[col <= q]
                right = idx[col > q]
                if len(left) < min_leaf or len(right) < min_leaf:
                    continue
                sse = ((y[left] - y[left].mean()) ** 2).sum() + \
                      ((y[right] - y[right].mean()) ** 2).sum()
                if best is None or sse < best[0] - 1e-15 or \
                        (abs(sse - best[0]) <= 1e-15 and (j, q) < best[1]):
                    best = (sse, (j, float(q)), left, right)
        if best is None:
            return node
        (_sse, (j, q), left, right) = best
        node["feat"], node["thr"] = j, q
        node["left"] = build(left, depth + 1)
        node["right"] = build(right, depth + 1)
        return node

    tree = build(np.arange(len(y)), 0)

    def predict(X2):
        out = np.zeros(len(X2))
        for i in range(len(X2)):
            node = tree
            while "feat" in node:
                node = node["left"] if X2[i, node["feat"]] <= node["thr"] \
                    else node["right"]
            out[i] = node["pred"]
        return out
    return predict


def spearman(a, b):
    n = len(a)
    if n < 3:
        return float("nan")
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean()
    rb -= rb.mean()
    d = (ra.std() * rb.std())
    return float((ra * rb).mean() / d) if d > 0 else float("nan")


def group_monotonic(pred, y, n_groups=5):
    order = np.argsort(pred)
    k = len(order) // n_groups
    if k < 5:
        return None, []
    means = []
    for g in range(n_groups):
        chunk = order[g * k:(g + 1) * k] if g < n_groups - 1 \
            else order[g * k:]
        means.append(float(y[chunk].mean()))
    asc = sum(1 for i in range(len(means) - 1) if means[i + 1] > means[i])
    return asc / (len(means) - 1), means


def main():
    rows = load()
    months_all = sorted({r["month"] for r in rows})
    is_months = sorted({r["month"] for r in rows if r["window"] == "IS"})
    print("rows=%d months IS=%d DEV=%s" % (len(rows), len(is_months),
          sorted({r["month"] for r in rows if r["window"] == "DEV"})))

    report = ["# 二次评分验证报告（constitution_008）", ""]
    report.append("模型: v1 ridge(λ=1) / v2 CART(depth4, leaf50)；特征 %d 个"
                  "（含阻塞声明：无行业特征）。" % len(FEATURES))
    report.append("标签: 全部候选的扣费净收益（含未成交候选）；"
                  "预处理只在训练集拟合；按月滚动。")
    report.append("")

    preds = {}   # (model, date, code) -> score
    for model, fitter in (("ridge", fit_ridge), ("cart", fit_cart)):
        fold_rows = []
        # IS 内滚动: 训练 ≥6 个月，验证随后 2 个月，步长 2
        start = 0
        while start + 6 + 2 <= len(is_months) + 1:
            tr_m = set(is_months[:start + 6])
            va_m = set(is_months[start + 6:start + 8])
            if not va_m:
                break
            tr = [r for r in rows if r["month"] in tr_m]
            va = [r for r in rows if r["month"] in va_m]
            if len(tr) < 100 or len(va) < 30:
                start += 2
                continue
            med, mu, sd = fit_preprocess(tr)
            Xtr, ytr = apply_prep(tr, med, mu, sd), np.array([r["y"]
                                                                for r in tr])
            predict = fitter(Xtr, ytr)
            Xva = apply_prep(va, med, mu, sd)
            pv = predict(Xva)
            yv = np.array([r["y"] for r in va])
            ic = spearman(pv, yv)
            mono, means = group_monotonic(pv, yv)
            fold_rows.append(("IS内部",
                              "%s..%s" % (min(tr_m), max(tr_m)),
                              "%s..%s" % (min(va_m), max(va_m)),
                              len(tr), len(va),
                              round(ic, 4) if ic == ic else "",
                              round(mono, 2) if mono is not None else "",
                              json.dumps([round(m * 100, 2) for m in means])
                              if means else ""))
            start += 2
        # 跨期: 全部 IS 训练 → DEV 验证
        tr = [r for r in rows if r["window"] == "IS"]
        va = [r for r in rows if r["window"] == "DEV"]
        med, mu, sd = fit_preprocess(tr)
        Xtr, ytr = apply_prep(tr, med, mu, sd), np.array([r["y"] for r in tr])
        predict = fitter(Xtr, ytr)
        Xva = apply_prep(va, med, mu, sd)
        pv = predict(Xva)
        yv = np.array([r["y"] for r in va])
        ic = spearman(pv, yv)
        mono, means = group_monotonic(pv, yv)
        fold_rows.append(("IS→DEV跨期", "IS全部", "DEV全部", len(tr),
                          len(va), round(ic, 4) if ic == ic else "",
                          round(mono, 2) if mono is not None else "",
                          json.dumps([round(m * 100, 2) for m in means])
                          if means else ""))
        with open(os.path.join(OUT, "folds_%s.csv" % model), "w",
                  newline="") as f:
            w = csv.writer(f)
            w.writerow(["折类型", "训练期", "验证期", "n_train", "n_val",
                        "spearman_ic", "单调占比(4对)", "分组净收益%(低→高)"])
            w.writerows(fold_rows)
        # 全量预测（IS 训练 → 全部候选，供 E2/E3）
        Xall = apply_prep(rows, med, mu, sd)
        pall = predict(Xall)
        with open(os.path.join(OUT, "pred_%s.csv" % model), "w",
                  newline="") as f:
            w = csv.writer(f)
            w.writerow(["window", "signal_date", "code", "pred_score"])
            for r, p in zip(rows, pall):
                w.writerow([r["window"], r["date"], r["code"],
                            round(float(p), 8)])
                preds[(model, r["window"], r["date"], r["code"])] = float(p)
        mono_vals = [fr[6] for fr in fold_rows[:-1] if fr[6] != ""]
        report.append("## %s" % model)
        report.append("")
        report.append("| 折 | 训练 | 验证 | n_tr | n_va | IC | 单调 | "
                      "分组净收益%(低→高) |")
        report.append("|---|---|---|---|---|---|---|---|")
        for fr in fold_rows:
            report.append("| %s | %s | %s | %d | %d | %s | %s | %s |" % fr)
        report.append("")
        ok_mono = mono_vals and (sum(mono_vals) / len(mono_vals)) >= 0.5
        ok_ic = (fold_rows[-1][5] != "" and fold_rows[-1][5] > 0.02) or \
                (mono and mono >= 0.5)
        verdict = "有效" if (ok_mono and (fold_rows[-1][6] != "" and
                           float(fold_rows[-1][6]) >= 0.5)) else \
                  ("部分有效(需人工判定)" if (ok_mono or ok_ic) else "无效")
        report.append("判定（预注册标准：跨期单调≥0.5 且 IS内部平均单调"
                      "≥0.5）: **%s**" % verdict)
        report.append("")

    open(os.path.join(OUT, "scoring_report.md"), "w",
         encoding="utf-8").write("\n".join(report))
    print("OK ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
