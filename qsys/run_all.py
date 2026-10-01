#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qsys 主入口：全量体检 + 九部分框架报告生成。
用法: python3 run_all.py [--refresh] [--quick]
产物: REPORT.md / results.json / experiments/实验日志
"""
from __future__ import print_function
import os, sys, json, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from qdata import load_panel, build_all, r443_spec      # noqa: E402
from qengine import run_r443, metrics                   # noqa: E402
from qanalyze import (regime_labels, regime_performance, # noqa: E402
                      factor_analysis, sell_attribution,
                      universe_audit, market_index)
from qstress import (stress_suite, param_sensitivity,   # noqa: E402
                     walk_forward, REAL_COST, m_of)

WARMUP = 80


def pct(x):
    return ("%+.2f%%" % (x * 100)) if isinstance(x, (int, float)) else "-"


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def main(refresh=False, quick=False):
    stocks, codes, common, susp = load_panel(refresh=refresh)
    n = len(common)
    feat, state = build_all(stocks, codes, common)
    a_start = WARMUP
    a_end = n - 1
    split = a_start + int((a_end - a_start) * 0.6)
    is_rng = (a_start, split - 6, split - 1)      # e_start, entry_end, win_end
    oos_rng = (split, a_end - 6, a_end)
    print("数据: %s ~ %s (%d日) | IS至 %s | OOS %s ~ %s" % (
        common[0], common[-1], n, common[split - 1],
        common[split], common[a_end]))

    spec = r443_spec()
    res = {"meta": {"data_start": common[0], "data_end": common[-1],
                    "n_days": n, "split_date": common[split],
                    "pool_size": len(codes),
                    "generated": datetime.datetime.now().isoformat()}}

    # ---- IS / OOS 基线（真实口径） ----
    runs = {}
    for label, (s, ee, we) in (("IS", is_rng), ("OOS", oos_rng)):
        r = run_r443(stocks, codes, feat, state, common, susp,
                     s, ee, we, spec, cost_buy=REAL_COST[0],
                     cost_sell=REAL_COST[1], ld_block=True)
        runs[label] = r
        res[label] = m_of(r)
        print(label, json.dumps(res[label], ensure_ascii=False))

    # ---- 全样本（用于市场状态/卖出归因，样本更足） ----
    full = run_r443(stocks, codes, feat, state, common, susp,
                    a_start, a_end - 6, a_end, spec,
                    cost_buy=REAL_COST[0], cost_sell=REAL_COST[1],
                    ld_block=True)
    res["FULL"] = m_of(full)

    labels = regime_labels(stocks, codes, common)
    res["regime"] = regime_performance(full, labels, a_start, a_end)
    res["sells"] = sell_attribution(full)
    res["universe"] = universe_audit(stocks, codes, common, susp)

    if not quick:
        res["factors"] = factor_analysis(stocks, codes, feat, common,
                                         a_start, a_end - 6, a_end)
        res["stress"], _ = stress_suite(stocks, codes, feat, state, common,
                                        susp, split, a_end - 6, a_end)
        res["sensitivity"] = param_sensitivity(stocks, codes, feat, state,
                                               common, susp,
                                               split, a_end - 6, a_end)
        res["walkforward"] = walk_forward(stocks, codes, feat, state,
                                          common, susp, WARMUP, n)

    with open(os.path.join(HERE, "results.json"), "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    write_report(res, common, codes)
    print("OK -> REPORT.md / results.json")


def write_report(res, common, codes):
    L = []
    A = L.append
    A("# R443 量化系统九部分体检报告")
    A("\n> 生成: %s | 数据: %s ~ %s (%d交易日) | 股票池 %d 只半导体 | "
      "样本切分: IS(60%%) / OOS(40%%)，切分日 %s"
      % (res["meta"]["generated"][:19], res["meta"]["data_start"],
         res["meta"]["data_end"], res["meta"]["n_days"],
         res["meta"]["pool_size"], res["meta"]["split_date"]))
    A("\n> 口径: 真实成本（买0.20%%/卖0.25%%含印花税）+ 停牌/跌停顺延成交修正。"
      "冻结参数 UV=0.8/K=3/keltner=1.5/max_hold=10 全程未动。")

    def met_block():
        return md_table(
            ["样本", "笔数", "年化", "Sharpe", "Sortino", "最大回撤",
             "Calmar", "胜率", "盈亏比", "换手×", "平均敞口", "总成本"],
            [[k, m["n_trades"], pct(m["annual_return"]),
              m["sharpe"], m["sortino"], pct(m["max_drawdown"]),
              m["calmar"], pct(m["win_rate"]),
              m["payoff_ratio"], m["turnover_x"],
              m["avg_exposure"], m["total_cost"]]
             for k, m in [("IS", res["IS"]), ("OOS", res["OOS"]),
                          ("全样本", res["FULL"])]])

    A("\n## 基线总览（真实成本口径）\n")
    A(met_block())

    # 第一部分
    A("\n## 第一部分：市场状态\n")
    A("市场状态按等权指数 MA60 趋势 + 20日已实现波动中位划分。"
      "失效环境 = 该状态下策略年化为负或显著低于基准。\n")
    rows = []
    for k, v in sorted(res["regime"].items()):
        rows.append([k, v["days"], v["entered"], pct(v["annual_return"]),
                     v["sharpe"], pct(v["max_drawdown"]),
                     v["avg_exposure"], v["day_win_rate"]])
    A(md_table(["状态(趋势|波动)", "天数", "入场笔数", "年化", "Sharpe",
                "最大回撤", "平均敞口", "日胜率"], rows))

    # 第二部分
    A("\n## 第二部分：股票池体检\n")
    A("人工 curated 龙头池（2026年定型）→ **存在幸存者偏差**：池子按「当下龙头」"
      "圈定，历史回测隐含「事先知道谁会成龙头」的假设，IS/OOS 绝对收益应"
      "视为乐观上界。池内无 ST/*ST、无次新（全部上市满一年）、流动性充裕。\n")
    rows = [[c, v["name"], v["first_date"], v["avg_daily_amount_M"],
             v["susp_days_250"], v["limit_up_open_days_250"],
             v["limit_down_open_days_250"]]
            for c, v in sorted(res["universe"].items())]
    A(md_table(["代码", "名称", "数据首日", "日均成交额(百万)",
                "近250日停牌日", "开盘涨停日", "开盘跌停日"], rows))

    # 第三部分
    if "factors" in res:
        A("\n## 第三部分：因子拆解（fwd = T+1开→T+6开，全样本）\n")
        A("IC=皮尔逊、RankIC=斯皮尔曼横截面相关，t 按日度 IC 序列；"
          "|t|≥2 视为强证据（一级），1.4~2 弱证据（二级），<1.4 不显著。\n")
        rows = []
        for name, v in sorted(res["factors"]["factors"].items(),
                              key=lambda kv: -abs(kv[1]["RankIC_t"])):
            rows.append([name, v["RankIC"], v["RankIC_t"], v["RankICIR"],
                         v["IC"], v["IC_t"], pct(v["top_minus_bottom_5d"]),
                         v["n_days"],
                         ", ".join("%s:%+.3f" % (y, ic)
                                   for y, ic in list(v["IC_by_year"].items())[-3:])])
        A(md_table(["因子", "RankIC", "t", "ICIR", "IC", "t",
                    "Top-Bot 5日", "天数", "近三年IC"], rows))
        A("\n因子日均横截面 Rank 相关（|r|>0.7 视为冗余）:\n")
        corr = sorted(res["factors"]["rank_corr"].items(),
                      key=lambda kv: -abs(kv[1]))
        A("```\n" + "\n".join("%s = %.2f" % kv for kv in corr[:15]) + "\n```")

    # 第五部分
    A("\n## 第五部分：卖出归因\n")
    rows = [[k, v["n"], pct(v["avg_net"]), pct(v["avg_win"]),
             pct(v["avg_loss"]), v["win_rate"], v["payoff"],
             v["avg_hold_days"], v["deferred_events"]]
            for k, v in res["sells"].items() if not k.startswith("_")]
    A(md_table(["出场方式", "笔数", "均净收益", "均盈", "均亏", "胜率",
                "盈亏比", "均持有日", "跌停顺延次数"], rows))
    s = res["sells"]["_summary"]
    A("\n亏损画像: 亏损笔 %d，合计 %s，亏损总额/盈利总额 = %s" % (
        s["n_loss_trades"], pct(s["loss_contrib"]),
        s["loss_share_of_wins"]))

    # 第七部分 + 第八部分
    if "stress" in res:
        A("\n## 第七部分：A股交易真实性（OOS 窗口压力）\n")
        rows = []
        for k, v in res["stress"].items():
            rows.append([k, v["n_trades"], pct(v["annual_return"]),
                         v["sharpe"], pct(v["max_drawdown"]),
                         v["calmar"], pct(v["win_rate"]), v["turnover_x"]])
        A(md_table(["场景", "笔数", "年化", "Sharpe", "最大回撤", "Calmar",
                    "胜率", "换手×"], rows))
        A("\n**判断**: 若 C1~C4 任一情形下年化转负或回撤翻倍，"
          "则策略真实收益依赖理想成交假设。\n")

        A("## 第八部分：防过拟合（参数敏感性与 Walk-Forward）\n")
        for pname, blk in res["sensitivity"].items():
            rows = [[r["value"], r["n_trades"], pct(r["annual_return"]),
                     r["sharpe"], pct(r["max_drawdown"]), r["calmar"]]
                    for r in blk["rows"]]
            A("\n**%s**（基线 %s，年化区间宽度/基线 = %s）\n" % (
                pname, blk["base"]["value"], pct(blk["range_pct"])))
            A(md_table(["取值", "笔数", "年化", "Sharpe", "最大回撤",
                        "Calmar"], rows))
        A("\n平坦度判据: 参数 ±20%% 内年化不应反转符号；"
          "若基线恰为网格峰值 → 过拟合红旗。\n")
        rows = []
        for f in res.get("walkforward", []):
            rows.append(["%d %s→%s" % (f["fold"], f["oos_window"][0][:7],
                                       f["oos_window"][1][:7]),
                         f["oos"]["n_trades"],
                         pct(f["oos"]["annual_return"]),
                         f["oos"]["sharpe"], pct(f["oos"]["max_drawdown"]),
                         f["oos"]["win_rate"] or "-"])
        if rows:
            A("**Walk-Forward（冻结参数，OOS 段）**\n")
            A(md_table(["折", "OOS笔数", "OOS年化", "OOS Sharpe",
                        "OOS回撤", "OOS胜率"], rows))

    A("\n## 结论与下一步\n")
    A("见 experiments/EXPERIMENTS.md 实验日志（含假设、裁决与理由）。")
    with open(os.path.join(HERE, "REPORT.md"), "w") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main(refresh="--refresh" in sys.argv,
         quick="--quick" in sys.argv)
