#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纸面验证运行器：三种模式
  open    —— 开盘执行报告（9:31跑）：按开盘价成交今日买卖单
  close   —— 收盘结算报告（15:40跑）：全天结算 + 明日计划
  intraday—— 盘中快照（手动触发）：实时持仓浮盈
输出报告到 stdout（供定时任务推送）；休市/无新数据时静默。
"""
from __future__ import print_function
import sys, os, json, subprocess
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LAST = os.path.join(HERE, "last_data_end.txt")
EVENTS = os.path.join(HERE, "daily_events.json")


def append_event(date, kind, text, nav=None):
    """把当天状态写进每日动态（同日期+同类覆盖），供看板时间线展示。"""
    try:
        with open(EVENTS) as f:
            arr = json.load(f)
    except Exception:
        arr = []
    # 同日同类去重
    arr = [e for e in arr if not (e.get("date") == date and e.get("kind") == kind)]
    arr.append({
        "date": date, "kind": kind, "text": text,
        "nav": nav, "time": datetime.now().strftime("%H:%M"),
    })
    arr.sort(key=lambda e: e["date"])
    with open(EVENTS, "w") as f:
        json.dump(arr, f, ensure_ascii=False, indent=1)


def run_paper(mode_flag):
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "paper_trade.py")]
        + ([mode_flag] if mode_flag else []),
        capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError("paper_trade.py 失败:\n" + proc.stderr[-1500:])
    out = proc.stdout
    start = out.find("{")
    if start < 0:
        raise RuntimeError("无 JSON 输出:\n" + out[-500:])
    return json.loads(out[start:])


def read_last():
    if os.path.exists(LAST):
        with open(LAST) as f:
            return f.read().strip()
    return None


def write_last(d):
    with open(LAST, "w") as f:
        f.write(d)


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def fmt_pos(p):
    flag = "+" if p["unreal_pct"] >= 0 else ""
    return ("  · %s(%s) 入%.2f@%s 现价%.2f %s%.2f%% 持%d日"
            % (p["name"], p["code"], p["entry_px"], p["entry_date"],
               p["mark"], flag, p["unreal_pct"], p["hold_days"]))


def nav_line(rep, lines):
    nav = rep["nav"]
    nav_pct = (nav - 1.0) * 100
    lines.append(u"💰 纸面净值: %.4f  (%s%.2f%%)"
                 % (nav, "+" if nav_pct >= 0 else "", nav_pct))
    if rep["closed_trades"]:
        lines.append(u"已平仓: %d 笔  |  胜率: %.1f%%"
                     % (rep["closed_trades"], rep["win_rate"] * 100))
    else:
        lines.append(u"已平仓: 0 笔")


def trades_section(rep, lines):
    if rep.get("today_buys"):
        lines.append(u"")
        lines.append(u"🟢 开盘买入 (%d 笔，按开盘价成交):" % len(rep["today_buys"]))
        for t in rep["today_buys"]:
            lines.append("  · %s(%s) 买入价 %.2f"
                         % (t["name"], t["code"], t["entry_px"]))
    if rep.get("today_sells"):
        lines.append(u"")
        lines.append(u"🔴 开盘卖出 (%d 笔):" % len(rep["today_sells"]))
        for t in rep["today_sells"]:
            flag = "+" if t["net_pct"] >= 0 else ""
            lines.append("  · %s(%s) 卖出价 %.2f 净收益 %s%.2f%% (%s)"
                         % (t["name"], t["code"], t["exit_px"], flag,
                            t["net_pct"], t["reason"]))


def positions_section(rep, lines):
    if rep["open_positions"]:
        lines.append(u"")
        lines.append(u"📈 当前持仓 (%d 笔):" % len(rep["open_positions"]))
        for p in rep["open_positions"]:
            lines.append(fmt_pos(p))
    else:
        lines.append(u"")
        lines.append(u"📈 当前持仓: 无")


def recent_section(rep, lines):
    if rep.get("recent_closed"):
        lines.append(u"")
        lines.append(u"✅ 最近平仓:")
        for t in rep["recent_closed"]:
            flag = "+" if t["net_pct"] >= 0 else ""
            lines.append("  · %s %s→%s %s%.2f%%"
                         % (t["name"], t["entry_date"], t["exit_date"],
                            flag, t["net_pct"]))


# ---------------- 模式1：开盘执行 ----------------
def run_open():
    rep = run_paper("--live")
    today = today_str()
    if rep["data_end"] != today:
        # 今日无行情（节假日/未开盘）→ 留痕后静默
        append_event(today, "market_closed",
                     u"今日非交易日（无行情数据），纸面盘休市")
        return
    lines = [u"📊 量化纸面盘 · 开盘执行 %s" % rep["run_at"][11:16]]
    # 实盘人工下单指令块（纸面同步记账，下单由人执行）
    order_lines = []
    if rep.get("today_buys"):
        order_lines.append(u"🛒 实盘买入指令 (Top%d 等权，每只约 1/3 仓位):"
                           % len(rep["today_buys"]))
        for t in rep["today_buys"]:
            order_lines.append(u"  ▶ %s(%s) 限价 %.2f 起挂，开盘即涨停(%.2f 以上)则放弃"
                               % (t["name"], t["code"], t["entry_px"],
                                  t.get("limit_up_threshold", 0) or 0))
    if rep.get("today_sells"):
        order_lines.append(u"📤 实盘卖出指令 (开盘尽快卖出):")
        for t in rep["today_sells"]:
            order_lines.append(u"  ▶ %s(%s) 现价参考 %.2f（%s）"
                               % (t["name"], t["code"], t["exit_px"], t["reason"]))
    if order_lines:
        lines.append(u"")
        lines.append(u"━━━━━━━━ 人工下单指令 ━━━━━━━━")
        lines.extend(order_lines)
        lines.append(u"（以下为纸面盘同步记账结果）")
        lines.append(u"")
    if not rep.get("today_buys") and not rep.get("today_sells"):
        lines.append(u"")
        lines.append(u"🎯 无买入/卖出操作，继续观察")
        if rep.get("yesterday_filter_pass") and rep.get("skipped_at_open"):
            lines.append(u"（昨日有信号，但以下标的今日开盘触涨停线被跳过）:")
            for s in rep["skipped_at_open"]:
                lines.append("  · %s(%s) 昨收%.2f 涨停线%.2f"
                             % (s["name"], s["code"], s["close"],
                                s["limit_up_threshold"]))
    else:
        trades_section(rep, lines)
        if rep.get("skipped_at_open"):
            lines.append(u"")
            lines.append(u"⏭️ 开盘触涨停线被跳过:")
            for s in rep["skipped_at_open"]:
                lines.append("  · %s(%s) 昨收%.2f 涨停线%.2f"
                             % (s["name"], s["code"], s["close"],
                                s["limit_up_threshold"]))
    positions_section(rep, lines)
    nav_line(rep, lines)
    # 每日动态留痕（无交易也记录）
    if not rep.get("today_buys") and not rep.get("today_sells"):
        ev_txt = u"开盘无交易 · 继续观察"
        if rep.get("yesterday_filter_pass") and rep.get("skipped_at_open"):
            ev_txt = u"开盘无交易 · 昨日候选触涨停线被跳过"
    else:
        parts = []
        if rep.get("today_buys"):
            parts.append(u"买入 %d 只: %s" % (len(rep["today_buys"]),
                         u"、".join(t["name"] for t in rep["today_buys"])))
        if rep.get("today_sells"):
            parts.append(u"卖出 %d 只: %s" % (len(rep["today_sells"]),
                         u"、".join(t["name"] for t in rep["today_sells"])))
        ev_txt = u"、".join(parts)
    append_event(today, "open", ev_txt, nav=rep["nav"])
    print("\n".join(lines))


# ---------------- 模式2：收盘结算 ----------------
def run_close():
    rep = run_paper("--close")
    today = today_str()
    data_end = rep["data_end"]
    prev = read_last()

    lines = [u"📊 量化纸面盘 · 收盘结算 %s" % rep["run_at"][:16]]
    if data_end != today:
        # 无今日K线：休市日静默（停更过久告警）
        if prev == data_end:
            try:
                last_dt = datetime.strptime(data_end, "%Y-%m-%d")
                stale = (datetime.now() - last_dt).days
            except Exception:
                stale = 0
            if stale >= 4:
                print(u"⚠️ 纸面验证数据源异常：最新数据停在 %s（已 %d 天未更新），"
                      u"请检查取数。" % (data_end, stale))
        return
    if prev == data_end and data_end == today:
        return  # 同日已结算过（重复触发）→ 静默

    lines.append(u"数据截至: %s" % data_end)
    lines.append(u"")
    trades_section(rep, lines)
    if not rep.get("today_buys") and not rep.get("today_sells"):
        lines.append(u"")
        lines.append(u"今日无买卖操作")
    positions_section(rep, lines)
    recent_section(rep, lines)
    lines.append(u"")
    nav_line(rep, lines)

    # 明日计划
    tm = rep.get("tomorrow", {})
    lines.append(u"")
    if not tm.get("filter_pass"):
        lines.append(u"🎯 明日计划: 市场门控未通过（广度/炸板率不达标），空仓观望")
    elif not tm.get("picks"):
        lines.append(u"🎯 明日计划: 门控通过但无个股满足全部条件，空仓观望")
    else:
        lines.append(u"🎯 明日开盘买入候选 (Top3 等权):")
        for p in tm["picks"]:
            lines.append("  · %s(%s) 今收%.2f 涨停线%.2f（开盘触线则跳过）"
                         % (p["name"], p["code"], p["close"],
                            p["limit_up_threshold"]))
    due = rep.get("exit_due_tomorrow", [])
    if due:
        lines.append(u"")
        lines.append(u"📤 明日开盘卖出:")
        for d in due:
            lines.append("  · %s(%s) 原因: %s" % (d["name"], d["code"],
                                                   d["reason"]))
    # 每日动态留痕（收盘结算必留）
    plan_txt = u"明日空仓观望（门控未通过）"
    if tm.get("filter_pass"):
        plan_txt = (u"明日候选 %d 只" % len(tm.get("picks", []))
                    if tm.get("picks") else u"明日空仓观望（无合格个股）")
    if rep.get("today_buys") or rep.get("today_sells"):
        day_txt = u"收盘 · 今日 %d 买 %d 卖" % (
            len(rep.get("today_buys", [])), len(rep.get("today_sells", [])))
    else:
        day_txt = u"收盘 · 今日无交易"
    append_event(today, "close", u"%s · 净值 %.4f · %s" % (
        day_txt, rep["nav"], plan_txt), nav=rep["nav"])
    write_last(data_end)
    print("\n".join(lines))


# ---------------- 模式3：盘中快照（手动） ----------------
def run_intraday():
    rep = run_paper("--live")
    today = today_str()
    if rep["data_end"] != today:
        print(u"（今日非交易日，无盘中数据）")
        return
    lines = [u"📊 量化纸面盘 · 盘中快照 %s" % rep["run_at"][11:16]]
    positions_section(rep, lines)
    nav_line(rep, lines)
    print("\n".join(lines))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "close"
    if mode == "open":
        run_open()
    elif mode == "intraday":
        run_intraday()
    else:
        run_close()


if __name__ == "__main__":
    main()
