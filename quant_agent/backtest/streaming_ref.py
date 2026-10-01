#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐日流式参考引擎（评审第 4 轮 S8 扩展）：只使用 ≤ 当日的数据做决策。

与 qengine.run_r443 的"入场日预扫描登记出场日"相对照，本实现严格
按时间推进：每日开盘先处理当日应出场的持仓（条件为前收触发或到时），
再处理当日入场；任何决策不索引未来数组元素。若两者在多截断点下
交易、日收益、敞口逐字段一致，则证明预扫描实现无未来函数泄漏。

会计口径与 qengine 完全一致（费用扣在权重口径、出场日记在 xd-1），
以便日向量可直接比较。
"""
from __future__ import print_function
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "qsys"))


def _band_of(code):
    return 0.195 if code.startswith(("688", "300", "301")) else 0.095


def run_streaming(stocks, codes, feat, state, common, susp,
                  e_start, entry_end, window_end, spec,
                  cost_buy=0.002, cost_sell=0.002,
                  ld_block=True, exposure_cap=None, exclude=None):
    K = spec["K"]
    H = 5
    kelt = spec["params"].get("keltner_mult", 1.5)
    max_hold = spec["params"].get("max_hold", 10)
    score_fn, filt_fn = spec["score"], spec["filt"]
    allow_fewer = spec.get("allow_fewer", True)
    exclude = exclude or set()

    trades = []
    contrib = {}
    exposure = {}
    daily_trade_val = {}
    total_cost = 0.0
    live = []          # [{code, e, w, time_exit, kelt_hit(bool 前收已触发)}]

    def add(d, r):
        contrib.setdefault(d, []).append(r)

    def kelt_hit(code, j):
        m20 = feat[code]["ma20"][j]
        atr = feat[code]["atr20"][j]
        c = stocks[code]["close"]
        return (m20 is not None and atr is not None
                and c[j] < m20 - kelt * atr)

    for d in range(e_start, window_end + 1):
        # ---- 开盘: 出场（只用 ≤ d 的数据）----
        still = []
        for pos in live:
            due_kelt = pos["kelt_hit"]           # 前收(d-1)触发 → 今开出场
            due_time = d >= pos["time_exit"]
            if not (due_kelt or due_time):
                still.append(pos)
                continue
            code = pos["code"]
            o = stocks[code]["open"]
            c = stocks[code]["close"]
            w = pos["w"]
            # 顺延判定（当日开盘可观察）: 停牌 / 跌停；窗口末日不顺延
            # （与引擎 xd=min(xe,window_end) 对齐，末日无条件成交）。
            # 顺延的持仓补记当日持有段（预测出场时持有段被跳过，见下）。
            if d < window_end:
                if common[d] in susp.get(code, ()):
                    add(d - 1, w * (o[d] / o[d - 1] - 1.0))
                    pos["kelt_hit"] = False
                    pos["time_exit"] = max(pos["time_exit"], d + 1)
                    pos["deferred"] = pos.get("deferred", 0) + 1
                    still.append(pos)
                    continue
                band = _band_of(code)
                if ld_block and o[d] <= c[d - 1] * (1.0 - band + 0.005):
                    add(d - 1, w * (o[d] / o[d - 1] - 1.0))
                    pos["kelt_hit"] = False
                    pos["time_exit"] = max(pos["time_exit"], d + 1)
                    pos["deferred"] = pos.get("deferred", 0) + 1
                    still.append(pos)
                    continue
            # 出场成交（含窗口末日强制）
            exit_px = o[d]
            add(d - 1, w * (exit_px / o[d - 1] - 1.0) - w * cost_sell)
            total_cost += w * cost_sell
            daily_trade_val[d] = daily_trade_val.get(d, 0.0) + w
            gross = exit_px / pos["entry_px"] - 1.0
            reason = "keltner" if due_kelt else "time"
            trades.append({
                "code": code, "e": pos["e"], "xe": d,
                "planned_reason": reason,
                "reason": reason if d >= pos["time_exit"] or due_kelt
                else "time+window_cut",
                "hold_days": d - pos["e"],
                "deferred_days": pos.get("deferred", 0),
                "entry_px": pos["entry_px"], "exit_px": exit_px,
                "gross": gross, "net": gross - cost_buy - cost_sell,
                "w": w, "suspended_entry": False,
                "entry_date": common[pos["e"]], "exit_date": common[d],
            })
        live = still

        # ---- 开盘入场（信号来自 d-1 收盘）----
        if d <= entry_end:
            t = d - 1
            if t < 0 or not filt_fn(state, t):
                continue
            scores = score_fn(feat, codes, t)
            picks = sorted(scores, key=lambda cc: -scores[cc])[:K]
            picks = [cc for cc in picks if scores[cc] > -1e8]
            if len(picks) < K and not (allow_fewer and picks):
                continue
            np_ = len(picks)
            w = 1.0 / H / np_
            exp_now = sum(p["w"] for p in live)
            for code in picks:
                if (code, d) in exclude:
                    continue
                if common[d] in susp.get(code, ()):
                    continue                       # 停牌买不进: 丢弃（非顺延）
                o = stocks[code]["open"]
                c = stocks[code]["close"]
                band = _band_of(code)
                if o[d] >= c[d - 1] * (1.0 + band - 0.005):
                    continue                       # 开盘近似涨停买不进
                if exposure_cap is not None and exp_now + w > exposure_cap:
                    continue
                exp_now += w
                add(d, -w * cost_buy)
                total_cost += w * cost_buy
                daily_trade_val[d] = daily_trade_val.get(d, 0.0) + w
                live.append({
                    "code": code, "e": d, "w": w,
                    "entry_px": o[d],
                    "time_exit": d + max_hold,     # 实际成交日起算
                    "kelt_hit": False, "deferred": 0,
                })
                # 持仓逐日: open[d] -> open[d+1] 的收益在循环 d 末统一记
            # 当日新开仓的逐日收益从下一日起由持仓循环记账

        # 收盘后评估全部持仓 Keltner 条件（含当日新入场，决定明日出场；
        # 与引擎扫描区间从 d=e 起对齐）
        for pos in live:
            pos["kelt_hit"] = kelt_hit(pos["code"], d)

        # ---- 逐日收益与敞口（与 qengine 同口径）----
        # 持有段 open[d]→open[d+1] 计入日 d；预测明日出场的持仓跳过
        # （该段由明日出场项或顺延补记项覆盖，保证恰计一次）。
        for pos in live:
            code = pos["code"]
            o = stocks[code]["open"]
            if pos["kelt_hit"] or (d + 1) >= pos["time_exit"] \
                    or (d + 1) >= window_end:
                # 末段一律由出场项覆盖（窗口末日所有持仓必然出场）
                continue
            add(d, pos["w"] * (o[d + 1] / o[d] - 1.0))
        if d < window_end:
            for pos in live:
                exposure[d] = exposure.get(d, 0.0) + pos["w"]

    # 窗口末尾未平仓: 强制出场（与 qengine xd=min(xe,window_end) 对齐）
    if live and window_end >= e_start:
        d = window_end
        for pos in list(live):
            code = pos["code"]
            o = stocks[code]["open"]
            w = pos["w"]
            exit_px = o[d]
            add(d - 1, w * (exit_px / o[d - 1] - 1.0) - w * cost_sell)
            total_cost += w * cost_sell
            daily_trade_val[d] = daily_trade_val.get(d, 0.0) + w
            gross = exit_px / pos["entry_px"] - 1.0
            trades.append({
                "code": code, "e": pos["e"], "xe": d,
                "planned_reason": "time", "reason": "time+window_cut",
                "hold_days": d - pos["e"],
                "deferred_days": 0,
                "entry_px": pos["entry_px"], "exit_px": exit_px,
                "gross": gross, "net": gross - cost_buy - cost_sell,
                "w": w, "suspended_entry": False,
                "entry_date": common[pos["e"]], "exit_date": common[d],
            })
        live = []

    daily = []
    for d in range(e_start, window_end + 1):
        daily.append(sum(contrib.get(d, [])) if contrib.get(d) else 0.0)
    avg_exp = (sum(exposure.values()) / len(exposure)) if exposure else 0.0
    denom = avg_exp * max(1, len(exposure))
    turnover = (sum(daily_trade_val.values()) / denom) if denom > 0 else 0.0
    return {"trades": trades, "daily": daily,
            "exposure": exposure, "avg_exposure": avg_exp,
            "turnover": turnover, "total_cost": total_cost}
