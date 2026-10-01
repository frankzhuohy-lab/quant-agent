# 第一轮实验报告（constitution_007）

## 比较表（基线 vs 实验A vs 实验B）

| 变体 | 窗口 | 净累计 | 年化 | Sharpe | 回撤 | 交易数 | 日度5%分位 | 换手 |
|---|---|---|---|---|---|---|---|---|
| baseline | IS | -5.4% | -3.2% | 0.01 | -19.7% | 282 | -2.53% | 0.21 |
| baseline | DEV | 55.0% | 140.2% | 3.16 | -9.6% | 128 | -2.26% | 0.29 |
| A_keltner_off | IS | -5.4% | -3.2% | 0.01 | -21.1% | 276 | -2.54% | 0.19 |
| A_keltner_off | DEV | 56.5% | 144.8% | 3.13 | -10.4% | 128 | -2.30% | 0.28 |
| B_block_reentry | IS | 3.6% | 2.1% | 0.21 | -12.2% | 159 | -1.40% | 0.21 |
| B_block_reentry | DEV | 20.7% | 45.6% | 1.92 | -6.9% | 82 | -1.83% | 0.24 |

## 实验A: Keltner 退出

- 完整组合重跑: 见 comparison.csv A_keltner_off 行（退出改变后的资金释放与后续交易由现金执行层联动重算）。
- 退出反事实（诊断）: 相同入场/相同数量，仅替换退出日。Σ基线盈亏 507790 元 vs Σ反事实 499053 元，差 -8737 元；明细 A_counterfactual.csv。该数字不视为可执行组合收益。

## Gate（预注册规则，DEV 窗口）: A_keltner_off → REJECT
- Sharpe 3.13 < 基线 3.16

## Gate（预注册规则，DEV 窗口）: B_block_reentry → REJECT
- DEV 净累计 20.68% 未优于基线 54.99%
- Sharpe 1.92 < 基线 3.16
- 滚动窗口正收益占比 0% < 2/3

## 实验B: 重复入场（688110.SH 重叠见 B_overlap_688110.csv）

预算台账: /Volumes/项目空间/projects/quant-paper/quant_agent/var/budget_ledger.jsonl（本批追加 3 条：A 变体、A 反事实诊断、B 变体）