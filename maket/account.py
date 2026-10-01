# -*- coding: utf-8 -*-
"""资金模拟账本: 初始100万, 每票15万底仓(整手取整), T+1语义。
回合资金流 = 买新仓(cash-out) + 卖等量底仓(cash-in), 股数不变 → T+1合法。
底仓在票首次进入交易名单当日以开盘价建立; 账户每日收盘按市价估值。
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ACC = os.path.join(HERE, "account.json")
INIT_CAPITAL = 1_000_000
BASE_ALLOT = 150_000      # 每票底仓目标市值
RT_COST = 0.0012          # 双边成本: 按买入与卖出金额各计


def load():
    try:
        return json.load(open(ACC))
    except (IOError, ValueError):
        return {"capital": INIT_CAPITAL, "cash": INIT_CAPITAL,
                "bases": {}, "value_history": []}


def save(acc):
    with open(ACC, "w") as f:
        json.dump(acc, f, ensure_ascii=False, indent=1)


def ensure_base(acc, code, name, px_open, date):
    """底仓首次建立: 目标市值/现价 → 整手股数; 高价股买不满额属正常"""
    if code in acc["bases"] or not px_open or px_open <= 0:
        return False
    shares = int(BASE_ALLOT / px_open / 100) * 100
    affordable = int(acc["cash"] / px_open / 100) * 100
    shares = min(shares, max(affordable, 0))
    if shares < 100:
        return False
    acc["cash"] -= shares * px_open
    acc["bases"][code] = {"name": name, "shares": shares,
                          "avg_cost": round(px_open, 3), "since": date}
    return True


def apply_round(acc, r):
    """回合资金流: -entry×n (买新仓) + exit×n (卖底仓) - 双边成本; 股数不变
    幂等保护: 同一回合只入账一次, 防止 close 重跑导致现金双记"""
    b = acc["bases"].get(r["code"])
    if not b:
        return None
    key = "%s_%s_%s" % (r.get("date"), r.get("code"), r.get("entry_time", ""))
    applied = acc.setdefault("applied_rounds", [])
    if key in applied:
        return None
    n = b["shares"]
    cost = (r["entry_px"] + r["exit_px"]) * n * RT_COST
    flow = (r["exit_px"] - r["entry_px"]) * n - cost
    acc["cash"] += flow
    applied.append(key)
    return round(flow, 2)


def mark(acc, close_px, date):
    """按当日收盘价估值并记录净值历史"""
    total = acc["cash"]
    bases_value = {}
    for code, b in acc["bases"].items():
        px = close_px.get(code)
        if px:
            v = b["shares"] * px
            bases_value[code] = round(v, 2)
            total += v
    acc["value_history"] = [h for h in acc["value_history"] if h["date"] != date]
    acc["value_history"].append({"date": date, "value": round(total, 2),
                                 "pnl": round(total - acc["capital"], 2)})
    acc["value_history"].sort(key=lambda h: h["date"])
    acc["last_mark"] = {"date": date, "total": round(total, 2),
                        "bases_value": bases_value,
                        "close_px": {k: round(v, 3) for k, v in close_px.items()}}
    return total


def sell_base(acc, code, px, date):
    """出池清底仓: 全部股数按现价卖出(底仓建立已满1日, T+1合法)
    审计修复(P1): 先校验px再pop——行情失败时保留底仓待下tick重试, 严禁静默删仓"""
    b = acc["bases"].get(code)
    if not b or not px or px <= 0:
        return None
    acc["bases"].pop(code)
    acc["cash"] += b["shares"] * px
    return {"code": code, "name": b["name"], "shares": b["shares"], "px": px}
