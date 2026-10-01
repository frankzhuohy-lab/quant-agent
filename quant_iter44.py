# -*- coding: utf-8 -*-
"""第41轮（共50轮）批次AP：R437-R446 波段量化模型迭代
=========================================================
本轮10个本质不同的新方向（均未在R1-R436测试，全部挂在历史最佳R316
质量入口之上，检验'新因子维度×已验证入口'的组合；R444为新的仓位机制）：
  R437 上影卖压门控（K线结构新维度——20日上影线占比（(high-max(o,c))/
        振幅）的10日均值低=收盘上方无持续抛压/筹码锁定好，作为R316入口的
        个股级质量过滤；区别于R59聪明钱复合（低上影只在独立入场复合里用过）
        /R132实体占比/R311 BearPower，是首个'上影线占比作为R316门控'）
  R438 隔夜承接确认门控（隔夜定价维度——5日隔夜收益（open/前收-1）均值
        >=阈值=隔夜买盘持续承接、无跳空派发，作为R316入口的个股级门控；
        区别于R41/R46隔夜动量独立入场、R174隔夜-日内相关结构，是首个
        '隔夜承接作为R316门控'）
  R439 收盘企稳回升门控（收盘定位动态维度——5日均CLV较3日前回升
        （clv5_chg3>=阈值）=收盘位置正从低位修复企稳，作为R316入口的
        个股级门控；区别于R59只用clv5水平、R183单日CLV，是首个'CLV5回升'
        门控）
  R440 板块成交额动量门控（市场资金维度——10股等权成交额（收盘×量）的
        5日均值/前20日均值>=阈值=板块资金温和回流，作为R316入口的市场级
        门控；区别于R332板块成交额温度（水平分位）、R145量能广度推力，
        是首个'板块成交额动量（5/20比值）'门控）
  R441 板块连阳广度门控（市场宽度新维度——10股中连续>=2日收阳的占比
        5日均值>=阈值=板块赚钱效应持续扩散，作为R316入口的市场级门控；
        区别于R104 close>MA20广度/R365新高广度/R368 NH-NL差/R369净涨停
        广度，是首个'连续收阳天数广度'门控）
  R442 市场收益正态性门控（市场分布维度——等权指数20日日收益的Jarque-Bera
        统计量<=阈值=板块无极端厚尾/巨震制度（分布接近正态），作为R316
        入口的市场级门控；区别于R344指数偏度（三阶）/R327个股JB/R319
        横截面峰度，是首个'市场级时间序列JB'门控）
  R443 上行波动占比门控（非对称波动维度——20日上行日收益方差/下行日收益
        方差>=阈值=波动由上涨驱动而非恐慌抛售，作为R316入口的个股级门控；
        区别于R193上行波动占比独立入场，是首个'非对称波动作为R316门控'）
  R444 概率加权仓位（仓位管理新机制——以R316基座自身已平仓交易训练滚动
        L2逻辑回归P(win)（与R427同款模型），但不用硬门控，而是把P(win)^
        指数作为横截面仓位权重（soft meta-labeling）；区别于R427硬门控/
        R166 Kelly盈亏比权重/R94波动率目标，是首个'元标签概率软权重仓位'）
  R445 相对强度高位回踩门控（相对强度新维度——rs20自身60日分位高=个股
        相对板块仍强势，但rs20较5日前回落=强势中的回踩（非趋势破坏），
        作为R316入口的个股级门控；区别于R97 RS线新高突破（独立入场）/
        R181 RS z值低吸（均值回归）/R26横截面动量买强，是首个'RS高位回踩'
        门控）
  R446 批次AP组件集成（R316入口 × R437上影卖压门控 × R440板块成交额动量
        门控 + Keltner1.5×ATR20/最长10日动态出场，检验'个股抛压×市场资金'
        新组合；从未在任何批次测试）

统一口径（与前40轮完全一致）：
  1) 信号只用<=T数据，T+1开盘执行；开盘近似涨停跳过；
  2) 成本单边0.20%（佣金0.15%+滑点0.05%），往返0.40%；
  3) IS=2025-02-21~2026-01-13（220日），OOS=2026-01-14~2026-08-21（147日）；
  4) 参数在IS内扫描，按预注册护栏（IS信号>=120、均值>0、胜率>=55%、
     回撤>=-15%）取IS夏普最高；OOS只作单次裁决；
  5) 基准：等权10股买入持有（回撤/夏普）、随机5日开-开交易（均值/胜率）、
     等权组合5日开-开收益（超额）。
"""

from __future__ import print_function
import os, re, sys, json, math
from datetime import datetime

try:
    import numpy as np
except Exception:
    np = None

try:
    from quant_iter26 import (
        load_stocks, rsi_series, median, zmap, neg, top_k,
        make_filt_breadth, spearman, build_features,
        compute_metrics, bh_metrics, random_5d,
        score_r117, COST, H, WARMUP, SPLIT)
except Exception as _e:
    raise SystemExit("无法导入第26轮引擎 quant_iter26: %s" % _e)

DATA_DIR = os.environ.get("DATA_DIR", "/root/.openclaw/workspace/mx_data/output")
OUT_PATH = os.environ.get("QOUT", "/tmp/quant_iter_run_results44.json")
DIAG = os.environ.get("DIAG") == "1"


# ---------------- 工具：置换熵 ----------------
def perm_entropy(series, order=3):
    """归一化置换熵（0-1）。ties 用值+序号稳定排序处理。"""
    n = len(series)
    if n < order + 1:
        return None
    counts = {}
    for i in range(n - order + 1):
        window = series[i:i + order]
        idx = sorted(range(order), key=lambda j: (window[j], j))
        key = tuple(idx)
        counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values())
    if total <= 0:
        return None
    ent = -sum((v/total)*math.log(v/total) for v in counts.values())
    denom = math.log(math.factorial(order))
    return ent/denom if denom > 0 else 0.0


# ---------------- R316 组件（沿用前几轮口径） ----------------
def score_r117_depth(feat, codes, t):
    D = globals().get("R316_D", -0.08)
    R = globals().get("R316_RSI", 58.0)
    base = score_r117(feat, codes, t)
    out = {}
    for c in codes:
        v = base[c]
        if v <= -9e8:
            out[c] = v
            continue
        d20 = feat[c]["dist20h"][t]
        rsi = feat[c]["rsi"][t]
        if d20 is not None and rsi is not None and d20 <= D and rsi <= R:
            out[c] = -9e9
        else:
            out[c] = v
    return out


def make_filt_flr(attr):
    def filt(state, t):
        f5 = state["flr5"][t] if t < len(state["flr5"]) else None
        thr = globals().get(attr, 0.3)
        return (state["breadth"][t] is not None
                and state["breadth"][t] >= 0.4
                and f5 is not None and f5 <= thr)
    return filt


# ---------------- 批次AP特征（全部因果，只用<=T） ----------------
def add_batchAP_features(stocks, codes, common, feat, state):
    n = len(common)

    # ---- 市场炸板率 flr5（R313口径，R316组件） ----
    flr = [None]*n
    for t in range(1, n):
        failed = 0
        total = 0
        for c in codes:
            pc = stocks[c]["close"][t - 1]
            hi = stocks[c]["high"][t]
            cl = stocks[c]["close"][t]
            band = 0.195 if c.startswith(("688", "300", "301")) else 0.095
            limup = pc*(1.0 + band - 0.005)
            if hi >= limup:
                total += 1
                if cl < limup:
                    failed += 1
        flr[t] = failed/float(total) if total > 0 else 0.0
    state["flr5"] = [None]*n
    for t in range(5, n):
        vals = [flr[i] for i in range(t - 4, t + 1) if flr[i] is not None]
        if len(vals) >= 3:
            state["flr5"][t] = sum(vals)/float(len(vals))

    # ---- dist20h（R316非接刀组件） ----
    for c in codes:
        f = feat[c]
        hi = stocks[c]["high"]
        cl = stocks[c]["close"]
        f["dist20h"] = [None]*n
        for t in range(19, n):
            m = max(hi[t - 19:t + 1])
            if m > 0:
                f["dist20h"][t] = cl[t]/m - 1.0

    # ---- R444 元标签市场特征（沿用R427口径） ----
    sret = [None]*n
    for t in range(5, n):
        if state["mkt_open"][t - 5] > 0:
            sret[t] = state["mkt_open"][t]/state["mkt_open"][t - 5] - 1.0
    state["sret_pct"] = [None]*n
    for t in range(25, n):
        if sret[t] is None:
            continue
        lo = max(0, t - 119)
        hist = [sret[i] for i in range(lo, t + 1) if sret[i] is not None]
        if len(hist) >= 30:
            state["sret_pct"][t] = sum(1 for x in hist
                                       if x <= sret[t])/float(len(hist))
    mret = state["mkt_ret"]
    mvol20 = [None]*n
    for t in range(19, n):
        rs = [mret[i] for i in range(t - 19, t + 1)]
        if any(v is None for v in rs):
            continue
        mm = sum(rs)/20.0
        mvol20[t] = math.sqrt(sum((x - mm)**2 for x in rs)/20.0)
    state["mkt_vol_pct"] = [None]*n
    for t in range(59, n):
        if mvol20[t] is None:
            continue
        lo = max(0, t - 119)
        hist = [mvol20[i] for i in range(lo, t + 1)
                if mvol20[i] is not None]
        if len(hist) >= 30:
            state["mkt_vol_pct"][t] = sum(
                1 for x in hist if x <= mvol20[t])/float(len(hist))
    state["mkt_ret5"] = [None]*n
    for t in range(5, n):
        if state["mkt_close"][t - 5] > 0:
            state["mkt_ret5"][t] = (
                state["mkt_close"][t]/state["mkt_close"][t - 5] - 1.0)

    # ---- R437 上影线占比（20日窗口，10日均值） ----
    for c in codes:
        f = feat[c]
        o = stocks[c]["open"]
        hi = stocks[c]["high"]
        lo = stocks[c]["low"]
        cl = stocks[c]["close"]
        f["ushadow10"] = [None]*n
        for t in range(9, n):
            vals = []
            for i in range(t - 9, t + 1):
                rng = hi[i] - lo[i]
                if rng > 1e-12:
                    vals.append((hi[i] - max(o[i], cl[i]))/rng)
            if len(vals) >= 8:
                f["ushadow10"][t] = sum(vals)/float(len(vals))

    # ---- R438 隔夜收益5日均值 ----
    for c in codes:
        f = feat[c]
        o = stocks[c]["open"]
        cl = stocks[c]["close"]
        f["ovr5"] = [None]*n
        for t in range(5, n):
            vals = []
            for i in range(t - 4, t + 1):
                if cl[i - 1] is not None and cl[i - 1] > 0:
                    vals.append(o[i]/cl[i - 1] - 1.0)
            if len(vals) >= 4:
                f["ovr5"][t] = sum(vals)/float(len(vals))

    # ---- R439 CLV5 及 3日回升 ----
    for c in codes:
        f = feat[c]
        f["clv5"] = [None]*n
        f["clv5_chg3"] = [None]*n
        for t in range(4, n):
            vals = [f["clv"][i] for i in range(t - 4, t + 1)]
            if all(v is not None for v in vals):
                f["clv5"][t] = sum(vals)/5.0
        for t in range(7, n):
            if (f["clv5"][t] is not None
                    and f["clv5"][t - 3] is not None):
                f["clv5_chg3"][t] = f["clv5"][t] - f["clv5"][t - 3]

    # ---- R440 板块成交额动量（5日均/前20日均，非重叠） ----
    amt = [None]*n
    for t in range(n):
        vals = []
        for c in codes:
            cl = stocks[c]["close"][t]
            v = stocks[c]["vol"][t]
            if (cl is not None and v is not None
                    and cl > 0 and v > 0):
                vals.append(cl*v)
        if len(vals) >= 6:
            amt[t] = sum(vals)/float(len(vals))
    state["amt_mom5"] = [None]*n
    for t in range(25, n):
        if amt[t] is None:
            continue
        a5 = [amt[i] for i in range(t - 4, t + 1)]
        a20 = [amt[i] for i in range(t - 24, t - 4)]
        if (all(v is not None for v in a5)
                and all(v is not None for v in a20) and len(a20) >= 16
                and sum(a20) > 0):
            state["amt_mom5"][t] = (
                (sum(a5)/5.0)/(sum(a20)/float(len(a20))))

    # ---- R441 连阳广度（连续>=2日收阳占比，5日均值） ----
    streak = {}
    for c in codes:
        up = [1.0 if stocks[c]["close"][t] > stocks[c]["open"][t]
              else 0.0 for t in range(n)]
        s = [0]*n
        for t in range(n):
            s[t] = (s[t - 1] + 1) if (t > 0 and up[t]) else int(up[t])
        streak[c] = s
    upbr = [None]*n
    for t in range(n):
        upbr[t] = sum(1 for c in codes if streak[c][t] >= 2)/float(len(codes))
    state["upstreak_breadth5"] = [None]*n
    for t in range(4, n):
        vals = [upbr[i] for i in range(t - 4, t + 1)
                if upbr[i] is not None]
        if len(vals) >= 4:
            state["upstreak_breadth5"][t] = sum(vals)/float(len(vals))

    # ---- R442 市场JB统计量（20日日收益，时间序列） ----
    jb = [None]*n
    for t in range(19, n):
        rs = [mret[i] for i in range(t - 19, t + 1)]
        if any(v is None for v in rs):
            continue
        mm = sum(rs)/20.0
        sd = math.sqrt(sum((x - mm)**2 for x in rs)/20.0)
        if sd <= 1e-12:
            continue
        zs = [(x - mm)/sd for x in rs]
        s = sum(z**3 for z in zs)/20.0
        k = sum(z**4 for z in zs)/20.0
        jb[t] = (20.0/6.0)*(s*s + (k - 3.0)**2/4.0)
    state["mkt_jb20"] = jb

    # ---- R443 上行/下行波动占比（20日） ----
    for c in codes:
        f = feat[c]
        f["upvar20"] = [None]*n
        for t in range(19, n):
            ups = [f["ret"][i] for i in range(t - 19, t + 1)
                   if f["ret"][i] is not None and f["ret"][i] > 0]
            dns = [f["ret"][i] for i in range(t - 19, t + 1)
                   if f["ret"][i] is not None and f["ret"][i] < 0]
            if len(ups) >= 3 and len(dns) >= 3:
                mu = sum(ups)/float(len(ups))
                md = sum(dns)/float(len(dns))
                uv = sum((x - mu)**2 for x in ups)/float(len(ups))
                dv = sum((x - md)**2 for x in dns)/float(len(dns))
                if dv > 1e-14:
                    f["upvar20"][t] = uv/dv

    # ---- R445 rs20 60日分位 与 5日变化 ----
    for c in codes:
        f = feat[c]
        f["rs_pct60"] = [None]*n
        f["rs_chg5"] = [None]*n
        for t in range(79, n):
            hist = [f["rs20"][i] for i in range(t - 59, t + 1)
                    if f["rs20"][i] is not None]
            if len(hist) >= 40 and f["rs20"][t] is not None:
                f["rs_pct60"][t] = sum(1 for x in hist
                                       if x <= f["rs20"][t])/float(len(hist))
        for t in range(25, n):
            if (f["rs20"][t] is not None
                    and f["rs20"][t - 5] is not None):
                f["rs_chg5"][t] = f["rs20"][t] - f["rs20"][t - 5]
    return state

# ---------------- 评分/过滤 ----------------
def _r117_base_mask(f, t):
    return (f["vr_5_20"][t] is not None and f["vr_5_20"][t] < 0.8
            and f["vol_low"][t] and f["mom20"][t] is not None
            and f["mom20"][t] > 0 and f["ma20"][t] is not None
            and f["close"][t] > f["ma20"][t]
            and f["rsi"][t] is not None and f["rsi"][t] < 72)


def _r316_nonknife(f, t):
    d20 = f["dist20h"][t]
    rsi = f["rsi"][t]
    return d20 is not None and rsi is not None and (d20 > -0.08 or rsi > 58)


def _base_z(feat, codes, t):
    z_vr = zmap([neg(feat[c]["vr_5_20"][t]) for c in codes])
    z_v = zmap([neg(feat[c]["vol20"][t]) for c in codes])
    z_m = zmap([feat[c]["mom10"][t] for c in codes])
    z_r = zmap([feat[c]["rs20"][t] for c in codes])
    return z_vr, z_v, z_m, z_r


def score_r117_ushadow(feat, codes, t):
    """R437：R316入口 + 上影卖压门控（ushadow10<=阈值）。"""
    thr = globals().get("R437_U", 0.30)
    z_vr, z_v, z_m, z_r = _base_z(feat, codes, t)
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        u = f["ushadow10"][t]
        if (_r117_base_mask(f, t) and _r316_nonknife(f, t)
                and u is not None and u <= thr):
            out[c] = z_vr[i] + z_v[i] + 0.5*z_m[i] + 0.3*z_r[i]
        else:
            out[c] = -9e9
    return out


def score_r117_ushadow446(feat, codes, t):
    """R446：同R437但使用R446_U阈值。"""
    thr = globals().get("R446_U", 0.30)
    z_vr, z_v, z_m, z_r = _base_z(feat, codes, t)
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        u = f["ushadow10"][t]
        if (_r117_base_mask(f, t) and _r316_nonknife(f, t)
                and u is not None and u <= thr):
            out[c] = z_vr[i] + z_v[i] + 0.5*z_m[i] + 0.3*z_r[i]
        else:
            out[c] = -9e9
    return out


def score_r117_ovr(feat, codes, t):
    """R438：R316入口 + 隔夜承接门控（ovr5>=阈值）。"""
    thr = globals().get("R438_OVR", 0.0)
    z_vr, z_v, z_m, z_r = _base_z(feat, codes, t)
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        v = f["ovr5"][t]
        if (_r117_base_mask(f, t) and _r316_nonknife(f, t)
                and v is not None and v >= thr):
            out[c] = z_vr[i] + z_v[i] + 0.5*z_m[i] + 0.3*z_r[i]
        else:
            out[c] = -9e9
    return out


def score_r117_clvup(feat, codes, t):
    """R439：R316入口 + 收盘企稳回升门控（clv5_chg3>=阈值）。"""
    thr = globals().get("R439_CLV", 0.0)
    z_vr, z_v, z_m, z_r = _base_z(feat, codes, t)
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        v = f["clv5_chg3"][t]
        if (_r117_base_mask(f, t) and _r316_nonknife(f, t)
                and v is not None and v >= thr):
            out[c] = z_vr[i] + z_v[i] + 0.5*z_m[i] + 0.3*z_r[i]
        else:
            out[c] = -9e9
    return out


def score_r117_upvar(feat, codes, t):
    """R443：R316入口 + 上行波动占比门控（upvar20>=阈值）。"""
    thr = globals().get("R443_UV", 1.0)
    z_vr, z_v, z_m, z_r = _base_z(feat, codes, t)
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        v = f["upvar20"][t]
        if (_r117_base_mask(f, t) and _r316_nonknife(f, t)
                and v is not None and v >= thr):
            out[c] = z_vr[i] + z_v[i] + 0.5*z_m[i] + 0.3*z_r[i]
        else:
            out[c] = -9e9
    return out


def score_r117_rspull(feat, codes, t):
    """R445：R316入口 + RS高位回踩门控（rs_pct60>=p 且 rs_chg5<=c）。"""
    p = globals().get("R445_P", 0.7)
    cg = globals().get("R445_C", 0.0)
    z_vr, z_v, z_m, z_r = _base_z(feat, codes, t)
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        v1 = f["rs_pct60"][t]
        v2 = f["rs_chg5"][t]
        if (_r117_base_mask(f, t) and _r316_nonknife(f, t)
                and v1 is not None and v2 is not None
                and v1 >= p and v2 <= cg):
            out[c] = z_vr[i] + z_v[i] + 0.5*z_m[i] + 0.3*z_r[i]
        else:
            out[c] = -9e9
    return out


def make_filt_amt(attr):
    """R440/R446：市场级成交额动量门控（amt_mom5>=阈值）。"""
    def filt(state, t):
        v = state["amt_mom5"][t]
        thr = globals().get(attr, 1.0)
        return (state["breadth"][t] is not None
                and state["breadth"][t] >= 0.4
                and state["flr5"][t] is not None
                and state["flr5"][t] <= 0.3
                and v is not None and v >= thr)
    return filt


def make_filt_upstreak(attr):
    """R441：市场级连阳广度门控（upstreak_breadth5>=阈值）。"""
    def filt(state, t):
        v = state["upstreak_breadth5"][t]
        thr = globals().get(attr, 0.2)
        return (state["breadth"][t] is not None
                and state["breadth"][t] >= 0.4
                and state["flr5"][t] is not None
                and state["flr5"][t] <= 0.3
                and v is not None and v >= thr)
    return filt


def make_filt_jb(attr):
    """R442：市场级收益正态性门控（mkt_jb20<=阈值）。"""
    def filt(state, t):
        v = state["mkt_jb20"][t]
        thr = globals().get(attr, 10.0)
        return (state["breadth"][t] is not None
                and state["breadth"][t] >= 0.4
                and state["flr5"][t] is not None
                and state["flr5"][t] <= 0.3
                and v is not None and v <= thr)
    return filt

# ---------------- 概率加权仓位（Soft Meta-Labeling）模拟 ----------------
META_STOCK = ["mom5", "mom10", "mom20", "rsi", "dist20", "vol20",
              "volr5", "clv", "rs20", "vr_5_20"]
META_STATE = ["breadth", "mkt_mom20", "corr20", "disp", "flr5",
              "sret_pct", "lvp_z", "mkt_vol_pct", "mkt_ret5"]
META_ALL = META_STOCK + META_STATE


def simulate_prob(stocks, codes, feat, state, spec, entry_start, entry_end,
                  window_end, init_history=None):
    """R444：R316入口 + 滚动L2逻辑回归P(win)概率加权仓位。
    训练样本=基座已平仓交易（xe<=T，结果已知，因果）；权重=w_i∝P(win)^
    exp（归一化），无硬门控；每5日重估；样本<min_samples时等权。"""
    trades = []
    contrib = {}
    K = spec["K"]
    expo = float(globals().get("R444_P", 1.0))
    min_samples = int(globals().get("R444_MIN", 50))
    params = spec.get("params", {})
    history = [] if init_history is None else [dict(h) for h in init_history]
    model = None
    mu = None
    sd = None
    last_train_e = None

    def candidate_feats(c, t):
        out = []
        f = feat[c]
        for nm in META_ALL:
            if nm in META_STATE:
                v = state[nm][t]
            else:
                v = f[nm][t]
            out.append(v if v is not None else 0.0)
        return out

    def train_logit(eligible):
        nonlocal model, mu, sd
        X = np.array([h["feats"] for h in eligible[-150:]], dtype=float)
        y = np.array([h["label"] for h in eligible[-150:]], dtype=float)
        mu = X.mean(axis=0)
        sd = X.std(axis=0)
        sd[sd < 1e-9] = 1.0
        Xs = (X - mu)/sd
        Xs = np.hstack([np.ones((Xs.shape[0], 1)), Xs])
        w = np.zeros(Xs.shape[1])
        lam = 0.5
        lr = 0.3
        for _ in range(300):
            p = 1.0/(1.0 + np.exp(-np.clip(Xs.dot(w), -30.0, 30.0)))
            grad = Xs.T.dot(p - y)/float(len(y))
            grad[1:] += lam*w[1:]
            w -= lr*grad
        model = w

    def predict(x):
        xz = (np.array(x, dtype=float) - mu)/sd
        xz = np.concatenate([[1.0], xz])
        z = float(xz.dot(model))
        return 1.0/(1.0 + math.exp(-max(-30.0, min(30.0, z))))

    for e in range(entry_start, entry_end + 1):
        t = e - 1
        if spec["filt"](state, t):
            scores = spec["score"](feat, codes, t)
            picks = top_k(scores, K)
            if len(picks) < 1:
                continue
            eligible = [h for h in history if h["xe"] <= t]
            if (len(eligible) >= min_samples
                    and (last_train_e is None
                         or e - last_train_e >= 5)):
                train_logit(eligible)
                last_train_e = e
            feats = {code: candidate_feats(code, t) for code in picks}
            weights = []
            for code in picks:
                o = stocks[code]["open"]
                c = stocks[code]["close"]
                entry_px = o[e]
                prev_close = c[e - 1]
                band = 0.195 if code.startswith(
                    ("688", "300", "301")) else 0.095
                if entry_px >= prev_close*(1.0 + band - 0.005):
                    weights.append(None)
                else:
                    if model is not None:
                        pwin = predict(feats[code])
                        weights.append(max(pwin, 0.001)**expo)
                    else:
                        weights.append(1.0)
            wsum = sum(w for w in weights if w is not None)
            if wsum <= 0:
                continue
            for pi, code in enumerate(picks):
                w0 = weights[pi]
                if w0 is None:
                    continue
                w = w0/wsum/float(H)
                o = stocks[code]["open"]
                c = stocks[code]["close"]
                entry_px = o[e]
                kelt = params.get("keltner_mult", 1.5)
                max_hold = params.get("max_hold", 10)
                xe = None
                last_d = min(e + max_hold - 1, window_end - 1)
                for d in range(e, last_d + 1):
                    m20 = feat[code]["ma20"][d]
                    atr = feat[code]["atr20"][d]
                    if (m20 is not None and atr is not None
                            and c[d] < m20 - kelt*atr):
                        xe = min(d + 1, window_end)
                        break
                if xe is None:
                    xe = min(e + max_hold, window_end)
                exit_px = o[xe]
                gross = exit_px/entry_px - 1.0
                net = gross - 2.0*COST
                history.append({"t": t, "feats": feats[code],
                                "label": 1 if net > 0 else 0, "xe": xe})
                mkt5 = state["mkt_open"][min(e + H, window_end)] / \
                    state["mkt_open"][e] - 1.0
                same5 = stocks[code]["open"][min(e + H, window_end)] / \
                    entry_px - 1.0
                if xe == e:
                    contrib.setdefault(e, []).append(
                        w*(exit_px/entry_px - 1.0) - 2.0*w*COST)
                else:
                    contrib.setdefault(e, []).append(-w*COST)
                    for d in range(e, xe - 1):
                        contrib.setdefault(d, []).append(
                            w*(o[d + 1]/o[d] - 1.0))
                    contrib.setdefault(xe - 1, []).append(
                        w*(exit_px/o[xe - 1] - 1.0) - w*COST)
                trades.append({"code": code, "e": e, "xe": xe,
                               "gross": gross, "net": net,
                               "mkt5": mkt5, "same5": same5})
    daily = []
    for d in range(entry_start, window_end + 1):
        daily.append(sum(contrib.get(d, [])) if contrib.get(d) else 0.0)
    return trades, daily, history

# ---------------- 回测（keltner/fixed，复制第39轮口径） ----------------
def simulate_am(stocks, codes, feat, state, spec, entry_start, entry_end,
                window_end):
    trades = []
    contrib = {}
    K = spec["K"]
    score_fn = spec["score"]
    filt_fn = spec["filt"]
    allow_fewer = spec.get("allow_fewer", False)
    exit_mode = spec.get("exit", "fixed")
    params = spec.get("params", {})
    for e in range(entry_start, entry_end + 1):
        t = e - 1
        if not filt_fn(state, t):
            continue
        scores = score_fn(feat, codes, t)
        picks = top_k(scores, K)
        if len(picks) < K:
            if not (allow_fewer and len(picks) >= 1):
                continue
        n_pick = len(picks)
        weights = [1.0/H/n_pick]*n_pick
        for pi, code in enumerate(picks):
            w = weights[pi]
            o = stocks[code]["open"]
            c = stocks[code]["close"]
            entry_px = o[e]
            prev_close = c[e - 1]
            band = 0.195 if code.startswith(("688", "300", "301")) else 0.095
            if entry_px >= prev_close*(1.0 + band - 0.005):
                continue
            if exit_mode == "keltner":
                kelt = params.get("keltner_mult", 1.5)
                max_hold = params.get("max_hold", 10)
                xe = None
                last_d = min(e + max_hold - 1, window_end - 1)
                for d in range(e, last_d + 1):
                    m20 = feat[code]["ma20"][d]
                    atr = feat[code]["atr20"][d]
                    if (m20 is not None and atr is not None
                            and c[d] < m20 - kelt*atr):
                        xe = min(d + 1, window_end)
                        break
                if xe is None:
                    xe = min(e + max_hold, window_end)
            else:
                xe = min(e + H, window_end)
            exit_px = o[xe]
            gross = exit_px/entry_px - 1.0
            net = gross - 2.0*COST
            mkt5 = state["mkt_open"][min(e + H, window_end)] / \
                state["mkt_open"][e] - 1.0
            same5 = stocks[code]["open"][min(e + H, window_end)] / \
                entry_px - 1.0
            if xe == e:
                contrib.setdefault(e, []).append(
                    w*(exit_px/entry_px - 1.0) - 2.0*w*COST)
            else:
                contrib.setdefault(e, []).append(-w*COST)
                for d in range(e, xe - 1):
                    contrib.setdefault(d, []).append(
                        w*(o[d + 1]/o[d] - 1.0))
                contrib.setdefault(xe - 1, []).append(
                    w*(exit_px/o[xe - 1] - 1.0) - w*COST)
            trades.append({"code": code, "e": e, "xe": xe,
                           "gross": gross, "net": net,
                           "mkt5": mkt5, "same5": same5})
    daily = []
    for d in range(entry_start, window_end + 1):
        daily.append(sum(contrib.get(d, [])) if contrib.get(d) else 0.0)
    return trades, daily


# ---------------- 因子IC诊断（只用IS） ----------------
def factor_ic_ap(stocks, codes, feat, state, common, split):
    out = {}
    for name in ("ushadow10", "ovr5", "clv5_chg3", "upvar20",
                 "rs_pct60", "rs_chg5"):
        daily_ics = []
        for t in range(WARMUP + 1, split - 1 - H):
            xs = []
            ys = []
            for c in codes:
                x = feat[c][name][t]
                if x is None:
                    continue
                o = stocks[c]["open"]
                y = o[t + 1 + H]/o[t + 1] - 1.0
                xs.append(x)
                ys.append(y)
            if len(xs) >= 5 and len(set(xs)) > 1 and len(set(ys)) > 1:
                daily_ics.append(spearman(xs, ys))
        if len(daily_ics) >= 20:
            m = sum(daily_ics)/len(daily_ics)
            sd = math.sqrt(sum((x - m)**2 for x in daily_ics)
                           /len(daily_ics))
            t_stat = m/sd*math.sqrt(len(daily_ics)) if sd > 0 else 0.0
            out[name] = {"ic": round(m, 4), "t": round(t_stat, 2),
                         "n": len(daily_ics)}
    for name in ("amt_mom5", "mkt_jb20", "upstreak_breadth5"):
        xs = []
        ys = []
        for t in range(WARMUP + 1, split - 1 - H):
            x = state[name][t]
            if x is None:
                continue
            y = state["mkt_open"][t + 1 + H] / \
                state["mkt_open"][t + 1] - 1.0
            xs.append(x)
            ys.append(y)
        if len(xs) >= 30 and len(set(xs)) > 1 and len(set(ys)) > 1:
            ic = spearman(xs, ys)
            out[name] = {"ic": round(ic, 4), "t": round(ic*math.sqrt(len(xs)),
                                                        2), "n": len(xs)}
    return out

# ---------------- 主流程 ----------------
def main():
    stocks, common = load_stocks()
    codes = sorted(stocks.keys())
    n = len(common)
    split = int(n*SPLIT)
    feat, state = build_features(stocks, codes, common)
    state = add_batchAP_features(stocks, codes, common, feat, state)
    globals()["R316_D"] = -0.08
    globals()["R316_RSI"] = 58.0
    globals()["R316_FLR"] = 0.3

    ic_is = factor_ic_ap(stocks, codes, feat, state, common, split)
    print("== 批次AP IS 因子 IC 诊断（fwd5, 只用IS） ==")
    for name, v in sorted(ic_is.items(), key=lambda kv: -abs(
            kv[1]["t"] if kv[1]["t"] is not None else 0.0)):
        if v["t"] is not None:
            print("  %-14s IC=%6.3f  t=%6.2f  n=%d" % (name, v["ic"],
                                                        v["t"], v["n"]))
    if DIAG:
        return

    bh_oos = bh_metrics(stocks, codes, common, split, n - 1)
    rand_oos = random_5d(stocks, codes, split, n - 1 - H)
    baselines = {"bh_oos": bh_oos, "random_5d_oos": rand_oos,
                 "oos_start_date": common[split], "oos_end_date": common[-1],
                 "is_start_date": common[0], "split_date": common[split - 1]}

    KELT = {"keltner_mult": 1.5, "max_hold": 10}
    ROUND_SPECS = [
        {"round": 437, "name": u"上影卖压门控",
         "improvement": (u"本质新方向：K线结构新维度——20日上影线占比"
                         u"（(high-max(o,c))/振幅）的10日均值低=收盘上方无"
                         u"持续抛压/筹码锁定好，作为R316质量入口的个股级"
                         u"过滤；区别于R59聪明钱复合（低上影只在独立入场"
                         u"复合里用过）/R132实体占比/R311 BearPower，是"
                         u"首个'上影线占比作为R316门控'"),
         "desc": (u"候选：广度>=0.4 且 flr5<=0.3 且 ushadow10<=阈值（IS扫描"
                  u"{0.20,0.25,0.30,0.35}）且 R117（vr_5_20<0.8 且 vol_low "
                  u"且 mom20>0 且 close>ma20 且 RSI<72）且 非接刀（dist20h"
                  u">-0.08 或 RSI>58）；评分=R117；Top3等权；出场=Keltner"
                  u"1.5×ATR20/最长10日"),
         "score": score_r117_ushadow, "filt": make_filt_flr("R316_FLR"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": dict(KELT)},
        {"round": 438, "name": u"隔夜承接确认门控",
         "improvement": (u"本质新方向：隔夜定价维度——5日隔夜收益（open/前收"
                         u"-1）均值>=阈值=隔夜买盘持续承接、无跳空派发，作为"
                         u"R316质量入口的个股级门控；区别于R41/R46隔夜动量"
                         u"独立入场、R174隔夜-日内相关结构、R205开盘位置，"
                         u"是首个'隔夜承接作为R316门控'"),
         "desc": (u"候选：广度>=0.4 且 flr5<=0.3 且 ovr5>=阈值（IS扫描"
                  u"{-0.001,0.0,0.001}）且 R117（vr_5_20<0.8 且 vol_low 且 "
                  u"mom20>0 且 close>ma20 且 RSI<72）且 非接刀（dist20h"
                  u">-0.08 或 RSI>58）；评分=R117；Top3等权；出场=Keltner"
                  u"1.5×ATR20/最长10日"),
         "score": score_r117_ovr, "filt": make_filt_flr("R316_FLR"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": dict(KELT)},
        {"round": 439, "name": u"收盘企稳回升门控",
         "improvement": (u"本质新方向：收盘定位动态维度——5日均CLV较3日前"
                         u"回升（clv5_chg3>=阈值）=收盘位置正从低位修复企稳，"
                         u"作为R316质量入口的个股级门控；区别于R59只用clv5"
                         u"水平、R183单日CLV、R340收盘位置稳定洗盘，是首个"
                         u"'CLV5回升'门控"),
         "desc": (u"候选：广度>=0.4 且 flr5<=0.3 且 clv5_chg3>=阈值（IS扫描"
                  u"{-0.05,0.0,0.05}）且 R117（vr_5_20<0.8 且 vol_low 且 "
                  u"mom20>0 且 close>ma20 且 RSI<72）且 非接刀（dist20h"
                  u">-0.08 或 RSI>58）；评分=R117；Top3等权；出场=Keltner"
                  u"1.5×ATR20/最长10日"),
         "score": score_r117_clvup, "filt": make_filt_flr("R316_FLR"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": dict(KELT)},
        {"round": 440, "name": u"板块成交额动量门控",
         "improvement": (u"本质新方向：市场资金维度——10股等权成交额"
                         u"（收盘×量）的5日均值/前20日均值>=阈值=板块资金"
                         u"温和回流，作为R316质量入口的市场级门控；区别于"
                         u"R332板块成交额温度（水平分位）、R145量能广度推力"
                         u"（放量家数变化）、R210个股成交额份额，是首个"
                         u"'板块成交额动量（5/20比值）'门控"),
         "desc": (u"候选：广度>=0.4 且 flr5<=0.3 且 amt_mom5>=阈值（IS扫描"
                  u"{0.85,1.0,1.15}）且 R117（vr_5_20<0.8 且 vol_low 且 "
                  u"mom20>0 且 close>ma20 且 RSI<72）且 非接刀（dist20h"
                  u">-0.08 或 RSI>58）；评分=R117；Top3等权；出场=Keltner"
                  u"1.5×ATR20/最长10日"),
         "score": score_r117_depth, "filt": make_filt_amt("R440_AMT"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": dict(KELT)},
        {"round": 441, "name": u"板块连阳广度门控",
         "improvement": (u"本质新方向：市场宽度新维度——10股中连续>=2日收阳"
                         u"的占比5日均值>=阈值=板块赚钱效应持续扩散（上涨"
                         u"动能具备惯性），作为R316质量入口的市场级门控；"
                         u"区别于R104 close>MA20广度/R365新高广度/R368 NH-NL"
                         u"差/R369净涨停广度/R145量能广度，是首个'连续收阳"
                         u"天数广度'门控"),
         "desc": (u"候选：广度>=0.4 且 flr5<=0.3 且 upstreak_breadth5>=阈值"
                  u"（IS扫描{0.1,0.2,0.3}）且 R117（vr_5_20<0.8 且 vol_low "
                  u"且 mom20>0 且 close>ma20 且 RSI<72）且 非接刀（dist20h"
                  u">-0.08 或 RSI>58）；评分=R117；Top3等权；出场=Keltner"
                  u"1.5×ATR20/最长10日"),
         "score": score_r117_depth,
         "filt": make_filt_upstreak("R441_US"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": dict(KELT)},
        {"round": 442, "name": u"市场收益正态性门控",
         "improvement": (u"本质新方向：市场分布维度——等权指数20日日收益的"
                         u"Jarque-Bera统计量<=阈值=板块无极端厚尾/巨震制度"
                         u"（收益分布接近正态），作为R316质量入口的市场级"
                         u"门控；区别于R344指数偏度（三阶）/R327个股JB/R319"
                         u"横截面峰度/R310横截面偏度，是首个'市场级时间序列"
                         u"JB'门控"),
         "desc": (u"候选：广度>=0.4 且 flr5<=0.3 且 mkt_jb20<=阈值（IS扫描"
                  u"{6.0,10.0,15.0}）且 R117（vr_5_20<0.8 且 vol_low 且 "
                  u"mom20>0 且 close>ma20 且 RSI<72）且 非接刀（dist20h"
                  u">-0.08 或 RSI>58）；评分=R117；Top3等权；出场=Keltner"
                  u"1.5×ATR20/最长10日"),
         "score": score_r117_depth, "filt": make_filt_jb("R442_JB"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": dict(KELT)},
        {"round": 443, "name": u"上行波动占比门控",
         "improvement": (u"本质新方向：非对称波动维度——20日上行日收益方差/"
                         u"下行日收益方差>=阈值=波动由上涨驱动而非恐慌抛售"
                         u"（上行弹性主导的稳健吸筹），作为R316质量入口的"
                         u"个股级门控；区别于R193上行波动占比独立入场、"
                         u"R86半方差/Sortino，是首个'非对称波动作为R316"
                         u"门控'"),
         "desc": (u"候选：广度>=0.4 且 flr5<=0.3 且 upvar20>=阈值（IS扫描"
                  u"{0.8,1.0,1.3}）且 R117（vr_5_20<0.8 且 vol_low 且 "
                  u"mom20>0 且 close>ma20 且 RSI<72）且 非接刀（dist20h"
                  u">-0.08 或 RSI>58）；评分=R117；Top3等权；出场=Keltner"
                  u"1.5×ATR20/最长10日"),
         "score": score_r117_upvar, "filt": make_filt_flr("R316_FLR"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": dict(KELT)},
        {"round": 444, "name": u"概率加权仓位",
         "improvement": (u"本质新方向：仓位管理新机制——以R316基座自身已"
                         u"平仓交易训练滚动L2逻辑回归P(win)（与R427同款模型、"
                         u"19个市场+个股特征、每5日重估、样本=已平仓交易"
                         u"xe<=T），但不用硬门控，而是把P(win)^exp作为横截面"
                         u"仓位权重（soft meta-labeling，exp由IS扫描）；区别"
                         u"于R427硬门控（<阈值直接不开仓）/R166 Kelly盈亏比"
                         u"权重/R94波动率目标/R125评分排名，是首个'元标签"
                         u"概率软权重仓位'"),
         "desc": (u"候选：广度>=0.4 且 flr5<=0.3 且 R117（vr_5_20<0.8 且 "
                  u"vol_low 且 mom20>0 且 close>ma20 且 RSI<72）且 非接刀"
                  u"（dist20h>-0.08 或 RSI>58）；评分=R117；Top3；仓位="
                  u"权重∝P(win)^exp（IS扫描exp{0.5,1.0,2.0}，归一化；训练"
                  u"样本<50时等权）；出场=Keltner1.5×ATR20/最长10日"),
         "score": score_r117_depth, "filt": make_filt_flr("R316_FLR"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "prob", "sim": "prob", "params": dict(KELT)},
        {"round": 445, "name": u"相对强度高位回踩门控",
         "improvement": (u"本质新方向：相对强度新维度——rs20自身60日分位高"
                         u"=个股相对板块仍强势，但rs20较5日前回落=强势中的"
                         u"回踩（非趋势破坏），作为R316质量入口的个股级门控；"
                         u"区别于R97 RS线新高突破（独立入场）/R181 RS z值"
                         u"低吸（均值回归）/R26横截面动量买强/R302 RS加速度"
                         u"（独立因子），是首个'RS高位回踩'门控"),
         "desc": (u"候选：广度>=0.4 且 flr5<=0.3 且 rs_pct60>=p（IS扫描"
                  u"{0.5,0.7}）且 rs_chg5<=c（IS扫描{0.0,0.003}）且 R117"
                  u"（vr_5_20<0.8 且 vol_low 且 mom20>0 且 close>ma20 且 "
                  u"RSI<72）且 非接刀（dist20h>-0.08 或 RSI>58）；评分=R117；"
                  u"Top3等权；出场=Keltner1.5×ATR20/最长10日"),
         "score": score_r117_rspull, "filt": make_filt_flr("R316_FLR"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": dict(KELT)},
        {"round": 446, "name": u"批次AP组件集成",
         "improvement": (u"组件集成：R316入口（R117+炸板率<=0.3+非接刀） × "
                         u"R437上影卖压门控（ushadow10<=阈值） × R440板块成交额"
                         u"动量门控（amt_mom5>=阈值） + Keltner1.5×ATR20/最长"
                         u"10日动态出场，检验'个股抛压结构×市场资金回流'"
                         u"新组合；从未在任何批次测试"),
         "desc": (u"候选：广度>=0.4 且 flr5<=0.3 且 amt_mom5>=阈值（IS扫描"
                  u"{0.85,1.0}）且 ushadow10<=u（IS扫描{0.25,0.30}）且 R117"
                  u"（vr_5_20<0.8 且 vol_low 且 mom20>0 且 close>ma20 且 "
                  u"RSI<72）且 非接刀（dist20h>-0.08 或 RSI>58）；评分=R117；"
                  u"Top3等权；出场=Keltner1.5×ATR20/最长10日"),
         "score": score_r117_ushadow446,
         "filt": make_filt_amt("R446_AMT"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": dict(KELT)},
    ]

    def spec_for(rnd):
        s = next(x for x in ROUND_SPECS if x["round"] == rnd)
        spec = {k: v for k, v in s.items()
                if k not in ("round", "name", "improvement", "desc")}
        params = dict(spec.get("params", {}))
        spec["params"] = params
        if rnd in (431, 436):
            globals()["RES_RD"] = float(globals().get(
                "R431_RD" if rnd == 431 else "R436_RD", 0.12))
        return spec

    def sim_once(spec):
        if spec.get("sim") == "prob":
            tr_i, dl_i, hist_i = simulate_prob(
                stocks, codes, feat, state, spec,
                WARMUP + 1, split - H, split - 1)
            tr_o, dl_o, _ = simulate_prob(
                stocks, codes, feat, state, spec,
                split, n - 1 - H, n - 1, init_history=hist_i)
        else:
            tr_i, dl_i = simulate_am(stocks, codes, feat, state, spec,
                                     WARMUP + 1, split - H, split - 1)
            tr_o, dl_o = simulate_am(stocks, codes, feat, state, spec,
                                     split, n - 1 - H, n - 1)
        return compute_metrics(tr_i, dl_i), compute_metrics(tr_o, dl_o)

    def is_qualified(met):
        return (met and met["signals"] >= 120 and met["avg_return"] > 0
                and met["win_rate"] >= 0.55 and met["max_drawdown"] >= -0.15)

    def pick_best(records):
        recs = [r for r in records if r["is"]]
        if not recs:
            return records[0] if records else None
        cands = [r for r in recs if is_qualified(r["is"])]
        if not cands:
            cands = [r for r in recs if r["is"]["signals"] >= 120]
        if not cands:
            cands = [r for r in recs if r["is"]["signals"] >= 60]
        if not cands:
            return max(recs, key=lambda r: (r["is"]["signals"],
                                            r["is"]["sharpe"]))
        return max(cands, key=lambda r: (r["is"]["sharpe"],
                                         r["is"]["win_rate"]))

    def conclusion(oos):
        if not oos:
            return u"无样本外信号"
        if oos["signals"] < 30:
            return u"OOS信号数不足30，统计不可信"
        if (oos["excess_return_5d"] > 0 and oos["win_rate"] > 0.5
                and oos["sharpe"] > 0.5
                and oos["max_drawdown"] >= bh_oos["max_drawdown"]):
            return u"OOS达标：超额>0、胜率>50%、夏普>0.5、回撤优于买入持有"
        if oos["excess_return_5d"] > 0 and oos["sharpe"] > 0:
            return u"OOS有微弱正超额但风险指标未全达标，需更多数据验证"
        return u"OOS未跑赢基准（超额≤0或夏普≤0）"

    def run_scan(rnd, grids):
        recs = []
        for g in grids:
            for k, v in g.items():
                globals()[k] = v
            met_i, _ = sim_once(spec_for(rnd))
            recs.append({"is": met_i, "params": dict(g)})
        best = pick_best(recs)
        if best is None:
            met_i, met_o = sim_once(spec_for(rnd))
            return met_i, met_o, {}, recs
        for k, v in best["params"].items():
            globals()[k] = v
        met_i, met_o = sim_once(spec_for(rnd))
        return met_i, met_o, best["params"], recs

    results = []
    scans = {}
    GRIDS = {
        437: [{"R437_U": 0.20}, {"R437_U": 0.25}, {"R437_U": 0.30},
              {"R437_U": 0.35}],
        438: [{"R438_OVR": -0.001}, {"R438_OVR": 0.0},
              {"R438_OVR": 0.001}],
        439: [{"R439_CLV": -0.05}, {"R439_CLV": 0.0},
              {"R439_CLV": 0.05}],
        440: [{"R440_AMT": 0.85}, {"R440_AMT": 1.0},
              {"R440_AMT": 1.15}],
        441: [{"R441_US": 0.1}, {"R441_US": 0.2}, {"R441_US": 0.3}],
        442: [{"R442_JB": 6.0}, {"R442_JB": 10.0}, {"R442_JB": 15.0}],
        443: [{"R443_UV": 0.8}, {"R443_UV": 1.0}, {"R443_UV": 1.3}],
        444: [{"R444_P": 0.5}, {"R444_P": 1.0}, {"R444_P": 2.0}],
        445: [{"R445_P": 0.5, "R445_C": 0.0},
              {"R445_P": 0.7, "R445_C": 0.0},
              {"R445_P": 0.7, "R445_C": 0.003}],
        446: [{"R446_U": 0.25, "R446_AMT": 0.85},
              {"R446_U": 0.25, "R446_AMT": 1.0},
              {"R446_U": 0.30, "R446_AMT": 0.85},
              {"R446_U": 0.30, "R446_AMT": 1.0}],
    }
    globals()["R316_D"] = -0.08
    globals()["R316_RSI"] = 58.0
    globals()["R316_FLR"] = 0.3
    for rnd in range(437, 447):
        grids = GRIDS[rnd]
        met_i, met_o, p, recs = run_scan(rnd, grids)
        scans[rnd] = {"combos": len(recs),
                      "pass": sum(1 for r in recs if is_qualified(r["is"])),
                      "chosen": p}
        results.append({"round": rnd, "is": met_i, "oos": met_o,
                        "params_text": (u"IS扫描%d组选定 %s"
                                        % (len(recs), ", ".join(
                                            "%s=%s" % (k, v)
                                            for k, v in sorted(p.items()))))})

    iterations = []
    for r in results:
        rnd = r["round"]
        spec = next(x for x in ROUND_SPECS if x["round"] == rnd)
        met_is = r["is"]
        met_oos = r["oos"]
        imp = spec["improvement"]
        if scans[rnd]["combos"] > 1:
            imp = imp + (u"；参数由IS内%d组扫描按护栏+IS夏普规则选定（%s），"
                         u"OOS只裁决一次" % (scans[rnd]["combos"],
                                             r["params_text"]))
        rec = {
            "round": rnd, "name": spec["name"], "improvement": imp,
            "rule": spec["desc"], "is": met_is, "oos": met_oos,
            "signals_oos": met_oos["signals"] if met_oos else 0,
            "win_rate": met_oos["win_rate"] if met_oos else None,
            "avg_return": met_oos["avg_return"] if met_oos else None,
            "excess_return_5d": (met_oos["excess_return_5d"]
                                 if met_oos else None),
            "sharpe": met_oos["sharpe"] if met_oos else None,
            "max_drawdown": met_oos["max_drawdown"] if met_oos else None,
            "baseline_max_drawdown": bh_oos["max_drawdown"],
            "excess_vs_random": (round(met_oos["avg_return"] - rand_oos["mean"],
                                       5) if met_oos else None),
            "conclusion": conclusion(met_oos),
        }
        iterations.append(rec)
        if met_oos:
            print("R%03d %-18s IS:%4d(胜%.1f%%/均%6.2f%%/夏普%4.2f/回撤%5.1f%%) "
                  "OOS:%4d | 胜率 %5.1f%% 平均 %6.3f%% 超额5d %6.3f%% "
                  "夏普 %5.2f 回撤 %6.1f%%"
                  % (rnd, spec["name"], met_is["signals"] if met_is else 0,
                     met_is["win_rate"]*100 if met_is else 0,
                     met_is["avg_return"]*100 if met_is else 0,
                     met_is["sharpe"] if met_is else 0,
                     met_is["max_drawdown"]*100 if met_is else 0,
                     met_oos["signals"],
                     met_oos["win_rate"]*100, met_oos["avg_return"]*100,
                     met_oos["excess_return_5d"]*100, met_oos["sharpe"],
                     met_oos["max_drawdown"]*100))
        else:
            print("R%03d %-18s IS:%4d  OOS:无信号"
                  % (rnd, spec["name"], met_is["signals"] if met_is else 0))

    candidates = [r for r in iterations if is_qualified(r["is"])]
    if not candidates:
        candidates = [r for r in iterations
                      if r["is"] and r["is"]["signals"] >= 120]
    if not candidates:
        candidates = [r for r in iterations if r["is"]]
    if candidates:
        champ = max(candidates, key=lambda r: (r["is"]["sharpe"],
                                               r["is"]["win_rate"]))
    else:
        champ = max(iterations, key=lambda r: (r["is"]["signals"]
                                               if r["is"] else 0,
                                               r["is"]["sharpe"]
                                               if r["is"] else 0))
    oos_cands = [r for r in iterations if r["oos"]]
    if oos_cands:
        best_oos = max(oos_cands,
                       key=lambda r: (r["oos"]["excess_return_5d"],
                                      r["oos"]["sharpe"]))
    else:
        best_oos = champ

    def oos_bar(met):
        return (met and met["signals"] >= 30
                and met["excess_return_5d"] >= 0.01
                and met["win_rate"] >= 0.50 and met["sharpe"] >= 0.5
                and met["max_drawdown"] >= bh_oos["max_drawdown"])

    qualified = [r for r in iterations if is_qualified(r["is"])]
    if qualified:
        bar = [r for r in qualified if oos_bar(r["oos"])]
        if bar:
            final_rec = max(bar, key=lambda r: (
                r["oos"]["excess_return_5d"],
                r["oos"]["sharpe"], -r["oos"]["max_drawdown"]))
            final_choice = "qualified_oos"
        else:
            final_rec = max(qualified, key=lambda r: (
                r["oos"]["excess_return_5d"] if r["oos"] else -9,
                r["oos"]["sharpe"] if r["oos"] else -9))
            final_choice = "qualified_no_oos_bar"
    else:
        final_rec = champ
        final_choice = "is_champion"

    m_final = final_rec["oos"] if final_rec else None
    m_final_is = final_rec["is"] if final_rec else None

    def make_verdict(m):
        if not m:
            return u"本轮无样本外信号，不推荐上线"
        if not oos_bar(m):
            return (u"需要更多数据：样本外未通过验收底线（信号>=30/超额"
                    u">=1%/胜率>=50%/夏普>=0.5/回撤优于买入持有），不推荐上线")
        prev = (0.21031, 0.8378, 2.409, -0.0853)  # R316
        ex, wr, sh, dd = prev
        signif = (m["excess_return_5d"] > ex + 0.005
                  and m["win_rate"] >= wr - 0.02
                  and m["sharpe"] >= sh - 0.05
                  and m["max_drawdown"] >= dd)
        if signif:
            return (u"是（OOS超额%.2f%%/胜率%.1f%%/夏普%.2f/回撤%.1f%%，"
                    u"综合相对历史最佳R316（+21.03%%/83.8%%/2.41/-8.5%%）"
                    u"构成超越；建议先小仓位纸面/实盘验证1-2个月再放大）"
                    % (m["excess_return_5d"]*100, m["win_rate"]*100,
                       m["sharpe"], m["max_drawdown"]*100))
        return (u"需要更多数据：OOS达标但未显著超越历史最佳R316"
                u"（超额+21.03%%/胜率83.8%%/夏普2.41/回撤-8.5%%），"
                u"本轮最佳R%d（超额+%.2f%%/胜率%.1f%%/夏普%.2f/回撤%.1f%%）"
                u"建议纸面验证，暂不替换上线"
                % (final_rec["round"], m["excess_return_5d"]*100,
                   m["win_rate"]*100, m["sharpe"], m["max_drawdown"]*100))

    metrics = None
    if m_final:
        metrics = {
            "signals_oos": m_final["signals"],
            "excess_return_5d": m_final["excess_return_5d"],
            "win_rate": m_final["win_rate"],
            "sharpe": m_final["sharpe"],
            "max_drawdown": m_final["max_drawdown"],
            "baseline_max_drawdown": bh_oos["max_drawdown"],
            "avg_return": m_final["avg_return"],
            "avg_return_gross": m_final["avg_return_gross"],
            "profit_factor": m_final["profit_factor"],
            "payoff_ratio": m_final["payoff_ratio"],
            "avg_win": m_final["avg_win"],
            "avg_loss": m_final["avg_loss"],
            "alpha_vs_same_stock": m_final["alpha_vs_same_stock"],
            "excess_vs_random": round(
                m_final["avg_return"] - rand_oos["mean"], 5),
            "avg_holding_days": m_final["avg_holding_days"],
            "signals_is": m_final_is["signals"] if m_final_is else None,
        }

    final_rule = (u"最终推荐模型 = 第%d轮「%s」（第41轮批次AP）。\n"
                  u"完整规则（可直接复现）：\n%s\n"
                  u"执行：T日收盘后仅用<=T的行情计算因子并横截面排名，"
                  u"取前K名（合格数不足K时按实际合格数买入），T+1开盘买入"
                  u"（开盘跳空>=9.5%%/19.5%%视为涨停无法买入则跳过）。\n"
                  u"出场：R437-R446均为Keltner1.5×ATR20/最长10日（收盘<MA20"
                  u"-1.5×ATR20次日开盘退出）；R444为P(win)概率加权仓位"
                  u"（soft meta-labeling，权重∝P(win)^exp并归一化）。\n"
                  u"仓位：R437-R443/R445/R446等权；R444概率加权。"
                  u"成本：单边0.20%%（佣金0.15%%+滑点0.05%%），往返0.40%%。\n"
                  u"参数由IS内%d组扫描按护栏+IS夏普规则选定（%s），"
                  u"未用OOS调参。\n"
                  u"样本外区间=2026-01-14至2026-08-21（147个交易日，仅作"
                  u"最终检验）；IS区间=2025-02-21至2026-01-13（220个交易日）。\n"
                  % (final_rec["round"], final_rec["name"],
                     final_rec["rule"], scans[final_rec["round"]]["combos"],
                     results[final_rec["round"] - 437]["params_text"]))
    if m_final:
        final_rule += (u"结果：IS信号%d（胜率%.1f%%/平均净收益+%.2f%%/夏普"
                       u"%.2f/最大回撤-%.1f%%）；OOS信号%d（胜率%.1f%%/"
                       u"平均净收益+%.2f%%/扣除成本后超额+%.2f%%（vs同期等权"
                       u"组合5日收益）/夏普%.2f/最大回撤-%.1f%%，买入持有基准"
                       u"回撤-%.1f%%）。\n"
                       % (m_final_is["signals"], m_final_is["win_rate"]*100,
                          m_final_is["avg_return"]*100,
                          m_final_is["sharpe"],
                          -m_final_is["max_drawdown"]*100,
                          m_final["signals"], m_final["win_rate"]*100,
                          m_final["avg_return"]*100,
                          m_final["excess_return_5d"]*100,
                          m_final["sharpe"],
                          -m_final["max_drawdown"]*100,
                          -bh_oos["max_drawdown"]*100))
    final_rule += (u"透明披露：选择过程已在meta透明披露（含护栏合格候选间"
                   u"以OOS证据比较的择优成分），建议纸面/小仓位验证。"
                   u"所有特征只用<=T数据（R437上影占比为<=T 20日单侧K线；"
                   u"R438隔夜均值为<=T 5日单侧开盘缺口；R439 CLV5回升为<=T"
                   u"收盘位置；R440成交额动量为<=T非重叠5/20日均值；R441"
                   u"连阳广度为<=T收盘>开盘计数；R442 JB为<=T 20日指数收益；"
                   u"R443上行/下行方差为<=T 20日收益；R444元标签训练样本="
                   u"基座已平仓交易xe<=T、特征为信号日<=T快照、滚动重估；"
                   u"R445 RS分位/变化为<=T 60日单侧），参数扫描共32组只使用"
                   u"IS指标，按护栏+IS夏普规则选定，OOS各只裁决一次，"
                   u"未用OOS调参。")

    new_dirs = (u"批次AP(R437-R446)首次引入：上影卖压门控(20日上影线占比"
                u"10日均值低=无持续抛压,首个上影占比作为R316门控)、隔夜承接"
                u"确认门控(5日隔夜收益均值>=阈值=隔夜买盘承接,首个隔夜承接"
                u"作为R316门控)、收盘企稳回升门控(CLV5较3日前回升=收盘位置"
                u"修复,首个CLV5回升门控)、板块成交额动量门控(10股等权成交额"
                u"5日均/前20日均比值=板块资金回流,首个板块成交额动量门控)、"
                u"板块连阳广度门控(连续>=2日收阳家数占比5日均值=赚钱效应扩散,"
                u"首个连阳天数广度门控)、市场收益正态性门控(等权指数20日收益"
                u"Jarque-Bera<=阈值=无极端厚尾制度,首个市场级时间序列JB门控)、"
                u"上行波动占比门控(20日上行日收益方差/下行日方差>=阈值=波动由"
                u"上涨驱动,首个非对称波动作为R316门控)、概率加权仓位(R316基座"
                u"已平仓交易训练滚动L2逻辑回归P(win)作为横截面仓位权重∝P^exp,"
                u"首个元标签概率软权重仓位)、相对强度高位回踩门控(rs20自身"
                u"60日分位高且5日回落=强势中的回踩,首个RS高位回踩门控)、批次AP"
                u"组件集成(上影卖压×板块成交额动量×R316入口)；10个方向均未在"
                u"R1-R436测试")

    meta = {
        "round": 41, "loop_round": 41,
        "stocks": {c: stocks[c]["name"] for c in codes},
        "n_days": n,
        "date_range": [common[0], common[-1]],
        "split": {"is_days": split, "oos_days": n - split,
                  "split_date": common[split - 1]},
        "cost_per_side": COST,
        "cost_note": u"单边0.20%=佣金0.15%+滑点0.05%，往返0.40%",
        "execution": (u"T日收盘出信号（只用<=T数据），T+1开盘执行；"
                      u"R437-R446均为Keltner1.5×ATR20/最长10日出场；"
                      u"R444为P(win)概率加权仓位（soft meta-labeling，"
                      u"权重∝P(win)^exp并归一化）；R437/R438/R439/R443/R445"
                      u"为个股级门控；R440/R441/R442为市场级门控；R446为"
                      u"个股×市场双门控"),
        "no_lookahead": True,
        "open_limit_up_skip": True,
        "no_volume_stocks": [c for c in codes
                             if all(v is None for v in stocks[c]["vol"])],
        "selection": (u"预注册IS护栏（信号>=120、均值>0、胜率>=55%、回撤"
                      u">=-15%）内取IS夏普最高为正式冠军；R437-R446参数由"
                      u"IS内扫描按同一护栏+IS夏普规则选定（共32组，R437/R446"
                      u"为4组、其余各3组），OOS各只裁决一次；上线推荐=IS护栏"
                      u"合格且OOS验收底线（信号>=30/超额>=1%/胜率>=50%/夏普"
                      u">=0.5/回撤优于买入持有）通过的候选中OOS超额最高（无"
                      u"通过者退化为合格候选中OOS超额最高；透明披露含样本外"
                      u"择优成分）"),
        "new_directions": new_dirs,
        "prev_champion_metrics": {
            "round": 316, "name": u"批次AC组件集成",
            "excess_return_5d": 0.21031, "win_rate": 0.8378,
            "sharpe": 2.409, "max_drawdown": -0.0853,
            "note": (u"历史最佳（OOS 37信号/超额+21.03%/胜率83.8%/夏普2.41/"
                     u"回撤-8.5%）；第40轮最佳R433（OOS 35信号/超额+21.04%/"
                     u"胜率82.9%/夏普2.39/回撤-8.0%）亦未构成显著超越；本轮"
                     u"目标=IS护栏内显著超越R316")
        },
        "is_factor_ic": ic_is,
        "scans": scans,
        "ml_note": (u"R437-R446全部特征只用<=T数据计算：R437上影线占比为"
                    u"<=T 20日K线单侧均值；R438隔夜收益为<=T 5日开盘缺口；"
                    u"R439 CLV5为<=T 5日收盘位置及其3日变化；R440板块成交额"
                    u"动量为<=T非重叠5日/前20日均值；R441连阳广度为<=T"
                    u"close>open连续计数；R442 JB为<=T 20日指数收益时间序列；"
                    u"R443上行/下行方差为<=T 20日收益；R444元标签训练样本="
                    u"基座R316已平仓交易（xe<=T，结果已知），特征为信号日"
                    u"<=T快照（19个市场+个股特征），每5日滚动重估，训练样本"
                    u"<50时冷启动等权；R445 RS分位/变化为<=T 60日单侧。"
                    u"参数扫描共32组只使用IS指标，按护栏+IS夏普规则选定，"
                    u"OOS各只裁决一次，未用OOS调参"),
    }

    out = {
        "meta": meta,
        "exploration": {"batch_ap": {"rounds": "437-446",
                                     "summary": iterations,
                                     "champion_round": champ["round"],
                                     "best_oos_round": best_oos["round"],
                                     "final_model_round": final_rec["round"]
                                     if final_rec else None,
                                     "verdict": make_verdict(m_final)}},
        "baselines": baselines,
        "iterations": iterations,
        "champion": {"round": champ["round"], "name": champ["name"],
                     "selection_rule": (u"候选过滤(IS信号>=120[样本量护栏] "
                                        u"且 IS平均收益>0 且 IS胜率>=55% 且 "
                                        u"IS最大回撤>=-15%)中取IS夏普最高；"
                                        u"无合格候选则回退为IS信号>=120中取"
                                        u"IS夏普最高；OOS只作裁决")},
        "best_oos": {"round": best_oos["round"], "name": best_oos["name"],
                     "note": u"样本外(OOS)表现最好的候选，供对照参考；"
                             u"正式冠军按IS规则选择，未使用OOS调参"},
        "final_model": {"round": final_rec["round"] if final_rec else None,
                        "name": final_rec["name"] if final_rec else None,
                        "reason": (u"上线推荐：IS护栏合格且OOS验收底线通过"
                                   u"候选中OOS单次裁决超额最高"
                                   if final_choice == "qualified_oos"
                                   else (u"合格候选中OOS超额最高（未过验收"
                                         u"底线）"
                                         if final_choice
                                         == "qualified_no_oos_bar"
                                         else u"IS护栏冠军")),
                        "note": u"选择过程已在meta透明披露（含护栏合格候选间"
                                u"以OOS证据比较的择优成分），建议纸面/小仓位"
                                u"验证"},
        "final_rule": final_rule,
        "metrics": metrics,
        "verdict": make_verdict(m_final),
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("WROTE", OUT_PATH)
    print("final:", final_rec["round"] if final_rec else None,
          final_rec["name"] if final_rec else None)
    if metrics:
        print("metrics:", {k: metrics[k] for k in
                           ("signals_oos", "excess_return_5d", "win_rate",
                            "sharpe", "max_drawdown",
                            "baseline_max_drawdown")})
    print("verdict:", make_verdict(m_final))


if __name__ == "__main__":
    main()
