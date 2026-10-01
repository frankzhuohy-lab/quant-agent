#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次八实验 E1-E3（constitution_008，任务书第四部分，先执行组）。

E1: 单股合计仓位上限 20%（per_stock_cap=0.20，仅限制新增买入，
    允许重复买入，全部批次合并计）。
E2: ridge 质量评分对当日意图降序（选股数量与 sizing 公式不变）。
E3: cart 质量评分降序（同 E2）。

晋级门槛沿用预注册冻结规则（DEV 净累计/Sharpe/回撤 slack/滚动2/3）。
附加检验（任务书第五部分）: 成本×2 压力测试、剔除 DEV 最大盈利股票
后的净收益、盈利集中度。重叠滚动窗口不作为独立成功证据。

每实验产物: config、account_<e>_<窗口>.csv（逐日账户）、
fills_<e>_<窗口>.csv（逐笔）、pos_daily_<e>_<窗口>.csv
（单股逐日合计仓位）、comparison.csv、monthly_<e>_<窗口>.csv
（月收益率/期初期末权益/权益变化/已实现盈亏 四分列）。
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

OUT = os.path.join(quant_agent.VAR, "exp_round3")
LEDGER = os.path.join(quant_agent.VAR, "budget_ledger.jsonl")
CAND = os.path.join(quant_agent.VAR, "candidates")

PROMOTION = {"dd_cap": 0.20, "dd_slack_vs_baseline_pp": 2.0,
             "rolling_window_months": 3,
             "rolling_min_positive_fraction": 2.0 / 3.0}


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


def load_rank(model, ctx):
    """pred_<model>.csv → {(code, e): score}（e = 信号日+1 的下单日索引）"""
    idx = {d: i for i, d in enumerate(ctx.common)}
    rank = {}
    with open(os.path.join(CAND, "pred_%s.csv" % model)) as f:
        for r in csv.DictReader(f):
            e = idx[r["signal_date"]] + 1
            rank[(r["code"], e)] = float(r["pred_score"])
    return rank


def metrics_of(tag, label, er):
    m = qengine.metrics(er)
    daily = er["daily"]
    mu = sum(daily) / len(daily)
    var = (sum((x - mu) ** 2 for x in daily) / (len(daily) - 1)
           if len(daily) > 1 else 0.0)
    return {"tag": tag, "window": label,
            "net_total": er["daily_equity"][er["end_day"]] / CASH0 - 1.0,
            "annual": m["annual_return"], "sharpe": m["sharpe"],
            "mdd": m["max_drawdown"], "n_trades": len(er["trades"]),
            "win_rate": m["win_rate"], "cost": er["total_cost"],
            "avg_exposure": er.get("avg_exposure"),
            "tail_5pct": sorted(daily)[max(0, int(len(daily) * 0.05) - 1)],
            "daily_std": var ** 0.5, "turnover": m.get("turnover_x", 0.0)}


def rolling_diff_fraction(ctx, base_er, cand_er, months=3):
    s, e = base_er["start_day"], base_er["end_day"]
    days = ctx.common[s:e + 1]
    mb = defaultdict(lambda: 1.0)
    mc = defaultdict(lambda: 1.0)
    for i, d in enumerate(days):
        mb[d[:7]] *= (1.0 + base_er["daily"][i])
        mc[d[:7]] *= (1.0 + cand_er["daily"][i])
    mlist = sorted(mb)
    wins = total = 0
    for i in range(len(mlist) - months + 1):
        rb = rc = 1.0
        for mth in mlist[i:i + months]:
            rb *= mb[mth]
            rc *= mc[mth]
        wins += (rc - 1.0) - (rb - 1.0) > 0
        total += 1
    return wins / total if total else 0.0


def gate(base_m, cand_m, roll_frac):
    reasons, ok = [], True
    if cand_m["net_total"] <= base_m["net_total"]:
        ok = False
        reasons.append("DEV 净累计 %.2f%% 未优于基线 %.2f%%" %
                       (cand_m["net_total"] * 100, base_m["net_total"] * 100))
    if cand_m["sharpe"] < base_m["sharpe"]:
        ok = False
        reasons.append("Sharpe %.2f < 基线 %.2f" % (cand_m["sharpe"],
                                                    base_m["sharpe"]))
    if cand_m["mdd"] < -PROMOTION["dd_cap"] or \
            cand_m["mdd"] < base_m["mdd"] - \
            PROMOTION["dd_slack_vs_baseline_pp"] / 100.0:
        ok = False
        reasons.append("回撤 %.1f%% 越线" % (cand_m["mdd"] * 100))
    if roll_frac < PROMOTION["rolling_min_positive_fraction"]:
        ok = False
        reasons.append("滚动窗口正收益占比 %.0f%% < 2/3" % (roll_frac * 100))
    if not reasons:
        reasons.append("满足预注册研究晋级条件（进入锁定前向观察）")
    return ("RESEARCH_ACCEPT" if ok else "REJECT"), reasons


def monthly_4col(ctx, er):
    """月收益率/期初权益/期末权益/权益变化/已实现盈亏 四分列。"""
    s = er["start_day"]
    by_month_eq = defaultdict(list)
    for i in range(er["end_day"] - s + 1):
        by_month_eq[ctx.common[s + i][:7]].append(i)
    realized = defaultdict(float)
    n_exit = defaultdict(int)
    for t in er["trades"]:
        realized[t["exit_date"][:7]] += pnl_yuan(t)
        n_exit[t["exit_date"][:7]] += 1
    rows = []
    prev_end = CASH0
    for mth in sorted(by_month_eq):
        idxs = by_month_eq[mth]
        eqs = [er["daily_equity"][s + i] for i in idxs]
        beg = prev_end
        end = eqs[-1]
        rows.append((mth, round(end / beg - 1.0, 6), round(beg, 2),
                     round(end, 2), round(end - beg, 2),
                     round(realized.get(mth, 0.0), 2),
                     n_exit.get(mth, 0)))
        prev_end = end
    return rows


def pos_daily(ctx, er):
    """单股逐日合计仓位（市值/当日权益）。"""
    idx = {d: i for i, d in enumerate(ctx.common)}
    batches = [(idx[t["entry_date"]], idx[t["exit_date"]], t)
               for t in er["trades"]]
    rows = []
    for d in range(er["start_day"], er["end_day"] + 1):
        val = defaultdict(float)
        for a, b, t in batches:
            if a <= d < b:
                val[t["code"]] += t["shares"] * ctx.stocks[t["code"]][
                    "open"][d]
        eq = er["daily_equity"].get(d)
        if not eq:
            continue
        for code in sorted(val):
            rows.append((ctx.common[d], code, round(val[code], 2),
                         round(val[code] / eq, 4)))
    return rows


def export_variant(tag, ctx, er_by, bench, cfg):
    with open(os.path.join(OUT, "config_%s.json" % tag), "w") as fp:
        json.dump(cfg, fp, ensure_ascii=False, indent=1)
    for label, er in er_by.items():
        wcsv("account_%s_%s.csv" % (tag, label),
             ["date", "daily_return", "equity", "cash", "market_value",
              "fees_cum", "exposure", "n_signals", "target_yuan",
              "filled_yuan", "n_skips", "benchmark_equity"],
             account_table(ctx, er, bench[label]))
        wcsv("fills_%s_%s.csv" % (tag, label),
             ["tid", "证券", "入场日", "入场价", "出场日", "出场价", "数量",
              "买费", "卖费", "净盈亏(元)", "退出原因"],
             [(t.get("tid", ""), t["code"], t["entry_date"], t["entry_px"],
               t["exit_date"], t["exit_px"], t["shares"],
               round(t["fee_buy"], 2), round(t["fee_sell"], 2),
               round(pnl_yuan(t), 0), t.get("reason"))
              for t in er["trades"]])
        wcsv("pos_daily_%s_%s.csv" % (tag, label),
             ["date", "code", "市值(元)", "占权益比"], pos_daily(ctx, er))
        wcsv("monthly_%s_%s.csv" % (tag, label),
             ["月份", "月收益率", "期初权益(元)", "期末权益(元)",
              "权益变化(元)", "已实现盈亏(元)", "平仓笔数"],
             monthly_4col(ctx, er))


def main():
    ensure()
    const = json.load(open(quant_agent.CONFIG))
    assert const["version"] in ("constitution_008", "constitution_009")
    ctx = Context(constitution=const)
    er_cfg = const["execution_rules"]

    from quant_agent.research.attribution import run_benchmark  # noqa
    import quant_agent.data.universe as U
    bench = run_benchmark(ctx, const, U.load_universe())

    def base_spec():
        return seed.materialize(seed.normalize({}, const), ctx.stocks,
                                ctx.codes, ctx.common,
                                universe=ctx.universe)

    results = {}
    # 基线（冻结对照组）
    sm, kw, _ = base_spec()
    results["baseline"] = ({label: run_window(ctx, sm, kw, *ctx.windows[label],
                                              er_cfg)
                            for label in ctx.dev_labels()}, None)
    print("baseline ok")

    # E1: 单股上限 20%
    er1 = json.loads(json.dumps(er_cfg))
    er1["cash_account"]["per_stock_cap"] = 0.20
    results["E1_cap20"] = ({label: run_window(ctx, sm, kw, *ctx.windows[label],
                                              er1)
                            for label in ctx.dev_labels()}, None)
    ledger("EXP_E1_per_stock_cap20", "variant",
           "E1: 单股合计仓位上限20%（仅限制新增买入）")
    print("E1 ok")

    # E2/E3: 评分降序
    for tag, model in (("E2_ridge_rank", "ridge"), ("E3_cart_rank", "cart")):
        rank = load_rank(model, ctx)
        results[tag] = ({label: run_window(ctx, sm, kw, *ctx.windows[label],
                                           er_cfg, intent_rank=rank)
                         for label in ctx.dev_labels()}, rank)
        ledger("EXP_%s" % tag, "variant",
               "%s: %s 质量评分当日意图降序（选股数量与sizing不变）" %
               (tag, model))
        print(tag, "ok")

    # 导出产物
    cfgs = {"baseline": {"frozen_baseline": "baseline_006"},
            "E1_cap20": {"per_stock_cap": 0.20},
            "E2_ridge_rank": {"intent_rank": "ridge"},
            "E3_cart_rank": {"intent_rank": "cart"}}
    for tag, (res, _r) in results.items():
        export_variant(tag, ctx, res, bench, cfgs[tag])

    # 比较表 + Gate + 压力/集中度
    rows, gates, stress, excl = [], {}, {}, {}
    base_m = {label: metrics_of("baseline", label, results["baseline"][0][label])
              for label in ctx.dev_labels()}
    for tag, (res, _r) in results.items():
        for label in ctx.dev_labels():
            rows.append(metrics_of(tag, label, res[label]))
        # 压力: 成本×2 重跑 DEV
        er2 = json.loads(json.dumps(er_cfg))
        er2["cost_buy"] = er_cfg["cost_buy"] * 2
        er2["cost_sell"] = er_cfg["cost_sell"] * 2
        rank = results[tag][1]
        er2r = json.loads(json.dumps(er2))
        if tag == "E1_cap20":
            er2r["cash_account"]["per_stock_cap"] = 0.20
        r2 = run_window(ctx, sm, kw, *ctx.windows["DEV"], er2r,
                        intent_rank=rank)
        stress[tag] = r2["daily_equity"][r2["end_day"]] / CASH0 - 1.0
        # 剔除 DEV 最大盈利股票（诊断近似：净累计 - 该票盈亏/本金）
        dev_tr = res["DEV"]["trades"]
        by_code = defaultdict(float)
        for t in dev_tr:
            by_code[t["code"]] += pnl_yuan(t)
        top = max(by_code, key=lambda c: by_code[c])
        excl[tag] = (top, by_code[top],
                     base_m["DEV"]["net_total"] * CASH0 if tag == "baseline"
                     else None)
    wcsv("comparison.csv",
         ["变体", "窗口", "净累计", "年化", "Sharpe", "回撤", "交易数",
          "胜率", "费用(元)", "日均敞口(全窗口)", "日度5%分位", "日波动",
          "换手"], [(r["tag"], r["window"], round(r["net_total"], 4),
                     round(r["annual"], 4), round(r["sharpe"], 3),
                     round(r["mdd"], 4), r["n_trades"],
                     round(r["win_rate"], 4), round(r["cost"], 0),
                     round(r["avg_exposure"] or 0, 4),
                     round(r["tail_5pct"], 6), round(r["daily_std"], 6),
                     round(r["turnover"], 3)) for r in rows])
    wcsv("stress_cost2x_DEV.csv", ["变体", "DEV净累计(成本x2)"],
         [(t, round(v, 4)) for t, v in stress.items()])
    wcsv("exclude_top_stock_DEV.csv",
         ["变体", "最大盈利股票", "其盈亏(元)", "DEV净累计_不含该票(近似)"],
         [(t, v[0], round(v[1], 0),
           round(((results[t][0]["DEV"]["daily_equity"][
               results[t][0]["DEV"]["end_day"]] - v[1]) / CASH0 - 1.0), 4))
          for t, v in excl.items()])

    for tag in ("E1_cap20", "E2_ridge_rank", "E3_cart_rank"):
        rf = rolling_diff_fraction(ctx, results["baseline"][0]["DEV"],
                                   results[tag][0]["DEV"])
        gates[tag] = gate(base_m["DEV"], metrics_of(tag, "DEV",
                                                    results[tag][0]["DEV"]),
                          rf)

    # 报告
    L = ["# 批次八实验报告 E1-E3（constitution_008）", ""]
    L.append("冻结基线 baseline_006；E4 因评分跨期无效（单调 0.25<0.5，"
             "IC 为负）按任务书停止，未执行。")
    L.append("")
    L.append("## 比较表")
    L.append("")
    L.append("| 变体 | 窗口 | 净累计 | 年化 | Sharpe | 回撤 | 交易数 | "
             "日均敞口 | 换手 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        L.append("| %s | %s | %.1f%% | %.1f%% | %.2f | %.1f%% | %d | "
                 "%.1f%% | %.2f |" %
                 (r["tag"], r["window"], r["net_total"] * 100,
                  r["annual"] * 100, r["sharpe"], r["mdd"] * 100,
                  r["n_trades"], (r["avg_exposure"] or 0) * 100,
                  r["turnover"]))
    L.append("")
    L.append("## 压力测试（DEV，成本×2）与集中度剔除")
    L.append("")
    L.append("| 变体 | DEV成本×2净累计 | DEV净累计(剔最大盈利股) |")
    L.append("|---|---|---|")
    for t in stress:
        L.append("| %s | %.1f%% | %.1f%% |" %
                 (t, stress[t] * 100,
                  ((results[t][0]["DEV"]["daily_equity"][
                      results[t][0]["DEV"]["end_day"]] - excl[t][1])
                   / CASH0 - 1.0) * 100))
    L.append("")
    for tag, (d, rs) in gates.items():
        L.append("## Gate（预注册冻结规则，DEV 窗口）: %s → %s" % (tag, d))
        for r in rs:
            L.append("- %s" % r)
    L.append("")
    L.append("预算台账: %s（本批追加 E1/E2/E3 三条）" % LEDGER)
    open(os.path.join(OUT, "report.md"), "w",
         encoding="utf-8").write("\n".join(L))
    print("OK ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
