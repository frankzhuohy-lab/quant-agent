#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选信号数据集构建器（批次八，constitution_008，任务书第二部分）。

对基线两窗口（IS/DEV）的每一天、每一个原始候选（横截面有限评分，
K 截断之前）输出一条记录：
  信号日期、股票、策略评分与横截面排名、因子原始值快照（信号日 t，
  即下单前一交易日收盘后可见）、逐层过滤原因、是否到达执行器、
  执行器成交/拒单及未成交原因、可执行性、扣费收益标签。

标签口径（任务书要求：全部候选独立计算，不只实际成交样本）:
  - 最早可成交时间 = 信号日次日 e 起，逐日顺延至首个非停牌且开盘未
    近似涨停的交易日；窗口内不可执行 → label 为空并记原因
    （停牌/退市/一字涨停锁定），不按理想价格成交；
  - 入场价 = 可执行日开盘价；出场用 qengine.plan_exit 同一实现
    （Keltner 扫描 + 固定期限 + 停牌/跌停顺延），与基线入场/退出
    规则逐字节一致；
  - 扣费标签 = exit_px*(1-cost_sell) / (entry_px*(1+cost_buy)) - 1；
  - 交叉校验：实际成交候选的标签必须与执行器成交记录一致。

特征（全部取 t=e-1，只允许下单前已知信息）:
  个股: score, rank_pct, mom5/10/20, rs20, rs_chg5, rsi, atr20_pct,
        vol_ratio(=vol5/vol20), dist20, upvar20, skew20
  市场: breadth, mkt_mom20, mkt_ret5（当日全候选共享）
  阻塞: 相对行业强弱（数据面无行业分类，不伪造，记 blocked）

产物: var/candidates/candidates_<窗口>.csv + build_report.md
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
from qengine import band_of, plan_exit  # noqa: E402

OUT = os.path.join(quant_agent.VAR, "candidates")

FEATS = ["mom5", "mom10", "mom20", "rs20", "rs_chg5", "rsi",
         "atr20", "vol5", "vol20", "dist20", "upvar20", "skew20"]
MKT = ["breadth", "mkt_mom20", "mkt_ret5"]


def ensure():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)


def _g(arr, i):
    v = arr[i] if arr is not None and i < len(arr) else None
    return v


def build_window(ctx, const, label):
    spec_m, kw, _ = seed.materialize(
        seed.normalize({}, const), ctx.stocks, ctx.codes, ctx.common,
        universe=ctx.universe)
    er = run_window(ctx, spec_m, kw, *ctx.windows[label],
                    const["execution_rules"])
    s, e1, w2 = ctx.windows[label]
    common = ctx.common
    susp = ctx.susp
    stocks = ctx.stocks
    feat = ctx.feat
    state = ctx.state
    cost_buy = const["execution_rules"]["cost_buy"]
    cost_sell = const["execution_rules"]["cost_sell"]
    band_cache = {}

    # 执行器成交/拒单按 (code, e) 索引
    filled = {}
    for t in er["trades"]:
        filled[(t["code"], t["e"])] = t
    skipped = {}
    for sk in er.get("skipped_entries", []):
        skipped[(sk["code"], sk["e"])] = sk

    rows = []
    n_not_exec = defaultdict(int)
    for (e, code, score, rank, in_topk, reason) in er["candidates"]:
        t = e - 1
        # 因子快照（t = 信号日，下单前信息）
        f = feat.get(code, {})
        atr = _g(f.get("atr20"), t)
        close = _g(f.get("close"), t)
        vol5, vol20 = _g(f.get("vol5"), t), _g(f.get("vol20"), t)
        snap = {
            "atr20_pct": (atr / close) if (atr and close) else None,
            "vol_ratio": (vol5 / vol20) if (vol5 and vol20) else None,
        }
        for k in FEATS:
            if k in ("atr20", "vol5", "vol20"):
                continue
            snap[k] = _g(f.get(k), t)
        mkt = {k: _g(state.get(k), t) for k in MKT}
        n_raw = er["sig_diag"].get(e, {}).get("raw", 0) or 1

        # 可执行性与标签
        label_net = None
        exec_day = None
        exec_reason = ""
        o = stocks[code]["open"]
        c = stocks[code]["close"]
        if code not in band_cache:
            band_cache[code] = band_of(code)
        band = band_cache[code]
        xe = xdx = None
        ed = None
        for d in range(e, w2 + 1):
            if common[d] in susp.get(code, ()):
                continue
            px = o[d]
            if px is None or px != px or px <= 0:
                exec_reason = "price_nan"      # 退市/数据缺失
                break
            pc = c[d - 1]
            if pc is not None and px >= pc * (1.0 + band - 0.005):
                continue                       # 开盘涨停，顺延
            ed = d
            break
        if ed is None and not exec_reason:
            exec_reason = "never_executable"   # 停牌/涨停锁定至窗口末
        if ed is not None:
            exec_day = common[ed]
            xdx, _pr, _ar, _df = plan_exit(
                stocks, feat, susp, common, code, ed,
                spec_m["params"]["max_hold"], spec_m["params"]["keltner_mult"],
                False, band, True, w2)
            exit_px = o[xdx]
            entry_px = o[ed]
            if exit_px is None or exit_px != exit_px or exit_px <= 0:
                exec_reason = "exit_price_nan"
            else:
                label_net = exit_px * (1.0 - cost_sell) / \
                    (entry_px * (1.0 + cost_buy)) - 1.0
        if exec_reason:
            n_not_exec[exec_reason] += 1

        fill = filled.get((code, e))
        sk = skipped.get((code, e))
        ordered = reason == "intent"
        rows.append([
            common[t], code, round(score, 6), rank,
            round((n_raw - rank) / max(1, n_raw - 1), 4) if n_raw > 1 else 1.0,
            in_topk, reason,
            ordered, bool(fill),
            (sk or {}).get("why", "") if sk else "",
            (sk or {}).get("detail", "") if sk else "",
            exec_day or "", exec_reason,
            round(label_net, 6) if label_net is not None else "",
            _pr if ed is not None else "",
        ] + [round(v, 6) if isinstance(v, float) else v for v in snap.values()]
          + [round(v, 6) if isinstance(v, float) else v for v in mkt.values()])

    # 交叉校验：实际成交候选的标签 vs 执行器成交净收益
    n_check = n_bad = 0
    for t in er["trades"]:
        key = (t["code"], t["e"])
        xd, _pr, _ar, _df = plan_exit(
            stocks, feat, susp, common, t["code"], t["e"],
            spec_m["params"]["max_hold"], spec_m["params"]["keltner_mult"],
            False, band_of(t["code"]), True, w2)
        sim_exit = stocks[t["code"]]["open"][xd]
        sim = sim_exit * (1.0 - cost_sell) / (t["entry_px"] * (1.0 + cost_buy)) - 1.0
        act = t["exit_px"] * (1.0 - cost_sell) / \
            (t["entry_px"] * (1.0 + cost_buy)) - 1.0
        n_check += 1
        if abs(sim - act) > 1e-9:
            n_bad += 1

    header = (["signal_date", "code", "score", "rank", "rank_pct",
               "in_topK", "engine_filter", "reached_executor",
               "filled", "skip_why", "skip_detail", "exec_day",
               "not_exec_reason", "label_net", "planned_exit_reason"]
              + list(snap.keys()) + MKT)
    with open(os.path.join(OUT, "candidates_%s.csv" % label), "w",
              newline="") as fp:
        w = csv.writer(fp)
        w.writerow(header)
        w.writerows(rows)
    stats = {
        "window": label, "n_candidates": len(rows),
        "n_intent": sum(1 for r in rows if r[7]),
        "n_filled": sum(1 for r in rows if r[8]),
        "n_skipped": sum(1 for r in rows if r[9]),
        "n_not_executable": dict(n_not_exec),
        "n_labelled": sum(1 for r in rows if r[13] != ""),
        "label_check_n": n_check, "label_check_bad": n_bad,
    }
    return stats


def main():
    ensure()
    const = json.load(open(quant_agent.CONFIG))
    assert const["version"] in ("constitution_008", "constitution_009")
    ctx = Context(constitution=const)
    all_stats = []
    for label in ctx.dev_labels():
        st = build_window(ctx, const, label)
        all_stats.append(st)
        print(label, st)
    L = ["# 候选信号数据集构建报告（constitution_008）", ""]
    L.append("标签口径：最早可成交日开盘价入场，qengine.plan_exit 同一"
             "出场实现（Keltner+固定期限+停牌/跌停顺延），"
             "扣费 label = exit*(1-卖费)/entry*(1+买费)-1。"
             "不可执行（停牌/涨停锁定/价格缺失）留空，不按理想价成交。")
    L.append("")
    L.append("特征取 t=信号日（下单前信息）；相对行业强弱因数据面无行业"
             "分类标记阻塞，未伪造。")
    L.append("")
    for st in all_stats:
        L.append("## %s" % st["window"])
        L.append("")
        for k, v in st.items():
            L.append("- %s: %s" % (k, v))
        L.append("")
    open(os.path.join(OUT, "build_report.md"), "w",
         encoding="utf-8").write("\n".join(L))
    print("OK ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
