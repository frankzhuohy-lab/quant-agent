#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qsys 压力测试层：交易成本 / 价格恶化 / 删除最佳交易 / 参数敏感性 / Walk-Forward。"""
from __future__ import print_function
import copy
from qengine import run_r443, metrics
from qdata import r443_spec

REAL_COST = (0.002, 0.0025)   # 买:佣金0.15%+滑点0.05%；卖:+印花税0.05%


def m_of(run):
    m = metrics(run)
    if m:
        m.pop("equity_curve", None)
    return m


def stress_suite(stocks, codes, feat, state, common, susp,
                 e_start, entry_end, window_end):
    base_spec = r443_spec()
    out = {}

    # 口径对照：冻结复制（无跌停/停牌修正）vs 真实口径
    frozen = run_r443(stocks, codes, feat, state, common, susp,
                      e_start, entry_end, window_end, base_spec,
                      cost_buy=0.002, cost_sell=0.002, ld_block=False)
    out["A_frozen_replica"] = m_of(frozen)
    base = run_r443(stocks, codes, feat, state, common, susp,
                    e_start, entry_end, window_end, base_spec,
                    cost_buy=REAL_COST[0], cost_sell=REAL_COST[1],
                    ld_block=True)
    out["B_realistic_baseline"] = m_of(base)

    # 1) 成本翻倍
    r = run_r443(stocks, codes, feat, state, common, susp,
                 e_start, entry_end, window_end, base_spec,
                 cost_buy=0.004, cost_sell=0.0045, ld_block=True)
    out["C1_cost_x2"] = m_of(r)
    # 2) 滑点恶化 +0.1%/边
    r = run_r443(stocks, codes, feat, state, common, susp,
                 e_start, entry_end, window_end, base_spec,
                 cost_buy=0.003, cost_sell=0.0035, ld_block=True)
    out["C2_slippage_plus_10bp"] = m_of(r)
    # 3) 买入价恶化0.2%（等效入场成本+0.2%）
    r = run_r443(stocks, codes, feat, state, common, susp,
                 e_start, entry_end, window_end, base_spec,
                 cost_buy=0.004, cost_sell=REAL_COST[1], ld_block=True)
    out["C3_entry_px_worse_0.2pct"] = m_of(r)
    # 4) 卖出价恶化0.2%
    r = run_r443(stocks, codes, feat, state, common, susp,
                 e_start, entry_end, window_end, base_spec,
                 cost_buy=REAL_COST[0], cost_sell=0.0045, ld_block=True)
    out["C4_exit_px_worse_0.2pct"] = m_of(r)
    # 5) 删除收益最高的5笔
    top5 = sorted(base["trades"], key=lambda x: -x["net"])[:5]
    excl = {(t["code"], t["e"]) for t in top5}
    r = run_r443(stocks, codes, feat, state, common, susp,
                 e_start, entry_end, window_end, base_spec,
                 cost_buy=REAL_COST[0], cost_sell=REAL_COST[1],
                 ld_block=True, exclude=excl)
    m = m_of(r)
    m["removed_top5_avg_net"] = round(
        sum(t["net"] for t in top5) / 5.0, 5)
    out["C5_remove_top5_trades"] = m
    # 6) 敞口上限 100%（原引擎理论敞口≈K/H*max_hold/H…实际见 avg_exposure）
    r = run_r443(stocks, codes, feat, state, common, susp,
                 e_start, entry_end, window_end, base_spec,
                 cost_buy=REAL_COST[0], cost_sell=REAL_COST[1],
                 ld_block=True, exposure_cap=1.0)
    out["C6_exposure_cap_100pct"] = m_of(r)
    return out, base


def param_sensitivity(stocks, codes, feat, state, common, susp,
                      e_start, entry_end, window_end):
    """参数 ±10%/±20% 敏感性（非寻优！只检验平坦度）"""
    out = {}
    grid = {
        "UV":        [0.64, 0.72, 0.80, 0.88, 0.96],
        "keltner":   [1.20, 1.35, 1.50, 1.65, 1.80],
        "max_hold":  [8, 9, 10, 11, 12],
        "K":         [2, 3, 4],
    }
    import quant_iter44 as q44
    saved_uv = q44.R443_UV
    for name, vals in grid.items():
        rows = []
        for v in vals:
            spec = r443_spec()
            if name == "UV":
                q44.R443_UV = v
            elif name == "keltner":
                spec["params"]["keltner_mult"] = v
            elif name == "max_hold":
                spec["params"]["max_hold"] = v
            elif name == "K":
                spec["K"] = v
            r = run_r443(stocks, codes, feat, state, common, susp,
                         e_start, entry_end, window_end, spec,
                         cost_buy=REAL_COST[0], cost_sell=REAL_COST[1],
                         ld_block=True)
            m = m_of(r)
            rows.append({"value": v, "n_trades": m["n_trades"],
                         "annual_return": m["annual_return"],
                         "sharpe": m["sharpe"],
                         "max_drawdown": m["max_drawdown"],
                         "calmar": m["calmar"]})
        q44.R443_UV = saved_uv
        base_row = [x for x in rows if x["value"] ==
                    {"UV": 0.8, "keltner": 1.5, "max_hold": 10, "K": 3}[name]][0]
        rets = [x["annual_return"] for x in rows]
        out[name] = {"rows": rows,
                     "base": base_row,
                     "min_ann": min(rets), "max_ann": max(rets),
                     "range_pct": round((max(rets) - min(rets)) /
                                        abs(base_row["annual_return"])
                                        if base_row["annual_return"] else 0, 3)}
    return out


def walk_forward(stocks, codes, feat, state, common, susp,
                 warmup, n, is_len=160, oos_len=60, step=60):
    """滚动前推：冻结参数，只检验时间稳定性（不在折内重新选参）"""
    folds = []
    start = warmup
    while start + is_len + oos_len <= n - 1:
        is_s, is_e = start, start + is_len - 1
        oos_s, oos_e = start + is_len, start + is_len + oos_len - 1
        spec = r443_spec()
        r_is = run_r443(stocks, codes, feat, state, common, susp,
                        is_s, is_e - 5, is_e, spec,
                        cost_buy=REAL_COST[0], cost_sell=REAL_COST[1],
                        ld_block=True)
        r_oos = run_r443(stocks, codes, feat, state, common, susp,
                         oos_s, oos_e - 5, oos_e, spec,
                         cost_buy=REAL_COST[0], cost_sell=REAL_COST[1],
                         ld_block=True)
        folds.append({
            "fold": len(folds) + 1,
            "is_window": [common[is_s], common[is_e]],
            "oos_window": [common[oos_s], common[oos_e]],
            "is": m_of(r_is), "oos": m_of(r_oos)})
        start += step
    return folds
