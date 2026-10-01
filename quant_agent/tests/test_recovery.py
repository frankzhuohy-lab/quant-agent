#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""恢复测试（方案文档第 7 章）：进程中断后继续，不重复登记/扣费/晋级。

R1 中断恢复: 非终态实验 → ERROR，终态不受影响
R2 runs 幂等: 同 run_id 重复登记只有一行（INSERT OR IGNORE）
R3 事务: 决策与 champion 指针同事务；终态不可再迁移
R4 失败实验留存: ERROR/REJECT 记录可查询，不被清理
"""
from __future__ import print_function
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quant_agent.experiments.store import Store  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as td:
        st = Store(os.path.join(td, "t.sqlite3"))
        # R1
        st.create_experiment("exp_A", "h", "baseline_001", "snap1", "cfg1")
        for s in ("SPEC_VALIDATED", "AUDITED", "BACKTESTED"):
            st.transition("exp_A", s)
        fixed = [(eid, s) for eid, s in st.non_terminal_experiments()]
        assert ("exp_A", "BACKTESTED") in fixed
        for eid, s in fixed:
            st.transition(eid, "ERROR", error="interrupted")
        assert st.non_terminal_experiments() == []
        assert st.get_experiment("exp_A")["state"] == "ERROR"
        print("PASS R1 recovery marks interrupted ERROR")
        # 重复 recover 不影响终态
        assert st.non_terminal_experiments() == []
        print("PASS R1b recover idempotent")
        # R2
        st.add_run("exp_A_OOS", "exp_A", "OOS", "2026-01-01", "2026-06-30",
                   {"buy": 0.002, "sell": 0.0025},
                   {"sharpe": 1.0, "annual_return": 0.1}, "h1")
        st.add_run("exp_A_OOS", "exp_A", "OOS", "2026-01-01", "2026-06-30",
                   {"buy": 0.002, "sell": 0.0025},
                   {"sharpe": 9.9, "annual_return": 9.9}, "h1")
        rows = st.get_runs("exp_A")
        assert len(rows) == 1 and rows[0]["metrics"]["sharpe"] == 1.0, rows
        print("PASS R2 runs idempotent (no double-booking)")
        # R3
        st.create_experiment("exp_B", "h2", "baseline_001", "snap1", "cfg1")
        for s in ("SPEC_VALIDATED", "AUDITED", "BACKTESTED", "VALIDATED",
                  "CRITIQUED"):
            st.transition("exp_B", s)
        st.add_decision("exp_B", "gate_v", "PROMOTE", ["ok"])
        st.transition("exp_B", "DECIDED")
        champ = st.current_champion()
        assert champ and champ["version"] == "baseline_001", champ
        try:
            st.transition("exp_B", "ERROR", error="x")
            raise AssertionError("terminal transition allowed!")
        except RuntimeError:
            pass
        print("PASS R3 transaction + terminal immutability")
        # R4
        n_err = st.conn.execute(
            "SELECT COUNT(*) FROM experiments WHERE state IN "
            "('ERROR','REJECTED','DECIDED')").fetchone()[0]
        assert n_err == 2, n_err
        ev = st.conn.execute(
            "SELECT COUNT(*) FROM state_events").fetchone()[0]
        assert ev >= 12, ev
        print("PASS R4 failed experiments retained with full events")
        st.close()
    print("RECOVERY TESTS: ALL PASS")


if __name__ == "__main__":
    main()
