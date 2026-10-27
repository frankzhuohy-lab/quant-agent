#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总 E-R1/E-R3 组合实验：按宪章第 9 条约束筛选后排序。"""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    rows = []
    for fp in sorted(glob.glob(os.path.join(
            HERE, "exp", "baseline_v2", "cost_*_p*", "metrics.json"))):
        m = json.load(open(fp))
        allr, dev, deg = m.get("ALL", {}), m.get("DEV", {}), m.get("degrade", {})
        ok = (deg.get("net_ann", -99) >= 0
              and allr.get("maxdd", -1) >= -0.35
              and dev.get("net_ann", -99) > 0)
        rows.append({
            "n": m["n"], "f": m["freq"], "p": m.get("phase", 0),
            "ann": allr.get("net_ann"), "sharpe": allr.get("sharpe"),
            "dev": dev.get("net_ann"), "deg": deg.get("net_ann"),
            "mdd": allr.get("maxdd"), "fee": m.get("fee_total"),
            "ok": ok})
    rows.sort(key=lambda r: (not r["ok"], -(r["ann"] or -99)))
    print("%-4s %-3s %-3s %8s %7s %8s %8s %8s %8s %5s" % (
        "N", "F", "P", "全段年化", "Sharpe", "DEV", "降级区", "最大回撤", "费用", "过约束"))
    for r in rows:
        print("%-4d %-3d %-3d %7.1f%% %7s %7.1f%% %7.1f%% %7.1f%% %8s %5s" % (
            r["n"], r["f"], r["p"], (r["ann"] or 0) * 100,
            r["sharpe"], (r["dev"] or 0) * 100, (r["deg"] or 0) * 100,
            (r["mdd"] or 0) * 100, r["fee"], "✅" if r["ok"] else "❌"))
    with open(os.path.join(HERE, "exp", "research_r1r3_table.json"), "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
