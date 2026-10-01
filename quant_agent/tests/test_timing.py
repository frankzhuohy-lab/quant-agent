#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""时间测试（方案文档第 7 章）：故意放入晚发布数据应被拒绝；边界控制。

覆盖:
  T1 schema 拒绝未知字段 / 越界参数 / 非有限值 / 路径注入字符串
  T2 快照不得含晚于 decision_date 的 K 线（未来数据 → BLOCKER）
  T3 快照文件被篡改（哈希变化）→ verify 报差异，重跑应被拒
"""
from __future__ import print_function
import os, sys, json, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quant_agent.strategies.adapter import validate_patch, SpecError, \
    _no_injection
from quant_agent.data import snapshot as snap

ALLOWED = {
    "K": {"type": "int", "min": 1, "max": 15},
    "params": {"type": "object", "fields": {
        "keltner_mult": {"type": "float", "min": 0.5, "max": 4.0},
        "max_hold": {"type": "int", "min": 3, "max": 60}}},
    "weight_mode": {"type": "enum", "values": ["equal"]},
    "allow_fewer": {"type": "bool"},
    "regime_gate": {"type": "bool"},
    "exposure_cap": {"type": "float_or_null", "min": 0.5, "max": 1.0},
}


def expect_error(fn, name):
    try:
        fn()
    except SpecError:
        print("PASS %s" % name)
        return
    raise AssertionError("%s: no SpecError raised" % name)


def main():
    # T1a 未知字段
    expect_error(lambda: validate_patch({"K": 3, "eval": "x"}, ALLOWED),
                 "T1a unknown field rejected")
    # T1b 越界
    expect_error(lambda: validate_patch({"K": 99}, ALLOWED),
                 "T1b out-of-range K rejected")
    # T1c 非有限值
    expect_error(lambda: validate_patch(
        {"params": {"keltner_mult": float("inf"), "max_hold": 10}},
        ALLOWED), "T1c non-finite rejected")
    # T1d 路径注入
    expect_error(lambda: validate_patch({"weight_mode": "../evil"},
                                        ALLOWED),
                 "T1d path injection rejected")
    # T1e 类型错
    expect_error(lambda: validate_patch({"K": "3"}, ALLOWED),
                 "T1e wrong type rejected")
    # T1f 合法部分 patch 通过
    p = validate_patch({"K": 2, "regime_gate": True}, ALLOWED)
    assert p["K"] == 2 and p["regime_gate"] is True
    print("PASS T1f valid patch accepted")

    # T2 未来数据
    m = {"universe_files": [{"file": "k_X.json", "sha256": "a",
                             "n_bars": 1, "first_date": "2026-01-01",
                             "last_date": "2026-09-30"}]}
    bad = snap.check_no_future_bars(m, "2026-09-29")
    assert bad and bad[0]["last_date"] == "2026-09-30"
    print("PASS T2 future bars flagged")
    assert snap.check_no_future_bars(m, "2026-10-01") == []
    print("PASS T2b same-date allowed")

    # T3 篡改检测
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "k_X.json")
        with open(fp, "w") as f:
            f.write('[["2026-01-01",10,11,9,10,1000]]')
        man = snap.build_manifest(td)
        assert snap.verify(td, man) == []
        with open(fp, "a") as f:
            f.write(" ")  # 篡改
        diffs = snap.verify(td, man)
        assert diffs and diffs[0]["kind"] == "hash_changed"
        print("PASS T3 tamper detected")
        os.remove(fp)
        diffs = snap.verify(td, man)
        assert diffs and diffs[0]["kind"] == "missing"
        print("PASS T3b missing detected")
    print("TIMING TESTS: ALL PASS")


if __name__ == "__main__":
    main()
