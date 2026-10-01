#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S9 封存硬约束测试（评审第 3 轮 P0-3）。

验收:
  S9-1 研究循环路径（无 _allow_sealed）对触及封存界的窗口必须抛错；
  S9-2 显式放行的执行路径（sealed_eval 语义）不被守卫拦截——
       用注入放行键的 constitution 副本验证不再抛错（不实际跑回测，
       用最小 monkeypatch 截断后续计算）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import quant_agent  # noqa: E402
from quant_agent.backtest.runner import Context, run_window  # noqa: E402


def main():
    const = json.load(open(quant_agent.CONFIG))
    ctx = Context(constitution=const, use_universe=False)

    sealed = ctx.windows.get("SEALED")
    if not sealed:
        print("SKIP: 当前数据无封存区间")
        return 0
    s0, s1, s2 = sealed

    # S9-1: 无放行键 → 必须抛错
    er = dict(const["execution_rules"])
    er.pop("_allow_sealed", None)
    try:
        run_window(ctx, {"K": 1, "score": None, "filt": None}, {},
                   s0, s1, s2, er)
        print("FAIL: S9-1 封存窗口未被拒跑")
        return 1
    except RuntimeError as e:
        assert "sealed" in str(e).lower() or "封存" in str(e), str(e)
        print("S9-1 拒跑通过: %s" % str(e)[:70])

    # S9-2: 显式放行 → 守卫放行（截断后续计算，只验证不抛错）
    er2 = dict(const["execution_rules"])
    er2["_allow_sealed"] = True
    import qengine
    orig = qengine.run_r443
    qengine.run_r443 = lambda *a, **k: {
        "trades": [], "daily": [], "exposure": {},
        "avg_exposure": 0.0, "turnover": 0.0, "total_cost": 0.0}
    try:
        out = run_window(ctx, {"K": 1, "score": None, "filt": None}, {},
                         s0, s1, s2, er2)
        assert out["sealed_eval_authorized"] is True
        print("S9-2 放行路径通过（sealed_eval 语义，计算已截断）")
    finally:
        qengine.run_r443 = orig
    print("S9 封存硬约束测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
