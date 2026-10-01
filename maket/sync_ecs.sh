#!/bin/bash
# maket 数据+看板 → 阿里云ECS（公网看板数据源）; 静默失败不影响本地引擎
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH
KEY="$HOME/.ssh/Hongyi.pem"
HOST="root@106.14.174.163"
WEB="/Volumes/项目空间/projects/quant-paper/web"
# web 前端与服务
rsync -az -e "ssh -i $KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no" \
  "$WEB/index.html" "$WEB/server.js" "$HOST:/opt/paper/" || exit 0
# 运行时数据(小文件, rsync跳过未变更)
rsync -az -e "ssh -i $KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no" \
  "$HOME/.maket/account.json" "$HOME/.maket/ledger_t.json" "$HOME/.maket/predictions.json" \
  "$HOME/.maket/whitelist_dynamic.json" "$HOME/.maket/predictions_log.md" \
  "$HOME/.maket"/zones_*.json \
  "$HOST:/opt/paper/maket_data/" || exit 0
rsync -az -e "ssh -i $KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no" \
  "$HOME/.maket/reports/" "$HOST:/opt/paper/maket_data/reports/" || exit 0
