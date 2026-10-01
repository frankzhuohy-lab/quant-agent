#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strategy Researcher 包装：诊断 → 提案 JSON（严格校验，有限重试）。"""
from __future__ import print_function
import os, json

from quant_agent.agents import llm

PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "prompts", "strategy_researcher.txt")

_REQUIRED = ("hypothesis", "change_scope", "allowed_patch",
             "expected_effect", "failure_conditions", "required_tests")
_ALLOWED_TOP = set(_REQUIRED) | {"experiment_id", "parent_version",
                                 "evidence_path", "needed_data"}


def validate_proposal(p):
    """严格 schema：拒绝额外字段/空假设/非法 change_scope。"""
    if not isinstance(p, dict):
        raise ValueError("proposal not object")
    extra = set(p) - _ALLOWED_TOP
    if extra:
        raise ValueError("extra fields: %s" % sorted(extra))
    for k in _REQUIRED:
        if k not in p or p[k] in (None, "", [], {}):
            raise ValueError("missing/empty field: %s" % k)
    if p["change_scope"] not in ("K", "params", "regime_gate",
                                 "exposure_cap"):
        raise ValueError("bad change_scope: %r" % p["change_scope"])
    if not isinstance(p["allowed_patch"], dict):
        raise ValueError("allowed_patch must be object")
    return p


def propose(diagnosis_summary, experiment_history, max_retries=2):
    """LLM 提案；失败抛异常（调用方决定记 ERROR 还是改用人工提案）。"""
    with open(PROMPT_PATH) as f:
        system = f.read()
    user = json.dumps({
        "diagnosis": diagnosis_summary,
        "experiment_history": experiment_history,
        "instruction": "基于诊断与历史，提出一个最有价值且可证伪的最小修改。"
    }, ensure_ascii=False)
    last = None
    for _ in range(max_retries + 1):
        try:
            p = llm.chat_json([{"role": "system", "content": system},
                               {"role": "user", "content": user}])
            return validate_proposal(p)
        except (ValueError, RuntimeError) as e:
            last = e
    raise RuntimeError("propose failed: %s" % last)
