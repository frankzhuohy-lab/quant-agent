#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maket 做 T 信号 90 交易日历史重放回测
与实盘 maket.py monitor 完全同一套逻辑（直接 import build_zones/classify/常量）：
  - 价位区/regime 只用 D-1 及之前日线（无未来函数）
  - m5 逐bar重放: 动量确认、区间触发、止损1.5%、14:55强平、每票每日≤2回合
  - 14:30后不开新仓、9:45前不进场
差异说明: 实盘每10分钟轮询实时价，回测用5分钟bar收盘价成交（粒度更细，无跳空内部成交假设）
用法: python3 bt_maket_90d.py [回测交易日数, 默认90]
"""
import sys, os, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maket as M

COST_BASE = 0.0012   # 系统当前成本假设
COST_ALT = [0.002, 0.003]  # 敏感性: 万5双边 / 千3双边


def fetch_m5_range(sym, start_ts):
    """锚点翻页拉取 sym 自 start_ts 起全部 m5，升序"""
    allb = {}
    anchor = ""
    for page in range(30):
        url = ("https://ifzq.gtimg.cn/appstock/app/kline/mkline"
               "?param=%s,m5,%s,320" % (sym, anchor))
        try:
            d = json.loads(M.http_get(url))
        except Exception:
            time.sleep(2)
            d = json.loads(M.http_get(url))
        bars = d.get("data", {}).get(sym, {}).get("m5") or []
        if not bars:
            break
        for b in bars:
            allb[b[0]] = [b[0], float(b[1]), float(b[2]), float(b[3]),
                          float(b[4]), float(b[5])]
        oldest = min(b[0] for b in bars)
        if oldest <= start_ts:
            break
        anchor = oldest
        time.sleep(0.15)
    return [allb[k] for k in sorted(allb) if k >= start_ts]


def replay_day(z, day_bars):
    """按 monitor 逻辑重放一个交易日，返回已平回合列表"""
    rounds = []
    if len(day_bars) < 8:
        return rounds
    regime = z["regime"]
    o_day = day_bars[0][1]
    closes = [b[4] for b in day_bars]
    lows = [b[3] for b in day_bars]
    atr = z["atr"]
    prev_c = z["prev_close"]
    tick_unit = min(0.012, 0.6 * atr / prev_c) if prev_c else 0.012
    sell_trig = z["sell_lo"] if regime == "NORMAL" \
        else min(z["sell_lo"], prev_c * (1 + 0.005))
    floor = z["buy_ext"] * 0.997
    pos = None
    low_today = lows[0]
    for i, b in enumerate(day_bars):
        hm = b[0][8:]
        px = closes[i]
        low_today = min(low_today, lows[i])
        # running vwap（含当前bar，与实盘口径一致）
        seg = day_bars[:i + 1]
        vwap = sum(x[4] * x[5] for x in seg) / max(sum(x[5] for x in seg), 1)
        mo = 0
        if i >= 2:
            if closes[i] > closes[i - 1] > closes[i - 2]:
                mo = 1
            elif closes[i] < closes[i - 1] < closes[i - 2]:
                mo = -1
        if pos:
            d, entry, target = pos["dir"], pos["entry"], pos["target"]
            done = None
            if d == 1:
                if px >= entry * (1 + 0.010) and vwap >= entry * (1 + 0.006):
                    done = ("反弹达标", px)
                elif target and px >= target:
                    done = ("到达高抛目标", px)
                elif px <= entry * (1 - M.STOP_PCT):
                    done = ("止损", px)
            else:
                if px <= entry * (1 - 0.010) and vwap <= entry * (1 - 0.006):
                    done = ("回落达标", px)
                elif target and px <= target:
                    done = ("到达低吸目标", px)
                elif px >= entry * (1 + M.STOP_PCT):
                    done = ("止损", px)
            if hm >= "1450" and not done:   # ≈14:55 强平
                done = ("尾盘强平", px)
            if done:
                reason, exit_px = done
                r = (exit_px / entry - 1.0) * d
                rounds.append({"dir": d, "entry": entry, "exit": exit_px,
                               "gross": r, "reason": reason, "hm": hm})
                pos = None
            continue
        # —— 开新仓条件 ——
        if len(rounds) >= M.MAX_ROUNDS or hm >= M.LATE_OPEN or hm < M.EARLY_OK:
            continue
        if regime == "STRONG":
            rally = px / o_day - 1 if o_day else 0
            if rally >= 0.05 and mo == -1 and px >= z["sell_ext"]:
                pos = {"dir": -1, "entry": px,
                       "target": max(z["buy_hi"], px * (1 - 0.02))}
            elif px <= z["buy_hi"] and mo == 1:
                pos = {"dir": 1, "entry": px,
                       "target": z["sell_lo"] if z["sell_lo"] > px else px * (1 + 0.012)}
            continue
        if px >= sell_trig and mo == -1:
            pos = {"dir": -1, "entry": px,
                   "target": max(z["buy_hi"], px * (1 - tick_unit))}
        elif px <= z["buy_hi"] and px >= floor and mo == 1:
            tgt = max(z["sell_lo"], vwap) if vwap > px * 1.004 \
                else px * (1 + tick_unit)
            pos = {"dir": 1, "entry": px, "target": tgt}
        elif low_today < floor and px > floor and mo == 1:
            tgt = max(z["sell_lo"], vwap) if vwap > px * 1.004 \
                else px * (1 + tick_unit)
            pos = {"dir": 1, "entry": px, "target": tgt}
    if pos:
        exit_px = closes[-1]
        r = (exit_px / pos["entry"] - 1.0) * pos["dir"]
        rounds.append({"dir": pos["dir"], "entry": pos["entry"], "exit": exit_px,
                       "gross": r, "reason": "日终未平", "hm": "1500"})
    return rounds


def main():
    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    pool = M.load_pool()
    print("拉取日线...")
    daily = {}
    for s in pool:
        try:
            daily[s["symbol"]] = M.fetch_daily(s["symbol"], 260)
        except Exception as e:
            print("  %s 日线失败: %s" % (s["name"], str(e)[:50]))
        time.sleep(0.2)
    all_dates = sorted({b[0] for bars in daily.values() for b in bars})
    bt_dates = all_dates[-(n_days + 1):-1] or all_dates[-n_days:]
    start_ts = bt_dates[0].replace("-", "") + "0000"
    print("回测区间: %s ~ %s（%d个交易日）" % (bt_dates[0], bt_dates[-1], len(bt_dates)))
    print("拉取m5分钟线（每票约15页，需2-4分钟）...")
    m5 = {}
    for i, s in enumerate(pool):
        sym = s["symbol"]
        if sym not in daily:
            continue
        m5[sym] = fetch_m5_range(sym, start_ts)
        print("  [%d/%d] %s: %d 根 (%s~%s)" % (
            i + 1, len(pool), s["name"], len(m5[sym]),
            m5[sym][0][0] if m5[sym] else "-", m5[sym][-1][0] if m5[sym] else "-"))

    # 逐日重放
    all_rounds = []   # {date,code,name,dir,gross,reason}
    day_pnl = {}      # date -> sum net% (base cost)
    for date in bt_dates:
        dkey = date.replace("-", "")
        day_sum = 0.0
        for s in pool:
            sym, code, name = s["symbol"], s["code"], s["name"]
            if sym not in daily or sym not in m5:
                continue
            hist = [b for b in daily[sym] if b[0] < date]
            if len(hist) < 25:
                continue
            day_bars = [b for b in m5[sym] if b[0].startswith(dkey)]
            if not day_bars:
                continue
            z = M.build_zones(code, hist)
            for r in replay_day(z, day_bars):
                rec = dict(date=date, code=code, name=name, **r)
                all_rounds.append(rec)
                day_sum += (r["gross"] - COST_BASE) * 100
        day_pnl[date] = day_sum

    # ---------- 汇总 ----------
    if not all_rounds:
        print("❌ 90天内无任何回合触发")
        return
    out = {"range": [bt_dates[0], bt_dates[-1]], "rounds": len(all_rounds)}

    def stats(cost):
        nets = [(r["gross"] - cost) * 100 for r in all_rounds]
        wins = [n for n in nets if n > 0]
        return {
            "cost": cost, "n": len(nets),
            "win_rate": 100.0 * len(wins) / len(nets),
            "avg": sum(nets) / len(nets),
            "total": sum(nets),
            "median": sorted(nets)[len(nets) // 2],
        }

    print("\n===== 回合级统计（%d 个回合）=====" % len(all_rounds))
    for st in [stats(COST_BASE)] + [stats(c) for c in COST_ALT]:
        print("  双边成本%.2f%%: 胜率 %.1f%% | 单笔均值 %+0.3f%% | 中位 %+0.3f%% | 合计 %+0.1f%%"
              % (st["cost"] * 100, st["win_rate"], st["avg"], st["median"], st["total"]))
    out["round_stats"] = [stats(c) for c in [COST_BASE] + COST_ALT]

    # 方向 / 退出原因 / regime
    from collections import Counter, defaultdict
    print("\n  按方向:")
    for d, tag in ((1, "正T(先买后卖)"), (-1, "反T(先卖后买)")):
        rs = [r for r in all_rounds if r["dir"] == d]
        if rs:
            nets = [(r["gross"] - COST_BASE) * 100 for r in rs]
            print("    %s: %d笔 胜率%.1f%% 均值%+.3f%%"
                  % (tag, len(rs), 100.0 * sum(1 for n in nets if n > 0) / len(nets),
                     sum(nets) / len(nets)))
    print("  按退出原因:")
    for reason, cnt in Counter(r["reason"] for r in all_rounds).most_common():
        nets = [(r["gross"] - COST_BASE) * 100 for r in all_rounds if r["reason"] == reason]
        print("    %-10s %3d笔 均值%+.3f%%" % (reason, cnt, sum(nets) / len(nets)))

    print("\n===== 个股明细（成本%.2f%%）=====" % (COST_BASE * 100))
    print("  %-8s %4s %6s %8s %8s" % ("股票", "笔数", "胜率", "单笔均值", "合计"))
    per_stock = defaultdict(list)
    for r in all_rounds:
        per_stock[r["name"]].append(r)
    for name, rs in sorted(per_stock.items(), key=lambda kv: -len(kv[1])):
        nets = [(r["gross"] - COST_BASE) * 100 for r in rs]
        print("  %-8s %4d %5.1f%% %+7.3f%% %+7.1f%%"
              % (name, len(rs), 100.0 * sum(1 for n in nets if n > 0) / len(nets),
                 sum(nets) / len(nets), sum(nets)))
    out["per_stock"] = {k: [(r["date"], r["dir"], round((r["gross"] - COST_BASE) * 100, 3), r["reason"]) for r in v]
                        for k, v in per_stock.items()}

    print("\n===== 交易日级（每天做T是否赚钱）=====")
    active = {d: v for d, v in day_pnl.items()
              if any(r["date"] == d for r in all_rounds)}
    pos_days = sum(1 for v in active.values() if v > 0)
    print("  %d个交易日中有信号的天数: %d（%.0f%%）；信号日中当日净盈利: %d天（%.0f%%）"
          % (len(bt_dates), len(active), 100.0 * len(active) / len(bt_dates),
             pos_days, 100.0 * pos_days / len(active) if active else 0))
    print("  全部%d天合计净利: %+.2f%%（等权每票每天满额做T口径）| 有信号日均%+.3f%%/天"
          % (len(bt_dates), sum(day_pnl.values()),
             sum(active.values()) / len(active) if active else 0))
    out["day_stats"] = {"days": len(bt_dates), "active_days": len(active),
                        "pos_days": pos_days,
                        "total": sum(day_pnl.values())}
    with open(os.path.join(HERE, "bt_90d_result.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n明细已存 bt_90d_result.json")


if __name__ == "__main__":
    main()
