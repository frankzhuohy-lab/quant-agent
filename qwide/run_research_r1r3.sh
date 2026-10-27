#!/bin/bash
# E-R1 相位 + E-R3 网格（执行器含 E-R2 跌停卖出约束）
set -e
cd "$(dirname "$0")"
PY=/usr/bin/python3
for P in 0 1 2 3 4; do
  echo "=== E-R1 n20 f5 phase=$P ==="
  $PY exp_portfolio_cost.py --tag baseline_v2 --n 20 --freq 5 --phase $P > /dev/null
done
for N in 10 20 30 50; do
  for F in 3 5 10; do
    if [ "$N" = "20" ] && [ "$F" = "5" ]; then continue; fi  # 与相位 0 重复
    echo "=== E-R3 n=$N f=$F ==="
    $PY exp_portfolio_cost.py --tag baseline_v2 --n $N --freq $F --phase 0 > /dev/null
  done
done
echo "=== 汇总 ==="
$PY aggregate_research.py
echo RESEARCH_DONE
