#!/bin/bash
# 每10分钟tick（launchd 调度）: 仅工作日交易时段调用 monitor
DOW=$(date +%u)
[ "$DOW" -ge 6 ] && exit 0
HM=$(date +%H%M)
M="/Volumes/项目空间/projects/quant-paper/maket"
if { [ "$HM" -ge 0930 ] && [ "$HM" -le 1130 ]; } || { [ "$HM" -ge 1300 ] && [ "$HM" -le 1500 ]; }; then
  exec "$M/run_maket.sh" monitor
fi
exit 0
