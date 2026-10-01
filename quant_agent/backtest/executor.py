#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""执行层现金账户模拟器（2026-09-30 评审后新增，P0）。

职责边界（不改写策略，符合方案文档"先接入原模型"纪律）：
  - qengine.run_r443 仍是信号/订单生成器（权重口径），保持冻结；
  - 本模块把引擎产出的"意图订单"在确定性现金账户里执行：
      · 名义额 = 目标权重 w × 当日开盘权益，三者取最小：
        目标额 / 敞口上限剩余额度 / 可用现金÷(1+买费)；
      · 整手（100 股）向下取整，不足一手放弃（记录在案）；
      · 费用从现金扣除（买费入成本、卖费扣 Proceeds）；
      · 出场日/顺延规则沿用引擎产出（引擎已处理停牌/跌停顺延）；
      · 禁止融券：名义额永不超过现金与敞口上限。
  - 产出与引擎 run 输出同构（trades/daily/exposure/...），
    metrics 复用 qengine.metrics，可直接进 Gate。

局限（如实声明）：
  - 部分成交未建模（整手粒度即最小成交单位假设）；
  - 开盘价集合竞价滑点未建模（沿用引擎 open 成交假设）；
  - 权重的 H=5 分仓语义由引擎保留，执行层只负责"买得起、不超仓"。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "qsys"))


def _fee(notional, rate, min_fee):
    """完整费用模型（评审第 3 轮）: 比例费率与最低收费取大，支持取整边界。

    纯比例费下 cash/(1+r) 闭式解足够；存在最低收费/固定费用时，
    金额上界 (cash-min_fee)/px 才是约束——两种情形取较小上界，
    再逐手回验，覆盖小额订单、最低收费边界、多笔批次与舍入。
    """
    if notional <= 0:
        return 0.0
    return max(notional * rate, min_fee)


def _max_affordable_shares(px, cash, rate, min_fee, lot=100):
    if px <= 0 or cash <= 0:
        return 0
    bound = min(cash / (px * (1.0 + rate)),
                max(cash - min_fee, 0.0) / px)
    shares = int(bound / lot) * lot
    while shares > 0:
        notional = shares * px
        if notional + _fee(notional, rate, min_fee) <= cash + 1e-9:
            return shares
        shares -= lot
    return 0


def _band_of(code):
    return 0.195 if code.startswith(("688", "300", "301")) else 0.095


def execute_cash(intended, stocks, common, susp, cost_buy, cost_sell,
                 cap=1.0, cash0=1_000_000.0, lot=100, end_day=None,
                 min_fee_buy=5.0, min_fee_sell=5.0,
                 check_limit_at_fill=True, start_day=0,
                 per_stock_cap=None, intent_rank=None):
    """intended: qengine.run_r443 的 trades 列表（已按 e 升序）。

    返回与引擎输出同构的 dict；trades 附 shares/fee_buy/fee_sell/
    notional/skip 原因（skipped_entries 另列）。

    同代码多笔重叠持仓按独立批次处理（引擎权重口径允许连续日
    对同一标的加仓；现金账户以独立 lot 批次实现同等语义）。

    start_day（批次七修正）: 日度序列覆盖 [start_day, end_day] 全窗口。
    此前序列从首个意图入场日起，调用方按窗口起点拼接日期造成索引
    偏移（equity_IS.csv 止于 2025-06-04 而成交至 06-30 的根因）。

    per_stock_cap（批次八 E1，默认 None=基线）: 单股合计仓位上限
    （对下单时权益的比例，全部批次合并计）；仅削减/拒绝新增买入，
    不因价格上涨自动卖出。
    intent_rank（批次八 E2/E3，默认 None=基线）: (code, e) -> 质量
    评分；提供时当日意图按评分降序处理（顺序决定 capacity 约束下
    的成交优先），不改变选股数量与 sizing 公式。
    """
    entries_by_day = {}
    for tr in intended:
        entries_by_day.setdefault(tr["e"], []).append(tr)

    cash = cash0
    positions = {}           # pid -> dict(shares, entry_px, e, xe, code,...)
    trades = []
    skipped = []             # 资金/上限不足而放弃的意图
    daily_exposure = {}
    daily_equity = {}        # 开盘口径权益（开盘成交后、开盘价估值）
    daily_cash = {}          # 当日全部开盘后流水完成后的现金
    entry_days = set()
    daily_traded = {}
    daily_signals = {}       # day -> 当日意图单数（批次七诊断）
    daily_target = {}        # day -> 目标金额合计
    daily_filled = {}        # day -> 实际成交金额合计
    daily_cum_cost = {}      # day -> 当日收盘后累计费用
    total_cost = 0.0

    n = len(common)
    if end_day is None:
        end_day = n - 1
    day0 = min(entries_by_day) if entries_by_day else start_day
    pending_exits = {}       # day -> [pid,...]
    seq = 0

    for d in range(start_day, end_day + 1):
        # 1) 开盘卖出（沿用引擎给出的出场日 xd）
        for pid in pending_exits.pop(d, []):
            pos = positions.pop(pid, None)
            if pos is None:
                continue
            code = pos["code"]
            o = stocks[code]["open"]
            if common[d] in susp.get(code, ()):
                # 引擎已顺延，理论上到不了这里；防御性顺延一日
                pending_exits.setdefault(d + 1, []).append(pid)
                positions[pid] = pos
                continue
            px = o[d]
            gross = pos["shares"] * px
            fee = _fee(gross, cost_sell, min_fee_sell)
            cash += gross - fee
            total_cost += fee
            daily_traded[d] = daily_traded.get(d, 0.0) + gross
            pos["exit_px"] = px
            pos["fee_sell"] = fee
            pos["exit_date"] = common[d]
            trades.append(pos)

        # 2) 开盘估值权益
        pos_val = 0.0
        for pid, pos in positions.items():
            pos_val += pos["shares"] * stocks[pos["code"]]["open"][d]
        equity = cash + pos_val
        daily_equity[d] = equity

        # 3) 开盘买入（信号日为 d-1 的订单）
        sig_list = entries_by_day.get(d, [])
        if intent_rank is not None:
            # E2/E3: 质量评分降序（同分保持引擎原序，stable）
            sig_list = sorted(sig_list,
                              key=lambda tr: -intent_rank.get(
                                  (tr["code"], d), float("-inf")))
        if sig_list:
            daily_signals[d] = len(sig_list)
            daily_target[d] = sum(tr["w"] for tr in sig_list)
        for tr in sig_list:
            code = tr["code"]
            o = stocks[code]["open"]
            if common[d] in susp.get(code, ()):
                skipped.append({"code": code, "e": d, "why": "suspended"})
                continue
            px = o[d]
            # 执行层复核涨停（评审第 3 轮）: 意图生成器的过滤是事前
            # 近似，成交时点必须重检开盘 vs 前收。可用 check_limit_at_fill
            # 独立关闭（归因消融实验用）。
            if check_limit_at_fill:
                prev_close = stocks[code]["close"][d - 1]
                band = _band_of(code)
                if px >= prev_close * (1.0 + band - 0.005):
                    skipped.append({"code": code, "e": d,
                                    "why": "limit_up_at_fill"})
                    continue
            target = tr["w"] * equity
            room = max(0.0, cap * equity - pos_val)
            # E1 单股合计仓位上限（批次八）: 全部同代码批次合并计，
            # 仅削减/拒绝新增买入；默认 None=基线语义不变。
            cap_room_stock = None
            if per_stock_cap is not None:
                held_val = sum(p2["shares"] for p2 in positions.values()
                               if p2["code"] == code) * px
                cap_room_stock = max(0.0,
                                     per_stock_cap * equity - held_val)
                room = min(room, cap_room_stock)
            # 最大合法数量: 成交金额 + 完整费用 ≤ 可用现金
            afford_shares = _max_affordable_shares(px, cash, cost_buy,
                                                   min_fee_buy, lot)
            target_shares = int(min(target, room) / (px * lot)) * lot
            shares = min(target_shares, afford_shares)
            if shares <= 0:
                # 拒单细分（批次七）: why 保持 cash_or_cap 兼容；detail 记录
                # 全部触发条件（多条件同时触发时以 + 连接，保留明细）。
                flags = []
                if afford_shares <= 0:
                    flags.append("cash_short")   # 一手+最低费用都付不起
                if target_shares <= 0:
                    if cap_room_stock is not None and \
                            cap_room_stock <= 0.0:
                        flags.append("cap_per_stock")  # E1 单股上限
                    elif room < target:
                        flags.append("cap_room")     # 敞口空间不足一手
                    else:
                        flags.append("below_min_lot")  # 目标金额不足一手
                if not flags:
                    # 目标与现金各自可买但取小后为零（含费上界回验失败）
                    flags.append("fee_gap")
                detail = "+".join(flags)
                skipped.append({"code": code, "e": d,
                                "why": "cash_or_cap", "detail": detail,
                                "target": round(target, 2),
                                "room": round(room, 2),
                                "cash": round(cash, 2),
                                "afford_shares": afford_shares})
                continue
            pay = shares * px
            fee = _fee(pay, cost_buy, min_fee_buy)
            assert pay + fee <= cash + 1e-6, "现金账户负现金（费用模型漏洞）"
            cash -= pay + fee
            total_cost += fee
            daily_traded[d] = daily_traded.get(d, 0.0) + pay
            daily_filled[d] = daily_filled.get(d, 0.0) + pay
            pos_val += pay
            seq += 1
            pid = (code, d, seq)
            positions[pid] = {
                "code": code, "e": d, "xe": tr["xe"],
                "planned_reason": tr.get("planned_reason"),
                "reason": tr.get("reason"),
                "hold_days": None, "deferred_days": tr.get("deferred_days"),
                "entry_px": px, "entry_date": common[d],
                "shares": shares, "notional": pay, "fee_buy": fee,
                "w": tr["w"], "gross": None, "net": None,
                "suspended_entry": False,
            }
            pending_exits.setdefault(tr["xe"], []).append(pid)

        # 敞口统一口径（第五轮核验修正）: 每个交易日都记录，
        # 空仓日敞口=0；此前只在有持仓时写入，均值变成"持仓日平均"，
        # 与日度账户表（空仓日填 0）复算不一致（汇总 64.0% vs 日度 38.8%）。
        daily_exposure[d] = (pos_val / max(equity, 1e-12)) if positions else 0.0
        daily_cash[d] = cash
        daily_cum_cost[d] = total_cost
        if sig_list:
            entry_days.add(d)

    # 窗口末尾未平仓：按最后开盘估值强制平仓（不可带入未来）
    if positions and end_day >= start_day:
        d = end_day
        for pid, pos in list(positions.items()):
            code = pos["code"]
            px = stocks[code]["open"][d]
            gross = pos["shares"] * px
            fee = _fee(gross, cost_sell, min_fee_sell)
            cash += gross - fee
            total_cost += fee
            pos["exit_px"] = px
            pos["fee_sell"] = fee
            pos["exit_date"] = common[d]
            pos["reason"] = (pos.get("reason") or "time") + "+window_cut"
            trades.append(pos)

    # 4) 日收益序列（开盘-开盘，费用内含；覆盖 [start_day, end_day] 全窗口）
    daily = []
    prev = cash0
    for d in range(start_day, end_day + 1):
        eq = daily_equity.get(d)
        if eq is None:
            eq = prev
        daily.append(eq / prev - 1.0 if prev > 0 else 0.0)
        prev = eq

    idx = {d: i for i, d in enumerate(common)}
    for tr in trades:
        tr["gross"] = tr["exit_px"] / tr["entry_px"] - 1.0
        tr["net"] = (tr["gross"] - cost_buy - cost_sell)
        tr["hold_days"] = idx.get(tr["exit_date"], 0) - \
            idx.get(tr["entry_date"], 0)
        tr["w"] = tr["notional"] / cash0  # 实际权重（对初始资金）

    n_days = max(1, end_day - start_day + 1)   # 全窗口自然日数
    span_days = end_day - start_day + 1
    avg_exp = (sum(daily_exposure[d] for d in range(start_day, end_day + 1))
               / span_days)
    hold_days = [d for d, v in daily_exposure.items() if v > 0]
    avg_exp_holding = (sum(daily_exposure[d] for d in hold_days)
                       / len(hold_days)) if hold_days else 0.0
    denom = avg_exp * n_days
    turnover = (sum(daily_traded.values()) /
                (denom * cash0)) if denom > 0 else 0.0
    # 敞口上限语义（评审澄清）: 仅下单时约束，非每日强制再平衡。
    # 价格漂移可使市值敞口越上限——如实记录越限天数（当日无任何买入）。
    breach = sorted(d for d, v in daily_exposure.items()
                    if v > cap + 0.01 and d not in entry_days)
    # 成交批次唯一编号（第五轮核验）: 反事实匹配与缺失分析按 tid 追踪，
    # 不再仅以 (code, entry_date) 隐含对应。
    for i, tr in enumerate(sorted(trades, key=lambda t: (t["e"], t["code"],
                                                         t["entry_px"]))):
        tr["tid"] = "T%06d" % (i + 1)
    return {"trades": trades, "daily": daily,
            "exposure": daily_exposure,
            "avg_exposure": avg_exp,
            "avg_exposure_holding": avg_exp_holding,
            "n_holding_days": len(hold_days),
            "turnover": turnover, "total_cost": total_cost,
            "cash": cash, "skipped_entries": skipped,
            "cash0": cash0, "cap": cap,
            "daily_cash": daily_cash,
            "exposure_breach_days": breach,
            # 批次七: 全窗口对齐的日度序列与诊断字段（索引 = 日序号 -
            # start_day；调用方按 common[start_day:end_day+1] 取日期）
            "start_day": start_day, "end_day": end_day,
            "first_entry_day": day0,
            "daily_equity": daily_equity,
            "daily_cum_cost": daily_cum_cost,
            "daily_signals": daily_signals,
            "daily_target": daily_target,
            "daily_filled": daily_filled}


if __name__ == "__main__":
    # 冒烟：用基线意图单在现金账户执行，验证无负现金、敞口≤上限
    from quant_agent.experiments.store import Store
    from quant_agent.backtest.runner import Context, BacktestRunner
    from quant_agent.strategies.adapter import normalize, materialize
    import quant_agent
    store = Store(os.path.join(quant_agent.VAR, "qa_store.sqlite3"))
    import json as _json
    const = _json.load(open(quant_agent.CONFIG))
    ctx = Context(constitution=const, use_universe=False)
    norm = normalize({}, const)
    spec, kw, _c = materialize(norm, ctx.stocks, ctx.codes, ctx.common)
    import qengine
    er = const["execution_rules"]
    isr = ctx.windows["IS"]
    run = qengine.run_r443(
        ctx.stocks, ctx.codes, ctx.feat, ctx.state, ctx.common, ctx.susp,
        isr[0], isr[1], isr[2], spec,
        cost_buy=er["cost_buy"], cost_sell=er["cost_sell"])
    ex = execute_cash(run["trades"], ctx.stocks, ctx.common, ctx.susp,
                      er["cost_buy"], er["cost_sell"], cap=1.0,
                      end_day=isr[2])
    m = qengine.metrics(ex)
    print("smoke: trades=%d skipped=%d final_cash=%.0f "
          "avg_exp=%.3f max_exp=%.3f ann=%.3f mdd=%.3f" % (
              len(ex["trades"]), len(ex["skipped_entries"]), ex["cash"],
              ex["avg_exposure"],
              max(ex["exposure"].values()) if ex["exposure"] else 0.0,
              m["annual_return"], m["max_drawdown"]))
