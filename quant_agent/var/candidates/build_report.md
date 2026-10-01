# 候选信号数据集构建报告（constitution_008）

标签口径：最早可成交日开盘价入场，qengine.plan_exit 同一出场实现（Keltner+固定期限+停牌/跌停顺延），扣费 label = exit*(1-卖费)/entry*(1+买费)-1。不可执行（停牌/涨停锁定/价格缺失）留空，不按理想价成交。

特征取 t=信号日（下单前信息）；相对行业强弱因数据面无行业分类标记阻塞，未伪造。

## IS

- window: IS
- n_candidates: 2027
- n_intent: 351
- n_filled: 282
- n_skipped: 69
- n_not_executable: {}
- n_labelled: 2027
- label_check_n: 282
- label_check_bad: 0

## DEV

- window: DEV
- n_candidates: 1833
- n_intent: 192
- n_filled: 128
- n_skipped: 64
- n_not_executable: {}
- n_labelled: 1833
- label_check_n: 128
- label_check_bad: 0
