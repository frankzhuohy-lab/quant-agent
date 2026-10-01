#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quant_agent —— A股量化研究自动化系统（按《A股量化Agent Python开发方案》v1.0 落地）。

核心原则（对应方案文档）:
  - 收益数字必须来自实际运行结果；Agent 不编造回测。
  - 交易规则、指标计算、Gate 始终为确定性组件；LLM 无否决/晋级豁免权。
  - 失败实验与完整版本全部留存。
  - 研究晋级 != 上线；晋级仅表示进入后续验证。

模块索引:
  experiments/store.py   SQLite 实验库（九表 + data_snapshots）
  data/snapshot.py       数据快照清单与内容哈希
  strategies/adapter.py  StrategyAdapter：声明式规格 + 白名单函数
  backtest/runner.py     BacktestRunner：固定执行逻辑
  validation/evaluate.py 滚动验证 / 压力 / 区块 bootstrap
  audit/structural.py    确定性结构审计（Bias Auditor 的代码化替身）
  gate/rules.py          规则 Gate（代码判定）
  orchestrator/machine.py 状态机 / loop.py 研究循环
  agents/                Strategy Researcher / Quant Critic（LLM，仅建议权）
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)            # quant-paper/
QSYS = os.path.join(ROOT, "qsys")
VAR = os.path.join(HERE, "var")          # 运行期产物（db/artifacts），不进版本库
CONFIG = os.path.join(HERE, "config", "constitution.json")

__version__ = "0.1.0"
