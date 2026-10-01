#!/bin/bash
# 纸面盘可视化数据同步：导出 + 推送到 ECS（失败静默记日志，不影响日报）
export HOME=/Users/francishy
cd "/Volumes/项目空间/projects/quant-paper" || exit 1
/usr/bin/python3 export_dashboard.py >> /tmp/quant_paper_sync.log 2>&1 || exit 0
rsync -az -e "ssh -i $HOME/.ssh/Hongyi.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes" \
  dashboard_data.json web/index.html root@106.14.174.163:/opt/paper/ >> /tmp/quant_paper_sync.log 2>&1 \
  && echo "[$(date '+%F %T')] sync OK" >> /tmp/quant_paper_sync.log
