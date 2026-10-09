#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次十二基本面特征构建（E10）：季度财务 → 可用日对齐（法定披露截止口径）。

数据源：新浪财经财务指标（ak.stock_financial_analysis_indicator，季度、
2022 年起）。特征：扣非净利润同比 np_yoy（同季度匹配）、净资产收益率 roe、
销售毛利率 gm。

PIT 口径（保守、事前固定）：报告期数值自法定披露截止日起视为可见——
Q1(03-31)→04-30；H1(06-30)→08-31；Q3(09-30)→10-31；FY(12-31)→次年04-30。

**已知局限（如实披露）**：新浪接口返回的是重述后历史序列，非当时披露原值，
存在温和前视污染（财报修订/追溯调整）；免费源无"当时披露值"通道。
22 只小盘半导体的重述幅度有限但非零，E10 结论需打折对待。

输出: quant_agent/var/fund_features/fund_features_daily.csv
列: date, code, np_yoy, roe, gm（仅"新可用"行；引擎侧按交易日向前取用最新）
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import akshare as ak  # noqa: E402
import pandas as pd  # noqa: E402

import quant_agent  # noqa: E402

OUT = os.path.join(quant_agent.VAR, "fund_features",
                   "fund_features_daily.csv")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

DEADLINE = {"03-31": (4, 30), "06-30": (8, 31), "09-30": (10, 31),
            "12-31": (4, 30)}


def usable_date(period):
    """报告期 -> 法定披露截止日（保守 PIT）。"""
    y, mm, dd = int(period[:4]), int(period[5:7]), int(period[8:10])
    key = "%02d-%02d" % (mm, dd)
    m, d = DEADLINE[key]
    return pd.Timestamp(y + (1 if (mm, dd) == (12, 31) else 0), m, d)


def fetch_one(code):
    raw = code.split(".")[0]
    for att in range(4):
        try:
            df = ak.stock_financial_analysis_indicator(
                symbol=raw, start_year="2022")
            return df
        except Exception:
            time.sleep(2 * (att + 1))
    return None


def main():
    from quant_agent.backtest.runner import Context
    const = json.load(open(quant_agent.CONFIG))
    ctx = Context(constitution=const, use_universe=False)
    codes = list(ctx.codes)
    rows = []
    fail = []
    for i, code in enumerate(codes):
        df = fetch_one(code)
        if df is None or df.empty:
            fail.append(code)
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["日期"])
        df = df.sort_values("date")
        np_col = [c for c in df.columns if "扣除非经常性损益后的净利润" in str(c)]
        roe_col = [c for c in df.columns if str(c) == "净资产收益率(%)"]
        gm_col = [c for c in df.columns if str(c) == "销售毛利率(%)"]
        if not np_col or not roe_col:
            fail.append(code)
            continue
        np_col, roe_col = np_col[0], roe_col[0]
        gm_col = gm_col[0] if gm_col else None
        prev_year = {}
        for _, r in df.iterrows():
            period = r["date"]
            npv = pd.to_numeric(r[np_col], errors="coerce")
            roe = pd.to_numeric(r[roe_col], errors="coerce")
            gm = pd.to_numeric(r[gm_col], errors="coerce") if gm_col else None
            # 扣非净利润为年内累计值：同比=今年累计-去年同期累计（同季度）
            qkey = (period.month, period.day)
            npy = None
            if pd.notna(npv):
                base = prev_year.get(qkey)
                if base is not None and base > 0:
                    npy = (npv - base) / base * 100.0
                prev_year[qkey] = npv
            u = usable_date(str(period.date()))
            if npy is not None or (roe is not None and pd.notna(roe)):
                rows.append((u.strftime("%Y-%m-%d"), code,
                             round(npy, 4) if npy is not None else "",
                             round(float(roe), 4) if pd.notna(roe) else "",
                             round(float(gm), 4)
                             if gm is not None and pd.notna(gm) else ""))
        time.sleep(0.5)
        print("[%d/%d] %s ok" % (i + 1, len(codes), code))
    out = pd.DataFrame(rows, columns=["date", "code", "np_yoy", "roe", "gm"])
    out = out.sort_values(["code", "date"])
    out.to_csv(OUT, index=False, encoding="utf-8-sig")
    print("rows:", len(out), "| codes ok:", len(codes) - len(fail),
          "| fail:", fail)
    print("sample:", out.dropna(subset=["np_yoy"]).tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
