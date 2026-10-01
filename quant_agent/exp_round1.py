#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第一轮实验（收益优化方案任务书，constitution_007）。

实验A: Keltner 退出显式开关（exit_keltner=True 关闭）vs 基线。
  A1 完整组合重跑（退出改变 → 资金释放与后续交易联动，现金执行层）；
  A2 退出反事实（诊断，非可执行组合收益）: 对基线相同入场/相同数量，
     仅替换退出日为纯固定期限退出日，比较逐笔盈亏。
实验B: block_reentry=True（已有该证券未平仓批次时不再新增入场）。
预算: 本脚本登记 3 个试验（A_off 组合、A 反事实诊断、B 变体），
诊断也计次（任务书第四部分）。门槛与晋级规则按优化方案 v1.0 第 45 行
预注册（计算前冻结）: DEV 净累计优于基线、Sharpe 不低于基线、回撤
≤20% 且不劣于基线超过 2pp、≥2/3 滚动窗口（3 个月滚动）净收益差为正。

产物: var/exp_round1/（artifacts、comparison.csv、report.md、
budget_ledger.jsonl 追加）
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
    CASH0, TOL_RECON  # noqa: E402

OUT = os.path.join(quant_agent.VAR, "exp_round1")
LEDGER = os.path.join(quant_agent.VAR, "budget_ledger.jsonl")

PROMOTION = {  # 预注册（方案 v1.0 §4，计算前冻结）
    "dd_cap": 0.20, "dd_slack_vs_baseline_pp": 2.0,
    "rolling_window_months": 3, "rolling_min_positive_fraction": 2.0 / 3.0,
}


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


def run_variant(ctx, const, over):
    norm = seed.normalize(over or {}, const)
    spec_m, kw, _ = seed.materialize(norm, ctx.stocks, ctx.codes,
                                     ctx.common, universe=ctx.universe)
    er_cfg = const["execution_rules"]
    return {label: run_window(ctx, spec_m, kw, *ctx.windows[label], er_cfg)
            for label in ctx.dev_labels()}


def metrics_row(tag, label, er):
    m = qengine.metrics(er)
    daily = er["daily"]
    var = _variance(daily)
    tail = sorted(daily)[max(0, int(len(daily) * 0.05) - 1)]
    return {
        "tag": tag, "window": label,
        "net_total": er["daily_equity"][er["end_day"]] / CASH0 - 1.0,
        "annual": m["annual_return"], "sharpe": m["sharpe"],
        "mdd": m["max_drawdown"], "n_trades": len(er["trades"]),
        "win_rate": m["win_rate"], "cost": er["total_cost"],
        "avg_exposure": m.get("avg_exposure", er.get("avg_exposure")),
        "tail_5pct_daily": tail, "daily_std": var ** 0.5,
        "turnover": m.get("turnover_x", 0.0),
    }


def _variance(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    mu = sum(xs) / n
    return sum((x - mu) ** 2 for x in xs) / (n - 1)


def rolling_diff_fraction(ctx, base_er, cand_er, months=3):
    """3 个月滚动窗口: 候选与基线的窗口净收益差为正的比例。"""
    s, e = base_er["start_day"], base_er["end_day"]
    days = ctx.common[s:e + 1]
    # 将日收益按日历月聚合后做滚动
    month_ret_b = defaultdict(lambda: 1.0)
    month_ret_c = defaultdict(lambda: 1.0)
    for i, d in enumerate(days):
        month_ret_b[d[:7]] *= (1.0 + base_er["daily"][i])
        month_ret_c[d[:7]] *= (1.0 + cand_er["daily"][i])
    mlist = sorted(month_ret_b)
    span = months
    wins, total = 0, 0
    rows = []
    for i in range(len(mlist) - span + 1):
        chunk = mlist[i:i + span]
        rb = 1.0
        rc = 1.0
        for mth in chunk:
            rb *= month_ret_b[mth]
            rc *= month_ret_c[mth]
        diff = (rc - 1.0) - (rb - 1.0)
        wins += diff > 0
        total += 1
        rows.append(("-".join(chunk), round((rb - 1.0) * 100, 2),
                     round((rc - 1.0) * 100, 2), round(diff * 100, 2)))
    return (wins / total if total else 0.0), rows


def gate_promotion(base_m, cand_m, roll_frac):
    """返回 (decision, reasons) —— 研究晋级判断（非部署）。"""
    reasons = []
    ok = True
    if cand_m["net_total"] <= base_m["net_total"]:
        ok = False
        reasons.append("DEV 净累计 %.2f%% 未优于基线 %.2f%%" %
                       (cand_m["net_total"] * 100,
                        base_m["net_total"] * 100))
    if cand_m["sharpe"] < base_m["sharpe"]:
        ok = False
        reasons.append("Sharpe %.2f < 基线 %.2f" %
                       (cand_m["sharpe"], base_m["sharpe"]))
    if cand_m["mdd"] < -PROMOTION["dd_cap"] or \
            cand_m["mdd"] < base_m["mdd"] - PROMOTION["dd_slack_vs_baseline_pp"] / 100.0:
        ok = False
        reasons.append("回撤 %.1f%% 越线" % (cand_m["mdd"] * 100))
    if roll_frac < PROMOTION["rolling_min_positive_fraction"]:
        ok = False
        reasons.append("滚动窗口正收益占比 %.0f%% < 2/3" % (roll_frac * 100))
    if not reasons:
        reasons.append("满足预注册研究晋级条件（进入锁定前向观察，不代表上线）")
    return ("RESEARCH_ACCEPT" if ok else "REJECT"), reasons


def main():
    ensure()
    const = json.load(open(quant_agent.CONFIG))
    assert const["version"] == "constitution_007"
    ctx = Context(constitution=const)

    base = run_variant(ctx, const, {})
    print("基线重跑完成")

    # ---- 实验 A ----
    a_off = run_variant(ctx, const, {"exit_keltner": True})
    ledger("EXP_A_keltner_off", "variant",
           "实验A: 显式关闭 Keltner 条件退出（exit_keltner=True）全组合重跑")
    print("A 关闭重跑完成")

    # A2 反事实（诊断）: 基线相同入场/数量，退出日替换为纯固定期限退出日
    cf_rows = []
    cf_sum_base = cf_sum_cf = 0.0
    for label in ctx.dev_labels():
        er_b, er_o = base[label], a_off[label]
        off_map = {}
        for t in er_o["trades"]:
            off_map.setdefault((t["code"], t["entry_date"]), t)
        for t in er_b["trades"]:
            key = (t["code"], t["entry_date"])
            ot = off_map.get(key)
            if ot is None:
                continue
            px_cf = ot["exit_px"]
            fee_sell_cf = t["fee_sell"]  # 近似: 费用按同口径比例
            cf_pnl = t["shares"] * (px_cf - t["entry_px"]) \
                - t["fee_buy"] - fee_sell_cf
            bp = pnl_yuan(t)
            cf_sum_base += bp
            cf_sum_cf += cf_pnl
            cf_rows.append((label, t["code"], t["entry_date"],
                            t["exit_date"], ot["exit_date"],
                            t["shares"], round(bp, 0), round(cf_pnl, 0),
                            round(cf_pnl - bp, 0)))
    wcsv("A_counterfactual.csv",
         ["窗口", "证券", "入场日", "基线出场日", "反事实出场日", "数量",
          "基线盈亏(元)", "反事实盈亏(元)", "差(元)"], cf_rows)
    ledger("EXP_A_counterfactual", "diagnostic",
           "实验A反事实: 相同入场/数量仅替换退出日（诊断，非组合收益）")
    print("A 反事实完成: base=%.0f cf=%.0f" % (cf_sum_base, cf_sum_cf))

    # ---- 实验 B ----
    b_var = run_variant(ctx, const, {"block_reentry": True})
    ledger("EXP_B_block_reentry", "variant",
           "实验B: 已有该证券未平仓批次时不再新增入场")
    print("B 重跑完成")

    # 688110 重叠分析（基线）
    ov_rows = []
    for label in ctx.dev_labels():
        batches = [t for t in base[label]["trades"]
                   if t["code"] == "688110.SH"]
        day_idx = {d: i for i, d in enumerate(ctx.common)}
        intervals = sorted((day_idx[t["entry_date"]],
                            day_idx[t["exit_date"]], t) for t in batches)
        peak = overlap_days = 0
        cur = []
        for s0, e0, t in intervals:
            cur = [x for x in cur if x[1] > s0]
            if cur:
                overlap_days += 1
            cur.append((s0, e0))
            peak = max(peak, len(cur))
        pnl = sum(pnl_yuan(t) for _s, _e, t in intervals)
        ov_rows.append((label, len(batches), peak, overlap_days,
                        round(pnl, 0)))
    wcsv("B_overlap_688110.csv",
         ["窗口", "批次数", "峰值并发批次数", "重叠天数", "净盈亏(元)"],
         ov_rows)

    # ---- 汇总与 Gate ----
    rows = []
    gates = {}
    for tag, res in (("baseline", base), ("A_keltner_off", a_off),
                     ("B_block_reentry", b_var)):
        for label in ctx.dev_labels():
            rows.append(metrics_row(tag, label, res[label]))
    wcsv("comparison.csv",
         ["变体", "窗口", "净累计", "年化", "Sharpe", "回撤", "交易数",
          "胜率", "费用(元)", "日均敞口", "日度5%分位", "日波动",
          "换手"], [(r["tag"], r["window"], round(r["net_total"], 4),
                     round(r["annual"], 4), round(r["sharpe"], 3),
                     round(r["mdd"], 4), r["n_trades"],
                     round(r["win_rate"], 4), round(r["cost"], 0),
                     round(r["avg_exposure"] or 0, 4),
                     round(r["tail_5pct_daily"], 6),
                     round(r["daily_std"], 6), round(r["turnover"], 3))
                    for r in rows])

    for tag, res in (("A_keltner_off", a_off),
                     ("B_block_reentry", b_var)):
        roll, roll_rows = rolling_diff_fraction(
            ctx, base["DEV"], res["DEV"],
            PROMOTION["rolling_window_months"])
        gates[tag] = gate_promotion(
            metrics_row("baseline", "DEV", base["DEV"]),
            metrics_row(tag, "DEV", res["DEV"]), roll)
        wcsv("rolling_%s.csv" % tag,
             ["窗口(月)", "基线净收益%%", "候选净收益%%", "差(pp)"],
             roll_rows)

    # 实验产物（统一账户表）
    from quant_agent.research.attribution import run_benchmark  # noqa
    import quant_agent.data.universe as U
    bench = run_benchmark(ctx, const, U.load_universe())
    for tag, res in (("A_keltner_off", a_off), ("B_block_reentry", b_var)):
        for label in ctx.dev_labels():
            wcsv("account_%s_%s.csv" % (tag, label),
                 ["date", "daily_return", "equity", "cash", "market_value",
                  "fees_cum", "exposure", "n_signals", "target_yuan",
                  "filled_yuan", "n_skips", "benchmark_equity"],
                 account_table(ctx, res[label], bench[label]))

    # 报告
    L = ["# 第一轮实验报告（constitution_007）", ""]
    L.append("## 比较表（基线 vs 实验A vs 实验B）")
    L.append("")
    L.append("| 变体 | 窗口 | 净累计 | 年化 | Sharpe | 回撤 | 交易数 | "
             "日度5%分位 | 换手 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        L.append("| %s | %s | %.1f%% | %.1f%% | %.2f | %.1f%% | %d | "
                 "%.2f%% | %.2f |" %
                 (r["tag"], r["window"], r["net_total"] * 100,
                  r["annual"] * 100, r["sharpe"], r["mdd"] * 100,
                  r["n_trades"], r["tail_5pct_daily"] * 100,
                  r["turnover"]))
    L.append("")
    L.append("## 实验A: Keltner 退出")
    L.append("")
    L.append("- 完整组合重跑: 见 comparison.csv A_keltner_off 行（退出改变"
             "后的资金释放与后续交易由现金执行层联动重算）。")
    L.append("- 退出反事实（诊断）: 相同入场/相同数量，仅替换退出日。"
             "Σ基线盈亏 %.0f 元 vs Σ反事实 %.0f 元，差 %.0f 元；"
             "明细 A_counterfactual.csv。该数字不视为可执行组合收益。" %
             (cf_sum_base, cf_sum_cf, cf_sum_cf - cf_sum_base))
    for tag in gates:
        d, rs = gates[tag]
        L.append("")
        L.append("## Gate（预注册规则，DEV 窗口）: %s → %s" % (tag, d))
        for r in rs:
            L.append("- %s" % r)
    L.append("")
    L.append("## 实验B: 重复入场（688110.SH 重叠见 B_overlap_688110.csv）")
    L.append("")
    L.append("预算台账: %s（本批追加 3 条：A 变体、A 反事实诊断、B 变体）"
             % LEDGER)
    open(os.path.join(OUT, "report.md"), "w",
         encoding="utf-8").write("\n".join(L))
    print("OK ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
