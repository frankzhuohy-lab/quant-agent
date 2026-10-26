#!/bin/bash
# qwide Phase 3 流水线（预注册见 EXPERIMENTS.md）
# 前置：fetch_universe.py 已完成（含 688 补漏）
set -e
cd "$(dirname "$0")"
PY=/usr/bin/python3
echo "=== 1/6 build_panel ==="
$PY build_panel.py
echo "=== 2/6 build_features（含 Volman 因子） ==="
$PY build_features.py
echo "=== 3/6 baseline_v2 walk-forward ==="
$PY exp_walkforward.py --tag baseline_v2
echo "=== 4/6 Volman 独立诊断 ==="
$PY exp_volman_diag.py
echo "=== 5/6 成本回测：baseline_v2 四变体 ==="
for N in 20 50; do
  for F in 1 5; do
    echo "--- cost n=$N freq=$F ---"
    $PY exp_portfolio_cost.py --tag baseline_v2 --n $N --freq $F
  done
done
echo "=== 6/6 完成 ==="
echo PIPELINE_DONE
