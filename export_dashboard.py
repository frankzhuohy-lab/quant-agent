#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可视化数据导出：回测曲线 + 纸面实盘快照 → dashboard_data.json
由每日定时任务调用；产物供工作站 /apps/paper/ 展示。"""
from __future__ import print_function
import sys, os, json
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import quant_iter26 as q26
import quant_iter44 as q44
import paper_trade as pt
import manual_ledger as ml

OUT = os.path.join(HERE, "dashboard_data.json")
HIST = os.path.join(HERE, "paper_history.json")
STATE = os.path.join(HERE, "paper_state.json")


def build_backtest():
    """回测 OOS 净值曲线（与冠军模型完全一致）+ 基准 + 逐笔。"""
    stocks, common = pt.load_clean()
    codes = sorted(stocks.keys())
    n = len(common)
    feat, state = q26.build_features(stocks, codes, common)
    state = q44.add_batchAP_features(stocks, codes, common, feat, state)
    spec = pt.build_spec()
    split = int(n * q26.SPLIT)
    trades, daily = q44.simulate_am(stocks, codes, feat, state, spec,
                                    split, n - 1 - q26.H, n - 1)
    # 净值：加性累计（与 nav_of 口径一致）
    nav, acc = [], 0.0
    for r in daily:
        acc += r
        nav.append(round(acc, 6))
    dates = common[split:n]
    # 基准：10 股等权买入持有（收盘口径）
    bench = []
    for i in range(split, n):
        bh = sum(stocks[c]["close"][i] / stocks[c]["close"][split]
                 for c in codes) / len(codes) - 1.0
        bench.append(round(bh, 6))
    # 逐笔（人类可读）
    tl = []
    for t in trades:
        code = t["code"]
        tl.append({
            "code": code, "name": pt.NAMES.get(code, code),
            "entry_date": common[t["e"]], "exit_date": common[t["xe"]],
            "entry_px": round(stocks[code]["open"][t["e"]], 3),
            "exit_px": round(stocks[code]["open"][t["xe"]], 3),
            "net_pct": round(t["net"] * 100, 2),
            "hold_days": t["xe"] - t["e"],
        })
    nets = [t["net"] for t in trades]
    wins = sum(1 for x in nets if x > 0)
    # 最大回撤（基于净值曲线）
    peak, mdd = -1e9, 0.0
    for v in nav:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > mdd:
            mdd = dd
    return {
        "oos_start": common[split], "oos_end": common[n - 1],
        "dates": dates, "nav": nav, "bench": bench, "trades": tl,
        "metrics": {
            "total_ret_pct": round(nav[-1] * 100, 2) if nav else 0,
            "bench_ret_pct": round(bench[-1] * 100, 2) if bench else 0,
            "excess_pct": round((nav[-1] - bench[-1]) * 100, 2) if nav else 0,
            "n_trades": len(trades),
            "win_rate_pct": round(wins / len(nets) * 100, 1) if nets else 0,
            "avg_net_pct": round(sum(nets) / len(nets) * 100, 2) if nets else 0,
            "max_dd_pct": round(mdd * 100, 2),
        },
    }


def load_paper():
    """纸面实盘快照 + 逐日净值历史（累积）。"""
    snap = {}
    if os.path.exists(STATE):
        with open(STATE) as f:
            snap = json.load(f)
    hist = []
    if os.path.exists(HIST):
        with open(HIST) as f:
            hist = json.load(f)
    # 追加今日净值（去重）
    d = snap.get("data_end")
    nav = snap.get("nav")
    if d and nav is not None:
        if not hist or hist[-1].get("date") != d:
            hist.append({"date": d, "nav": round(nav, 6)})
            with open(HIST, "w") as f:
                json.dump(hist, f, ensure_ascii=False, indent=1)
    return {"snapshot": snap, "history": hist}


def build_manual():
    """人工干预仓快照（独立账本，不进模型净值）。取价失败时降级为成本价。"""
    try:
        st = ml.load()
    except Exception:
        return {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "positions": [], "closed": [], "nav": 1.0, "cash_pct": 100.0}
    pos = []
    for p in st["positions"]:
        px, name = p["entry_px"], p.get("name", p["code"])
        try:
            px, name2 = ml.live_px(p["code"])
            name = name2 or name
        except Exception:
            pass
        bars = []
        try:
            bars = ml.daily_bars(p["code"], 30)
        except Exception:
            pass
        closes = [b[4] for b in bars]
        hu = ml.kelt_upper(closes[:-1]) if len(closes) > 20 else None
        hd = sum(1 for b in bars if b[0] > p["entry_date"]) if bars else 0
        pos.append({
            "code": p["code"], "name": name,
            "entry_date": p["entry_date"], "entry_px": p["entry_px"],
            "mark": round(px, 3),
            "unreal_pct": round((px / p["entry_px"] - 1.0) * 100, 2),
            "net_pct": round((px / p["entry_px"] - 1.0 - 2 * ml.COST) * 100, 2),
            "weight": p["weight"], "hold_days": hd,
            "stop_px": round(p["entry_px"] * (1 - ml.STOP), 2),
            "kelt_upper": round(hu, 2) if hu else None,
            "max_hold": p.get("max_hold", ml.MAX_HOLD),
            "note": p.get("note", ""),
        })
    nav = st["cash"] + sum(p["weight"] for p in st["positions"])
    return {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rules": {"stop_pct": ml.STOP * 100, "max_hold": ml.MAX_HOLD,
                      "keltner_mult": ml.KELT_MULT, "cost_one_side_pct": ml.COST * 100,
                      "max_total_weight_pct": 30},
            "nav": round(nav, 4), "cash_pct": round(st["cash"] * 100, 1),
            "positions": pos, "closed": st["closed"]}


def build_events():
    """每日动态时间线（空仓日/休市日也留痕），看板自动展示。"""
    evf = os.path.join(HERE, "daily_events.json")
    try:
        with open(evf) as f:
            arr = json.load(f)
    except Exception:
        arr = []
    arr.sort(key=lambda e: e.get("date", ""))
    return arr[-120:]


def main():
    bt = build_backtest()
    paper = load_paper()
    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "paper_start": pt.PAPER_START,
        "initial_capital": 10_000_000,  # 初始资金 1000 万元人民币（展示口径；引擎按净值比例记账）
        "model": {
            "name": u"上行波动占比门控 (R443)",
            "batch": 41, "iter": 443,
            "rules": [
                u"入场：20日动量>0 + 站上MA20 + 低方差回踩VR(5)<1 + 回撤∈[-10%,-2%]",
                u"门控：市场广度≥0.4 + 炸板率≤0.3 + upvar20≥0.8（上行波动主导）",
                u"选股：相对强度−0.6×回撤深度，Top3 等权",
                u"出场：Keltner 1.5×ATR20 止盈 / 最长持有 10 交易日",
                u"执行：T+1 开盘买入（涨停跳过），成本单边 0.20%",
            ],
            "frozen": {"UV": 0.8, "K": 3, "keltner_mult": 1.5,
                        "max_hold": 10, "cost_one_side": 0.002},
        },
        "backtest": bt,
        "paper": paper,
        "events": build_events(),
        "manual": build_manual(),
    }
    with open(OUT, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    m = bt["metrics"]
    print("backtest: %d笔 胜率%.1f%% 总收益%.2f%% 超额%.2f%% 回撤%.2f%%"
          % (m["n_trades"], m["win_rate_pct"], m["total_ret_pct"],
             m["excess_pct"], m["max_dd_pct"]))
    print("paper snapshot: data_end=%s nav=%s history=%d点"
          % (paper["snapshot"].get("data_end"),
             paper["snapshot"].get("nav"), len(paper["history"])))
    print("written:", OUT)


if __name__ == "__main__":
    main()
