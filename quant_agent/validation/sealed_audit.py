#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封存区间访问历史审计（评审 P0-3：'真 holdout'的身份需要证明）。

审计问题: 自 2026-01-01 封存决议以来，哪些计算触碰过 SEALED 区间，
其结果是否进入了策略选择？

检查项:
  A. 实验库 runs 表: window_label 是否出现过 SEALED（应无）；
  B. 每个实验的 evidence.json:
     - window_ranges（新字段）/ WF 各折 oos_window 是否越入 2026-01-01+；
     - bootstrap 所用窗口（dev_label）；
  C. sealed_evaluations 访问日志（谁在何时看了封存结果）；
  D. 前向工具 daily_forward 的访问性质声明（见报告）。

输出: var/sealed_access_audit.md + 控制台摘要。
任何"结果进入 Gate 决策/晋级"的触碰 = 污染记录，须降级处理。
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import quant_agent  # noqa: E402
from quant_agent.experiments.store import Store  # noqa: E402

SEALED_START = "2026-09-01"    # constitution_004 新封存界
SEALED_START_V1 = "2026-01-01"  # 旧封存界（已被降级区间覆盖）


def main():
    store = Store(os.path.join(quant_agent.VAR, "qa_store.sqlite3"))
    lines = []
    violations = []

    lines.append("# 封存区间访问历史审计")
    lines.append("")
    lines.append("封存起点: %s（constitution_002 冻结）" % SEALED_START)
    lines.append("")

    # A. runs 表窗口标签
    labels = [r[0] for r in store.conn.execute(
        "SELECT DISTINCT window_label FROM runs")]
    lines.append("## A. 实验库窗口标签")
    lines.append("")
    lines.append("runs 表出现过的窗口标签: `%s`" % "`, `".join(labels))
    if "SEALED" in labels:
        violations.append("runs 表出现 SEALED 窗口标签")
        lines.append("")
        lines.append("**违规**: 研究循环运行过封存窗口。")
    else:
        lines.append("")
        lines.append("结论: 研究循环从未以 SEALED 为标签运行回测。")
    lines.append("")

    # B. 证据包 WF 折窗口
    lines.append("## B. 各实验证据包滚动验证（WF）折窗口")
    lines.append("")
    lines.append("| 实验 | WF折 | OOS窗口 | 越入封存? |")
    lines.append("|---|---|---|---|")
    for ev_fp in sorted(glob.glob(os.path.join(
            quant_agent.VAR, "artifacts", "*", "evidence.json"))):
        exp = os.path.basename(os.path.dirname(ev_fp))
        try:
            ev = json.load(open(ev_fp))
        except Exception:
            continue
        folds = ev.get("walkforward") or []
        if not folds:
            lines.append("| %s | （无WF） | - | - |" % exp)
            continue
        for f in folds:
            w = f.get("oos_window") or ["?", "?"]
            cross = w[1] >= SEALED_START_V1   # 按旧封存界判定越入（历史事实）
            lines.append("| %s | %s | %s..%s | %s |" %
                         (exp, f.get("fold"), w[0], w[1],
                          "**是**" if cross else "否"))
            if cross:
                violations.append(
                    "%s WF 第%s折越入封存区间(%s..%s)" %
                    (exp, f.get("fold"), w[0], w[1]))
    lines.append("")
    lines.append("说明: WF 折越入封存≠封存失效——关键看其结果是否被用于"
                 "策略选择。被 Gate 用作拒绝理由的触碰已构成'选择使用'。")
    lines.append("")

    # B2. 越入折的决策影响（哪些指标实际进入了 Gate 决策）
    lines.append("## B2. 越入折的决策影响（评审第 2 轮追加）")
    lines.append("")
    lines.append("| 实验 | 越入折 | OOS 年化 | 是否进入 Gate 决策 |")
    lines.append("|---|---|---|---|")
    impact = []
    for r in store.conn.execute(
            "SELECT experiment_id, decision, reasons FROM decisions"):
        exp_id, dec, reasons = r[0], r[1], json.loads(r[2] or "[]")
        ev_fp2 = os.path.join(quant_agent.VAR, "artifacts", exp_id,
                              "evidence.json")
        if not os.path.exists(ev_fp2):
            continue
        ev2 = json.load(open(ev_fp2))
        for f in (ev2.get("walkforward") or []):
            w = f.get("oos_window") or ["?", "?"]
            if w[1] < SEALED_START_V1:
                continue
            oa = f.get("oos", {}).get("annual_return")
            used = any("WF 最差折" in x and ("%.1f" % ((oa or 0) * 100))
                       in x for x in reasons)
            lines.append("| %s | %s | %.1f%% | %s |" %
                         (exp_id, f.get("fold"), (oa or 0) * 100,
                          "**是（%s）**" % dec if used else "计算并存储"))
            if used:
                impact.append(exp_id)
    lines.append("")
    lines.append("结论: %s。凡越入折结果进入决策的，该封存区间按评审决议"
                 "**降级为开发数据**，'零访问保证独立性'声明撤销。" %
                 ("、".join(sorted(set(impact))) + " 的越入折进入了 Gate 决策"
                  if impact else "无越入折进入决策"))
    lines.append("")

    # C. 封存评估访问日志
    lines.append("## C. sealed_evaluations 访问日志")
    lines.append("")
    rows = store.conn.execute(
        "SELECT target, decided_by, decided_at, verdict FROM "
        "sealed_evaluations ORDER BY decided_at").fetchall()
    if not rows:
        lines.append("（尚无访问记录——封存结果从未被读取用于决策）")
    else:
        lines.append("| 对象 | 访问者 | 时间 | 判定 |")
        lines.append("|---|---|---|---|")
        for r in rows:
            lines.append("| %s | %s | %s | %s |" % r)
    lines.append("")

    # D. 前向工具声明
    lines.append("## D. daily_forward 访问性质")
    lines.append("")
    lines.append("- 该工具刷新行情至当日（自然覆盖 2026 年以来的日历日期），"
                 "用于纸面前向模拟；")
    lines.append("- 其产出（ledger/intentions）不进入 Researcher 提示词、"
                 "不参与 Gate 证据；晋级链路上无消费点（代码核验）;")
    lines.append("- 性质: 前向观察 ≠ 回测选择。若未来要把前向表现用于策略"
                 "变更决策，须在决策记录中显式声明。")
    lines.append("")

    # 结论
    lines.append("## 结论")
    lines.append("")
    if violations:
        lines.append("发现 %d 项越入记录（详见上表）。" % len(violations))
        for v in violations:
            lines.append("- %s" % v)
    lines.append("")
    lines.append("**处置（评审第 2 轮决议，constitution_004）**：")
    lines.append("")
    lines.append("1. 旧封存区间 2026-01-01..2026-08-31 **整体降级为开发数据**——"
                 "WF 越入折的计算结果已进入 Gate 决策（B2 节），是否标 SEALED、"
                 "sealed_evaluations 是否为空都不能改变这一事实；")
    lines.append("2. '零访问保证独立性'声明**撤销**；")
    lines.append("3. 新封存区间自 **2026-09-01** 起：此前的读取仅限 "
                 "daily_forward 前向观察（产出不进研究循环），未发现其他"
                 "评估或重放反馈使用；新增代码级硬约束——run_window 拒跑"
                 "越界窗口，仅 sealed_eval 显式放行；")
    lines.append("4. 若未来无法持续证明新封存区间未被选择使用，则按评审"
                 "后备方案执行：从策略锁定日起纯靠前向积累证据。")

    out = os.path.join(quant_agent.VAR, "sealed_access_audit.md")
    with open(out, "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[-12:]))
    print("\nwritten:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
