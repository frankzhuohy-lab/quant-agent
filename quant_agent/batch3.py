#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次三登记（评审后 P0）：可执行现金口径基线。

  1. 现金账户口径下连跑两遍 IS+DEV，验证确定性（复现验收）；
  2. baseline_003 登记并接任 champion；
  3. baseline_001/002 标注 REFERENCE_NON_EXECUTABLE
     （权重口径、隐含融资，不可执行——保留作历史参考，不进晋级比较）。

EXP_0003 在可执行口径下的重跑走标准循环：
  python3 -m quant_agent.run_research --proposal \
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


def main():
    const = json.load(open(quant_agent.CONFIG))
    assert const["version"] == "constitution_003", \
        "本脚本要求 constitution_003（现金账户口径）"
    store = Store(os.path.join(quant_agent.VAR, "qa_store.sqlite3"))
    snap_id = "31d677be6e555817"  # 批次二快照（数据未变，宪法版本演进）

    ctx = Context(constitution=const)          # PIT 宇宙 + v3 窗口
    runner = BacktestRunner(ctx, const)

    canonical = seed.materialize(
        seed.normalize({}, const), ctx.stocks, ctx.codes, ctx.common,
        universe=ctx.universe)[2]

    # ---- 复现验收：现金口径连跑两遍，逐字节比较 ----
    a = runner.run(canonical, os.path.join(quant_agent.VAR, "artifacts",
                                           "batch3_verify_a"))
    b = runner.run(canonical, os.path.join(quant_agent.VAR, "artifacts",
                                           "batch3_verify_b"))
    for label in ctx.dev_labels():
        if json.dumps(a[label]["metrics"], sort_keys=True) != \
           json.dumps(b[label]["metrics"], sort_keys=True):
            print("FAIL: %s 两遍不一致" % label)
            return 1
    print("复现验收通过（现金口径两遍一致）")

    # ---- 登记 baseline_003 并接任 champion ----
    seed.register_baseline(store, ctx, const, snap_id,
                           version="baseline_003", promote=True)
    for v in ("baseline_001", "baseline_002"):
        store.conn.execute(
            "UPDATE strategies SET status=? WHERE version=?",
            ("REFERENCE_NON_EXECUTABLE", v))
    store.conn.commit()

    ch = store.conn.execute(
        "SELECT version FROM champion_history ORDER BY promoted_at DESC "
        "LIMIT 1").fetchone()
    print("champion:", ch[0])

    for label in ctx.dev_labels():
        m = a[label]["metrics"]
        print("baseline_003 %s: ann=%.3f sharpe=%.3f mdd=%.3f trades=%d "
              "avg_exp=%.3f" %
              (label, m["annual_return"], m["sharpe"], m["max_drawdown"],
               m["n_trades"], m.get("avg_exposure", float("nan"))))
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
