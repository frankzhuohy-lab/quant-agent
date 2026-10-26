# qwide 实验预注册台账

> 宪章第 3 条：改动、预期、判定标准在跑之前固定。跑完按此判定，不改口径。
> 评估窗口：IS ≤2025-06-30；DEV 2025-07-01~2025-12-31；降级区 2026-01-01~2026-08-31；
> 封存段 2026-09-01 起不评估。所有实验只到降级区为止。

## E-Phase3 多头组合成本回测（2026-10-26 预注册）

- 信号：ridge fwd1 得分（同基线超参，λ=10、504 日训练窗，不重训调参）
- 组合：每日得分 top20 / top50，等权，本金 100 万
- 频率：每日调仓 / 每 5 交易日调仓
- 成本：买 0.20‰、卖 0.25‰（含印花税），每笔最低 5 元；T+1 才可卖；
  涨停不可买（limit_up 标记）、停牌不可买卖
- 判定（以 DEV 段为准，IS 作对照）：
  - PASS：净年化 > 8% 且净 Sharpe > 0.5
  - FAIL：净年化 < 0（信号死于成本）
  - NEEDS_MORE_TESTING：其余（0~8% 或 Sharpe 不足）
- 禁区：不剔 ST、不挪评估区间、不为结果加因子

## E-Data-688 科创板补漏（2026-10-26）

- 改动：SSE 查询增加 STOCK_TYPE=8（科创板 688/689），代码表重建
- 影响： universe 变化后基线指标会小幅变化；所有对照在同一 universe 下进行
  （baseline_v2 = 同超参在新 universe 重跑），不与旧 4606 只基线直接比数字

## E-V1 假突破（Volman false break）（2026-10-26 预注册）

- 定义：prev_high20 = max(high[t-20..t-1])；当日 high > prev_high20 且 close < prev_high20
  → false_break20 = 1（其余 0，数据不足 20 日为 NaN）
- 假设：事件后 fwd5 收益显著低于全市场同期均值（反转）
- 判定：事件数 ≥500，事件 fwd5 均值 − 全市场 fwd5 均值 ≤ −0.5pp 且 t 值 ≥2 → PASS，
  可作为负向因子；否则 FAIL，写入证伪记录
- 只检验一次，不改窗口（20）不改阈值

## E-V2 压力积累（Volman pressure buildup）（2026-10-26 预注册）

- 定义：near_high60 = close/max(high[t-59..t]) − 1（≤0，越近 0 越贴近前高）；
  volr = std(ret,5)/std(ret,60)；pressure = (1+near_high60)/(volr+0.2)
  （值大 = 贴近前高且短期波动收缩）
- 假设：pressure 与 fwd5 正相关；对基线有增量
- 判定：单因子 RankIC(fwd5) DEV 均值 > 0.01；且加入基线后 DEV fwd5 RankIC
  提升 ≥0.003 且 IS 下降不超过 0.003 → 采纳进扩展模型；否则证伪

## E-V3 高低点结构（Volman HH/LL structure）（2026-10-26 预注册）

- 定义：hh = max(high,10)/max(high,10).shift(10) − 1；
  ll = min(low,10)/min(low,10).shift(10) − 1；struct = hh+ll（>0 = 高点低点同步上移）
- 假设与判定：同 E-V2（单因子 RankIC>0.01，增量 ≥0.003，IS 降幅 ≤0.003）

## E-V4 扩展模型（2026-10-26 预注册）

- 仅纳入 V1~V3 中通过判定的因子（V1 以负向 dummy 的 z 分进入）
- 对照：同 universe、同 walk-forward、同超参的 baseline_v2
- 判定：DEV fwd5 RankIC 提升 ≥0.003 且 IR 不下降 → PASS 采用为 champion；
  否则保留 baseline_v2，记录扩展失败
- 成本回测通过与否独立于信号判定：信号 PASS 但成本 FAIL 时结论为
  "信号有效、暂不可交易"
