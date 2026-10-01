#!/bin/bash
# 自选股做T系统定时入口（launchd 调用 · 内部磁盘运行时）
# 2026-09-14: 外置卷脚本在 launchd 上下文被 TCC 拒绝读取（Operation not permitted / exit 78），
# 故运行时部署于内部磁盘 ~/.maket/；外置卷仅保留研究/回测/文档。
# 用法: run_maket.sh {plan|monitor|close}
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH
[ -f "$HOME/.mx_env" ] && . "$HOME/.mx_env"
MAKET="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$MAKET/logs"
cd "$MAKET" || exit 1

case "$1" in
  plan)    python3 pool_sync.py && python3 maket.py plan && bash sync_ecs.sh ;;
  monitor) python3 maket.py monitor && bash sync_ecs.sh ;;
  close)   python3 maket.py close && { python3 flow_data.py snapshot; if python3 -c "import trading_day,sys; sys.exit(0 if trading_day.is_trading_day() else 1)"; then python3 screen_candidates.py; python3 publish_gate.py ledger || echo "publish_gate: 未阻断收盘"; else echo "休市日: 跳过筛选/发布门禁(保护池文件)"; fi; } && bash sync_ecs.sh ;;
  *) echo "用法: run_maket.sh {plan|monitor|close}"; exit 2 ;;
esac
