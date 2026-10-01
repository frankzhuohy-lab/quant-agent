#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人工干预仓（discretionary overlay）— 独立于 R443 模型账本
模型是模型、人是人：本账本不进 replay，不与 paper_state.json 互写。
规则内置: 止损-6% / Keltner(1.5)止盈 / 最长持10交易日 / 成本单边0.2%
用法:
  manual_ledger.py buy   <605358.SH> <0.15> [备注]
  manual_ledger.py sell  <605358.SH> [原因]
  manual_ledger.py check          # 盘中：有触发→自动平仓并打印；无触发→零输出
  manual_ledger.py report         # 当前人工仓状态（可为空）
"""
import sys, os, json, math
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "manual_ledger.json")
COST = 0.002          # 单边，与 R443 冻结口径一致
STOP = 0.06           # 硬止损 6%
MAX_HOLD = 10         # 最长持有交易日
KELT_MULT = 1.5
import urllib.request

NAMES = {"605358.SH": u"立昂微"}


def sym(code):
    # "605358.SH" -> "sh605358"
    num, mkt = code.split(".")
    return mkt.lower() + num


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def live_px(code):
    raw = http_get("https://qt.gtimg.cn/q=%s" % sym(code)).decode("gbk", "ignore")
    f = raw.split("~")
    return float(f[3]), f[1]


def daily_bars(code, n=30):
    """最近 n 根前复权日线: [(date, o,h,l,c)]"""
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq"
           % (sym(code), n))
    d = json.loads(http_get(url).decode("utf-8", "ignore"))
    node = d.get("data", {}).get(sym(code), {})
    bars = node.get("qfqday") or node.get("day") or []
    return [(b[0], float(b[1]), float(b[2]), float(b[3]), float(b[4])) for b in bars]


def kelt_upper(closes, mult=KELT_MULT, win=20):
    """与 R443 同款近似: EMA20 + 1.5*std20（用收盘价窗口的标准差）"""
    if len(closes) < win + 1:
        return None
    window = closes[-win:]
    ema = sum(window) / win
    var = sum((x - ema) ** 2 for x in window) / win
    return ema + mult * math.sqrt(var)


def load():
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            return json.load(f)
    return {"cash": 1.0, "positions": [], "closed": []}


def save(st):
    with open(LEDGER, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def hold_days(code, entry_date):
    bars = daily_bars(code, 15)
    return sum(1 for b in bars if b[0] > entry_date)


def settle(st, p, px, reason):
    net = px / p["entry_px"] - 1.0 - 2 * COST
    st["cash"] += p["weight"] * (1.0 + net)
    st["closed"].append({
        "code": p["code"], "name": p.get("name") or NAMES.get(p["code"], p["code"]),
        "entry_date": p["entry_date"], "entry_px": p["entry_px"],
        "exit_date": datetime.now().strftime("%Y-%m-%d"), "exit_px": round(px, 3),
        "weight": p["weight"], "net_pct": round(net * 100, 2), "reason": reason,
        "note": p.get("note", "")})
    st["positions"].remove(p)
    return net


def cmd_buy(code, weight, note=""):
    st = load()
    cur_w = sum(p["weight"] for p in st["positions"])
    if cur_w + weight > 0.30 + 1e-9:
        print(u"❌ 拒绝: 人工仓总权重上限 30%%（已用 %.0f%%，本次要 %.0f%%）"
              % (cur_w * 100, weight * 100))
        return 1
    if st["cash"] + 1e-9 < weight:
        print(u"❌ 拒绝: 人工仓现金不足 (cash=%.3f)" % st["cash"])
        return 1
    px, name = live_px(code)
    p = {"code": code, "name": name, "entry_date": datetime.now().strftime("%Y-%m-%d"),
         "entry_px": px, "weight": weight, "stop": STOP, "max_hold": MAX_HOLD,
         "note": note or u"人工判断单（模型信号外）"}
    st["positions"].append(p)
    st["cash"] -= weight
    save(st)
    print(u"✅ 人工仓买入 %s(%s) @%.2f  权重%.0f%%  止损-%.0f%%  Keltner止盈  到期%d日"
          % (name, code, px, weight * 100, STOP * 100, MAX_HOLD))
    return 0


def cmd_sell(code, reason=u"人工指令"):
    st = load()
    for p in st["positions"]:
        if p["code"] == code:
            px, _ = live_px(code)
            net = settle(st, p, px, reason)
            save(st)
            print(u"📤 人工仓卖出 %s(%s) @%.2f  净%+.2f%%（%s）"
                  % (p.get("name", code), code, px, net * 100, reason))
            return 0
    print(u"未找到 %s 的人工持仓" % code)
    return 1


def cmd_check():
    """静默哨兵语义：无触发零输出"""
    st = load()
    out = []
    changed = False
    for p in list(st["positions"]):
        try:
            px, _ = live_px(p["code"])
            bars = daily_bars(p["code"], 30)
            closes = [b[4] for b in bars]
            hu = kelt_upper(closes[:-1])   # 不含今日未完成bar的口径：用截至昨日的ema带
            hd = sum(1 for b in bars if b[0] > p["entry_date"])
        except Exception as e:
            out.append(u"⚠️ 人工仓 %s 检查失败: %s" % (p["code"], str(e)[:60]))
            continue
        reason = None
        if px <= p["entry_px"] * (1 - STOP):
            reason = u"止损-6%%"
        elif hd >= MAX_HOLD:
            reason = u"持满%d日" % MAX_HOLD
        elif hu and px >= hu and px > p["entry_px"]:
            reason = u"Keltner止盈(上轨%.2f)" % hu
        if reason:
            net = settle(st, p, px, reason)
            changed = True
            out.append(u"📤 人工仓卖出 %s(%s) @%.2f  净%+.2f%%（%s）"
                       % (p.get("name", p["code"]), p["code"], px, net * 100, reason))
    if changed:
        save(st)
    if out:
        print(u"\n".join(out))
    return 0


def cmd_report():
    st = load()
    lines = []
    if st["positions"]:
        lines.append(u"🧠 人工干预仓（独立于模型 %d%%）:" % 100)
        for p in st["positions"]:
            try:
                px, _ = live_px(p["code"])
                net = px / p["entry_px"] - 1.0 - 2 * COST
                lines.append(u"  ▶ %s(%s) 成本%.2f → 现价%.2f  净%+.2f%%  权重%.0f%%  %s"
                             % (p.get("name", p["code"]), p["code"], p["entry_px"], px,
                                net * 100, p["weight"] * 100, p.get("note", "")))
            except Exception:
                lines.append(u"  ▶ %s 取价失败" % p["code"])
    if st["closed"]:
        c = st["closed"][-1]
        lines.append(u"  最近平仓: %s %s→%s @%.2f→%.2f 净%+.2f%%（%s）"
                     % (c["name"], c["entry_date"][5:], c["exit_date"][5:],
                        c["entry_px"], c["exit_px"], c["net_pct"], c["reason"]))
    nav = st["cash"] + sum(p["weight"] for p in st["positions"])
    lines.append(u"  人工仓净值: %.4f (现金%.0f%%+持仓%.0f%%)"
                 % (nav, st["cash"] * 100, sum(p["weight"] for p in st["positions"]) * 100))
    print(u"\n".join(lines))
    return 0


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "report"
    if a == "buy":
        sys.exit(cmd_buy(sys.argv[2], float(sys.argv[3]),
                         sys.argv[4] if len(sys.argv) > 4 else ""))
    elif a == "sell":
        sys.exit(cmd_sell(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else u"人工指令"))
    elif a == "check":
        sys.exit(cmd_check())
    else:
        sys.exit(cmd_report())
