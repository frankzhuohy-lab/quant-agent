#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次十二 E10：基本面闸（constitution_012，预注册单变体）。

依据 var/market_study_20261008/research_memo.md：
- β(上证) 1.45~2.56 >> β(SOX) 0.05~0.35：科创50 定价身份是国内高 β，
  风格价差是状态判别的一阶特征；
- E7 单指标（mkt_mom20）REJECT：单一动量阈值对风格切换失明。

E8 规则（全部事前固定）：
  S(t) = z_IS(style20) + z_IS(mom20)
  style20 = 20日收益(科创50) - 20日收益(上证)，t 日收盘后可见
  mom20   = state["mkt_mom20"][t]（引擎既有，t 日收盘后可见）
  z 参数与放行阈值（S 的 IS 75 分位）均在 IS 段（<=2025-06-30）估计后冻结；
  filt(R316_FLR) 未通过且 S(t) > 阈值 → 放行当日，其余规则与基线字节一致。
排除（纪律）：crowd20（符号先验不确定）、breadth（历史数据不可得）。

同一执行器/成本模型；冻结 Gate；成本×2 压力；剔除 DEV 最大盈利股；
逐日账户/逐笔/单股仓位/月度四分列产物齐全；预算台账登记。
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
from quant_agent.research.attribution import account_table, pnl_yuan, \
    CASH0  # noqa: E402
from quant_agent.exp_round3 import rolling_diff_fraction, gate, \
    metrics_of, monthly_4col, pos_daily  # noqa: E402

OUT = os.path.join(quant_agent.VAR, "exp_round8")
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
    assert const["version"] == "constitution_012"
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
    sm1, kw1 = build({"fund_gate": "np_yoy_pos"})
    e8 = {label: run_window(ctx, sm1, kw1, *ctx.windows[label], er_cfg)
          for label in ctx.dev_labels()}
    ledger("EXP_E10_fund_gate", "variant",
           "E8: 组合状态门 state_bypass=style_mom_q75（新浪季度财务，法定截止 PIT，重述序列局限已披露）")

    # 放行日统计
    for label in ctx.dev_labels():
        s, e1, w2 = ctx.windows[label]
        wcsv("gate_pass_days_%s.csv" % label,
             ["指标", "值"],
             [["窗口交易日", w2 - s + 1],
              ["基线日闸通过日", len(base[label]["sig_diag"])],
              ["E8 日闸通过日", len(e8[label]["sig_diag"])]])
        for tag, er in (("baseline", base[label]), ("E10_fund_gate", e8[label])):
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
    for t in e8["DEV"]["trades"]:
        by_code[t["code"]] += pnl_yuan(t)
    top = max(by_code, key=lambda c: by_code[c])

    rf = rolling_diff_fraction(ctx, base["DEV"], e8["DEV"])
    g = gate(metrics_of("baseline", "DEV", base["DEV"]),
             metrics_of("E10_fund_gate", "DEV", e8["DEV"]), rf)

    rows = []
    for tag, res in (("baseline", base), ("E10_fund_gate", e8)):
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
         [("E10_fund_gate", round(stress, 4))])
    wcsv("exclude_top_stock_DEV.csv",
         ["变体", "最大盈利股票", "其盈亏(元)", "DEV净累计_不含该票(近似)"],
         [("E10_fund_gate", top, round(by_code[top], 0),
           round((e8["DEV"]["daily_equity"][e8["DEV"]["end_day"]] -
                  by_code[top]) / CASH0 - 1.0, 4))])

    L = ["# E10 基本面闸（constitution_012）", ""]
    L.append("规则：候选的最新可用（法定披露截止口径）扣非净利润同比须>0，"
             "否则逐出候选；评分/出场/资金规则与冻结基线一致。")
    L.append("数据源：新浪季度财务指标；局限=重述后序列非当时披露原值。")
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
              (e8["DEV"]["daily_equity"][e8["DEV"]["end_day"]] -
               by_code[top]) / CASH0 * 100 - 100))
    L.append("")
    L.append("## Gate（预注册冻结规则，DEV 窗口）: E10_fund_gate → %s" % g[0])
    for r in g[1]:
        L.append("- %s" % r)
    open(os.path.join(OUT, "report.md"), "w",
         encoding="utf-8").write("\n".join(L))
    print("OK ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
