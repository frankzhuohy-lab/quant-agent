#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qwide Phase 2a：横截面因子层。

从 panel/daily.parquet 计算每只票的时序因子，再按日横截面 z 分（去极值）。
PIT 纪律：因子 t 日值只用 ≤t 日数据；标签 fwd_ret_h = close(t+h)/close(t)-1。

输出 qwide/panel/features.parquet：code,date, feat_*（z 后）, fwd1,fwd5,
ret1, suspended, is_st, limit_up（主板的 9.5%/科创 19.5% 粗阈值，买侧不可成交标记）。
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.join(HERE, "panel", "daily.parquet")
OUT = os.path.join(HERE, "panel", "features.parquet")


def _roll(a, w, fn):
    """右闭滚动窗口（含当日），前 w-1 位 NaN。"""
    out = np.full(len(a), np.nan)
    for i in range(w - 1, len(a)):
        out[i] = fn(a[i - w + 1:i + 1])
    return out


def compute_group(g):
    g = g.sort_values("date")  # 保留全局 index，join 靠标签对齐
    c = g["close"].astype(float)
    v = g["vol"].astype(float)
    hi = g["high"].astype(float)
    lo = g["low"].astype(float)
    amt = g["amount"].astype(float)
    n = len(g)
    ret = c.pct_change()
    sc = c  # Series 容器，rolling 为 C 速

    def mom(k):
        return c / c.shift(k) - 1.0

    ma20 = sc.rolling(20).mean()
    f = {
        "mom5": mom(5), "mom10": mom(10), "mom20": mom(20), "mom60": mom(60),
        "rev1": -ret,
        "maxret5": ret.rolling(5).max(),
        "vol20": ret.rolling(20).std(),
        "vol60": ret.rolling(60).std(),
        "skew20": ret.rolling(20).skew(),
        "bias20": c / ma20 - 1.0,
        "hl20": ((hi - lo) / c).rolling(20).mean(),
        "amihud20": (ret.abs() / amt.where(amt > 0))
                   .rolling(20).mean(),
        "turn20": v / v.rolling(60).mean(),
        "liq": np.log(amt.where(amt > 0)),
        "lp": np.log(c),
    }
    f["fwd1"] = c.shift(-1) / c - 1.0
    f["fwd5"] = c.shift(-5) / c - 1.0
    # --- Volman 概念因子（E-V1/V2/V3，公式见 EXPERIMENTS.md 预注册） ---
    prev_high20 = hi.shift(1).rolling(20).max()  # max(high[t-20..t-1])
    fb = ((hi > prev_high20) & (c < prev_high20)).astype(float)
    fb[prev_high20.isna()] = np.nan
    f["false_break20"] = fb
    near_high60 = c / hi.rolling(60).max() - 1.0
    vol5 = ret.rolling(5).std()
    vol60s = ret.rolling(60).std()
    f["pressure"] = (1.0 + near_high60) / (vol5 / vol60s + 0.2)
    hh = hi.rolling(10).max() / hi.rolling(10).max().shift(10) - 1.0
    ll = lo.rolling(10).min() / lo.rolling(10).min().shift(10) - 1.0
    f["struct"] = hh + ll
    out = pd.DataFrame(f)
    out.index = g.index
    out["ret1"] = ret
    out["suspended"] = (v <= 0).values
    lim = 0.195 if str(g["code"].iloc[0]).startswith("68") else 0.095
    out["limit_up"] = ((ret > lim) | (hi / c - 1 > lim * 0.999)).values
    return out


def _fwd(c, h):
    out = np.full(len(c), np.nan)
    out[:-h] = c[h:] / c[:-h] - 1.0
    return out


def _skew(x):
    x = x[~np.isnan(x)]
    if len(x) < 5:
        return np.nan
    s = x.std()
    return float(((x - x.mean()) ** 3).mean() / s ** 3) if s > 0 else 0.0


def main():
    panel = pd.read_parquet(PANEL)
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    print("panel rows=%d codes=%d" % (len(panel), panel["code"].nunique()))
    feats = []
    for code, g in panel.groupby("code", sort=False):
        feats.append(compute_group(g))
    feat = pd.concat(feats).sort_index()
    df = panel.join(feat.drop(columns=["ret1", "suspended", "limit_up"],
                              errors="ignore"))
    df = df.join(feat[["ret1", "limit_up"]])
    # 横截面 z（按日分组，MAD 去极值）
    zcols = ["mom5", "mom10", "mom20", "mom60", "rev1", "maxret5", "vol20",
             "vol60", "skew20", "bias20", "hl20", "amihud20", "turn20",
             "liq", "lp", "false_break20", "pressure", "struct"]
    for col in zcols:
        df["z_" + col] = df.groupby("date")[col].transform(_robust_z)
    keep = ["code", "date", "name", "is_st", "suspended", "limit_up",
            "ret1", "fwd1", "fwd5", "false_break20", "pressure", "struct"] \
        + ["z_" + c for c in zcols]
    df[keep].to_parquet(OUT, index=False)
    print("features -> %s rows=%d (%.1f MB)"
          % (OUT, len(df), os.path.getsize(OUT) / 1e6))


def _robust_z(s):
    med = s.median()
    mad = (s - med).abs().median()
    scale = 1.4826 * mad if mad > 0 else s.std()
    if not scale or np.isnan(scale):
        return s * np.nan
    z = (s - med) / scale
    return z.clip(-3, 3)


if __name__ == "__main__":
    main()
