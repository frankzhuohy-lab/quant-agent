#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""石药创新 三年日K 区间估算（分钟线历史仅125天, 3年5分钟数据任何免费源都没有）
UB只正T规则在日K上只能给出上下界:
  入场条件: low <= open*(1-d) (当日触及低吸线), 成交价按线价
  乐观界: 当日 high >= open*(1+d) 即认为到达目标 (+2d量级)   [忽略盘中先后顺序, 偏乐观]
  悲观界: 只有 close >= open*(1+d) 才算到达目标(冲高回落不算)  [偏保守]
  兜底日(未触及低吸线): 14:30进场→收盘平, 毛利≈0, 只亏成本
结论取两界之间的区间, 与125天分钟级精确回测互相印证。
"""
import sys, os, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maket as M

SYM = "sz300765"
COST = 0.0012


def main():
    bars = M.fetch_daily(SYM, 800)
    # 取最近三年
    bars = [b for b in bars if b[0] >= "2023-09-13"]
    print("三年日K: %d 个交易日 (%s ~ %s)" % (len(bars), bars[0][0], bars[-1][0]))
    print("\nUB只正T网格 · 三年区间估算 (成本%.2f%%)" % (COST * 100))
    print("%7s %8s %10s %10s %12s %12s" %
          ("网格d", "有交易", "乐观合计", "悲观合计", "乐观日为正", "悲观日为正"))
    for d in (0.005, 0.006, 0.008, 0.010, 0.012):
        opt_tot = pess_tot = 0.0
        opt_pos = pess_pos = n_traded = 0
        for b in bars:
            o, c, h, l = float(b[1]), float(b[2]), float(b[3]), float(b[4])
            lo, hi = o * (1 - d), o * (1 + d)
            if l > lo:                      # 未触及低吸线 → 兜底日
                g = -COST * 100
                opt_tot += g; pess_tot += g
                continue
            n_traded += 1
            tgt_gross = (hi / lo - 1) * 100
            opt = tgt_gross - COST * 100    # 乐观: 冲到高线即达标
            pess_gross = (c / lo - 1) * 100 if c >= hi else \
                (tgt_gross if False else (hi / lo - 1) * 100)
            # 悲观: 收盘仍在高线上方才算达标, 否则冲高回落按收盘出
            pess = ((c / lo - 1) * 100) - COST * 100
            opt_tot += opt; pess_tot += pess
            opt_pos += opt > 0; pess_pos += pess > 0
        print("%6.1f%% %7d天 %9.1f%% %9.1f%% %11.0f%% %11.0f%%"
              % (d * 100, n_traded, opt_tot, pess_tot,
                 100.0 * opt_pos / len(bars), 100.0 * pess_pos / len(bars)))
    print("\n注: 真实值介于乐观/悲观之间; 精确值见125天分钟级回测(bt_3y_result.json流程)")


if __name__ == "__main__":
    main()
