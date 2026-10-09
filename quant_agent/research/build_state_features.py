#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次十状态特征构建：风格价差 + 拥挤度（日频，2023-06 起）。

E8（constitution_010）状态门使用的原始特征。全部为事后可复现的公开行情：
- style20: 20 日收益差（科创50 - 上证指数），收盘口径，t 日收盘后可知
- crowd20: 科创50 成交金额 / 中证全指成交金额，20 日均，t 日收盘后可知

宽度（涨跌家数占比）因数据源不可用且符号先验不确定，按纪律不纳入 E8。
输出: quant_agent/var/state_features/state_features_daily.csv
"""
import os
import time

import akshare as ak
import numpy as np
import pandas as pd

OUT = "quant_agent/var/state_features/state_features_daily.csv"
os.makedirs(os.path.dirname(OUT), exist_ok=True)


def sina_close(symbol):
    df = ak.stock_zh_index_daily(symbol=symbol)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["close"]


def cs_amount(symbol):
    df = ak.stock_zh_index_hist_csindex(symbol=symbol, start_date="20230601",
                                        end_date="20500101")
    df.columns = [str(c).strip() for c in df.columns]
    dcol = [c for c in df.columns if "日期" in c][0]
    acol = [c for c in df.columns if "成交金额" in c][0]
    out = pd.DataFrame({
        "date": pd.to_datetime(df[dcol]),
        "amount": pd.to_numeric(df[acol], errors="coerce"),
    }).drop_duplicates("date").set_index("date")
    return out["amount"]


k50 = sina_close("sh000688")
szz = sina_close("sh000001")
a_k = cs_amount("000688")
a_all = cs_amount("000985")

panel = pd.DataFrame({"k50": k50, "szz": szz}).sort_index()
panel = panel[panel.index >= "2023-06-01"]

# 风格价差：20 日收益差（百分比点）
r_k = panel["k50"].pct_change(20) * 100
r_s = panel["szz"].pct_change(20) * 100
panel["style20"] = r_k - r_s

# 拥挤度：成交额占比 20 日均（指数成分口径，单位一致可直接相除）
amt = pd.DataFrame({"k": a_k, "all": a_all}).sort_index()
amt["crowd_raw"] = amt["k"] / amt["all"]
panel["crowd20"] = amt["crowd_raw"].reindex(panel.index).rolling(20).mean()

out = panel[["style20", "crowd20"]].reset_index()
out.columns = ["date", "style20", "crowd20"]
out.to_csv(OUT, index=False, encoding="utf-8-sig", float_format="%.6f")
nn = out.dropna(subset=["style20"])
print("rows:", len(out), "| style20 非空:", len(nn),
      "| 范围:", nn["date"].iloc[0].date(), "->", nn["date"].iloc[-1].date())
print(nn.tail(3).to_string(index=False))
