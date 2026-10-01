# 核验包索引（评审第 4 轮要求的可核查证据）

| 文件 | 内容 |
|---|---|
| snapshot_v5_manifest.json | 组合快照 v5 全量清单（216 文件 sha256） |
| code_config_versions.md | 关键代码与配置版本哈希 |
| artifact_hashes.md | 基线两遍/消融四配置/EXP_0003 v6 的全部产物文件哈希 |
| baseline005_equity_IS/DEV.json | 冠军基线净值曲线 |
| baseline005_trades_IS/DEV.json | 冠军基线成交与持仓批次 |
| baseline005_metrics_IS/DEV.json | 指标 |
| exp0003_v6_equity/trades_IS/DEV.json | EXP_0003（v6）净值/订单/成交 |
| batch6_ablation.md | 2×2 消融（费用×涨停复核独立开关） |
| corrections_export.json | 三条修正事件（污染证据与有效证据分列） |
| sealed_access_policy.md | 封存入口审计 + 三层结论表 |
| sealed_access_audit.md | WF 越入逐折审计（含 B2 决策影响） |
| sealed_access_log.jsonl | 前向运行封存访问日志 |
| revision_log.jsonl | 行情修订巡检记录 |

测试代码在 quant_agent/tests/（S7/S8/S8d/S9 等），运行方式见代码文档。
