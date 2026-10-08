#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场结构研究：A股科技 vs 美股科技背离（2026-10-08）。

性质：市场研究备忘录（非策略变体，不消耗试验预算台账）。
数据来源：东方财富行情接口（A股指数日K含成交额）、新浪财经（美股指数日K）。
口径声明：美股日期 d 的收盘对应北京时间 d 日晚 21:30-04:00 的交易时段，
A股 d+1 交易日对其作出反应——故隔夜反应关系用 CN(d+1) vs US(d) 对齐。
"""
import json
import os
import time
import urllib.request

import numpy as np
import pandas as pd
import akshare as ak

OUT = "quant_agent/var/market_study_20261008"
os.makedirs(OUT, exist_ok=True)

UA = {"Referer": "https://quote.eastmoney.com/", "User-Agent": "Mozilla/5.0"}


def em_kline(secid, beg="20250101"):
    """东财日K: f51=date f53=close f56=volume f57=amount（带重试）"""
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?"
           f"secid={secid}&klt=101&fqt=1&beg={beg}&end=20500101"
           "&fields1=f1,f2,f3&fields2=f51,f53,f56,f57")
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())
            ks = d["data"]["klines"]
            break
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    else:
        raise last
    rows = [k.split(",") for k in ks]
    df = pd.DataFrame(rows, columns=["date", "close", "volume", "amount"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ["close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("date")


def sina_index(symbol):
    """新浪 A 股指数日K（close/volume，无成交额）"""
    df = ak.stock_zh_index_daily(symbol=symbol)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")[["close", "volume"]]


def sina_us(symbol):
    df = ak.index_us_stock_sina(symbol=symbol)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")[["close"]]


def csindex(symbol, name):
    """中证指数官网日行情（含成交额），2025 年以来"""
    df = ak.stock_zh_index_hist_csindex(symbol=symbol, start_date="20250101",
                                        end_date="20500101")
    df.columns = [str(c).strip() for c in df.columns]
    dcol = [c for c in df.columns if "日期" in c][0]
    acol = [c for c in df.columns if "成交金额" in c][0]
    ccol = [c for c in df.columns if c == "收盘" or "收盘" in c][0]
    out = pd.DataFrame({
        "date": pd.to_datetime(df[dcol]),
        "close": pd.to_numeric(df[ccol], errors="coerce"),
        "amount": pd.to_numeric(df[acol], errors="coerce"),
    }).set_index("date")
    return out


print("== 拉取 A 股指数（新浪 close + 中证官网成交额）==")
CN = {}
for symbol, name in [("sh000001", "上证指数"), ("sz399001", "深证成指"),
                     ("sz399006", "创业板指"), ("sh000300", "沪深300"),
                     ("sh000905", "中证500"), ("sh000852", "中证1000"),
                     ("sh000688", "科创50")]:
    CN[name] = sina_index(symbol)
    time.sleep(0.5)
AMT = {"科创50": csindex("000688", "科创50"),
       "中证全指": csindex("000985", "中证全指")}
print("== 拉取美股指数 ==")
US = {"SOX": sina_us(".SOX"), "IXIC": sina_us(".IXIC")}
for k in US:
    US[k] = US[k][US[k].index >= "2025-01-01"]

# 合并面板（交易日对齐）
panel = pd.DataFrame(index=CN["上证指数"].index)
for name, df in CN.items():
    panel[name] = df["close"]
    panel[name + "_vol"] = df["volume"]
for name, df in US.items():
    panel[name] = df["close"]
panel["科创50_amt"] = AMT["科创50"]["amount"]
panel["全指_amt"] = AMT["中证全指"]["amount"]
panel = panel.sort_index()
panel.to_csv(f"{OUT}/daily_panel.csv", encoding="utf-8-sig")

rets = panel[[c for c in panel.columns
              if not c.endswith(("_amt", "_vol"))]].pct_change()

# 隔夜对齐：CN 日 d 的反应对象是 US 日 d-1（同日期映射即滞后一期）
rets_al = rets.copy()
for name in US:
    rets_al[name] = rets[name].shift(1)
valid = rets_al.dropna(subset=["科创50", "SOX", "IXIC", "上证指数"])
valid = valid[valid.index >= "2025-01-15"]

# 分季度收益（2025年以来；QE 占位行已删，用自定义区间）
def period_ret(s, a, b):
    seg = s[(s.index >= a) & (s.index <= b)]
    if len(seg) < 2:
        return np.nan
    return seg.iloc[-1] / seg.iloc[0] - 1

periods = [("2025全年", "2025-01-01", "2025-12-31"),
           ("2026Q1", "2026-01-01", "2026-03-31"),
           ("2026Q2", "2026-04-01", "2026-06-30"),
           ("2026Q3", "2026-07-01", "2026-09-30")]
names = ["上证指数", "沪深300", "创业板指", "科创50", "中证1000", "SOX", "IXIC"]
lines = ["# 分区间累计收益（收盘价口径）", "",
         "| 区间 | " + " | ".join(names) + " |", "|" + "---|" * (len(names) + 1)]
for label, a, b in periods:
    row = [label] + [f"{period_ret(panel[n], a, b) * 100:+.1f}%"
                     if not np.isnan(period_ret(panel[n], a, b)) else "—" for n in names]
    lines.append("| " + " | ".join(row) + " |")
open(f"{OUT}/period_returns.md", "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))

# 滚动 60 日相关性：科创50 vs SOX（隔夜对齐）
corr60 = valid["科创50"].rolling(60).corr(valid["SOX"])
corr_tab = []
for label, a, b in [("2025H1", "2025-01-01", "2025-06-30"),
                    ("2025H2", "2025-07-01", "2025-12-31"),
                    ("2026H1", "2026-01-01", "2026-06-30"),
                    ("2026Q3", "2026-07-01", "2026-09-30")]:
    seg = corr60[(corr60.index >= a) & (corr60.index <= b)].dropna()
    corr_tab.append((label, f"{seg.mean():.2f}", f"{seg.iloc[-1]:.2f}" if len(seg) else "—"))

# 分季度 beta（科创50 ~ SOX 隔夜）
def beta(y, x):
    m = np.isfinite(y) & np.isfinite(x)
    if m.sum() < 20:
        return np.nan
    return np.polyfit(x[m], y[m], 1)[0]

beta_tab = []
for label, a, b in periods:
    seg = valid[(valid.index >= a) & (valid.index <= b)]
    b_sox = beta(seg["科创50"].values, seg["SOX"].values)
    b_sh = beta(seg["科创50"].values, seg["上证指数"].values)
    beta_tab.append((label, f"{b_sox:.2f}" if np.isfinite(b_sox) else "—",
                     f"{b_sh:.2f}" if np.isfinite(b_sh) else "—"))

# 拥挤度代理：科创50 成交额 / 中证全指成交额，20 日均
ratio = (panel["科创50_amt"] / panel["全指_amt"])
ratio20 = ratio.rolling(20).mean()
cur = ratio20.dropna().iloc[-1]
pct = (ratio20.dropna() <= cur).mean()

style = (panel["科创50"] / panel["上证指数"])
style_ret_2026 = style[style.index >= "2026-01-01"]
style_dd = style_ret_2026.iloc[-1] / style_ret_2026.cummax() - 1

summary = f"""# 背离结构研究摘要（截至 2026-10-08 午盘）

## 1. 分季度相关性（科创50 日收益 vs 费半隔夜收益，滚动60日均值 / 期末值）
| 区间 | 60日均 | 期末 |
|---|---|---|
"""
for a, m, l in corr_tab:
    summary += f"| {a} | {m} | {l} |\n"
summary += """
## 2. 分季度 beta（科创50 ~ SOX 隔夜 / 科创50 ~ 上证）
| 区间 | β(SOX) | β(上证) |
|---|---|---|
"""
for label, bs, bh in beta_tab:
    summary += f"| {label} | {bs} | {bh} |\n"
summary += f"""
## 3. 拥挤度代理（科创50 成交额占沪深两市比重，20日均）
- 当前值: {cur * 100:.2f}%
- 2025 年以来分位: {pct * 100:.0f}%
- 2026 年以来科创50/上证相对净值最大回撤: {style_dd.iloc[-1] * 100:.1f}%
"""
open(f"{OUT}/summary.md", "w", encoding="utf-8").write(summary)
print(summary)

# 今日市场宽度（东财全A快照，带重试）
print("== 今日宽度 ==")
url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=6000&po=1&np=1"
       "&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
       "&fields=f2,f3,f12,f14")
d = None
rows = []
pn = 1
while True:
    page_url = url.replace("pz=6000", "pz=100") + f"&pn={pn}"
    got = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(page_url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
            got = d["data"]["diff"]
            break
        except Exception:
            time.sleep(2 * (attempt + 1))
    if not got:
        break
    rows.extend(got)
    total = d["data"].get("total", 0)
    if len(rows) >= total or pn > 80:
        break
    pn += 1
    time.sleep(0.3)
chs = np.array([r["f3"] for r in rows if isinstance(r["f3"], (int, float))])
breadth = {
    "样本数": len(chs),
    "上涨家数": int((chs > 0).sum()),
    "下跌家数": int((chs < 0).sum()),
    "涨跌幅中位数": float(np.median(chs)),
    "跌幅超5%家数": int((chs <= -5).sum()),
    "跌幅超9%家数": int((chs <= -9).sum()),
    "涨停家数(≥9.8%)": int((chs >= 9.8).sum()),
}
json.dump(breadth, open(f"{OUT}/breadth_20261008.json", "w"),
          ensure_ascii=False, indent=1)
print(breadth)
print("DONE ->", OUT)
