#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qwide Phase 1：JSON 缓存 → parquet 面板（PIT 安全）。

读 qwide/cache/k_<code>.json（腾讯 qfq 日线），写列式面板：
  qwide/panel/daily.parquet   行=(code,date)，列=OHLCV+amount+name+is_st
PIT 纪律：面板是纯 asof 快照（包含其日期的全部已上市票），
训练/验证窗口切分由下游消费方负责（不在此层裁剪）。

用法: python3 qwide/build_panel.py
"""
import json
import os
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
OUTDIR = os.path.join(HERE, "panel")


def load_one(fp, name_map):
    code = os.path.basename(fp)[2:-5]
    d = None
    for _ in range(4):
        try:
            d = json.load(open(fp))
            break
        except (UnicodeDecodeError, ValueError):
            time.sleep(0.4)  # exFAT 上 replace 非原子：撞上写入期则重试
    if d is None:
        return None
    rows = []
    for b in (d.get("bars") or []):
        if not (isinstance(b, (list, tuple)) and len(b) >= 6):
            continue
        # 腾讯日线 6 字段：[date,open,close,high,low,vol(手)]；
        # 仅除权日存在第 7 字段=分红 dict。成交额需近似：vol×100×close。
        amt = float(b[5]) * 100.0 * float(b[2])
        rows.append((b[0], float(b[1]), float(b[2]), float(b[3]),
                     float(b[4]), float(b[5]), amt))
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "date", "open", "close", "high", "low", "vol", "amount"])
    df["code"] = code
    df["date"] = pd.to_datetime(df["date"])
    df["amount_est"] = True  # 全为近似值（腾讯日线无成交额字段）
    df = df[["code", "date", "open", "close", "high", "low", "vol",
             "amount", "amount_est"]]
    name = name_map.get(code, "")
    df["name"] = name
    df["is_st"] = ("ST" in name) or name.startswith("*")
    df["suspended"] = df["vol"] <= 0
    return df


def main():
    if not os.path.isdir(OUTDIR):
        os.makedirs(OUTDIR)
    codes = json.load(open(os.path.join(HERE, "code_list.json")))
    fps = sorted(os.listdir(CACHE))
    fps = [f for f in fps if f.startswith("k_") and f.endswith(".json")]
    parts = []
    bad = 0
    for f in fps:
        df = load_one(os.path.join(CACHE, f), codes)
        if df is None:
            bad += 1
            continue
        parts.append(df)
    panel = pd.concat(parts, ignore_index=True)
    panel = panel.sort_values(["date", "code"]).reset_index(drop=True)
    out = os.path.join(OUTDIR, "daily.parquet")
    panel.to_parquet(out, index=False)
    n_codes = panel["code"].nunique()
    n_days = panel["date"].nunique()
    print("rows=%d codes=%d days=%d [%s..%s] bad=%d -> %s (%.1f MB)"
          % (len(panel), n_codes, n_days,
             panel["date"].min().date(), panel["date"].max().date(),
             bad, out, os.path.getsize(out) / 1e6))


if __name__ == "__main__":
    main()
