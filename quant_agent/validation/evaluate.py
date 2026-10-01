#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证层：滚动验证 / 成本压力 / 区块 bootstrap（方案文档第 4/5 章）。

- walk_forward: 冻结参数只检验时间稳定性（不在折内重新选参）。
- stress_cost_x2: 成本×2 压力（确定性验证工具，不是 Agent 可调字段）。
- block_bootstrap_diff: 对候选 vs 基线的日收益差做 moving-block bootstrap，
  处理时间依赖；报告 diff<=0 的经验概率（选择偏差在 Gate 理由中注明）。
"""
from __future__ import print_function
import os, sys, math, json, random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import quant_agent
sys.path.insert(0, quant_agent.QSYS)

from qengine import run_r443, metrics   # noqa: E402
from qdata import r443_spec             # noqa: E402


def _m(run):
    m = metrics(run)
    if m:
        m.pop("equity_curve", None)
    return m


def walk_forward(ctx, canonical_spec, constitution, is_len=160, oos_len=60,
                 step=60):
    """与 qstress.walk_forward 同口径，但接受任意已验证规格。"""
    from quant_agent.strategies.adapter import normalize, materialize
    canonical_spec = dict(canonical_spec or {})
    allowed = set(constitution["research_policy"]["allowed_patch_fields"])
    patch_in = {k: v for k, v in canonical_spec.items() if k in allowed}
    norm = normalize(patch_in, constitution)
    spec, kw, _canon = materialize(norm, ctx.stocks, ctx.codes, ctx.common,
                                   universe=getattr(ctx, "universe", None))
    er = constitution["execution_rules"]
    from quant_agent.backtest.runner import run_window
    folds = []
    # 修正（2026-09-30 评审后）：WF 严格限制在 IS 窗口内，不触碰 DEV/SEALED
    is_lo = ctx.windows["IS"][0]
    is_hi = ctx.windows["IS"][1]
    start = max(ctx.warmup, is_lo)
    while start + is_len + oos_len <= is_hi + 1:
        is_s, is_e = start, start + is_len - 1
        oos_s, oos_e = start + is_len, start + is_len + oos_len - 1
        r_is = run_window(ctx, spec, kw, is_s, is_e - 5, is_e, er)
        r_oos = run_window(ctx, spec, kw, oos_s, oos_e - 5, oos_e, er)
        folds.append({
            "fold": len(folds) + 1,
            "is_window": [ctx.common[is_s], ctx.common[is_e]],
            "oos_window": [ctx.common[oos_s], ctx.common[oos_e]],
            "is": _m(r_is), "oos": _m(r_oos)})
        start += step
    return folds


def worst_fold_annual(folds):
    vals = [f["oos"]["annual_return"] for f in folds
            if f["oos"] and f["oos"].get("annual_return") is not None]
    return min(vals) if vals else None


def block_bootstrap_diff(daily_a, daily_b, n_boot=500, block=10, seed=42):
    """候选-基线日收益差的 moving-block bootstrap。

    返回 {"mean_diff_ann": 年化差, "p_le_0": P(diff<=0), "n": 样本数}。
    样本不足（<40 日重叠）返回 None（承认不确定性，不硬算）。
    """
    n = min(len(daily_a), len(daily_b))
    if n < 40:
        return None
    d = [daily_a[i] - daily_b[i] for i in range(n)]
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        acc, cnt = 0.0, 0
        while cnt < n:
            b = min(block, n - cnt)
            s = rng.randrange(0, n - b + 1)
            for i in range(s, s + b):
                acc += d[i]
            cnt += b
        means.append(acc / n)
    means.sort()
    mean_diff = sum(d) / n
    p_le_0 = sum(1 for x in means if x <= 0) / float(len(means))
    return {"mean_diff_daily": round(mean_diff, 6),
            "mean_diff_ann": round(mean_diff * 252, 4),
            "p_le_0": round(p_le_0, 3),
            "ci90": [round(means[int(0.05 * n_boot)], 6),
                     round(means[int(0.95 * n_boot) - 1], 6)],
            "n": n, "n_boot": n_boot, "block": block}


def validate(candidate, baseline, ctx, canonical_spec, constitution,
             artifact_dir, baseline_spec=None):
    """完整验证套件：返回证据包（Gate 的输入）。

    candidate/baseline: BacktestRunner.run() 的输出（IS/DEV 等开发窗口）。
    baseline_spec: 基线的 canonical 规格（压力公平比较用）。
    """
    from quant_agent.backtest.runner import BacktestRunner
    labels = [k for k in candidate if not k.startswith("_")]
    dev = labels[-1] if labels else "DEV"
    ev = {"windows": {}, "stress": {}, "walkforward": None,
          "bootstrap": None, "dev_label": dev}
    for label in labels:
        cm = candidate[label]["metrics"]
        bm = baseline[label]["metrics"]
        ev["windows"][label] = {
            "candidate": cm, "baseline": bm,
            "delta_sharpe": round(cm["sharpe"] - bm["sharpe"], 3),
            "delta_maxdd_pp": round((cm["max_drawdown"] -
                                     bm["max_drawdown"]) * 100, 2),
            "delta_annual_pp": round((cm["annual_return"] -
                                      bm["annual_return"]) * 100, 2)}
    # 成本×2 压力（最后开发窗口，现金口径与候选一致）
    runner = BacktestRunner(ctx, constitution)
    st = runner.run(canonical_spec, os.path.join(artifact_dir, "stress_x2"),
                    windows=(dev,),
                    cost_override=(constitution["execution_rules"]["cost_buy"] * 2,
                                   constitution["execution_rules"]["cost_sell"] * 2))
    ev["stress"]["cost_x2_" + dev] = st[dev]["metrics"]
    # 公平比较：基线同压（评审要求，表中不再出现"—"）
    stb = runner.run(baseline_spec, os.path.join(artifact_dir, "stress_x2_base"),
                     windows=(dev,),
                     cost_override=(constitution["execution_rules"]["cost_buy"] * 2,
                                    constitution["execution_rules"]["cost_sell"] * 2))
    ev["stress_baseline"] = {"cost_x2_" + dev: stb[dev]["metrics"]}
    # 滚动验证（限 IS 窗口内）
    ev["walkforward"] = walk_forward(ctx, canonical_spec, constitution)
    ev["wf_worst_fold_oos_annual"] = worst_fold_annual(ev["walkforward"])
    # 窗口日期范围（封存访问审计的依据）
    ev["window_ranges"] = {k: list(ctx.window_dates(k)) for k in labels}
    ev["bootstrap_method"] = {
        "diff": "候选−基线 %s 窗口日收益序列逐日相减" % dev,
        "resampling": "moving-block bootstrap（block=%d，保持时间依赖）" % 10,
        "n_boot": 500, "caveat": ("半年窗口样本短，p 值只作辅助证据；"
                                  "拒绝须以预设规则为准")}
    return ev


def _daily_from_equity(eq_file):
    curve = eq_file["curve"]
    out = []
    for i in range(1, len(curve)):
        out.append(curve[i] / curve[i - 1] - 1.0)
    return out
