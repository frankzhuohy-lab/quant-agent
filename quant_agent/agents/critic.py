#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quant Critic 包装：证据包 → 反证报告 JSON（仅建议权）。"""
from __future__ import print_function
import os, json

from quant_agent.agents import llm

PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "prompts", "quant_critic.txt")

_TOP = ("confirmed_issues", "conjectures", "stress_suggestions",
        "residual_risks", "small_sample_warning", "overall_comment")


def critique_evidence(evidence, constitution, max_retries=1):
    """返回 dict；LLM 不可用时抛异常（循环记录 INFO，不阻塞 Gate）。"""
    with open(PROMPT_PATH) as f:
        system = f.read()
    # 证据包瘦身: 净值曲线等大数据不入 prompt
    slim = {k: v for k, v in evidence.items() if k != "_baseline_equity_path"}
    user = json.dumps({"evidence": slim,
                       "budget_note": "研究批次 %s" %
                       constitution["version"]},
                      ensure_ascii=False, default=str)
    p = llm.chat_json([{"role": "system", "content": system},
                       {"role": "user", "content": user}],
                      max_retries=max_retries)
    if not isinstance(p, dict):
        raise ValueError("critic output not object")
    return {k: p.get(k) for k in _TOP}
