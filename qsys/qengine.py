#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qsys 执行引擎：R443 全量重放 + A股真实性修正。

相对 quant_iter26.simulate / paper_trade.replay 的修正（全部有据）：
1. 停牌日禁止买入/卖出（vol=0 → 顺延），原引擎用「交集日历」静默吞掉停牌；
2. 跌停开盘禁止卖出（open <= 前收*(1-band+0.005) → 顺延至可卖日），
   原引擎假设跌停也能按开盘价成交；
3. 成本可配：默认冻结口径 0.20% 双边；真实口径 = 买 0.20% / 卖 0.25%
   （佣金0.15% + 滑点0.05% + 印花税0.05%卖出计）；
4. 可暴露度上限（原引擎 w=1/15/只、最长持有10日 → 理论敞口可达200%，
   隐含杠杆；exposure_cap=1.0 时现金约束下跳过新入场）；
5. 记录每笔卖出的原因分类（keltner/time/limit-down-deferred）。
信号口径与回测一致：T 收盘信号 → T+1 开盘执行，开盘近似涨停跳过。
"""
from __future__ import print_function
import math


def band_of(code):
    return 0.195 if code.startswith(("688", "300", "301")) else 0.095


def plan_exit(stocks, feat, susp, common, code, e, max_hold, kelt,
              keltner_off, band, ld_block, window_end):
    """计划出场日（纯函数提取，批次八）: Keltner 扫描 + 固定期限 +
    停牌/跌停顺延修正。run_r443 与候选标签共用同一实现，保证
    "相同入场和退出规则"逐字节一致。返回 (xd, planned_reason,
    actual_reason, deferred)。
    """
    o = stocks[code]["open"]
    c = stocks[code]["close"]
    xe, reason = None, None
    last_d = min(e + max_hold - 1, window_end)
    for d in range(e, last_d + 1):
        m20 = feat[code]["ma20"][d]
        atr = feat[code]["atr20"][d]
        if (not keltner_off
                and m20 is not None and atr is not None
                and c[d] < m20 - kelt * atr):
            xe, reason = d + 1, "keltner"
            break
    if xe is None:
        xe, reason = e + max_hold, "time"
    # A股真实性：实际出场日修正（停牌/跌停顺延，不晚于 window_end）
    xd = min(xe, window_end)
    deferred = 0
    while xd < window_end:
        if common[xd] in susp.get(code, ()):
            xd += 1; deferred += 1; continue
        if ld_block:
            pxc = c[xd - 1]
            if o[xd] <= pxc * (1.0 - band + 0.005):
                xd += 1; deferred += 1; continue
        break
    actual_reason = reason
    if deferred and xd < window_end:
        actual_reason = reason + "+deferred"
    elif deferred:
        actual_reason = reason + "+window_cut"
    return xd, reason, actual_reason, deferred



def run_r443(stocks, codes, feat, state, common, susp,
             e_start, entry_end, window_end, spec,
             cost_buy=0.002, cost_sell=0.002,
             ld_block=True, exposure_cap=None, exclude=None):
    K = spec["K"]
    H = 5  # q26.H
    kelt = spec["params"].get("keltner_mult", 1.5)
    max_hold = spec["params"].get("max_hold", 10)
    # 实验开关（constitution_007 预注册；默认 False → 基线语义字节不变）:
    # exit_keltner=True → 显式关闭 Keltner 条件退出（纯固定期限），
    #                      取代此前 keltner_mult=4.0 的近似冒充（评审要求）；
    # block_reentry=True → 同一证券已有未平仓批次时不再新增入场。
    keltner_off = bool(spec.get("exit_keltner", False))
    block_reentry = bool(spec.get("block_reentry", False))
    score_fn, filt_fn = spec["score"], spec["filt"]
    allow_fewer = spec.get("allow_fewer", True)
    scan_last = window_end
    exclude = exclude or set()
    trades = []
    contrib = {}          # day -> [return contributions]
    exposure = {}         # day -> sum of open weights
    daily_trade_val = {}  # day -> traded notional (entry+exit weight)
    total_cost = 0.0
    live = []             # open positions
    sig_diag = {}         # 第五轮核验: 信号链路逐日诊断（raw→picks→引擎过滤→意图）
    cand_rows = []        # 批次八: 候选信号全量记录（含过滤原因，供数据集/评分）

    def exp_of(d):
        return exposure.get(d, 0.0)

    def add(d, r):
        contrib.setdefault(d, []).append(r)

    for e in range(e_start, entry_end + 1):
        t = e - 1
        if t < 0:
            continue
        gate = filt_fn(state, t)
        # E7 状态依赖日闸（批次九预注册；默认 None=基线语义不变）:
        # filt 未通过时，若事前可见市场状态满足规则则放行当日。
        # 当前唯一规则: mkt_mom20_pos（t 日 mkt_mom20>0，t=下单前一日）。
        fro = spec.get("filt_regime_off")
        if not gate and fro == "mkt_mom20_pos":
            mm = state.get("mkt_mom20")
            gate = bool(mm is not None and t < len(mm)
                        and mm[t] is not None and mm[t] > 0)
        if not gate:
            continue
        scores = score_fn(feat, codes, t)
        # 原始信号 = 当日横截面有限评分的全部候选（K 截断之前）
        raw = [c for c in scores if scores[c] > -1e8]
        raw_sorted = sorted(raw, key=lambda c: (-scores[c], c))
        rank_of = {c: i + 1 for i, c in enumerate(raw_sorted)}
        # picks 保持原始排序语义（stable sort over scores 迭代序，平局序
        # 与基线字节一致）；raw_sorted 仅用于排名展示。
        picks = sorted(scores, key=lambda c: -scores[c])[:K]
        picks = [c for c in picks if scores[c] > -1e8]
        if len(picks) < K and not (allow_fewer and picks):
            continue
        np_ = len(picks)
        w = 1.0 / H / np_
        dd = {"raw": len(raw), "picks": np_, "excluded": 0,
              "susp": 0, "limit_up": 0, "cap": 0, "reentry": 0,
              "intents": 0}
        for code in picks:
            if (code, e) in exclude:
                dd["excluded"] += 1
                cand_rows.append((e, code, scores[code], rank_of[code],
                                  False, "excluded"))
                continue
            o = stocks[code]["open"]
            c = stocks[code]["close"]
            entry_px = o[e]
            prev_close = c[e - 1]
            band = band_of(code)
            if common[e] in susp.get(code, ()):
                dd["susp"] += 1
                cand_rows.append((e, code, scores[code], rank_of[code],
                                  True, "suspended"))
                continue  # 停牌买不进
            if entry_px >= prev_close * (1.0 + band - 0.005):
                dd["limit_up"] += 1
                cand_rows.append((e, code, scores[code], rank_of[code],
                                  True, "limit_up"))
                continue  # 开盘近似涨停 → 买不进
            if exposure_cap is not None and exp_of(e) + w > exposure_cap:
                dd["cap"] += 1
                cand_rows.append((e, code, scores[code], rank_of[code],
                                  True, "engine_cap"))
                continue  # 现金约束
            if block_reentry and any(t2["code"] == code and t2["xe"] > e
                                     for t2 in trades):
                dd["reentry"] += 1
                cand_rows.append((e, code, scores[code], rank_of[code],
                                  True, "block_reentry"))
                continue  # 实验B: 已有未平仓批次，平仓后才允许再次入场
            dd["intents"] += 1
            cand_rows.append((e, code, scores[code], rank_of[code],
                              True, "intent"))
            entry_px_eff = entry_px * (1.0)  # 入场价恶化由调用方改 spec/cost 近似
            # 计划出场日（批次八：plan_exit 纯函数提取，与候选标签共用）
            xd, reason, actual_reason, deferred = plan_exit(
                stocks, feat, susp, common, code, e, max_hold, kelt,
                keltner_off, band, ld_block, window_end)
            # 记账：入场日
            add(e, -w * cost_buy)
            total_cost += w * cost_buy
            daily_trade_val[e] = daily_trade_val.get(e, 0.0) + w
            # 持仓逐日
            exit_eff = o[xd] * (1.0)
            d = e
            while d < xd:
                # 日收益：open[d] -> open[d+1]（与 q26.simulate 口径一致）。
                # 出场日最后一段（open[xd-1] -> open[xd]）只由下方出场项
                # 计入一次——2026-10-01 修复双计（批次六引擎勘误；评审第 4 轮
                # S8 对账发现 daily 累乘与交易 gross 不符）。
                if d + 1 < xd:
                    add(d, w * (o[d + 1] / o[d] - 1.0))
                exposure[d] = exposure.get(d, 0.0) + w
                d += 1
            # 出场日：open[xd] 成交，扣卖出成本
            add(xd - 1, w * (exit_eff / o[xd - 1] - 1.0) - w * cost_sell)
            total_cost += w * cost_sell
            daily_trade_val[xd] = daily_trade_val.get(xd, 0.0) + w
            gross = exit_eff / entry_px - 1.0
            trades.append({"code": code, "e": e, "xe": xd,
                           "planned_reason": reason,
                           "reason": actual_reason,
                           "hold_days": xd - e,
                           "deferred_days": deferred,
                           "entry_px": entry_px, "exit_px": exit_eff,
                           "gross": gross, "net": gross - cost_buy - cost_sell,
                           "w": w, "suspended_entry": False,
                           "entry_date": common[e], "exit_date": common[xd]})
        sig_diag[e] = dd
        picks_set = set(picks)
        for code in raw:
            if code not in picks_set:
                cand_rows.append((e, code, scores[code], rank_of[code],
                                  False, "not_topK"))
    daily = []
    for d in range(e_start, window_end + 1):
        daily.append(sum(contrib.get(d, [])) if contrib.get(d) else 0.0)
    avg_exp = (sum(exposure.values()) / len(exposure)) if exposure else 0.0
    denom = avg_exp * max(1, len(exposure))
    turnover = (sum(daily_trade_val.values()) / denom) if denom > 0 else 0.0
    return {"trades": trades, "daily": daily,
            "exposure": exposure, "avg_exposure": avg_exp,
            "turnover": turnover, "total_cost": total_cost,
            "sig_diag": sig_diag,
            "candidates": cand_rows}


def metrics(run, dates=None):
    """run = run_r443 输出。dates = 与 daily 对齐的日期串（可选）"""
    daily = run["daily"]
    trades = run["trades"]
    n = len(daily)
    if n == 0:
        return None
    eq, peak, mdd, curve = 1.0, 1.0, 0.0, []
    for r in daily:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)
        curve.append(eq)
    total_ret = eq - 1.0
    ann = (1.0 + total_ret) ** (252.0 / n) - 1.0 if n > 0 else 0.0
    m = sum(daily) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in daily) / n) if n > 1 else 0.0
    downside = [min(0.0, x) for x in daily]
    dm = sum(downside) / n
    dsd = math.sqrt(sum((x - dm) ** 2 for x in downside) / n)
    sharpe = (m / sd) * math.sqrt(252.0) if sd > 0 else 0.0
    sortino = (m / dsd) * math.sqrt(252.0) if dsd > 0 else 0.0
    calmar = ann / abs(mdd) if mdd < 0 else None
    nets = [t["net"] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    wr = len(wins) / float(len(nets)) if nets else None
    payoff = ((sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
              if wins and losses else None)
    avg_hold = (sum(t["hold_days"] for t in trades) / float(len(nets))
                if nets else None)
    return {"n_trades": len(nets), "total_return": round(total_ret, 4),
            "annual_return": round(ann, 4), "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3), "max_drawdown": round(mdd, 4),
            "calmar": (round(calmar, 3) if calmar is not None else None),
            "win_rate": (round(wr, 4) if wr is not None else None),
            "payoff_ratio": (round(payoff, 3) if payoff is not None else None),
            "avg_holding_days": (round(avg_hold, 2) if avg_hold else None),
            "turnover_x": round(run["turnover"], 2),
            "avg_exposure": round(run["avg_exposure"], 3),
            "total_cost": round(run["total_cost"], 5),
            "equity_curve": curve,
            "positive_days": round(sum(1 for x in daily if x > 0) / float(n), 4)}
