#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qsys 自动优化循环（第九部分框架的代码化）。

每轮实验严格走 9 步：
  Step1 诊断当前最大问题（人工/上一轮报告写入 exp 的 diagnosis 字段）
  Step2 提出 ≤3 个假设（HYPOTHESES 注册表）
  Step3 按 预期价值 × 理论合理性 × (1-过拟合风险) 选 1 个
  Step4 只改一个主要变量
  Step5 重新回测（IS + OOS，真实成本口径）
  Step6 与 Baseline 对比（必须输出 9 项指标）
  Step7 样本外 + 压力测试
  Step8 裁决 ACCEPT / REJECT / NEEDS MORE TESTING（附理由）
  Step9 追加实验日志（experiments/EXP_XXXX.json + EXPERIMENTS.md，永不删除）

用法:
  python3 optimize.py list                 # 列出假设
  python3 optimize.py run <hyp_id> [--note "..."]   # 跑一轮实验并记录
  python3 optimize.py diagnose             # 输出当前诊断摘要
"""
from __future__ import print_function
import os, sys, json, datetime, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
EXP_DIR = os.path.join(HERE, "experiments")
LOG_MD = os.path.join(EXP_DIR, "EXPERIMENTS.md")
sys.path.insert(0, HERE)

from qdata import load_panel, build_all, r443_spec   # noqa: E402
from qengine import run_r443, metrics                # noqa: E402
from qstress import REAL_COST, m_of, stress_suite    # noqa: E402

WARMUP = 80

# ---------------- Step2/3: 假设注册表 ----------------
# score = 预期价值(1-5) × 理论合理性(1-5) × 防过拟合(1-5)；只选总分最高且未跑过的
HYPOTHESES = {
    "H1_exposure_cap": {
        "title": "组合敞口上限 100%（消除隐含杠杆）",
        "change": "exposure_cap 1.0（原引擎理论敞口可达 ~200%）",
        "one_variable": "仅加现金约束，不动信号/出场/参数",
        "v": 4, "r": 5, "rob": 5,
        "rationale": "Part6 发现 avg_exposure>100%；去杠杆降回撤，"
                     "对信号零改动，过拟合风险最低"},
    "H2_regime_gate": {
        "title": "非上涨趋势不开新仓（只在指数>MA60 且 20日动量>+3% 时入场）",
        "change": "entry 增加市场趋势过滤（震荡/下跌趋势空仓）",
        "one_variable": "仅入场门控加一个市场级条件",
        "v": 5, "r": 4, "rob": 4,
        "rationale": "Part1 铁证：震荡市年化-47.3%/回撤-47.4%（252笔入场），"
                     "下跌市-27.6%；全部利润来自上涨市。趋势过滤是结构"
                     "性改动（非参数微调），理论依据=动量策略只在趋势市"
                     "有正期望，样本外稳健性文献支持"},
    "H3_stamp_cost_realism": {
        "title": "卖出成本计入印花税 0.05%（对齐真实 A股）",
        "change": "cost_sell 0.20%→0.25%",
        "one_variable": "仅成本口径",
        "v": 3, "r": 5, "rob": 5,
        "rationale": "Part7；不是优化而是真实性修正，必须并入基线"},
    "H4_h1h2_combo": {
        "title": "部署候选 = H2 趋势门控 + H1 敞口上限 100%",
        "change": "组合两个已单测的结构性改动",
        "one_variable": "组合实验（成分各自单变量已验证）",
        "v": 4, "r": 5, "rob": 4,
        "rationale": "EXP_0001 证明 H1 大幅改善 IS、EXP_0002 证明 H2 回撤"
                     "减半；组合检验去杠杆+趋势过滤的部署形态"},
}


def score(h):
    return h["v"] * h["r"] * h["rob"]


def load_ctx(refresh=False):
    stocks, codes, common, susp = load_panel(refresh=refresh)
    feat, state = build_all(stocks, codes, common)
    n = len(common)
    split = WARMUP + int((n - 1 - WARMUP) * 0.6)
    ctx = {"stocks": stocks, "codes": codes, "common": common,
           "susp": susp, "feat": feat, "state": state, "n": n,
           "split": split}
    return ctx


def baseline_runs(ctx):
    spec = r443_spec()
    out = {}
    for label, (s, ee, we) in (
            ("IS", (WARMUP, ctx["split"] - 6, ctx["split"] - 1)),
            ("OOS", (ctx["split"], ctx["n"] - 6, ctx["n"] - 1))):
        r = run_r443(ctx["stocks"], ctx["codes"], ctx["feat"], ctx["state"],
                     ctx["common"], ctx["susp"], s, ee, we, spec,
                     cost_buy=REAL_COST[0], cost_sell=REAL_COST[1],
                     ld_block=True)
        out[label] = {"run": r, "m": m_of(r)}
    return out


def apply_hypothesis(ctx, hyp_id):
    """Step4: 只改一个变量 → 返回 (spec, engine_kwargs)"""
    spec = r443_spec()
    kw = {}
    if hyp_id == "H1_exposure_cap":
        kw["exposure_cap"] = 1.0
    elif hyp_id == "H2_regime_gate":
        # 只在上涨趋势开仓：等权指数 > MA60*1.01 且 mom20 > +3%
        import qanalyze
        labels = qanalyze.regime_labels(ctx["stocks"], ctx["codes"],
                                        ctx["common"])
        bull = set(t for t, (tr, vo) in enumerate(labels) if tr == "上涨")
        orig_filt = spec["filt"]

        def filt(state, t, _bull=bull, _orig=orig_filt):
            return _orig(state, t) and t in _bull
        spec["filt"] = filt
    elif hyp_id == "H3_stamp_cost_realism":
        pass  # 仅成本口径，engine 层处理
    elif hyp_id == "H4_h1h2_combo":
        # 部署候选：H2 趋势门控 + H1 敞口上限（两者均已单变量测试）
        kw["exposure_cap"] = 1.0
        import qanalyze
        labels = qanalyze.regime_labels(ctx["stocks"], ctx["codes"],
                                        ctx["common"])
        bull = set(t for t, (tr, vo) in enumerate(labels) if tr == "上涨")
        orig_filt = spec["filt"]

        def filt(state, t, _bull=bull, _orig=orig_filt):
            return _orig(state, t) and t in _bull
        spec["filt"] = filt
    else:
        raise SystemExit("未知假设: %s" % hyp_id)
    return spec, kw


def run_experiment(hyp_id, note="", refresh=False):
    if hyp_id not in HYPOTHESES:
        raise SystemExit("未知假设 %s，可选: %s" % (hyp_id, HYPOTHESES.keys()))
    h = HYPOTHESES[hyp_id]
    ctx = load_ctx(refresh=refresh)
    base = baseline_runs(ctx)
    spec, kw = apply_hypothesis(ctx, hyp_id)
    if hyp_id == "H3_stamp_cost_realism":
        costs = (0.002, 0.0025)
    else:
        costs = (REAL_COST[0], REAL_COST[1])
    exp = {"hyp_id": hyp_id, "title": h["title"],
           "change": h["change"], "one_variable": h["one_variable"],
           "rationale": h["rationale"],
           "score_v_r_rob": [h["v"], h["r"], h["rob"]],
           "note": note,
           "ts": datetime.datetime.now().isoformat(),
           "baseline": {k: v["m"] for k, v in base.items()},
           "variant": {}, "stress": {}, "decision": None, "reason": None}
    for label, (s, ee, we) in (
            ("IS", (WARMUP, ctx["split"] - 6, ctx["split"] - 1)),
            ("OOS", (ctx["split"], ctx["n"] - 6, ctx["n"] - 1))):
        r = run_r443(ctx["stocks"], ctx["codes"], ctx["feat"], ctx["state"],
                     ctx["common"], ctx["susp"], s, ee, we, spec,
                     cost_buy=costs[0], cost_sell=costs[1],
                     ld_block=True, **kw)
        exp["variant"][label] = m_of(r)
    # Step7: OOS 压力
    s, ee, we = ctx["split"], ctx["n"] - 6, ctx["n"] - 1
    r2 = run_r443(ctx["stocks"], ctx["codes"], ctx["feat"], ctx["state"],
                  ctx["common"], ctx["susp"], s, ee, we, spec,
                  cost_buy=costs[0] * 2, cost_sell=costs[1] * 2,
                  ld_block=True, **kw)
    exp["stress"]["oos_cost_x2"] = m_of(r2)
    r3 = run_r443(ctx["stocks"], ctx["codes"], ctx["feat"], ctx["state"],
                  ctx["common"], ctx["susp"], s, ee, we, spec,
                  cost_buy=costs[0], cost_sell=costs[1],
                  ld_block=True, exposure_cap=kw.get("exposure_cap"), **{
                      k: v for k, v in kw.items() if k != "exposure_cap"})
    exp["stress"]["oos_cap100"] = m_of(r3)
    # Step8: 初判（最终由人确认后写回）
    exp["decision"] = "PENDING_HUMAN"
    exp["_auto_hint"] = auto_hint(exp)
    save_experiment(exp)
    print(json.dumps(exp, ensure_ascii=False, indent=1))
    return exp


def auto_hint(exp):
    b, v = exp["baseline"]["OOS"], exp["variant"]["OOS"]
    if v["n_trades"] < 20:
        return "样本过少，建议 NEEDS MORE TESTING"
    better_risk = (v["max_drawdown"] > b["max_drawdown"] * 0.8
                   and v["sharpe"] >= b["sharpe"] * 0.95)
    if better_risk and v["annual_return"] >= b["annual_return"] * 0.7:
        return "OOS 回撤改善且 Sharpe 基本不降 → 倾向 ACCEPT"
    if v["annual_return"] < b["annual_return"] * 0.5:
        return "OOS 收益腰斩 → 倾向 REJECT"
    return "NEEDS MORE TESTING"


def save_experiment(exp):
    if not os.path.isdir(EXP_DIR):
        os.makedirs(EXP_DIR)
    seq = len([f for f in os.listdir(EXP_DIR)
               if f.startswith("EXP_") and f.endswith(".json")]) + 1
    exp["seq"] = seq
    exp["id"] = "EXP_%04d" % seq
    fp = os.path.join(EXP_DIR, exp["id"] + ".json")
    with open(fp, "w") as f:
        json.dump(exp, f, ensure_ascii=False, indent=1)
    if not os.path.exists(LOG_MD):
        with open(LOG_MD, "w") as f:
            f.write("# 实验日志（第九部分 Step9：禁止删除失败实验）\n\n"
                    "| 编号 | 日期 | 假设 | 改动 | OOS年化 | OOS Sharpe | "
                    "OOS回撤 | 裁决 | 理由 |\n"
                    "|---|---|---|---|---|---|---|---|---|\n")
    v = exp["variant"]["OOS"]
    line = ("| %s | %s | %s | %s | %s | %s | %s | %s | %s |\n" % (
        exp["id"], exp["ts"][:10], exp["hyp_id"], exp["change"],
        v["annual_return"], v["sharpe"], v["max_drawdown"],
        exp["decision"], exp["_auto_hint"]))
    with open(LOG_MD, "a") as f:
        f.write(line)
    print("已记录 %s -> %s" % (exp["id"], fp))


def decide(exp_id, decision, reason):
    """Step8 人工裁决写回"""
    fp = os.path.join(EXP_DIR, exp_id + ".json")
    with open(fp) as f:
        exp = json.load(f)
    exp["decision"] = decision
    exp["reason"] = reason
    with open(fp, "w") as f:
        json.dump(exp, f, ensure_ascii=False, indent=1)
    # 同步更新 md 行
    with open(LOG_MD) as f:
        lines = f.readlines()
    for i, ln in enumerate(lines):
        if ln.startswith("| " + exp_id + " "):
            parts = ln.strip().split("|")
            parts[7] = " " + decision + " "
            parts[8] = " " + reason + " "
            lines[i] = "|".join(parts) + "\n"
    with open(LOG_MD, "w") as f:
        f.writelines(lines)
    print("%s -> %s (%s)" % (exp_id, decision, reason))


def main():
    args = sys.argv[1:]
    if not args or args[0] == "list":
        for hid, h in sorted(HYPOTHESES.items(),
                             key=lambda kv: -score(kv[1])):
            print("%-18s score=%3d  %s" % (hid, score(h), h["title"]))
            print("    %s" % h["rationale"])
    elif args[0] == "run" and len(args) >= 2:
        note = ""
        if "--note" in args:
            note = args[args.index("--note") + 1]
        run_experiment(args[1], note=note,
                       refresh="--refresh" in args)
    elif args[0] == "decide" and len(args) >= 4:
        decide(args[1], args[2], args[3])
    elif args[0] == "diagnose":
        fp = os.path.join(HERE, "results.json")
        if os.path.exists(fp):
            with open(fp) as f:
                res = json.load(f)
            print("IS :", json.dumps(res.get("IS"), ensure_ascii=False))
            print("OOS:", json.dumps(res.get("OOS"), ensure_ascii=False))
        else:
            print("无 results.json，先跑 run_all.py")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
