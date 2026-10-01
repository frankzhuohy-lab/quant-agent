# quant_agent —— A股量化研究自动化系统

按《A股量化Agent Python开发方案》v1.0 落地的第一版（阶段一~三裁剪版）。
**研究晋级 ≠ 上线；本系统不执行实盘订单。**

## 已实现组件（对照方案文档章节）

| 方案组件 | 实现 | 说明 |
|---|---|---|
| Experiment Store（§5 九表） | `experiments/store.py` | SQLite WAL + 单写入队列；`data_snapshots` 为第十表；失败实验永不删除 |
| 数据快照与内容哈希（§2/§4） | `data/snapshot.py` | 快照 = 文件清单 + sha256 + 日期范围；重跑前 `verify()` 不一致即拒绝 |
| StrategyAdapter（§3） | `strategies/adapter.py` | 纯 JSON 声明式规格 + 白名单函数注册表；拒绝额外字段/非有限值/越界/路径注入 |
| BacktestRunner（§3） | `backtest/runner.py` | 固定执行逻辑（qengine）；产物落盘带 sha256；指标全部来自实际运行 |
| 滚动验证/压力/bootstrap（§4/§5） | `validation/evaluate.py` | WF 冻结参数只验时间稳定性；成本×2 压力；moving-block bootstrap 差异检验 |
| Bias Auditor（§6，阶段三替身） | `audit/structural.py` | 确定性结构审计：快照一致性/未来K线/规格白名单/成本口径。**局限：财务 PIT 血缘待阶段二，静态通过 ≠ 无泄漏证明** |
| Rule Gate（§1/§5） | `gate/rules.py` | 代码判定，LLM 无豁免权；PROMOTE/REJECT/NEEDS_MORE_TESTING |
| Orchestrator 状态机（§4） | `orchestrator/machine.py` `loop.py` | PROPOSED→…→DECIDED；每转换落 state_events；异常 → ERROR；中断恢复不重复登记 |
| Strategy Researcher / Quant Critic（§6） | `agents/` | 仅建议权；JSON 严格校验、有限重试；LLM 不可用记 INFO/ERROR，**不伪造结果** |

## 快速开始

```bash
cd /Volumes/项目空间/projects/quant-paper

# 阶段一验收：登记快照/基线/历史 + 基线连跑两遍比对
python3 -m quant_agent.seed --verify-twice

# 关键测试（时间/执行/恢复）
python3 -m quant_agent.tests.test_timing
python3 -m quant_agent.tests.test_execution
python3 -m quant_agent.tests.test_recovery

# 阶段二（batch_002，PIT 宇宙 + 封存 + 前向）
python3 -m quant_agent.data.universe                # 候选超集→K线→宇宙日历
python3 -m quant_agent.batch2                       # 快照v2+基线登记+幸存者偏差量化
python3 -m quant_agent.validation.sealed_eval --list    # 封存评估（独立流程）
python3 -m quant_agent.forward.daily_forward        # 每日前向模拟（收盘后）

# 跑一轮研究（人工提案）
python3 -m quant_agent.run_research --proposal <file>.json
# LLM 提案（默认本地 xiaoji，可用 QUANT_AGENT_LLM_BASE/MODEL 覆盖）
python3 -m quant_agent.run_research --llm
# 中断恢复
python3 -m quant_agent.run_research --recover
```

## 提案协议（Agent 可改字段）

```json
{
  "hypothesis": "...", "change_scope": "regime_gate",
  "allowed_patch": {"regime_gate": true},
  "expected_effect": "...", "failure_conditions": ["..."],
  "required_tests": ["rolling_validation", "cost_stress"]
}
```

| 字段 | 范围 | 基线 |
|---|---|---|
| K | 1..15 int | 3 |
| params.keltner_mult | 0.5..4.0 | 1.5 |
| params.max_hold | 3..60 日 | 10 |
| regime_gate | bool | false |
| exposure_cap | null 或 0.5..1.0 | null |
| weight_mode | 仅 "equal"；allow_fewer: bool | — |

执行规则（成本 买0.20%/卖0.25%、T+1、涨跌停/停牌处理）由 `config/constitution.json`
冻结，**Agent 不可修改**；修改须新研究批次，不回写旧结果。

## 当前状态与已知局限

- **基线**: baseline_001（R443 冻结口径，定型22池，batch_001）；baseline_002
  （同参数 + PIT 宇宙，batch_002，REFERENCE）。快照 v1 `30ea7331`（22池）、
  v2 `31d677be`（189 候选 + 宇宙日历 `2e301098`）。注意：OOS 段数据与
  R443 报告时刻相比已漂移（前复权修订），报告绝对数字勿再引用。
- **batch_002（2026-09-30 冻结）**: PIT 宇宙上线（`data/universe.py` +
  `config/universe_rule.json`）；窗口改日期边界 IS(≤2025-06-30)/
  DEV(2025-07-01..2025-12-31)/SEALED(2026-01-01+，仅 sealed_eval.py 可访问)。
- **幸存者偏差量化**（var/batch2_survivorship.md，同规格同窗口仅宇宙不同）:
  IS -25.75%(PIT) vs -23.38%(定型池)；DEV 回撤 -21.63% vs -6.99%。
  **PIT 口径是唯一可作未来预期参考的口径。**
- **batch_001 结论稳健性**: EXP_0002（趋势门控）在 PIT 宇宙复现
  （exp_20260930_210829）未通过门槛：DEV Sharpe 1.01 vs 基线 2.75，
  bootstrap P(diff≤0)=0.994 → **批次一 ACCEPT 结论在诚实宇宙下不成立**，
  偏差量化与结论修正正是本系统存在的目的。
- **宇宙规则局限**（config/universe_rule.json 注记）: 候选超集=当前板块
  成分+关键词退市股，残余幸存者偏差不可宣称零；ST 过滤用当前名称近似；
  腾讯对部分证券仅返回约640根 bar，缺数据期按保守规则排除。
- **前向模拟**: `forward/daily_forward.py`（默认 EXP_0003 部署形态），
  台账 var/forward/ledger.jsonl append-only，意图单 var/forward/intentions/。
- **未建**: Alpha Researcher / 完整 LLM Bias Auditor、Market Regime /
  Portfolio / Risk Agent 扩展、PostgreSQL 迁移。

## 目录

```
quant_agent/
  config/constitution.json    # 冻结门槛/执行规则/预算
  data/snapshot.py            # 快照与哈希
  strategies/adapter.py       # 规格 schema + 白名单物化
  backtest/runner.py          # 回测执行与产物
  validation/evaluate.py      # WF/压力/bootstrap
  audit/structural.py         # 确定性结构审计
  gate/rules.py               # 规则 Gate
  orchestrator/               # 状态机 + 研究循环
  agents/                     # LLM 提案/反证（仅建议权）
  experiments/store.py        # SQLite 实验库
  tests/                      # 时间/执行/恢复测试
  seed.py / run_research.py   # 入口
  var/                        # 运行产物（db/artifacts/proposals）
```
