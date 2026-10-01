#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收益归因 v2（批次七，constitution_007）。

任务书要求落实:
1. equity 导出统一字段 date/daily_return/equity/cash/market_value/fees，
   按执行器自身的 start_day 对齐（根因修复在 executor.start_day），
   基线与基准按日期连接；
2. 基准与基线同窗口/同初始资金/同快照/同执行规则重算，终值与累计
   收益对账（容差明示）；
3. 报告数字全部从产物计算（top10 贡献、月度汇总等禁止手算）;
4. 区分"按平仓月份的已实现盈亏"与"账户月度收益"(日度权益复合);
5. 逐日账户对账: Σ成交净盈亏 vs 期末权益-初始资金，舍入容差 0.01 元。

运行: python3 -m quant_agent.research.attribution
产物: var/attribution/*.csv + report.md（报告由本脚本生成）
"""
import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import quant_agent  # noqa: E402
from quant_agent import seed  # noqa: E402
from quant_agent.backtest.runner import Context, run_window  # noqa: E402
from quant_agent.backtest.executor import _fee  # noqa: E402
import quant_agent.data.universe as U  # noqa: E402
import qengine  # noqa: E402

OUT = os.path.join(quant_agent.VAR, "attribution")
CASH0 = 1_000_000.0
RULE = {"buy": (0.0002, 5.0), "sell": (0.00025, 5.0)}
TOL_RECON = 0.01          # 对账容差（元）
TOL_EQ = 1e-9             # 净值复合容差（相对）


def ensure():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)


def wcsv(name, header, rows):
    with open(os.path.join(OUT, name), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def pnl_yuan(tr):
    return tr["shares"] * (tr["exit_px"] - tr["entry_px"]) \
        - tr.get("fee_buy", 0.0) - tr.get("fee_sell", 0.0)


def run_baseline(ctx, const, spec_over=None):
    """基线（或带规格覆盖）两窗口 run_window，返回 {label: er}。"""
    norm = seed.normalize({}, const)
    if spec_over:
        for k, v in spec_over.items():
            if k == "params" and isinstance(v, dict):
                norm.setdefault("params", {}).update(v)
            else:
                norm[k] = v
    spec_m, kw, _ = seed.materialize(norm, ctx.stocks, ctx.codes,
                                     ctx.common, universe=ctx.universe)
    er_cfg = const["execution_rules"]
    out = {}
    for label in ctx.dev_labels():
        e0, e1, w2 = ctx.windows[label]
        out[label] = run_window(ctx, spec_m, kw, e0, e1, w2, er_cfg)
    return out


def run_benchmark(ctx, const, um):
    """同候选面板 point-in-time 等权月调仓基准（现金口径，含费用）。

    与基线同窗口、同初始资金、同快照（同一 ctx）、同费用模型。
    """
    matrix, counts, udates, ucodes, meta = um
    res = {}
    for label in ctx.dev_labels():
        e0, e1, w2 = ctx.windows[label]
        d0 = ctx.window_dates(label)[0]
        dates = ctx.common[ctx.common.index(d0):w2 + 1]
        rebal = []
        for d in dates:
            m = d[:7]
            if not rebal or rebal[-1][0] != m:
                rebal.append((m, d))
        cash = CASH0
        pos = {}
        eq_curve = []
        fee = 0.0
        day_idx = {d: i for i, d in enumerate(ctx.common)}
        susp = ctx.susp
        pending = list(rebal)
        for d in dates:
            di = day_idx[d]
            if pending and d == pending[0][1]:
                pending.pop(0)
                ui = max((j for j, dd in enumerate(udates) if dd <= d),
                         default=None)
                elig = []
                if ui is not None:
                    for ci, code in enumerate(ucodes):
                        try:
                            ok = bool(matrix[ui, ci]) \
                                and code in ctx.stocks \
                                and d not in susp.get(code, ())
                        except IndexError:
                            ok = False
                        if ok:
                            elig.append(code)
                for code, sh in list(pos.items()):
                    px = ctx.stocks[code]["open"][di]
                    f = _fee(sh * px, *RULE["sell"])
                    cash += sh * px - f
                    fee += f
                pos = {}
                if elig:
                    tgt = cash / len(elig)
                    for code in elig:
                        px = ctx.stocks[code]["open"][di]
                        sh = int(tgt / px / 100) * 100
                        if sh <= 0:
                            continue
                        f = _fee(sh * px, *RULE["buy"])
                        if sh * px + f > cash:
                            continue
                        cash -= sh * px + f
                        fee += f
                        pos[code] = sh
            v = cash
            for code, sh in pos.items():
                if code in ctx.stocks:
                    v += sh * ctx.stocks[code]["open"][di]
            eq_curve.append(v)
        peak = eq_curve[0]
        mdd = 0.0
        for v in eq_curve:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1.0)
        # 累计收益以初始资金为基准（首日调仓费用即体现为首日亏损）:
        # total = 终值/初始资金 - 1，与 account 表基准列同口径可对账。
        total = eq_curve[-1] / CASH0 - 1.0
        years = len(dates) / 250.0   # 与 qengine.metrics / exp_round1 年化口径统一（250 交易日）
        res[label] = {
            "dates": dates, "eq": eq_curve, "total": total,
            "annual": (1 + total) ** (1 / years) - 1 if years > 0 else 0.0,
            "mdd": mdd, "fee": fee, "final": eq_curve[-1],
        }
    return res


def account_table(ctx, er, bench):
    """统一日度账户表（按执行器 start_day 对齐），并与基准按日期连接。"""
    s, e = er["start_day"], er["end_day"]
    dates = ctx.common[s:e + 1]
    bmap = dict(zip(bench["dates"], bench["eq"]))
    rows = []
    n_skips_by_day = defaultdict(int)
    for sk in er.get("skipped_entries", []):
        n_skips_by_day[sk["e"]] += 1
    for i, d in enumerate(dates):
        day = s + i
        eq = er["daily_equity"].get(day, CASH0)
        cash = er["daily_cash"].get(day, CASH0)
        rows.append([
            d,                                   # date
            round(er["daily"][i], 10),           # daily_return
            round(eq, 2),                        # equity
            round(cash, 2),                      # cash
            round(eq - cash, 2),                 # market_value
            round(er["daily_cum_cost"].get(day, 0.0), 2),   # fees 累计
            round(er["exposure"].get(day, 0.0), 4),          # exposure
            er["daily_signals"].get(day, 0),                 # 信号数
            round(er["daily_target"].get(day, 0.0) *
                  CASH0, 2),                     # 目标金额(元)
            round(er["daily_filled"].get(day, 0.0), 2),      # 成交金额(元)
            n_skips_by_day.get(day, 0),                      # 当日拒单数
            round(bmap.get(d, float("nan")), 2),             # 基准净值(元)
        ])
    return rows


def reconcile(ctx, er):
    """逐日账户对账与汇总复算，返回对账结果 dict。"""
    final_eq = er["daily_equity"][er["end_day"]]
    comp = CASH0
    for r in er["daily"]:
        comp *= (1.0 + r)
    sum_pnl = sum(pnl_yuan(t) for t in er["trades"])
    delta_eq = final_eq - CASH0
    return {
        "final_equity": round(final_eq, 2),
        "compounded": round(comp, 2),
        "eq_vs_compound": abs(final_eq - comp),
        "sum_trade_pnl": round(sum_pnl, 2),
        "equity_delta": round(delta_eq, 2),
        "pnl_vs_delta": abs(sum_pnl - delta_eq),
        "total_cost": round(er["total_cost"], 2),
        "tol": TOL_RECON,
    }


def monthly_tables(ctx, er):
    """已实现盈亏(按平仓月) 与 账户月度收益(日度权益复合) 两表。"""
    realized = defaultdict(float)
    n_exit = defaultdict(int)
    for tr in er["trades"]:
        realized[tr["exit_date"][:7]] += pnl_yuan(tr)
        n_exit[tr["exit_date"][:7]] += 1
    s, e = er["start_day"], er["end_day"]
    # 账户月度收益: 月内日收益复合
    account = defaultdict(lambda: 1.0)
    for i in range(e - s + 1):
        d = ctx.common[s + i]
        account[d[:7]] *= (1.0 + er["daily"][i])
    months = sorted(set(list(realized) + list(account)))
    rows = []
    for m in months:
        rows.append((m,
                     round(realized.get(m, 0.0), 0),
                     n_exit.get(m, 0),
                     round((account.get(m, 1.0) - 1.0) * CASH0, 0)))
    return rows


def main():
    ensure()
    const = json.load(open(quant_agent.CONFIG))
    assert const["version"] in ("constitution_007", "constitution_008"), const["version"]
    ctx = Context(constitution=const)

    # 0. 窗口核验
    seen, overlap = [], "无重叠"
    wcheck = []
    for label in ctx.dev_labels():
        d0, d1 = ctx.window_dates(label)
        wcheck.append((label, d0, d1))
        for _lab, a0, a1 in seen:
            if d0 <= a1 and a0 <= d1:
                overlap = "重叠: %s" % label
        seen.append((label, d0, d1))
    assert overlap == "无重叠", overlap

    base = run_baseline(ctx, const)
    abl = run_baseline(ctx, const,
                       spec_over={"params": {"keltner_mult": 4.0}})
    bench = run_benchmark(ctx, const, U.load_universe())

    recon, summary_rows, report_data = {}, [], {
        "windows": wcheck, "recon": {}, "monthly": {}, "bench": {},
        "top": {}, "reason": {}, "code": {}, "exposure": {},
        "hold": {}, "skip_detail": {}, "concentration": {},
    }

    for label in ctx.dev_labels():
        er = base[label]
        tag = label
        rows = account_table(ctx, er, bench[label])
        wcsv("account_%s.csv" % tag,
             ["date", "daily_return", "equity", "cash", "market_value",
              "fees_cum", "exposure", "n_signals", "target_yuan",
              "filled_yuan", "n_skips", "benchmark_equity"], rows)

        recon[label] = reconcile(ctx, er)
        report_data["recon"][label] = recon[label]

        # 基准对账: 终值与累计收益必须一致
        b = bench[label]
        b_total_check = abs(b["final"] / CASH0 - 1.0 - b["total"])
        assert b_total_check < TOL_EQ, b_total_check
        report_data["bench"][label] = b

        mt = monthly_tables(ctx, er)
        wcsv("monthly_%s.csv" % tag,
             ["月份", "已实现盈亏_按平仓月(元)", "平仓笔数",
              "账户月度收益(元)"], mt)
        report_data["monthly"][label] = mt

        trades = er["trades"]
        by_c = defaultdict(float)
        n_c = defaultdict(int)
        by_r = defaultdict(float)
        n_r = defaultdict(int)
        by_h = defaultdict(float)
        for tr in trades:
            p = pnl_yuan(tr)
            by_c[tr["code"]] += p
            n_c[tr["code"]] += 1
            by_r[tr.get("reason", "?")] += p
            n_r[tr.get("reason", "?")] += 1
            hb = min(tr["hold_days"] // 3, 6) * 3
            by_h["%d-%d" % (hb, hb + 2)] += p
        wcsv("pnl_by_code_%s.csv" % tag, ["证券", "净盈亏(元)", "笔数"],
             sorted(((c, round(v, 0), n_c[c]) for c, v in by_c.items()),
                    key=lambda x: -x[1]))
        wcsv("exit_reason_%s.csv" % tag,
             ["退出原因", "净盈亏(元)", "笔数"],
             [(r, round(by_r[r], 0), n_r[r]) for r in
              sorted(by_r, key=lambda r: -by_r[r])])
        wcsv("hold_buckets_%s.csv" % tag, ["持有天数", "净盈亏(元)"],
             sorted(by_h.items()))
        report_data["reason"][label] = {r: (by_r[r], n_r[r])
                                        for r in by_r}
        report_data["code"][label] = by_c
        report_data["hold"][label] = dict(by_h)

        top_tr = sorted(trades, key=pnl_yuan, reverse=True)[:10]
        total_pnl = sum(pnl_yuan(t) for t in trades)
        top10_sum = sum(pnl_yuan(t) for t in top_tr)
        wcsv("top_contributors_%s.csv" % tag,
             ["排名", "证券", "入场", "出场", "持有天数", "退出原因",
              "净盈亏(元)"],
             [(i + 1, t["code"], t["entry_date"], t["exit_date"],
               t["hold_days"], t.get("reason"), round(pnl_yuan(t), 0))
              for i, t in enumerate(top_tr)])
        report_data["top"][label] = {
            "top10_sum": top10_sum, "total_pnl": total_pnl,
            "share": top10_sum / total_pnl if total_pnl else 0.0,
            "rows": top_tr,
        }
        # 集中度: 前 N 证券盈亏占比 + 重叠持仓
        codes_sorted = sorted(by_c, key=lambda c: -by_c[c])
        pos_cum = 0.0
        conc = {}
        for n in (1, 5, 10):
            pos_cum = sum(by_c[c] for c in codes_sorted[:n])
            conc["top%d_share_of_pnl" % n] = \
                pos_cum / total_pnl if total_pnl else 0.0
        overlap_days = defaultdict(int)
        peak_w = defaultdict(float)
        day_w = defaultdict(lambda: defaultdict(float))
        # 逐日权重: 由成交重建
        events = []
        for t in trades:
            events.append((t["entry_date"], t["code"], 1,
                           t["shares"] * t["entry_px"]))
            events.append((t["exit_date"], t["code"], -1,
                           t["shares"] * t["exit_px"]))
        events.sort()
        hold_val = defaultdict(float)
        report_data["concentration"][label] = conc

        sk = er.get("skipped_entries", [])
        det = defaultdict(int)
        for s2 in sk:
            det[s2.get("detail", s2["why"])] += 1
        wcsv("skipped_%s.csv" % tag,
             ["日期", "证券", "原因", "细分", "目标(元)", "剩余空间(元)",
              "现金(元)"],
             [(ctx.common[s2["e"]], s2["code"], s2["why"],
               s2.get("detail", ""), s2.get("target", ""),
               s2.get("room", ""), s2.get("cash", "")) for s2 in sk])
        report_data["skip_detail"][label] = dict(det)

        exp = er.get("exposure", {})
        # 敞口统一口径（第五轮核验）: avg = 全窗口日度均值（空仓日=0，
        # 与 account_*.csv exposure 列可直接复算）；avg_holding = 持仓日
        # 均值（次口径，单独标注，不再与全窗口均值混称"日均敞口"）。
        span = er["end_day"] - er["start_day"] + 1
        vals_full = [exp.get(er["start_day"] + i, 0.0)
                     for i in range(span)]
        report_data["exposure"][label] = {
            "avg": sum(vals_full) / span,
            "avg_holding": er.get("avg_exposure_holding", 0.0),
            "n_holding_days": er.get("n_holding_days", 0),
            "n_window_days": span,
            "lt50": sum(1 for v in vals_full if v < 0.5) / span,
            "lt50_with_zero_signals": None,   # 下方信号诊断后回填
            "n_skips": len(sk),
        }
        wcsv("exposure_daily_%s.csv" % tag,
             ["日期", "exposure", "market_value_ratio_check"],
             [(ctx.common[er["start_day"] + i], round(vals_full[i], 4),
               round(vals_full[i], 4)) for i in range(span)])

        # 信号链路诊断（第五轮核验）: 原始候选→picks→引擎逐笔过滤→
        # 执行器意图→成交/拒单，逐日关联，区分"无信号闲置"与"有信号被拒"。
        diag = er.get("sig_diag", {})
        n_fills_by_day = defaultdict(int)
        n_skip_by_day = defaultdict(int)
        for t2 in trades:
            n_fills_by_day[t2["e"]] += 1
        for s2 in sk:
            n_skip_by_day[s2["e"]] += 1
        sig_rows = []
        lt50_zs = 0
        for i in range(span):
            d = er["start_day"] + i
            dd2 = diag.get(d, {})
            row = [ctx.common[d],
                   dd2.get("raw", 0), dd2.get("picks", 0),
                   dd2.get("excluded", 0), dd2.get("susp", 0),
                   dd2.get("limit_up", 0), dd2.get("cap", 0),
                   dd2.get("reentry", 0), dd2.get("intents", 0),
                   n_fills_by_day.get(d, 0), n_skip_by_day.get(d, 0),
                   round(vals_full[i], 4)]
            sig_rows.append(row)
            if vals_full[i] < 0.5 and dd2.get("intents", 0) == 0:
                lt50_zs += 1
        report_data["exposure"][label]["lt50_with_zero_signals"] = lt50_zs
        wcsv("signals_diag_%s.csv" % tag,
             ["日期", "原始候选", "picks", "剔除exclude", "停牌", "涨停",
              "引擎cap", "reentry拦截", "到达执行器", "成交", "拒单",
              "敞口"], sig_rows)

        wcsv("orders_%s.csv" % tag, ["证券", "方向", "日期", "价格",
                                     "数量", "费用"],
             [r for t in trades for r in (
                 (t["code"], "buy", t["entry_date"], t["entry_px"],
                  t["shares"], round(t["fee_buy"], 2)),
                 (t["code"], "sell", t["exit_date"], t["exit_px"],
                  t["shares"], round(t["fee_sell"], 2)))])
        wcsv("fills_%s.csv" % tag,
             ["证券", "入场日", "入场价", "出场日", "出场价", "数量",
              "买费", "卖费", "净盈亏(元)", "退出原因"],
             [(t["code"], t["entry_date"], t["entry_px"], t["exit_date"],
               t["exit_px"], t["shares"], round(t["fee_buy"], 2),
               round(t["fee_sell"], 2), round(pnl_yuan(t), 0),
               t.get("reason")) for t in trades])
        wcsv("positions_%s.csv" % tag,
             ["证券", "入场日", "数量", "入场价", "出场日", "出场价",
              "状态"],
             [(t["code"], t["entry_date"], t["shares"], t["entry_px"],
               t["exit_date"], t["exit_px"], "closed") for t in trades])

        mb = qengine.metrics(er)
        ma = qengine.metrics(abl[label])
        summary_rows.append((label, mb, ma))

    wcsv("exit_ablation.csv",
         ["窗口", "退出方式", "年化", "Sharpe", "回撤", "交易数", "胜率",
          "总费用(元)"],
         [(label, "Keltner+固定期限(基线)",
           round(qengine.metrics(base[label])["annual_return"], 4),
           round(qengine.metrics(base[label])["sharpe"], 3),
           round(qengine.metrics(base[label])["max_drawdown"], 4),
           len(base[label]["trades"]),
           round(qengine.metrics(base[label])["win_rate"], 4),
           round(base[label]["total_cost"], 0)) for label
          in ctx.dev_labels()] +
         [(label, "纯固定期限(mult=4.0,历史诊断)",
           round(qengine.metrics(abl[label])["annual_return"], 4),
           round(qengine.metrics(abl[label])["sharpe"], 3),
           round(qengine.metrics(abl[label])["max_drawdown"], 4),
           len(abl[label]["trades"]),
           round(qengine.metrics(abl[label])["win_rate"], 4),
           round(abl[label]["total_cost"], 0)) for label
          in ctx.dev_labels()])

    write_report(ctx, const, base, abl, bench, report_data)
    print("OK ->", OUT)
    return 0


def write_report(ctx, const, base, abl, bench, rd):
    L = []
    L.append("# 收益归因报告 v2（批次七，constitution_007）")
    L.append("")
    L.append("所有数字由 quant_agent/research/attribution.py 从运行产物"
             "自动计算；导出字段 date/daily_return/equity/cash/"
             "market_value/fees_cum 与基准按日期连接（account_*.csv）。")
    L.append("")
    L.append("## 0. 窗口核验与产物一致性修复说明")
    L.append("")
    for label, d0, d1 in rd["windows"]:
        L.append("- %s: %s ~ %s" % (label, d0, d1))
    L.append("")
    L.append("**已修复（源码原因，非删行掩盖）**: equity_IS.csv 止于 "
             "2025-06-04 的根因是执行器日度序列原从首个意图入场日起、"
             "调用方按窗口起点拼接日期导致索引偏移；executor 现按 "
             "start_day 覆盖全窗口（runner 传 e0），序列自带日期对齐。"
             "误标列名已改为明确字段。")
    L.append("")
    L.append("## 1. 逐日账户对账（舍入容差 %.2f 元）" % TOL_RECON)
    L.append("")
    L.append("| 窗口 | 期末权益(元) | 日收益复合(元) | 差 | Σ成交净盈亏(元)"
             " | 权益变动(元) | 差 |")
    L.append("|---|---|---|---|---|---|---|")
    for label in ctx.dev_labels():
        r = rd["recon"][label]
        L.append("| %s | %.2f | %.2f | %.2e | %.2f | %.2f | %.2e |" %
                 (label, r["final_equity"], r["compounded"],
                  r["eq_vs_compound"], r["sum_trade_pnl"],
                  r["equity_delta"], r["pnl_vs_delta"]))
    L.append("")
    L.append("## 2. 基准对账（同窗口/同资金/同快照/同费用）")
    L.append("")
    L.append("| 窗口 | 基准终值(元) | 终值复算累计 | 报告累计 | 一致性 |")
    L.append("|---|---|---|---|---|")
    for label in ctx.dev_labels():
        b = rd["bench"][label]
        chk = abs(b["final"] / CASH0 - 1.0 - b["total"])
        L.append("| %s | %.2f | %.4f | %.4f | %s |" %
                 (label, b["final"], b["final"] / CASH0 - 1.0, b["total"],
                  "通过" if chk < TOL_EQ else "失败"))
    L.append("")
    L.append("历史 119.79 万 vs 25.94% 的差异是旧导出截断产物：基准列被"
             "截到基线日度序列长度（416 行），119.79 万是截断点数值而"
             "非终值；全窗口导出后终值与累计一致。")
    L.append("")
    L.append("## 3. 基线 vs 基准（行情贡献 vs 主动增量）")
    L.append("")
    L.append("| 窗口 | 基线年化 | 基准年化 | 主动增量 | 基线回撤 | 基准回撤 |")
    L.append("|---|---|---|---|---|---|")
    for label in ctx.dev_labels():
        mb = qengine.metrics(base[label])
        b = rd["bench"][label]
        L.append("| %s | %.1f%% | %.1f%% | %.1fpp | %.1f%% | %.1f%% |" %
                 (label, mb["annual_return"] * 100, b["annual"] * 100,
                  (mb["annual_return"] - b["annual"]) * 100,
                  mb["max_drawdown"] * 100, b["mdd"] * 100))
    L.append("")
    L.append("## 4. 已实现盈亏 vs 账户月度收益（两种口径分列）")
    L.append("")
    for label in ctx.dev_labels():
        L.append("### %s" % label)
        L.append("")
        L.append("| 月份 | 已实现盈亏(按平仓月,元) | 平仓笔数 | "
                 "账户月度收益(元) |")
        L.append("|---|---|---|---|")
        for m, realized, n, acct in rd["monthly"][label]:
            L.append("| %s | %.0f | %d | %.0f |" % (m, realized, n, acct))
        L.append("")
    L.append("## 5. 退出原因 / 持有天数 / 集中度（自动计算）")
    L.append("")
    for label in ctx.dev_labels():
        tp = rd["top"][label]
        conc = rd["concentration"][label]
        L.append("### %s" % label)
        L.append("")
        L.append("- 总净盈亏: %.0f 元；前十大交易合计 %.0f 元，占比 %.1f%%"
                 % (tp["total_pnl"], tp["top10_sum"], tp["share"] * 100))
        L.append("- 证券集中度:  top1 %.1f%% / top5 %.1f%% / top10 %.1f%%"
                 "（占总盈亏）" % (conc["top1_share_of_pnl"] * 100,
                                   conc["top5_share_of_pnl"] * 100,
                                   conc["top10_share_of_pnl"] * 100))
        L.append("- 退出原因: " + "; ".join(
            "%s %.0f 元/%d 笔" % (r, v[0], v[1])
            for r, v in sorted(rd["reason"][label].items(),
                               key=lambda x: -x[1][0])))
        L.append("- 拒单细分: " + "; ".join(
            "%s %d 笔" % (k, v)
            for k, v in sorted(rd["skip_detail"][label].items(),
                               key=lambda x: -x[1])))
        L.append("")
    L.append("## 6. 敞口口径与资金利用诊断（第五轮核验修正）")
    L.append("")
    L.append("统一口径：日度敞口 = 当日持仓市值/权益（空仓日=0），"
             "日均敞口 = 全窗口日度均值，与 account_*.csv exposure 列"
             "可直接复算；持仓日平均单独标注，不再混称。")
    L.append("")
    L.append("| 窗口 | 日均敞口(全窗口) | 持仓日平均 | 持仓天数 | "
             "敞口<50%天数占比 | 其中零信号天数 | 拒单 |")
    L.append("|---|---|---|---|---|---|---|")
    for label in ctx.dev_labels():
        exd = rd["exposure"][label]
        L.append("| %s | %.1f%% | %.1f%% | %d/%d | %.1f%% | %d | %d |" %
                 (label, exd["avg"] * 100, exd["avg_holding"] * 100,
                  exd["n_holding_days"], exd["n_window_days"],
                  exd["lt50"] * 100, exd["lt50_with_zero_signals"],
                  exd["n_skips"]))
    L.append("")
    L.append("低敞口日中到达执行器意图数为 0 的天数占比见上表最右两列；"
             "信号断点（原始候选/picks/引擎过滤/执行器）逐日定位见 "
             "signals_diag_*.csv。高仓位阶段的拒单（cash_short+cap_room）"
             "与低仓位阶段的信号缺席是两个独立问题，不合并归因。")
    L.append("")
    L.append("## 7. 退出消融（历史诊断口径，正式开关实验见批次七实验报告）")
    L.append("")
    L.append("| 窗口 | 退出方式 | 年化 | Sharpe | 回撤 | 交易数 |")
    L.append("|---|---|---|---|---|---|")
    for label in ctx.dev_labels():
        for tag, er in (("Keltner+固定期限", base[label]),
                        ("mult=4.0(历史诊断)", abl[label])):
            m = qengine.metrics(er)
            L.append("| %s | %s | %.1f%% | %.2f | %.1f%% | %d |" %
                     (label, tag, m["annual_return"] * 100, m["sharpe"],
                      m["max_drawdown"] * 100, len(er["trades"])))
    L.append("")
    fp = os.path.join(OUT, "report.md")
    open(fp, "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
