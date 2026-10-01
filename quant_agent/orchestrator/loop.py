#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""研究循环（Orchestrator，方案文档第 4 章伪代码的落地）。

每轮: 取任务(人工JSON/LLM提案) → 建实验 → schema校验 → 结构审计 →
     回测(IS+OOS) → 验证(压力/WF) → Critic 反证(仅建议) → Gate 判定 → 落库。

纪律:
  - 不读封存测试集；不执行实盘订单。
  - 预算: max_total_experiments / stop_after_no_progress。
  - 所有异常 → ERROR 状态落库，绝不伪造结果；中断恢复后不重复登记。
"""
from __future__ import print_function
import os, sys, json, hashlib, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import quant_agent
from quant_agent.experiments.store import Store, TERMINAL
from quant_agent.orchestrator.machine import StateMachine
from quant_agent.strategies.adapter import normalize, materialize, \
    spec_hash, SpecError
from quant_agent.backtest.runner import Context, BacktestRunner
from quant_agent.validation.evaluate import validate
from quant_agent.audit.structural import audit
from quant_agent.gate.rules import Gate


def load_constitution():
    with open(quant_agent.CONFIG) as f:
        return json.load(f)


def config_hash(constitution):
    blob = json.dumps(constitution, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _aid(ctx):
    ts = __import__("datetime").datetime.now()
    return "exp_%s" % ts.strftime("%Y%m%d_%H%M%S")


class Orchestrator(object):
    def __init__(self, store_path=None, ctx=None):
        self.const = load_constitution()
        self.store = Store(store_path or
                           os.path.join(quant_agent.VAR, "qa_store.sqlite3"))
        self.sm = StateMachine(self.store)
        self.ctx = ctx or Context()
        self.runner = BacktestRunner(self.ctx, self.const)
        self.cache_dir = os.path.join(quant_agent.QSYS, "cache")
        self.snapshot_manifest = None   # 由 seed/调用方注入并登记

    # ---------- 中断恢复（方案: 恢复测试）----------
    def recover(self):
        """启动时扫描非终态实验 → 标记 ERROR(interrupted)。

        已登记的 runs/artifacts 用 INSERT OR IGNORE 幂等，重跑不会重复。
        """
        fixed = []
        for exp_id, state in self.store.non_terminal_experiments():
            self.store.transition(
                exp_id, "ERROR",
                error="interrupted: non-terminal state %s at restart" % state)
            fixed.append((exp_id, state))
        return fixed

    # ---------- 预算 ----------
    def budget_exhausted(self):
        rp = self.const["research_policy"]
        if self.store.count_experiments() >= rp["max_total_experiments"]:
            return "max_total_experiments(%d) reached" % \
                   rp["max_total_experiments"]
        if self.store.experiments_since_promotion() >= \
                rp["stop_after_no_progress"]:
            return "stop_after_no_progress(%d)" % \
                   rp["stop_after_no_progress"]
        return None

    # ---------- 单实验全流程 ----------
    def run_experiment(self, proposal, snapshot_id=None, champion=None):
        """proposal: 文档第 3 章结构化协议 dict。"""
        rp = self.const["research_policy"]
        for field in ("hypothesis", "change_scope", "allowed_patch"):
            if field not in proposal:
                raise ValueError("proposal missing %s" % field)
        exp_id = _aid(self.ctx)
        snap_id = snapshot_id or (self.snapshot_manifest or {}).get(
            "snapshot_id", "unknown")
        ch = champion or self.store.current_champion() or \
            {"version": "baseline_001"}
        trial = self.store.trial_index_next()
        self.store.create_experiment(
            exp_id, proposal["hypothesis"], ch["version"], snap_id,
            config_hash(self.const), spec_json=None, seed=0,
            trial_index=trial)
        artifact_dir = os.path.join(quant_agent.VAR, "artifacts", exp_id)
        try:
            # SPEC_VALIDATED: 严格 schema
            try:
                norm = normalize(proposal["allowed_patch"], self.const)
            except SpecError as e:
                self.store.transition(exp_id, "REJECTED",
                                      error="schema: %s" % e)
                return {"experiment_id": exp_id, "decision": "REJECTED",
                        "reasons": ["schema: %s" % e]}
            self.store.set_spec(exp_id, norm)
            self.sm.advance(exp_id, "SPEC_VALIDATED")

            # AUDITED: 确定性结构审计
            manifest = self.store.get_snapshot(snap_id) or \
                self.snapshot_manifest
            canonical = materialize(norm, self.ctx.stocks, self.ctx.codes,
                                    self.ctx.common)[2]
            canonical["_exp_id"] = exp_id
            findings = audit(manifest, self.cache_dir, canonical,
                             self.const,
                             decision_date=self.ctx.common[-1])
            for f in findings:
                self.store.add_audit(exp_id, "structural", f,
                                     f["severity"], f.get("locator"))
            self.sm.advance(exp_id, "AUDITED")
            if any(f["severity"] == "BLOCKER" for f in findings):
                self.store.transition(exp_id, "REJECTED",
                                      error="audit blocker")
                return {"experiment_id": exp_id, "decision": "REJECTED",
                        "reasons": [f["finding"] for f in findings
                                    if f["severity"] == "BLOCKER"]}

            # BACKTESTED: 候选 + 基线（同快照/同窗口/同成本）
            cand = self.runner.run(canonical, artifact_dir)
            base_canonical = normalize({}, self.const)
            base = self.runner.run(base_canonical,
                                   os.path.join(artifact_dir, "_baseline"))
            costs = {"buy": self.const["execution_rules"]["cost_buy"],
                     "sell": self.const["execution_rules"]["cost_sell"]}
            for label in self.ctx.dev_labels():
                if label not in cand:
                    continue
                self.store.add_run(
                    "%s_%s" % (exp_id, label), exp_id, label,
                    *self.ctx.window_dates(label), costs,
                    cand[label]["metrics"], cand["_artifact_hashes"].get(
                        "metrics_%s" % label))
            self.sm.advance(exp_id, "BACKTESTED")

            # VALIDATED: 验证套件 + bootstrap
            ev = validate(cand, base, self.ctx, canonical, self.const,
                          artifact_dir, baseline_spec=base_canonical)
            bd = _bootstrap_pair(self.runner, canonical, base_canonical,
                                 self.ctx, self.const, artifact_dir)
            ev["bootstrap_oos"] = bd
            ev["consistent"] = True   # 同 Context/同成本由本函数结构保证
            with open(os.path.join(artifact_dir, "evidence.json"), "w") as f:
                json.dump(ev, f, ensure_ascii=False, indent=1,
                          default=str)
            self.sm.advance(exp_id, "VALIDATED")

            # CRITIQUED: LLM 反证（建议权；不可用≠豁免——评审决议:
            # critic 为必需晋级环节时不可用 → NEEDS_MORE_TESTING）
            critique = None
            critic_status = "unavailable"
            try:
                from quant_agent.agents.critic import critique_evidence
                critique = critique_evidence(ev, self.const)
                critic_status = "pass" if critique.get(
                    "verdict") != "reject" else "fail"
                self.store.add_audit(exp_id, "quant_critic", critique,
                                     "INFO", "evidence.json")
            except Exception as e:
                self.store.add_audit(
                    exp_id, "quant_critic",
                    {"finding": "critic unavailable: %s" % e},
                    "INFO", None)
            ev["critic_status"] = critic_status
            self.sm.advance(exp_id, "CRITIQUED")

            # DECIDED: 规则 Gate（代码判定）
            gate = Gate(self.const,
                        audit_findings=[f for f in findings
                                        if f["severity"] == "BLOCKER"])
            decision = gate.evaluate(exp_id, ev)
            self.store.add_decision(exp_id, decision["gate_version"],
                                    decision["decision"],
                                    decision["reasons"])
            self.sm.advance(exp_id, "DECIDED")
            return decision
        except Exception as e:
            err = "%s\n%s" % (e, traceback.format_exc())
            try:
                cur = self.store.get_experiment(exp_id)["state"]
                if cur not in TERMINAL:
                    self.store.transition(exp_id, "ERROR", error=err[:2000])
            except Exception:
                pass
            return {"experiment_id": exp_id, "decision": "ERROR",
                    "reasons": [str(e)]}

    def close(self):
        self.store.close()


def _bootstrap_pair(runner, canonical, base_canonical, ctx, const,
                    artifact_dir):
    from quant_agent.validation.evaluate import block_bootstrap_diff, \
        _daily_from_equity
    dev = ctx.dev_labels()[-1]
    d = os.path.join(artifact_dir, "_btmp")
    c = runner.run(canonical, d + "_c", windows=(dev,))
    b = runner.run(base_canonical, d + "_b", windows=(dev,))
    dc = _daily_from_equity(json.load(open(os.path.join(
        d + "_c", "equity_%s.json" % dev))))
    db = _daily_from_equity(json.load(open(os.path.join(
        d + "_b", "equity_%s.json" % dev))))
    return block_bootstrap_diff(dc, db)
