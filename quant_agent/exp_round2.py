#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二轮：统计修正复跑 + 持有期邻值敏感性（constitution_007）。

第五轮核验修正（本脚本产物替代 var/exp_round1/ 中相应统计）:
1. 反事实配对补 tid 与缺失清单（A_counterfactual_missing.csv）；
2. 重叠持仓口径修正：并发天数按 [入场, 出场) 逐日统计（入场日计入、
   出场日不计入），≥2 并发与峰值并发分列；
3. 滚动年度窗口：252 交易日 + 真实日历 12 个月两种口径逐窗累计收益；
4. 敞口统一为全窗口日度均值（executor 修正后口径）。

H3 持有期邻值敏感性（2 个新变体，登记预算台账）:
  max_hold ∈ {8, 12}（基线 10），其余逻辑与基线完全一致（Keltner 保留、
  重复入场保留）。目的：检验 10 天持有期限是否稳健。
  预注册门槛沿用第一轮冻结规则；不通过即停止扩展持有期网格。

每变体报告：IS/DEV 主要指标、滚动年度窗口（min/max/达标数）、
风险（回撤/尾部）、同股集中度（最大并发批次/峰值单股权重）。

注意：IS/DEV 均为开发数据；滚动年度窗口属开发历史表现，
不构成独立样本外证据。
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

OUT = os.path.join(quant_agent.VAR, "exp_round2")
LEDGER = os.path.join(quant_agent.VAR, "budget_ledger.jsonl")

PROMOTION = {  # 与第一轮相同的预注册冻结规则
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
    mu = sum(daily) / len(daily)
    var = (sum((x - mu) ** 2 for x in daily) / (len(daily) - 1)
           if len(daily) > 1 else 0.0)
    tail = sorted(daily)[max(0, int(len(daily) * 0.05) - 1)]
    return {
        "tag": tag, "window": label,
        "net_total": er["daily_equity"][er["end_day"]] / CASH0 - 1.0,
        "annual": m["annual_return"], "sharpe": m["sharpe"],
        "mdd": m["max_drawdown"], "n_trades": len(er["trades"]),
        "win_rate": m["win_rate"], "cost": er["total_cost"],
        "avg_exposure": er.get("avg_exposure"),       # 全窗口日度均值
        "tail_5pct_daily": tail, "daily_std": var ** 0.5,
        "turnover": m.get("turnover_x", 0.0),
    }


def rolling_diff_fraction(ctx, base_er, cand_er, months=3):
    s, e = base_er["start_day"], base_er["end_day"]
    days = ctx.common[s:e + 1]
    month_ret_b = defaultdict(lambda: 1.0)
    month_ret_c = defaultdict(lambda: 1.0)
    for i, d in enumerate(days):
        month_ret_b[d[:7]] *= (1.0 + base_er["daily"][i])
        month_ret_c[d[:7]] *= (1.0 + cand_er["daily"][i])
    mlist = sorted(month_ret_b)
    wins, total, rows = 0, 0, []
    for i in range(len(mlist) - months + 1):
        chunk = mlist[i:i + months]
        rb = rc = 1.0
        for mth in chunk:
            rb *= month_ret_b[mth]
            rc *= month_ret_c[mth]
        diff = (rc - 1.0) - (rb - 1.0)
        wins += diff > 0
        total += 1
        rows.append(("-".join(chunk), round((rb - 1.0) * 100, 2),
                     round((rc - 1.0) * 100, 2), round(diff * 100, 2)))
    return (wins / total if total else 0.0), rows


def _add_months(ymd, months):
    y, m, d = int(ymd[:4]), int(ymd[5:7]), int(ymd[8:10])
    m += months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    import calendar
    d = min(d, calendar.monthrange(y, m)[1])
    return "%04d-%02d-%02d" % (y, m, d)


def rolling_annual(ctx, er):
    """两种年度长度口径的滚动累计收益窗口。

    A. 252 交易日固定长度（用户复算口径，183 窗）；
    B. 真实日历 12 个月（起点日 + 12 个月对齐到首个交易日，≥该日）。
    返回 (rows252, rows12m, stats)
    """
    s, e = er["start_day"], er["end_day"]
    days = ctx.common[s:e + 1]
    n = len(days)
    # 前缀复合
    pref = [1.0]
    for r in er["daily"]:
        pref.append(pref[-1] * (1.0 + r))
    rows252, rows12 = [], []
    W = 252
    for i in range(0, n - W + 1):
        ret = pref[i + W] / pref[i] - 1.0
        rows252.append((days[i], days[i + W - 1], round(ret * 100, 2)))
    for i in range(n):
        end_date = _add_months(days[i], 12)
        j = next((k for k in range(i + 1, n) if days[k] >= end_date), None)
        if j is None:
            break
        ret = pref[j + 1] / pref[i] - 1.0
        rows12.append((days[i], days[j], round(ret * 100, 2)))

    def stats(rows):
        vals = [r[2] for r in rows]
        if not vals:
            return {"n": 0, "min": None, "max": None, "ge100": 0}
        return {"n": len(vals), "min": min(vals), "max": max(vals),
                "ge100": sum(1 for v in vals if v >= 100.0)}
    return rows252, rows12, {"w252": stats(rows252), "m12": stats(rows12)}


def concentration(ctx, er):
    """同股集中度：任意证券的最大并发批次数、峰值单股日度权重、
    ≥2 并发天数（[入场,出场) 口径）。"""
    idx = {d: i for i, d in enumerate(ctx.common)}
    batches = []
    for t in er["trades"]:
        batches.append((idx[t["entry_date"]], idx[t["exit_date"]],
                        t["code"], t))
    batches.sort()
    n_days = er["end_day"] - er["start_day"] + 1
    # 每日各代码并发批次数
    per_day_code = defaultdict(lambda: defaultdict(int))
    peak_batches = defaultdict(int)   # code -> max concurrent
    for s0, e0, code, _t in batches:
        for d in range(s0, e0):
            per_day_code[d][code] += 1
        # 峰值并发（滑动）
    for d, codes in per_day_code.items():
        for code, k in codes.items():
            if k > peak_batches[code]:
                peak_batches[code] = k
    # 峰值单股权重（用开盘价逐日盯市）
    open_px = {c: ctx.stocks[c]["open"] for c in set(t["code"]
                                                     for t in er["trades"])}
    peak_w = 0.0
    for d in range(er["start_day"], er["end_day"] + 1):
        val = defaultdict(float)
        for s0, e0, code, t in batches:
            if s0 <= d < e0:
                val[code] += t["shares"] * open_px[code][d]
        eq = er["daily_equity"].get(d)
        if eq and val:
            peak_w = max(peak_w, max(val.values()) / eq)
    multi_days = sum(1 for d, codes in per_day_code.items()
                     if max(codes.values()) >= 2)
    worst_code = max(peak_batches, key=lambda c: peak_batches[c]) \
        if peak_batches else ""
    return {
        "max_concurrent_batches_any_code":
            max(peak_batches.values()) if peak_batches else 0,
        "worst_code": worst_code,
        "days_with_multi_batch": multi_days,
        "n_window_days": n_days,
        "peak_single_weight": peak_w,
    }


def overlap_688110(ctx, er):
    """688110.SH 批次重叠（核验口径）：[入场日, 出场日) 逐日并发统计，
    ≥2 并发天数与峰值并发（4 批）天数分列。"""
    idx = {d: i for i, d in enumerate(ctx.common)}
    batches = sorted((idx[t["entry_date"]], idx[t["exit_date"]], t)
                     for t in er["trades"] if t["code"] == "688110.SH")
    if not batches:
        return None
    counts = defaultdict(int)
    peak = 0
    for s0, e0, _t in batches:
        peak = max(peak, sum(1 for a, b, _x in batches if a <= s0 < b))
    for d in range(batches[0][0], max(b for _a, b, _t in batches)):
        k = sum(1 for a, b, _t in batches if a <= d < b)
        if k >= 2:
            counts[d] = k
    ge2 = len(counts)
    at_peak = sum(1 for k in counts.values() if k >= peak)
    pnl = sum(pnl_yuan(t) for _a, _b, t in batches)
    return {"batches": len(batches), "peak_concurrent": peak,
            "days_ge2_concurrent": ge2, "days_at_peak": at_peak,
            "pnl": pnl}


def gate_promotion(base_m, cand_m, roll_frac):
    reasons = []
    ok = True
    if cand_m["net_total"] <= base_m["net_total"]:
        ok = False
        reasons.append("DEV 净累计 %.2f%% 未优于基线 %.2f%%" %
                       (cand_m["net_total"] * 100, base_m["net_total"] * 100))
    if cand_m["sharpe"] < base_m["sharpe"]:
        ok = False
        reasons.append("Sharpe %.2f < 基线 %.2f" %
                       (cand_m["sharpe"], base_m["sharpe"]))
    if cand_m["mdd"] < -PROMOTION["dd_cap"] or \
            cand_m["mdd"] < base_m["mdd"] - \
            PROMOTION["dd_slack_vs_baseline_pp"] / 100.0:
        ok = False
        reasons.append("回撤 %.1f%% 越线" % (cand_m["mdd"] * 100))
    if roll_frac < PROMOTION["rolling_min_positive_fraction"]:
        ok = False
        reasons.append("滚动窗口正收益占比 %.0f%% < 2/3" %
                       (roll_frac * 100))
    if not reasons:
        reasons.append("满足预注册研究晋级条件（进入锁定前向观察，"
                       "不代表上线）")
    return ("RESEARCH_ACCEPT" if ok else "REJECT"), reasons


def main():
    ensure()
    const = json.load(open(quant_agent.CONFIG))
    assert const["version"] in ("constitution_007", "constitution_008")
    ctx = Context(constitution=const)

    ledger("ROUND2_REBUILD", "process",
           "第五轮核验统计修正复跑（tid/缺失清单/重叠口径/滚动年度窗口/"
           "敞口统一）：不计新试验，A/B 结果不变仅统计修正")
    base = run_variant(ctx, const, {})
    a_off = run_variant(ctx, const, {"exit_keltner": True})
    b_var = run_variant(ctx, const, {"block_reentry": True})
    h3_8 = run_variant(ctx, const, {"params": {"max_hold": 8}})
    ledger("EXP_H3_maxhold8", "variant",
           "H3 敏感性: max_hold=8（基线10），检验持有期稳健性")
    h3_12 = run_variant(ctx, const, {"params": {"max_hold": 12}})
    ledger("EXP_H3_maxhold12", "variant",
           "H3 敏感性: max_hold=12（基线10），检验持有期稳健性")
    variants = [("baseline", base), ("A_keltner_off", a_off),
                ("B_block_reentry", b_var), ("H3_maxhold8", h3_8),
                ("H3_maxhold12", h3_12)]

    # ---- 反事实（A2，补 tid + 缺失清单）----
    cf_rows, miss_rows = [], []
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
                # 缺失配对：A 变体资金路径不同导致该入场未发生；
                # 如实列出，不选择性剔除。
                miss_rows.append((label, t.get("tid", ""), t["code"],
                                  t["entry_date"], t["exit_date"],
                                  t.get("reason", ""),
                                  round(pnl_yuan(t), 0), "A变体无同键成交"))
                continue
            px_cf = ot["exit_px"]
            cf_pnl = t["shares"] * (px_cf - t["entry_px"]) \
                - t["fee_buy"] - t["fee_sell"]
            bp = pnl_yuan(t)
            cf_sum_base += bp
            cf_sum_cf += cf_pnl
            cf_rows.append((label, t.get("tid", ""), t["code"],
                            t["entry_date"], t["exit_date"],
                            ot["exit_date"], t["shares"],
                            round(bp, 0), round(cf_pnl, 0),
                            round(cf_pnl - bp, 0)))
    wcsv("A_counterfactual.csv",
         ["窗口", "tid", "证券", "入场日", "基线出场日", "反事实出场日",
          "数量", "基线盈亏(元)", "反事实盈亏(元)", "差(元)"], cf_rows)
    wcsv("A_counterfactual_missing.csv",
         ["窗口", "tid", "证券", "入场日", "基线出场日", "基线退出原因",
          "基线盈亏(元)", "缺失原因"], miss_rows)

    # ---- 688110 重叠（修正口径）----
    ov_rows = []
    for tag, res in variants[:3]:
        for label in ctx.dev_labels():
            ov = overlap_688110(ctx, res[label])
            if ov:
                ov_rows.append((tag, label, ov["batches"],
                                ov["peak_concurrent"],
                                ov["days_ge2_concurrent"],
                                ov["days_at_peak"], round(ov["pnl"], 0)))
    wcsv("overlap_688110.csv",
         ["变体", "窗口", "批次数", "峰值并发", "≥2并发天数",
          "峰值并发天数", "净盈亏(元)"], ov_rows)

    # ---- 比较表 + 滚动年度窗口 + 集中度 ----
    rows, ra_stats, conc_stats = [], {}, {}
    for tag, res in variants:
        for label in ctx.dev_labels():
            rows.append(metrics_row(tag, label, res[label]))
        r252, r12, st = {}, {}, {}
        for label in ctx.dev_labels():
            a, b, s = rolling_annual(ctx, res[label])
            r252[label], r12[label] = a, b
            st[label] = s
            conc_stats[(tag, label)] = concentration(ctx, res[label])
        ra_stats[tag] = st
        for label in ctx.dev_labels():
            wcsv("rolling_annual252_%s_%s.csv" % (tag, label),
                 ["起点", "终点", "累计收益%%"], r252[label])
            wcsv("rolling_annual12m_%s_%s.csv" % (tag, label),
                 ["起点", "终点", "累计收益%%"], r12[label])
    wcsv("comparison.csv",
         ["变体", "窗口", "净累计", "年化", "Sharpe", "回撤", "交易数",
          "胜率", "费用(元)", "日均敞口(全窗口)", "日度5%分位", "日波动",
          "换手"], [(r["tag"], r["window"], round(r["net_total"], 4),
                     round(r["annual"], 4), round(r["sharpe"], 3),
                     round(r["mdd"], 4), r["n_trades"],
                     round(r["win_rate"], 4), round(r["cost"], 0),
                     round(r["avg_exposure"] or 0, 4),
                     round(r["tail_5pct_daily"], 6),
                     round(r["daily_std"], 6), round(r["turnover"], 3))
                    for r in rows])
    wcsv("concentration.csv",
         ["变体", "窗口", "最大并发批次", "峰值并发证券", "≥2并发天数",
          "窗口天数", "峰值单股权重"], [
             (tag, label, conc_stats[(tag, label)]
              ["max_concurrent_batches_any_code"],
              conc_stats[(tag, label)]["worst_code"],
              conc_stats[(tag, label)]["days_with_multi_batch"],
              conc_stats[(tag, label)]["n_window_days"],
              round(conc_stats[(tag, label)]["peak_single_weight"], 4))
             for tag, _res in variants for label in ctx.dev_labels()])

    # ---- Gate（A/B 复判 + H3）----
    gates = {}
    for tag, res in variants[1:]:
        roll, roll_rows = rolling_diff_fraction(
            ctx, base["DEV"], res["DEV"],
            PROMOTION["rolling_window_months"])
        gates[tag] = gate_promotion(
            metrics_row("baseline", "DEV", base["DEV"]),
            metrics_row(tag, "DEV", res["DEV"]), roll)
        wcsv("rolling_%s.csv" % tag,
             ["窗口(月)", "基线净收益%", "候选净收益%", "差(pp)"],
             roll_rows)

    # ---- 报告 ----
    L = ["# 第二轮报告：统计修正 + 持有期邻值敏感性（constitution_007）",
         ""]
    L.append("A/B 结果与第一轮一致（确定性复跑），仅统计修正：")
    L.append("反事实配对 %d 对，缺失 %d 笔（清单见 "
             "A_counterfactual_missing.csv，非选择性剔除）；重叠口径修正为 "
             "[入场,出场) 逐日并发。" % (len(cf_rows), len(miss_rows)))
    L.append("A 的结论措辞修正为：差异较小、未通过冻结门槛"
             "（不声称统计等价）。")
    L.append("")
    L.append("## 比较表")
    L.append("")
    L.append("| 变体 | 窗口 | 净累计 | 年化 | Sharpe | 回撤 | 交易数 | "
             "日均敞口 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        L.append("| %s | %s | %.1f%% | %.1f%% | %.2f | %.1f%% | %d | "
                 "%.1f%% |" %
                 (r["tag"], r["window"], r["net_total"] * 100,
                  r["annual"] * 100, r["sharpe"], r["mdd"] * 100,
                  r["n_trades"], (r["avg_exposure"] or 0) * 100))
    L.append("")
    L.append("## 滚动年度窗口（开发历史表现，252 交易日口径）")
    L.append("")
    L.append("| 变体 | 窗口 | 窗数 | 最小 | 最大 | 达到100%窗数 |")
    L.append("|---|---|---|---|---|---|")
    for tag, _res in variants:
        for label in ctx.dev_labels():
            st = ra_stats[tag][label]["w252"]
            L.append("| %s | %s | %d | %s | %s | %d |" %
                     (tag, label, st["n"],
                      "%.1f%%" % st["min"] if st["min"] is not None else "-",
                      "%.1f%%" % st["max"] if st["max"] is not None else "-",
                      st["ge100"]))
    L.append("")
    L.append("## 滚动年度窗口（真实日历 12 个月口径）")
    L.append("")
    L.append("| 变体 | 窗口 | 窗数 | 最小 | 最大 | 达到100%窗数 |")
    L.append("|---|---|---|---|---|---|")
    for tag, _res in variants:
        for label in ctx.dev_labels():
            st = ra_stats[tag][label]["m12"]
            L.append("| %s | %s | %d | %s | %s | %d |" %
                     (tag, label, st["n"],
                      "%.1f%%" % st["min"] if st["min"] is not None else "-",
                      "%.1f%%" % st["max"] if st["max"] is not None else "-",
                      st["ge100"]))
    L.append("")
    L.append("## 同股集中度")
    L.append("")
    L.append("| 变体 | 窗口 | 最大并发批次 | 峰值并发证券 | ≥2并发天数 | "
             "峰值单股权重 |")
    L.append("|---|---|---|---|---|---|")
    for tag, _res in variants:
        for label in ctx.dev_labels():
            c = conc_stats[(tag, label)]
            L.append("| %s | %s | %d | %s | %d/%d | %.1f%% |" %
                     (tag, label, c["max_concurrent_batches_any_code"],
                      c["worst_code"], c["days_with_multi_batch"],
                      c["n_window_days"], c["peak_single_weight"] * 100))
    L.append("")
    L.append("## 688110.SH 重叠（修正口径）")
    L.append("")
    L.append("| 变体 | 窗口 | 批次数 | 峰值并发 | ≥2并发天数 | "
             "峰值并发天数 | 净盈亏(元) |")
    L.append("|---|---|---|---|---|---|---|")
    for r in ov_rows:
        L.append("| %s | %s | %d | %d | %d | %d | %.0f |" % r)
    L.append("")
    L.append("## 反事实（诊断，非组合收益）")
    L.append("")
    L.append("配对 %d 对；Σ基线 %.0f 元 vs Σ反事实 %.0f 元，差 %.0f 元。"
             "缺失 %d 笔（IS 8 / DEV 1 与核验一致），原因：A 变体资金"
             "路径不同导致同键入场未发生；配对键 (证券, 入场日)，"
             "不按盈亏选择性剔除。" %
             (len(cf_rows), cf_sum_base, cf_sum_cf,
              cf_sum_cf - cf_sum_base, len(miss_rows)))
    L.append("结论限定：反事实仅描述已配对样本，不代表全部基线交易。")
    L.append("")
    for tag in gates:
        d, rs = gates[tag]
        L.append("## Gate（预注册冻结规则，DEV 窗口）: %s → %s" %
                 (tag, d))
        for r in rs:
            L.append("- %s" % r)
    L.append("")
    L.append("## H3 敏感性说明")
    L.append("")
    L.append("max_hold 8/12 为有限敏感性检查，检验 10 天持有期限是否"
             "稳健；短持亏损/长持盈利的归因可能来自退出规则对交易的"
             "分类，不能单独证明延长持有有利。若两档均不通过预注册"
             "门槛，停止扩展持有期网格。")
    L.append("")
    L.append("预算台账: %s（本批追加 H3 两变体；A/B 复跑不计新试验）"
             % LEDGER)
    open(os.path.join(OUT, "report.md"), "w",
         encoding="utf-8").write("\n".join(L))
    print("OK ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
