#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次十一 E9：因子族整族测试（constitution_011，预注册 9 变体）。

背景（证据链）：
- E5  日闸消融 → 瓶颈非机会范围
- E7/E8 状态门 → 样本内即失败，方向关闭
- E1/E2/E3 执行与排序 → 无效
唯一未被证伪的方向 = 信号质量本身。quant_iter26/44 历史迭代库中有 8 个
事前既有评分函数（非本批次新造），整族同批、全部披露、不做事后挑选。

每个变体仅替换 score_fn；filt(R316_FLR)/出场/K/资金分配与基线完全一致。
判定沿用冻结 Gate（多期扣费收益/回撤/滚动252/Sharpe），同族 9 变体的
偶然达标风险在报告注明（family disclosure）。
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
from quant_agent.exp_round3 import metrics_of  # noqa: E402

OUT = os.path.join(quant_agent.VAR, "exp_round7")
LEDGER = os.path.join(quant_agent.VAR, "budget_ledger.jsonl")

FAMILY = ["score_r117_upvar", "score_shape", "score_resrev", "score_rankjump",
          "score_lowprice", "score_maalign", "score_asym", "score_x266",
          "score_r117"]


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
    assert const["version"] == "constitution_011"
    ctx = Context(constitution=const)
    er_cfg = const["execution_rules"]

    from quant_agent.research.attribution import run_benchmark  # noqa
    import quant_agent.data.universe as U
    bench = run_benchmark(ctx, const, U.load_universe())

    def build(over):
        sm, kw, canon = seed.materialize(seed.normalize(over, const),
                                         ctx.stocks, ctx.codes, ctx.common,
                                         universe=ctx.universe)
        return sm, kw, canon

    results = {}
    canons = {}
    for fn in FAMILY:
        sm, kw, canon = build({"score_fn": fn})
        canons[fn] = canon
        results[fn] = {label: run_window(ctx, sm, kw, *ctx.windows[label],
                                         er_cfg)
                       for label in ctx.dev_labels()}
        ledger("EXP_E9_%s" % fn, "variant",
               "E9 因子族: score_fn=%s（整族 9 变体同批）" % fn)

    json.dump({k: {"spec_hash": seed.spec_hash(v)}
               for k, v in canons.items()},
              open(os.path.join(OUT, "config_hashes.json"), "w"),
              ensure_ascii=False, indent=1)

    base = results["score_r117_upvar"]
    rows = []
    for fn in FAMILY:
        for label in ctx.dev_labels():
            m = metrics_of(fn, label, results[fn][label])
            rows.append((fn, label, round(m["net_total"], 4),
                         round(m["annual"], 4), round(m["sharpe"], 3),
                         round(m["mdd"], 4), m["n_trades"],
                         round(m["cost"], 0),
                         round(m["avg_exposure"] or 0, 4)))
    wcsv("comparison.csv",
         ["评分函数", "窗口", "净累计", "年化", "Sharpe", "回撤", "交易数",
          "费用(元)", "日均敞口"], rows)

    # 每变体核心产物（DEV 主窗口）+ 集中度/压力
    stress_rows, excl_rows = [], []
    for fn in FAMILY:
        er = results[fn]["DEV"]
        wcsv("account_%s_DEV.csv" % fn,
             ["date", "daily_return", "equity", "cash", "market_value",
              "fees_cum", "exposure", "n_signals", "target_yuan",
              "filled_yuan", "n_skips", "benchmark_equity"],
             account_table(ctx, er, bench["DEV"]))
        wcsv("fills_%s_DEV.csv" % fn,
             ["tid", "证券", "入场日", "入场价", "出场日", "出场价",
              "数量", "买费", "卖费", "净盈亏(元)", "退出原因"],
             [(t.get("tid", ""), t["code"], t["entry_date"], t["entry_px"],
               t["exit_date"], t["exit_px"], t["shares"],
               round(t["fee_buy"], 2), round(t["fee_sell"], 2),
               round(pnl_yuan(t), 0), t.get("reason"))
              for t in er["trades"]])
        by_code = defaultdict(float)
        for t in er["trades"]:
            by_code[t["code"]] += pnl_yuan(t)
        top = max(by_code, key=lambda c: by_code[c]) if by_code else "-"
        excl_rows.append((fn, top, round(by_code[top], 0) if by_code else 0,
                          round((er["daily_equity"][er["end_day"]] -
                                 by_code[top]) / CASH0 - 1.0, 4)
                          if by_code else 0.0))

    # 成本×2 压力（全部变体 DEV）
    er2 = json.loads(json.dumps(er_cfg))
    er2["cost_buy"] *= 2
    er2["cost_sell"] *= 2
    for fn in FAMILY:
        sm, kw, _ = build({"score_fn": fn})
        r2 = run_window(ctx, sm, kw, *ctx.windows["DEV"], er2)
        stress_rows.append((fn, round(r2["daily_equity"][r2["end_day"]]
                                      / CASH0 - 1.0, 4)))
    wcsv("stress_cost2x_DEV.csv", ["评分函数", "DEV净累计(成本x2)"], stress_rows)
    wcsv("exclude_top_stock_DEV.csv",
         ["评分函数", "最大盈利股票", "其盈亏(元)", "DEV净累计_不含该票(近似)"],
         excl_rows)

    L = ["# E9 因子族（constitution_011，整族 9 变体全部披露）", ""]
    L.append("仅替换 score_fn；其余规则与冻结基线完全一致。同族多 variant")
    L.append("存在偶然达标风险：任何晋级判定须过与基线相同的冻结 Gate，")
    L.append("且本报告不做事后挑选——包括全部失败变体。")
    L.append("")
    L.append("| 评分函数 | 窗口 | 净累计 | 年化 | Sharpe | 回撤 | 交易数 | 日均敞口 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        L.append("| %s | %s | %.1f%% | %.1f%% | %.2f | %.1f%% | %d | %.1f%% |"
                 % (r[0], r[1], r[2] * 100, r[3] * 100, r[4], r[5] * 100,
                    r[6], r[8] * 100))
    L.append("")
    L.append("## DEV 集中度与压力")
    L.append("| 评分函数 | 成本×2 DEV | 剔除最大盈利股后 DEV |")
    L.append("|---|---|---|")
    sd = dict(stress_rows)
    for fn, top, pnl, excl in excl_rows:
        L.append("| %s | %.1f%% | %.1f%% |" % (fn, sd[fn] * 100, excl * 100))
    L.append("")
    L.append("冻结基线 = score_r117_upvar（baseline_006）。晋级须过冻结 Gate：")
    L.append("DEV 净累计与 Sharpe 均不劣于基线，且跨期（IS 不回撤恶化）。")
    open(os.path.join(OUT, "report.md"), "w",
         encoding="utf-8").write("\n".join(L))
    print("OK ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
