#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qsys 分析层：市场状态分段 / 因子拆解 / 卖出归因 / 股票池体检。"""
from __future__ import print_function
import math
from collections import defaultdict

H = 5


def _spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((x - mx) ** 2 for x in rx)
    vy = sum((y - my) ** 2 for y in ry)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


# ---------------- 第一部分：市场状态 ----------------
def market_index(stocks, codes, common):
    n = len(common)
    return [sum(stocks[c]["close"][t] for c in codes) / len(codes)
            for t in range(n)]


def regime_labels(stocks, codes, common):
    """每天打标：trend ∈ {上涨,下跌,震荡}；vol ∈ {高波动,低波动}"""
    n = len(common)
    idx = market_index(stocks, codes, common)
    ma60 = [None] * n
    for t in range(60, n):
        ma60[t] = sum(idx[t - 59:t + 1]) / 60.0
    mom20 = [None] * n
    for t in range(20, n):
        mom20[t] = idx[t] / idx[t - 20] - 1.0
    rets = [idx[t] / idx[t - 1] - 1.0 if t > 0 else 0.0 for t in range(n)]
    vol20 = [None] * n
    for t in range(20, n):
        w = rets[t - 19:t + 1]
        m = sum(w) / 20.0
        vol20[t] = math.sqrt(sum((x - m) ** 2 for x in w) / 20.0)
    vals = [v for v in vol20 if v is not None]
    vs = sorted(vals)
    med_vol = vs[len(vs) // 2] if vs else 0.0
    out = []
    for t in range(n):
        tr = "震荡"
        if ma60[t] is not None:
            if idx[t] > ma60[t] * 1.01 and (mom20[t] or 0) > 0.03:
                tr = "上涨"
            elif idx[t] < ma60[t] * 0.99 and (mom20[t] or 0) < -0.03:
                tr = "下跌"
        vo = "高波动" if (vol20[t] or 0) > med_vol else "低波动"
        out.append((tr, vo))
    return out


def regime_performance(run, labels, e_start, window_end):
    """按市场状态统计策略表现（基于日收益序列）"""
    daily = run["daily"]
    exposure = run["exposure"]
    trades = run["trades"]
    stats = defaultdict(lambda: {"days": 0, "rets": [], "expo": [],
                                 "entered": 0})
    for i, d in enumerate(range(e_start, window_end + 1)):
        tr, vo = labels[d]
        for key in ((tr, vo), (tr, "*"), ("*", vo)):
            s = stats[key]
            s["days"] += 1
            s["rets"].append(daily[i])
            s["expo"].append(exposure.get(d, 0.0))
    for t in trades:
        tr, vo = labels[t["e"]]
        stats[(tr, vo)]["entered"] += 1
        stats[(tr, "*")]["entered"] += 1
    out = {}
    for key, s in stats.items():
        rets = s["rets"]
        n = len(rets)
        if n == 0:
            continue
        eq, peak, mdd = 1.0, 1.0, 0.0
        comp = 1.0
        for r in rets:
            comp *= (1 + r)
            eq *= (1 + r)
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1.0)
        ann = comp ** (252.0 / n) - 1.0
        m = sum(rets) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in rets) / n) if n > 1 else 0.0
        sharpe = (m / sd) * math.sqrt(252.0) if sd > 0 else 0.0
        out["%s|%s" % key] = {
            "days": n, "entered": s["entered"],
            "annual_return": round(ann, 4), "sharpe": round(sharpe, 2),
            "max_drawdown": round(mdd, 4),
            "avg_daily": round(m, 6),
            "avg_exposure": round(sum(s["expo"]) / n, 3),
            "day_win_rate": round(sum(1 for x in rets if x > 0) / float(n), 3)}
    return out


# ---------------- 第三部分：因子拆解 ----------------
STOCK_FACTORS = [
    ("mom20", "Momentum"), ("mom10", "Momentum"), ("rs20", "Momentum"),
    ("rsi", "Momentum/Trend"), ("vr_5_20", "Trend质量"),
    ("ma_align", "Trend"), ("vol20", "Volatility"), ("volr5", "Liquidity"),
    ("clv", "Volume/Price"), ("dist20", "Volume/Price"),
    ("dist20h", "Volume/Price"), ("ushadow10", "Volume/Price"),
    ("upvar20", "Volatility(非对称)"), ("skew20", "Quality"),
    ("atr20", "Volatility"), ("shape20", "Volume/Price"),
]


def factor_analysis(stocks, codes, feat, common, e_start, entry_end,
                    window_end, min_valid=8):
    """横截面 IC / RankIC / ICIR / Top-Bottom spread（fwd = T+1开→T+6开，H=5）"""
    n = len(common)
    results = {}
    series_store = {}
    for name, _cat in STOCK_FACTORS:
        ics, rank_ics, spreads = [], [], []
        top_rets_all, bot_rets_all = [], []
        per_year = defaultdict(list)
        for t in range(e_start, min(entry_end, window_end - H - 1) + 1):
            xs, ys = [], []
            e = t + 1
            xe = min(e + H, window_end)
            for c in codes:
                v = feat[c].get(name, [None] * n)[t]
                o = stocks[c]["open"]
                if v is None or o[e] <= 0:
                    continue
                fwd = o[xe] / o[e] - 1.0
                xs.append(v)
                ys.append(fwd)
            if len(xs) < min_valid:
                continue
            ic = _pearson(xs, ys)
            ric = _spearman(xs, ys)
            if ic is not None:
                ics.append(ic)
                per_year[common[t][:4]].append(ic)
            if ric is not None:
                rank_ics.append(ric)
            # Top-Bottom（按因子值排序取前1/3后1/3）
            order = sorted(range(len(xs)), key=lambda i: xs[i])
            k = max(2, len(order) // 3)
            bot = [ys[i] for i in order[:k]]
            top = [ys[i] for i in order[-k:]]
            spreads.append(sum(top) / len(top) - sum(bot) / len(bot))
            top_rets_all.append(sum(top) / len(top))
            bot_rets_all.append(sum(bot) / len(bot))
        if not rank_ics:
            continue
        def _icir(xs_):
            m = sum(xs_) / len(xs_)
            sd = math.sqrt(sum((x - m) ** 2 for x in xs_) / len(xs_)) \
                if len(xs_) > 1 else 0.0
            tstat = m / (sd / math.sqrt(len(xs_))) if sd > 0 else 0.0
            return m, sd, (m / sd if sd > 0 else 0.0), tstat
        ic_m, ic_sd, icir, ic_t = _icir(ics)
        ric_m, ric_sd, ricir, ric_t = _icir(rank_ics)
        sp_m = sum(spreads) / len(spreads) if spreads else None
        yearly = {y: round(sum(v) / len(v), 4) for y, v in
                  sorted(per_year.items())}
        results[name] = {
            "IC": round(ic_m, 4), "IC_t": round(ic_t, 2),
            "ICIR": round(icir, 3),
            "RankIC": round(ric_m, 4), "RankIC_t": round(ric_t, 2),
            "RankICIR": round(ricir, 3), "n_days": len(rank_ics),
            "top_minus_bottom_5d": (round(sp_m, 5) if sp_m is not None
                                    else None),
            "top_5d_ret": (round(sum(top_rets_all) / len(top_rets_all), 5)
                           if top_rets_all else None),
            "bottom_5d_ret": (round(sum(bot_rets_all) / len(bot_rets_all), 5)
                              if bot_rets_all else None),
            "IC_by_year": yearly}
        series_store[name] = rank_ics
    corr = factor_correlation(feat, codes, common, e_start, entry_end)
    return {"factors": results, "rank_corr": corr}


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def factor_correlation(feat, codes, common, e_start, entry_end):
    """日均横截面 Rank 相关（因子冗余诊断），按 code 对齐后配对"""
    n = len(common)
    names = [x[0] for x in STOCK_FACTORS]
    acc = defaultdict(list)
    for t in range(e_start, entry_end + 1):
        per = {}
        for name in names:
            d = {}
            for c in codes:
                v = feat[c].get(name, [None] * n)[t]
                if v is not None:
                    d[c] = v
            if len(d) >= 8:
                per[name] = d
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                if a in per and b in per:
                    common_codes = sorted(set(per[a]) & set(per[b]))
                    if len(common_codes) < 8:
                        continue
                    xa = [per[a][c] for c in common_codes]
                    xb = [per[b][c] for c in common_codes]
                    r = _spearman(xa, xb)
                    if r is not None:
                        acc[(a, b)].append(r)
    out = {}
    for (a, b), rs in acc.items():
        out["%s~%s" % (a, b)] = round(sum(rs) / len(rs), 3)
    return out


# ---------------- 第五部分：卖出归因 ----------------
def sell_attribution(run):
    groups = defaultdict(list)
    for t in run["trades"]:
        key = t["reason"].split("+")[0]
        groups[key].append(t)
    out = {}
    for key, ts in groups.items():
        nets = [x["net"] for x in ts]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        hold = sum(x["hold_days"] for x in ts) / float(len(ts))
        out[key] = {
            "n": len(ts),
            "total_net_contrib": round(sum(nets), 4),
            "avg_net": round(sum(nets) / len(nets), 5),
            "avg_win": (round(sum(wins) / len(wins), 5) if wins else None),
            "avg_loss": (round(sum(losses) / len(losses), 5) if losses
                         else None),
            "win_rate": (round(len(wins) / float(len(nets)), 3)
                         if nets else None),
            "payoff": ((sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
                       if wins and losses else None),
            "avg_hold_days": round(hold, 2),
            "deferred_events": sum(1 for x in ts if x["deferred_days"] > 0),
            "worst": round(min(nets), 5), "best": round(max(nets), 5)}
    # 亏损来源分解
    total_pnl = sum(x["net"] for x in run["trades"])
    loss_trades = [x for x in run["trades"] if x["net"] <= 0]
    out["_summary"] = {
        "total_net_sum": round(total_pnl, 4),
        "n_loss_trades": len(loss_trades),
        "loss_contrib": round(sum(x["net"] for x in loss_trades), 4),
        "loss_share_of_wins": (
            round(abs(sum(x["net"] for x in loss_trades)) /
                  abs(sum(x["net"] for x in run["trades"]
                          if x["net"] > 0)), 3)
            if any(x["net"] > 0 for x in run["trades"]) else None)}
    return out


# ---------------- 第二部分：股票池体检 ----------------
def universe_audit(stocks, codes, common, susp):
    n = len(common)
    out = {}
    for c in codes:
        rec = stocks[c]
        vols = rec["vol"][-250:]
        cls = rec["close"][-250:]
        amt = sum(v * p for v, p in zip(vols, cls)) / max(1, len(vols))
        band = 0.195 if c.startswith(("688", "300", "301")) else 0.095
        limup = limdn = 0
        for t in range(max(1, n - 250), n):
            pc = rec["close"][t - 1]
            if pc <= 0:
                continue
            if rec["open"][t] >= pc * (1.0 + band - 0.005):
                limup += 1
            if rec["open"][t] <= pc * (1.0 - band + 0.005):
                limdn += 1
        n_susp = len([d for d in susp.get(c, ()) if d >= common[max(0, n - 250)]])
        out[c] = {
            "name": rec["name"], "first_date": rec["dates"][0],
            "last_date": rec["dates"][-1],
            "avg_daily_amount_M": round(amt / 1e6, 1),
            "susp_days_250": n_susp,
            "limit_up_open_days_250": limup,
            "limit_down_open_days_250": limdn}
    return out
