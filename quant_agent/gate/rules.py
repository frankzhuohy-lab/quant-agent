#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规则 Gate：全部由代码判定（方案文档第 1/5 章：LLM 无否决豁免权）。

判定顺序:
  1) 证据完整性 → 缺失/NaN = NEEDS_MORE_TESTING
  2) 可信性硬门槛（未来数据/不可复现/规则绕过）→ REJECT
  3) 比较一致性（同窗/同池/同成本/同快照）→ 违反即 REJECT
  4) 开发验证（净收益与风险调整改善；风险约束不能被收益豁免）
  5) 稳定性与压力（WF 最差折、成本×2、敞口上限）
输出 PROMOTE / REJECT / NEEDS_MORE_TESTING + 理由列表。
"""
from __future__ import print_function
import json, math


def _finite_metrics(m):
    if m is None:
        return False
    for k in ("sharpe", "annual_return", "max_drawdown", "n_trades"):
        v = m.get(k)
        if v is None:
            return False
        if isinstance(v, float) and not math.isfinite(v):
            return False
    return True


class Gate(object):
    def __init__(self, constitution, audit_findings=None):
        self.const = constitution
        self.g = constitution["gates"]
        self.version = constitution["version"]
        self.audit_findings = audit_findings or []

    def evaluate(self, experiment_id, evidence):
        """evidence: validate() 的证据包 + 审计发现。返回决策 dict。"""
        reasons = []

        # 1) 证据完整性
        for label in self.g["evidence_completeness"]["required_windows"]:
            w = evidence["windows"].get(label)
            if not w or not _finite_metrics(w["candidate"]):
                return self._decide(experiment_id, "NEEDS_MORE_TESTING",
                                    ["证据不完整: %s 窗口指标缺失/NaN" % label])
        # 2) 可信性硬门槛
        blockers = [f for f in self.audit_findings
                    if f.get("severity") == "BLOCKER"]
        if blockers:
            for f in blockers:
                reasons.append("审计阻塞: %s (%s)" %
                               (f.get("finding"), f.get("locator")))
            return self._decide(experiment_id, "REJECT", reasons)
        if not evidence.get("consistent", True):
            return self._decide(experiment_id, "REJECT",
                                ["比较不一致: 基线与候选未使用同一"
                                 "日期/股票池/资金/执行配置"])
        # 3) 开发验证（promotion 门槛，使用最后一个开发窗口 DEV）
        p = self.g["promotion"]
        dev = evidence.get("dev_label") or "DEV"
        if dev not in evidence["windows"]:
            return self._decide(experiment_id, "NEEDS_MORE_TESTING",
                                ["缺开发验证窗口 %s 的证据" % dev])
        c = evidence["windows"][dev]["candidate"]
        b = evidence["windows"][dev]["baseline"]
        ok = True
        if p.get("oos_sharpe_not_below_baseline") and \
                c["sharpe"] < b["sharpe"]:
            reasons.append("DEV Sharpe %.2f < 基线 %.2f" %
                           (c["sharpe"], b["sharpe"]))
            ok = False
        dd_tol = p.get("oos_maxdd_not_worse_than_baseline_pp", 0.0)
        if c["max_drawdown"] < b["max_drawdown"] - dd_tol / 100.0:
            reasons.append("DEV 回撤 %.1f%% 劣于基线 %.1f%% 超过容差 %.1fpp" %
                           (c["max_drawdown"] * 100, b["max_drawdown"] * 100,
                            dd_tol))
            ok = False
        frac = p.get("oos_annual_not_below_baseline_frac")
        if frac is not None and c["annual_return"] < \
                b["annual_return"] * frac:
            reasons.append("DEV 年化 %.1f%% 低于基线 %.1f%% 的 %.0f%%" %
                           (c["annual_return"] * 100, b["annual_return"] * 100,
                            frac * 100))
            ok = False
        # 5) 稳定性与压力
        s = None
        for k in sorted(evidence.get("stress", {})):
            s = evidence["stress"][k]
        sb = None
        for k in sorted(evidence.get("stress_baseline", {})):
            sb = evidence["stress_baseline"][k]
        if s and s.get("sharpe") is not None:
            if s["sharpe"] < p.get("stress_cost_x2_oos_sharpe_min", 0.0):
                reasons.append("成本×2 压力下 DEV Sharpe %.2f < %.2f" %
                               (s["sharpe"],
                                p["stress_cost_x2_oos_sharpe_min"]))
                ok = False
        else:
            reasons.append("缺成本×2压力证据")
            ok = False
        wf = evidence.get("wf_worst_fold_oos_annual")
        if wf is not None and wf < p.get("walkforward_no_fold_annual_worse_than", -1.0):
            reasons.append("WF 最差折年化 %.1f%% 低于阈值 %.0f%%" %
                           (wf * 100,
                            p["walkforward_no_fold_annual_worse_than"] * 100))
            ok = False
        if c.get("avg_exposure", 0.0) > p.get("exposure_cap_max", 1.0) + 1e-6:
            reasons.append("平均敞口 %.1f%% 超上限" %
                           (c["avg_exposure"] * 100))
            ok = False
        if ok:
            reasons.append("全部门槛通过: DEV Sharpe %.2f(基线%.2f), "
                           "回撤 %.1f%%(基线%.1f%%), 成本×2 Sharpe %.2f"
                           "(基线同压 %.2f), WF最差折 %.1f%%" %
                           (c["sharpe"], b["sharpe"],
                            c["max_drawdown"] * 100, b["max_drawdown"] * 100,
                            s["sharpe"] if s else float("nan"),
                            (sb["sharpe"] if sb else float("nan")),
                            (wf or 0) * 100))
            dec = self._decide(experiment_id, "PROMOTE", reasons)
        else:
            # 语义澄清（评审第 2 轮）: 走到这里 = 证据完整、无阻塞、
            # 但违反预设门槛 → REJECT（想法被证据否决）。
            # NEEDS_MORE_TESTING 仅用于证据缺失/流程缺席（Critic 不可用等）。
            dec = self._decide(experiment_id, "REJECT", reasons)
        # Critic 必需性检查（评审决议，只影响 PROMOTE）
        d2, r2 = self._critic_gate(experiment_id, dec["decision"],
                                   dec["reasons"], evidence)
        dec["decision"] = d2
        dec["reasons"] = r2
        return dec

    def _critic_gate(self, experiment_id, decision, reasons, evidence):
        """评审决议（2026-09-30）: Critic 为必需晋级环节时不可用 →
        不得 PROMOTE（NEEDS_MORE_TESTING），不可用≠豁免。"""
        rp = self.const.get("research_policy", {})
        if not rp.get("critic_required", False):
            return decision, reasons
        if decision == "PROMOTE" and \
                evidence.get("critic_status") != "pass":
            return ("NEEDS_MORE_TESTING",
                    reasons + ["Critic 不可用/未通过: 必需晋级环节缺席"
                               "（critic_status=%s）" %
                               evidence.get("critic_status")])
        return decision, reasons

    def _decide(self, exp_id, decision, reasons):
        return {"experiment_id": exp_id, "gate_version": self.version,
                "decision": decision, "reasons": reasons,
                "reviewer": "code"}
