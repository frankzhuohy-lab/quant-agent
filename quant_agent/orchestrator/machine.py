#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性状态机（方案文档第 4 章）。

PROPOSED → SPEC_VALIDATED → AUDITED → BACKTESTED → VALIDATED → CRITIQUED → DECIDED
                    ↓             ↓
                 REJECTED      REJECTED(审计阻塞)
任意非终态遇异常 → ERROR（每个转换保存输入哈希/状态事件/产物路径，见 store）。
"""
from __future__ import print_function

FLOW = {
    "PROPOSED": "SPEC_VALIDATED",
    "SPEC_VALIDATED": "AUDITED",
    "AUDITED": "BACKTESTED",
    "BACKTESTED": "VALIDATED",
    "VALIDATED": "CRITIQUED",
    "CRITIQUED": "DECIDED",
}


class StateMachine(object):
    def __init__(self, store):
        self.store = store

    def advance(self, exp_id, next_state, **kw):
        return self.store.transition(exp_id, next_state, **kw)

    def next_of(self, state):
        return FLOW.get(state)
