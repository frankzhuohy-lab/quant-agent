#!/bin/bash
# 量化纸面验证 每日运行包装（供 Hermes cron 调用）
cd "/Volumes/项目空间/projects/quant-paper" || exit 1
exec python3 daily_run.py 2>>/tmp/quant_paper_err.log
