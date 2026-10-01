#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次八 E5（过滤漏斗统计 + 单条件消融）与 E6（股票池，阻塞判定）。

E5 证据链（候选数据集逐层过滤统计）:
  全窗口交易日 → 日闸(filt R316_FLR)通过 → 原始候选>0 → top-K picks →
  引擎逐笔过滤（停牌/涨停/cap/reentry）→ 执行器意图 → 成交/拒单。
消融对象: R316_FLR 日闸（唯一一个有过严证据的条件——IS 71%/DEV 44%
交易日被整层拦截）。filt=R316_FLR_off 预注册于 constitution_008。

E6: 扩大股票覆盖需要历史时点成分数据；本库仅有所定型池（189 只）
的 PIT 宇宙，无更宽指数的历史成分股数据 → 标记 BLOCKED（不伪造）。
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
from quant_agent.research.attribution import account_table, CASH0  # noqa

OUT = os.path.join(quant_agent.VAR, "exp_round4")
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


def funnel(ctx, er, label):
    s, e1, w2 = ctx.windows[label]
    n_days = w2 - s + 1
    diag = er.get("sig_diag", {})
    gate_days = len(diag)
    raw_days = sum(1 for d in diag.values() if d["raw"] > 0)
    sums = defaultdict(int)
    for d in diag.values():
        for k in ("picks", "excluded", "susp", "limit_up", "cap",
                  "reentry", "intents"):
            sums[k] += d[k]
    n_fill = len(er["trades"])
    n_skip = len(er.get("skipped_entries", []))
    rows = [
        ("全窗口交易日", n_days, ""),
        ("日闸通过(filt R316_FLR)", gate_days, "%.1f%%" %
         (100.0 * gate_days / n_days)),
        ("原始候选>0", raw_days, ""),
        ("top-K picks 累计", sums["picks"], ""),
        ("引擎过滤: 停牌", sums["susp"], ""),
        ("引擎过滤: 涨停", sums["limit_up"], ""),
        ("引擎过滤: cap", sums["cap"], ""),
        ("到达执行器意图", sums["intents"], ""),
        ("执行器成交", n_fill, ""),
        ("执行器拒单", n_skip, ""),
    ]
    return rows


def main():
    ensure()
    const = json.load(open(quant_agent.CONFIG))
    assert const["version"] in ("constitution_008", "constitution_009")
    ctx = Context(constitution=const)
    er_cfg = const["execution_rules"]

    from quant_agent.research.attribution import run_benchmark  # noqa
    import quant_agent.data.universe as U
    bench = run_benchmark(ctx, const, U.load_universe())

    # 基线（重跑以取诊断）
    sm0, kw0, _ = seed.materialize(seed.normalize({}, const), ctx.stocks,
                                   ctx.codes, ctx.common,
                                   universe=ctx.universe)
    base = {label: run_window(ctx, sm0, kw0, *ctx.windows[label], er_cfg)
            for label in ctx.dev_labels()}

    # E5 消融: 关闭日闸
    sm1, kw1, _ = seed.materialize(
        seed.normalize({"filt": "R316_FLR_off"}, const), ctx.stocks,
        ctx.codes, ctx.common, universe=ctx.universe)
    abl = {label: run_window(ctx, sm1, kw1, *ctx.windows[label], er_cfg)
           for label in ctx.dev_labels()}
    ledger("EXP_E5_filt_gate_ablation", "variant",
           "E5: 消融 R316_FLR 日闸（唯一有过严证据的条件）")

    for label in ctx.dev_labels():
        wcsv("funnel_baseline_%s.csv" % label, ["层", "数量", "占比"],
             funnel(ctx, base[label], label))
        wcsv("funnel_E5_gateoff_%s.csv" % label, ["层", "数量", "占比"],
             funnel(ctx, abl[label], label))
        for tag, er in (("baseline", base[label]), ("E5_gateoff", abl[label])):
            wcsv("account_%s_%s.csv" % (tag, label),
                 ["date", "daily_return", "equity", "cash", "market_value",
                  "fees_cum", "exposure", "n_signals", "target_yuan",
                  "filled_yuan", "n_skips", "benchmark_equity"],
                 account_table(ctx, er, bench[label]))

    rows = []
    for tag, res in (("baseline", base), ("E5_gateoff", abl)):
        for label in ctx.dev_labels():
            m = qengine.metrics(res[label])
            rows.append((tag, label,
                         round(res[label]["daily_equity"][
                             res[label]["end_day"]] / CASH0 - 1.0, 4),
                         round(m["annual_return"], 4), round(m["sharpe"], 3),
                         round(m["max_drawdown"], 4),
                         len(res[label]["trades"]),
                         round(res[label]["total_cost"], 0)))
    wcsv("comparison.csv",
         ["变体", "窗口", "净累计", "年化", "Sharpe", "回撤", "交易数",
          "费用(元)"], rows)

    # 补全（批次九）: 滚动 Gate + 逐笔/单股仓位/月度四分列产物
    from quant_agent.exp_round3 import rolling_diff_fraction, gate, \
        metrics_of, monthly_4col, pos_daily  # noqa
    from quant_agent.research.attribution import pnl_yuan  # noqa
    rf = rolling_diff_fraction(ctx, base["DEV"], abl["DEV"])
    g5 = gate(metrics_of("baseline", "DEV", base["DEV"]),
              metrics_of("E5_gateoff", "DEV", abl["DEV"]), rf)
    for tag, res in (("baseline", base), ("E5_gateoff", abl)):
        for label in ctx.dev_labels():
            er = res[label]
            wcsv("fills_%s_%s.csv" % (tag, label),
                 ["tid", "证券", "入场日", "入场价", "出场日", "出场价",
                  "数量", "买费", "卖费", "净盈亏(元)", "退出原因"],
                 [(t.get("tid", ""), t["code"], t["entry_date"],
                   t["entry_px"], t["exit_date"], t["exit_px"], t["shares"],
                   round(t["fee_buy"], 2), round(t["fee_sell"], 2),
                   round(pnl_yuan(t), 0), t.get("reason"))
                  for t in er["trades"]])
            wcsv("pos_daily_%s_%s.csv" % (tag, label),
                 ["date", "code", "市值(元)", "占权益比"],
                 pos_daily(ctx, er))
            wcsv("monthly_%s_%s.csv" % (tag, label),
                 ["月份", "月收益率", "期初权益(元)", "期末权益(元)",
                  "权益变化(元)", "已实现盈亏(元)", "平仓笔数"],
                 monthly_4col(ctx, er))

    L = ["# E5 过滤消融 与 E6 阻塞判定（constitution_008）", ""]
    L.append("## 逐层过滤漏斗（基线）")
    L.append("")
    for label in ctx.dev_labels():
        L.append("### %s" % label)
        L.append("")
        for name, cnt, pct in funnel(ctx, base[label], label):
            L.append("- %s: %d %s" % (name, cnt, pct))
        L.append("")
    L.append("过严证据: 日闸整层拦截 IS %d/%d 交易日（%.0f%%）、DEV %d/%d"
             "（%.0f%%）；其下游引擎过滤（停牌/涨停/cap）量级极小。"
             "据此按预注册对日闸做单条件消融。" %
             (len(base["IS"]["sig_diag"]),
              ctx.windows["IS"][2] - ctx.windows["IS"][0] + 1,
              100.0 * len(base["IS"]["sig_diag"]) /
              (ctx.windows["IS"][2] - ctx.windows["IS"][0] + 1),
              len(base["DEV"]["sig_diag"]),
              ctx.windows["DEV"][2] - ctx.windows["DEV"][0] + 1,
              100.0 * len(base["DEV"]["sig_diag"]) /
              (ctx.windows["DEV"][2] - ctx.windows["DEV"][0] + 1)))
    L.append("")
    L.append("## 消融结果（新增机会的扣费收益）")
    L.append("")
    L.append("| 变体 | 窗口 | 净累计 | 年化 | Sharpe | 回撤 | 交易数 | 费用 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        L.append("| %s | %s | %.1f%% | %.1f%% | %.2f | %.1f%% | %d | %.0f |" %
                 (r[0], r[1], r[2] * 100, r[3] * 100, r[4], r[5] * 100,
                  r[6], r[7]))
    L.append("")
    L.append("## E5 Gate（预注册冻结规则，DEV 窗口）: E5_gateoff → %s" % g5[0])
    for r in g5[1]:
        L.append("- %s" % r)
    L.append("")
    L.append("注：即便 rolling 门槛通过，IS 侧 -26.2% vs -5.4% 的跨期"
             "恶化已构成'优势不跨时期成立'，按任务书第五部分不晋级。")
    L.append("")
    L.append("## E6 判定: BLOCKED")
    L.append("")
    L.append("扩大覆盖需要历史时点成分股数据；本库仅有 189 只定型池的 "
             "PIT 宇宙（universe_rule.json + universe_candidates.json），"
             "无更宽指数的历史成分数据。按任务书规则标记阻塞，"
             "不以当前成分替代历史成分。")
    ledger("EXP_E6_universe_expand", "blocked",
           "E6: 无历史成分数据，PIT 无法保证，标记 BLOCKED 不执行")
    open(os.path.join(OUT, "report.md"), "w",
         encoding="utf-8").write("\n".join(L))
    print("OK ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
