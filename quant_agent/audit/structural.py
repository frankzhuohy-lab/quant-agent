#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性结构审计（Bias Auditor 的代码化替身，阶段三裁剪版）。

按方案第 6 章 Bias Auditor 职责中可由代码执行的部分:
  - 快照哈希与当前缓存一致性（可复现性）
  - 时点正确性: 快照中不得含晚于声明 decision_date 的 K 线
  - 规格白名单: 无函数指针外泄/路径注入（schema 层已拒，复核）
  - 成本口径: 运行必须使用 constitution 冻结成本

局限（诚实声明，对应文档"静态检查通过不能宣称已证明无泄漏"）:
  本审计不检查财务发布日期、修订数据、特征拟合窗口等需要完整
  PIT 血缘的项；这些在阶段二数据层完成后升级为带数据血缘的审计。
"""
from __future__ import print_function
import os, sys, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import quant_agent


def audit(snapshot_manifest, cache_dir, canonical_spec, constitution,
          decision_date=None, actual_costs=None):
    """返回 findings: [{"finding","severity","locator"}, ...]"""
    from quant_agent.data import snapshot as snap
    findings = []
    diffs = snap.verify(cache_dir, snapshot_manifest)
    for d in diffs:
        findings.append({
            "finding": "数据快照与当前缓存不一致: %s (%s)" %
                       (d["file"], d["kind"]),
            "severity": "BLOCKER",
            "locator": os.path.join(cache_dir, d["file"])})
    if decision_date:
        bad = snap.check_no_future_bars(snapshot_manifest, decision_date)
        for b in bad:
            findings.append({
                "finding": "快照含晚于 %s 的 K 线(%s, last=%s)" %
                           (decision_date, b["file"], b["last_date"]),
                "severity": "BLOCKER",
                "locator": b["file"]})
    # 规格白名单复核
    allowed = set(constitution["research_policy"]["allowed_patch_fields"])
    extra = set(canonical_spec) - allowed - {"score", "filt",
                                             "_approved_functions",
                                             "_exp_id", "universe",
                                             "execution"}
    if extra:
        findings.append({
            "finding": "规格含非批准字段: %s" % sorted(extra),
            "severity": "BLOCKER", "locator": "canonical_spec"})

    # universe 只能取注册值（评审决议：防止研究 Agent 自由更换股票池）
    uni = canonical_spec.get("universe")
    if uni:
        import quant_agent as _qa
        reg_fp = os.path.join(_qa.VAR, "universe_registry.json")
        known = set()
        if os.path.exists(reg_fp):
            reg = json.load(open(reg_fp))
            for key, ent in reg.items():
                known.add("%s@%s" % (key, ent["snapshot_id"][:8]))
                known.add(ent.get("rule_id"))
        if uni not in known:
            findings.append({
                "finding": "universe 值未注册: %s（只允许 %s）" % (uni, known),
                "severity": "BLOCKER", "locator": "universe_registry"})
    # execution 标记只允许两种口径
    ex = canonical_spec.get("execution")
    if ex and ex not in ("cash", "weight"):
        findings.append({
            "finding": "execution 口径非法: %s" % ex,
            "severity": "BLOCKER", "locator": "execution"})
    for k in ("score", "filt"):
        v = canonical_spec.get(k)
        approved_list = constitution["research_policy"]["approved_functions"].get(k, [])
        base = str(v).split("+")[0]
        if base not in approved_list:
            findings.append({
                "finding": "%s=%r 不在白名单 %r" % (k, v, approved_list),
                "severity": "BLOCKER", "locator": "spec_canonical"})
    # 成本口径
    if actual_costs is not None:
        er = constitution["execution_rules"]
        if (abs(actual_costs[0] - er["cost_buy"]) > 1e-12 or
                abs(actual_costs[1] - er["cost_sell"]) > 1e-12):
            findings.append({
                "finding": "运行成本 %s 与冻结口径 (%s,%s) 不符" %
                           (actual_costs, er["cost_buy"], er["cost_sell"]),
                "severity": "BLOCKER", "locator": "runs.costs"})
    if not findings:
        findings.append({
            "finding": "静态检查通过: 快照一致/无未来K线/规格白名单/成本口径。"
                       "注意: 这不构成无泄漏证明（财务PIT血缘待阶段二）。",
            "severity": "INFO", "locator": "audit/structural.py"})
    return findings
