#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次九 E7：状态依赖日闸（constitution_009，预注册单变体）。

E5 消融证据：日闸（R316_FLR）在 DEV 趋势市过严（放松 +20pp），在 IS
震荡市有效（放松 -21pp）。E7 按事前可见状态区分两者：filt 未通过且
信号日 t 的 mkt_mom20 > 0 时放行，其余日子维持基线日闸。

同一执行器/成本模型；晋级门槛沿用预注册冻结规则；附加成本×2压力、
剔除 DEV 最大盈利股、月度四分列、单股逐日仓位产物齐全。
"""
import csv
import datetime
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import quant_agent  # noqa: E402
from quant_agent import seed  # noqa: E402
from quant_agent.backtest.runner import Context, run_window  # noqa: E402
import qengine  # noqa: E402
from quant_agent.research.attribution import account_table, pnl_yuan, \
    CASH0  # noqa: E402
from quant_agent.exp_round3 import rolling_diff_fraction, gate, \
    metrics_of, monthly_4col, pos_daily  # noqa: E402

OUT = os.path.join(quant_agent.VAR, "exp_round5")
LEDGER = os.path.join(quant_agent.VAR, "budget_ledger.jsonl")


def ensure():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)


def wcsv(name, header, rows):
    with open(os.path.join(OUT, name), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def ledger(trial, kind, note):
    with open(LEDGER, "a") as f:
        f.write(json.dumps({
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "trial": trial, "kind": kind, "note": note,
        }, ensure_ascii=False) + "\n")


def main():
    ensure()
    const = json.load(open(quant_agent.CONFIG))
    assert const["version"] == "constitution_009"
    ctx = Context(constitution=const)
    er_cfg = const["execution_rules"]

    from quant_agent.research.attribution import run_benchmark  # noqa
    import quant_agent.data.universe as U
    bench = run_benchmark(ctx, const, U.load_universe())

    def build(over):
        sm, kw, _ = seed.materialize(seed.normalize(over, const), ctx.stocks,
                                     ctx.codes, ctx.common,
                                     universe=ctx.universe)
        return sm, kw

    sm0, kw0 = build({})
    base = {label: run_window(ctx, sm0, kw0, *ctx.windows[label], er_cfg)
            for label in ctx.dev_labels()}
    sm1, kw1 = build({"filt_regime_off": "mkt_mom20_pos"})
    e7 = {label: run_window(ctx, sm1, kw1, *ctx.windows[label], er_cfg)
          for label in ctx.dev_labels()}
    ledger("EXP_E7_state_gate", "variant",
           "E7: 状态依赖日闸 filt_regime_off=mkt_mom20_pos（E5 证据驱动）")

    # 放行日统计（事前可见状态）
    for label in ctx.dev_labels():
        s, e1, w2 = ctx.windows[label]
        mm = ctx.state["mkt_mom20"]
        n_pos = sum(1 for t in range(s, min(e1, w2) + 1)
                    if mm[t - 1] is not None and mm[t - 1] > 0)
        wcsv("gate_pass_days_%s.csv" % label,
             ["指标", "值"],
             [["窗口交易日", w2 - s + 1],
              ["mkt_mom20>0 日数(t)", n_pos],
              ["基线日闸通过日", len(base[label]["sig_diag"])],
              ["E7 日闸通过日", len(e7[label]["sig_diag"])]])
        for tag, er in (("baseline", base[label]), ("E7_state_gate", e7[label])):
            wcsv("account_%s_%s.csv" % (tag, label),
                 ["date", "daily_return", "equity", "cash", "market_value",
                  "fees_cum", "exposure", "n_signals", "target_yuan",
                  "filled_yuan", "n_skips", "benchmark_equity"],
                 account_table(ctx, er, bench[label]))
            wcsv("fills_%s_%s.csv" % (tag, label),
                 ["tid", "证券", "入场日", "入场价", "出场日", "出场价",
                  "数量", "买费", "卖费", "净盈亏(元)", "退出原因"],
                 [(t.get("tid", ""), t["code"], t["entry_date"],
                   t["entry_px"], t["exit_date"], t["exit_px"], t["shares"],
                   round(t["fee_buy"], 2), round(t["fee_sell"], 2),
                   round(pnl_yuan(t), 0), t.get("reason"))
                  for t in er["trades"]])
            wcsv("pos_daily_%s_%s.csv" % (tag, label),
                 ["date", "code", "市值(元)", "占权益比"], pos_daily(ctx, er))
            wcsv("monthly_%s_%s.csv" % (tag, label),
                 ["月份", "月收益率", "期初权益(元)", "期末权益(元)",
                  "权益变化(元)", "已实现盈亏(元)", "平仓笔数"],
                 monthly_4col(ctx, er))

    # 压力与集中度
    er2 = json.loads(json.dumps(er_cfg))
    er2["cost_buy"] *= 2
    er2["cost_sell"] *= 2
    r2 = run_window(ctx, sm1, kw1, *ctx.windows["DEV"], er2)
    stress = r2["daily_equity"][r2["end_day"]] / CASH0 - 1.0
    by_code = defaultdict(float)
    for t in e7["DEV"]["trades"]:
        by_code[t["code"]] += pnl_yuan(t)
    top = max(by_code, key=lambda c: by_code[c])

    rf = rolling_diff_fraction(ctx, base["DEV"], e7["DEV"])
    g = gate(metrics_of("baseline", "DEV", base["DEV"]),
             metrics_of("E7_state_gate", "DEV", e7["DEV"]), rf)

    rows = []
    for tag, res in (("baseline", base), ("E7_state_gate", e7)):
        for label in ctx.dev_labels():
            m = metrics_of(tag, label, res[label])
            rows.append((tag, label, round(m["net_total"], 4),
                         round(m["annual"], 4), round(m["sharpe"], 3),
                         round(m["mdd"], 4), m["n_trades"],
                         round(m["cost"], 0),
                         round(m["avg_exposure"] or 0, 4)))
    wcsv("comparison.csv",
         ["变体", "窗口", "净累计", "年化", "Sharpe", "回撤", "交易数",
          "费用(元)", "日均敞口"], rows)
    wcsv("stress_cost2x_DEV.csv", ["变体", "DEV净累计(成本x2)"],
         [("E7_state_gate", round(stress, 4))])
    wcsv("exclude_top_stock_DEV.csv",
         ["变体", "最大盈利股票", "其盈亏(元)", "DEV净累计_不含该票(近似)"],
         [("E7_state_gate", top, round(by_code[top], 0),
           round((e7["DEV"]["daily_equity"][e7["DEV"]["end_day"]] -
                  by_code[top]) / CASH0 - 1.0, 4))])

    L = ["# E7 状态依赖日闸（constitution_009）", ""]
    L.append("规则：filt 未通过且信号日 t 的 mkt_mom20>0（事前可见）"
             "则放行；其余日子维持基线日闸。单变体，门槛冻结。")
    L.append("")
    L.append("| 变体 | 窗口 | 净累计 | 年化 | Sharpe | 回撤 | 交易数 | "
             "日均敞口 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        L.append("| %s | %s | %.1f%% | %.1f%% | %.2f | %.1f%% | %d | "
                 "%.1f%% |" %
                 (r[0], r[1], r[2] * 100, r[3] * 100, r[4], r[5] * 100,
                  r[6], r[8] * 100))
    L.append("")
    L.append("附加：成本×2 DEV %.1f%%；剔除最大盈利股(%s, %.0f 元)后 "
             "DEV %.1f%%。" %
             (stress * 100, top, by_code[top],
              (e7["DEV"]["daily_equity"][e7["DEV"]["end_day"]] -
               by_code[top]) / CASH0 * 100 - 100))
    L.append("")
    L.append("## Gate（预注册冻结规则，DEV 窗口）: E7_state_gate → %s" % g[0])
    for r in g[1]:
        L.append("- %s" % r)
    L.append("")
    L.append("跨期一致性：IS 侧见比较表（状态闸应只在 mkt_mom20>0 日"
             "放行，IS 段放行日的新增交易盈亏见月度表与 fills）。")
    open(os.path.join(OUT, "report.md"), "w",
         encoding="utf-8").write("\n".join(L))
    print("OK ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
