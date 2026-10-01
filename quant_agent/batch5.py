#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次五（评审第 3 轮 P0-2/5）：新快照上重建证据链。

  1. 旧快照（硬链接时代）全部标注 integrity_uncertain——当前内容已逐一
     校验一致，但旧哈希不再承担可复现证明；
  2. 组合快照 v5（qsys/cache + ucache + 宇宙规则/注册制/宪法）登记；
  3. 同一新快照上现金口径连跑两遍（复现验收）→ baseline_004 接任
     champion；baseline_003 标记 SUPERSEDED（费用模型升级，非数字错误）；
  4. 修正事件入库（原实验 → 修正解释 → 影响，可追溯）；
  5. 输出 var/batch5_rebuild.md 新旧对照。

EXP_0003 重跑走标准循环（新 Gate 语义预期 REJECT）:
  python3 -m quant_agent.run_research --proposal \\
      quant_agent/var/proposals/exp_batch3_exp0003.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import quant_agent  # noqa: E402
from quant_agent import seed  # noqa: E402
from quant_agent.backtest.runner import Context, BacktestRunner  # noqa: E402
from quant_agent.experiments.store import Store  # noqa: E402
from quant_agent.data import snapshot as snap  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(quant_agent.__file__)))


def main():
    const = json.load(open(quant_agent.CONFIG))
    assert const["version"] == "constitution_005", \
        "本脚本要求 constitution_005（费用模型 + 封存降级）"
    store = Store(os.path.join(quant_agent.VAR, "qa_store.sqlite3"))

    # ---- 1. 旧快照标注 ----
    note = ("integrity_uncertain: 硬链接冻结时代登记的清单；2026-09-30 深夜逐一"
            "verify 与当前内容一致（diffs=0），但旧哈希不再承担不可变证明；"
            "比较结论以批次五 v5 组合快照重建为准（评审第 3 轮）。")
    old_ids = [r[0] for r in store.conn.execute(
        "SELECT snapshot_id FROM data_snapshots").fetchall()]
    for sid in old_ids:
        store.add_snapshot_note(sid, note)
    print("旧快照标注 integrity_uncertain: %d 个" % len(old_ids))

    # ---- 2. 组合快照 v5 ----
    extra = [
        os.path.join(os.path.dirname(quant_agent.CONFIG), "universe_rule.json"),
        os.path.join(quant_agent.VAR, "universe_registry.json"),
        quant_agent.CONFIG,
    ]
    for fn in sorted(os.listdir(os.path.join(quant_agent.VAR, "universe"))):
        if fn.endswith(".npz"):
            extra.append(os.path.join(quant_agent.VAR, "universe", fn))
    m5 = snap.build_combined_manifest(
        [os.path.join(ROOT, "qsys", "cache"),
         os.path.join(quant_agent.VAR, "ucache")],
        extra_files=extra, source="tencent_qfq_day+universe_panel+v005")
    store.register_snapshot(m5["snapshot_id"], m5)
    print("组合快照 v5: %s files=%d" % (m5["snapshot_id"],
                                        len(m5["universe_files"])))

    # ---- 3. 新快照上重建基线 ----
    ctx = Context(constitution=const)
    runner = BacktestRunner(ctx, const)
    canonical = seed.materialize(
        seed.normalize({}, const), ctx.stocks, ctx.codes, ctx.common,
        universe=ctx.universe)[2]
    a = runner.run(canonical, os.path.join(quant_agent.VAR, "artifacts",
                                           "batch5_a"))
    b = runner.run(canonical, os.path.join(quant_agent.VAR, "artifacts",
                                           "batch5_b"))
    for label in ctx.dev_labels():
        assert json.dumps(a[label]["metrics"], sort_keys=True) == \
            json.dumps(b[label]["metrics"], sort_keys=True), \
            "%s 两遍不一致" % label
    print("复现验收通过（v5 快照两遍一致）")

    seed.register_baseline(store, ctx, const, m5["snapshot_id"],
                           version="baseline_004", promote=True)
    store.conn.execute(
        "UPDATE strategies SET status=? WHERE version=?",
        ("SUPERSEDED_FEE_MODEL", "baseline_003"))
    store.conn.commit()
    ch = store.conn.execute(
        "SELECT version FROM champion_history ORDER BY promoted_at DESC "
        "LIMIT 1").fetchone()
    print("champion:", ch[0])

    # ---- 4. 修正事件 ----
    store.add_correction(
        "exp_20260930_210829", "NEEDS_MORE_TESTING",
        "判定理由含封存污染指标（WF 第 9 折 2026-05-11..08-03 是最差折依据）；"
        "按 constitution_005 语义应为 REJECT（证据完整且违反预设门槛）",
        "封存审计 B2 发现 WF 折越入 2026 区间且结果进入 Gate；"
        "REJECT/NEEDS 语义第 3 轮澄清",
        "候选队列已移除（EXP_0002 方向证伪）；结论方向不变，"
        "champion 指针不受影响")
    store.add_correction(
        "exp_20260930_224330", "NEEDS_MORE_TESTING",
        "REJECT（DEV Sharpe 1.61 < 基线 3.16、bootstrap P(≤0)=0.996，"
        "证据完整且违反门槛）",
        "Gate 语义澄清（第 3 轮）：证据完整被否 = REJECT",
        "champion 保持基线（baseline_003 → 今 baseline_004 接任）")
    print("修正事件入库: 2 条")

    # ---- 5. 新旧对照 ----
    lines = ["# 批次五：新快照重建对照（评审第 3 轮）", "",
             "组合快照 v5: `%s`（%d 文件，含宇宙规则/注册制/宪法）"
             % (m5["snapshot_id"], len(m5["universe_files"])), ""]
    lines.append("## baseline_003（旧，SUPERSEDED）vs baseline_004（v5）")
    lines.append("")
    lines.append("| 窗口 | 指标 | baseline_003 | baseline_004 |")
    lines.append("|---|---|---|---|")
    old = store.conn.execute(
        "SELECT metrics FROM runs WHERE experiment_id='baseline_003'").fetchall()
    for label in ctx.dev_labels():
        mo = a[label]["metrics"]
        lines.append("| %s | 年化 | — | %.2f%% |" % (label, mo["annual_return"] * 100))
        lines.append("| %s | Sharpe | — | %.3f |" % (label, mo["sharpe"]))
        lines.append("| %s | 回撤 | — | %.2f%% |" % (label, mo["max_drawdown"] * 100))
        lines.append("| %s | 交易数 | — | %d |" % (label, mo["n_trades"]))
    lines.append("")
    lines.append("说明：baseline_003 的运行产物以旧口径落盘（runs 表按"
                 "experiment_id 关联），其数字在批次三文档中已有记录；"
                 "此处不篡改历史，仅声明其快照证据力降级并以 v5 重建为准。")
    for label in ctx.dev_labels():
        m = a[label]["metrics"]
        print("baseline_004 %s: ann=%.3f sharpe=%.3f mdd=%.3f trades=%d" %
              (label, m["annual_return"], m["sharpe"], m["max_drawdown"],
               m["n_trades"]))
    out = os.path.join(quant_agent.VAR, "batch5_rebuild.md")
    open(out, "w").write("\n".join(lines))
    print("OK ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
