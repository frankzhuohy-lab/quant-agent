#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化纸面盘 · 盘中实时监控（每分钟）
- 检查持仓是否触发 Keltner 止盈或到期
- 检查是否有新信号（门控通过+有个股入选）
- 触发则记录到 paper_state.json 并推送通知
"""
import sys, os, json, subprocess
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "paper_state.json")
LAST_CHECK = os.path.join(HERE, "last_intraday_check.json")

def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg))

def is_trading_time():
    """A股交易时段：9:30-11:30, 13:00-15:00"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (570 <= hm <= 690) or (780 <= hm <= 900)

def run_paper_live():
    """跑一次 live 模式，返回报告"""
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "paper_trade.py"), "--live"],
        capture_output=True, text=True, timeout=300, cwd=HERE)
    if proc.returncode != 0:
        log("paper_trade.py 失败: %s" % proc.stderr[-500:])
        return None
    out = proc.stdout
    start = out.find("{")
    if start < 0:
        return None
    return json.loads(out[start:])

def main():
    if not is_trading_time():
        log("非交易时段，跳过")
        return

    log("盘中监控触发")

    # 读取当前状态
    state = {}
    if os.path.exists(STATE):
        with open(STATE) as f:
            state = json.load(f)

    opens = state.get("open_positions", [])
    if not opens:
        log("无持仓，检查新信号")
    else:
        log("持仓 %d 只: %s" % (len(opens), ", ".join(p["name"] for p in opens)))

    # 跑 live 引擎
    rep = run_paper_live()
    if rep is None:
        log("引擎返回空")
        return

    # 检查持仓是否有触发
    alerts = []
    if opens:
        # 用实时价格检查 Keltner 和到期
        stocks = rep.get("stocks", {})
        data_end = rep.get("data_end")
        feat = rep.get("feat", {})
        params = rep.get("spec", {}).get("params", {})
        kelt = params.get("keltner_mult", 1.5)
        max_hold = params.get("max_hold", 10)

        for p in opens:
            code = p["code"]
            e = None
            # 找 entry 对应的 bar index
            for i, d in enumerate(rep.get("common", [])):
                if d == p.get("entry_date"):
                    e = i
                    break
            if e is None:
                continue

            m20 = feat.get(code, {}).get("ma20", [None])[data_end] if data_end else None
            atr = feat.get(code, {}).get("atr20", [None])[data_end] if data_end else None
            close = stocks.get(code, {}).get("close", [None])[data_end] if data_end else None

            if m20 and atr and close:
                trig = close < m20 - kelt * atr
                maxed = (e + max_hold == data_end + 1)
                if trig:
                    alerts.append({"code": code, "name": p["name"], "reason": "Keltner止盈触发", "close": close})
                elif maxed:
                    alerts.append({"code": code, "name": p["name"], "reason": "持满%d日到期" % max_hold, "close": close})

    # 检查新信号（如果无持仓）
    new_signals = []
    if not opens:
        yesterday_pass = rep.get("yesterday_filter_pass", False)
        if yesterday_pass:
            # 获取 picks
            picks = rep.get("tomorrow", {}).get("picks", [])
            for pick in picks:
                new_signals.append(pick)

    # 输出结果
    if alerts:
        log("⚠️ 触发卖出信号:")
        for a in alerts:
            log("  %s(%s) %s @ %.2f" % (a["name"], a["code"], a["reason"], a["close"]))

    if new_signals:
        log("📈 新买入信号:")
        for s in new_signals:
            log("  %s(%s) close=%.2f" % (s.get("name"), s.get("code"), s.get("close", 0)))

    if not alerts and not new_signals:
        log("无触发")

    # 记录检查结果
    check = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "positions": len(opens),
        "alerts": alerts,
        "new_signals": new_signals,
        "data_end": rep.get("data_end"),
        "nav": rep.get("nav"),
    }
    with open(LAST_CHECK, "w") as f:
        json.dump(check, f, ensure_ascii=False, indent=2)

    # 同步到看板
    subprocess.run(["bash", os.path.join(HERE, "sync_dashboard.sh")],
                   capture_output=True, cwd=HERE)

if __name__ == "__main__":
    main()
