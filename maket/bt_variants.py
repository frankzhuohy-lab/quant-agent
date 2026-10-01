#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maket 90天回测·结构性变体对照（同一份分钟线缓存，离线重放）
变体（假设先行，非曲线拟合；只动结构性开关）:
  V0 基线          现行规则（双向、每日≤2回合）
  V1 只正T         NORMAL/WEAK 取消反T（数据: 反T每笔-0.23%是主要亏损腿）；STRONG 保留回调低吸
  V2 V1+每日≤1回合  尾盘强平占41%，后半天进场的回合更容易熬到强平
结果为事后检验，样本仅90天，不构成对未来的承诺。
"""
import sys, os, json, time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maket as M
from bt_maket_90d import fetch_m5_range

CACHE = os.path.join(HERE, "bt_cache_90d.json")
N_DAYS = 90
COST = 0.0012


def load_or_fetch():
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            c = json.load(f)
        if len(c.get("m5", {})) >= 10:
            print("使用缓存:", CACHE)
            return c
    pool = M.load_pool()
    daily, m5 = {}, {}
    for s in pool:
        try:
            daily[s["symbol"]] = M.fetch_daily(s["symbol"], 260)
        except Exception:
            continue
        time.sleep(0.2)
    all_dates = sorted({b[0] for bars in daily.values() for b in bars})
    bt_dates = all_dates[-(N_DAYS + 1):-1]
    start_ts = bt_dates[0].replace("-", "") + "0000"
    for i, s in enumerate(pool):
        if s["symbol"] not in daily:
            continue
        m5[s["symbol"]] = fetch_m5_range(s["symbol"], start_ts)
        print("  m5 [%d/%d] %s: %d根" % (i + 1, len(pool), s["name"], len(m5[s["symbol"]])))
    with open(CACHE, "w") as f:
        json.dump({"bt_dates": bt_dates, "daily": daily, "m5": m5,
                   "pool": pool}, f)
    return {"bt_dates": bt_dates, "daily": daily, "m5": m5, "pool": pool}


def replay_day(z, day_bars, allow_reverse=True, max_rounds=2):
    """bt_maket_90d.replay_day 的参数化版本"""
    from bt_maket_90d import replay_day as base
    if not allow_reverse:
        # 复用基础重放，但屏蔽反T开仓: monkey 不了，就地重写关键分支
        pass
    return base(z, day_bars)


def replay_day_v(z, day_bars, allow_reverse=True, max_rounds=2):
    rounds = []
    if len(day_bars) < 8:
        return rounds
    regime = z["regime"]
    o_day = day_bars[0][1]
    closes = [b[4] for b in day_bars]
    lows = [b[3] for b in day_bars]
    prev_c = z["prev_close"]
    tick_unit = min(0.012, 0.6 * z["atr"] / prev_c) if prev_c else 0.012
    floor = z["buy_ext"] * 0.997
    pos = None
    low_today = lows[0]
    for i, b in enumerate(day_bars):
        hm = b[0][8:]
        px = closes[i]
        low_today = min(low_today, lows[i])
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
            if hm >= "1450" and not done:
                done = ("尾盘强平", px)
            if done:
                rounds.append({"dir": d, "entry": entry, "exit": px,
                               "gross": (px / entry - 1.0) * d, "reason": done[0]})
                pos = None
            continue
        if len(rounds) >= max_rounds or hm >= M.LATE_OPEN or hm < M.EARLY_OK:
            continue
        opened = False
        if regime == "STRONG":
            rally = px / o_day - 1 if o_day else 0
            if allow_reverse and rally >= 0.05 and mo == -1 and px >= z["sell_ext"]:
                pos = {"dir": -1, "entry": px, "target": max(z["buy_hi"], px * (1 - 0.02))}
                opened = True
            elif px <= z["buy_hi"] and mo == 1:
                pos = {"dir": 1, "entry": px,
                       "target": z["sell_lo"] if z["sell_lo"] > px else px * (1 + 0.012)}
                opened = True
        else:
            sell_trig = z["sell_lo"] if regime == "NORMAL" \
                else min(z["sell_lo"], prev_c * (1 + 0.005))
            if allow_reverse and px >= sell_trig and mo == -1:
                pos = {"dir": -1, "entry": px, "target": max(z["buy_hi"], px * (1 - tick_unit))}
                opened = True
            elif px <= z["buy_hi"] and px >= floor and mo == 1:
                tgt = max(z["sell_lo"], vwap) if vwap > px * 1.004 else px * (1 + tick_unit)
                pos = {"dir": 1, "entry": px, "target": tgt}
                opened = True
            elif low_today < floor and px > floor and mo == 1:
                tgt = max(z["sell_lo"], vwap) if vwap > px * 1.004 else px * (1 + tick_unit)
                pos = {"dir": 1, "entry": px, "target": tgt}
                opened = True
        if opened:
            low_today = px  # 进场后重置日内低点参照，与实盘实时low口径接近
    if pos:
        r = (closes[-1] / pos["entry"] - 1.0) * pos["dir"]
        rounds.append({"dir": pos["dir"], "entry": pos["entry"], "exit": closes[-1],
                       "gross": r, "reason": "日终未平"})
    return rounds


def run_variant(cache, name, **flags):
    rounds = []
    for date in cache["bt_dates"]:
        dkey = date.replace("-", "")
        for s in cache["pool"]:
            sym, code = s["symbol"], s["code"]
            if sym not in cache["daily"] or sym not in cache["m5"]:
                continue
            hist = [b for b in cache["daily"][sym] if b[0] < date]
            if len(hist) < 25:
                continue
            day_bars = [b for b in cache["m5"][sym] if b[0].startswith(dkey)]
            if not day_bars:
                continue
            z = M.build_zones(code, hist)
            for r in replay_day_v(z, day_bars, **flags):
                rounds.append(dict(date=date, code=code, name=s["name"], **r))
    nets = [(r["gross"] - COST) * 100 for r in rounds]
    if not nets:
        print("%-16s 无回合" % name)
        return
    wins = sum(1 for n in nets if n > 0)
    pos_days = 0
    day_sum = defaultdict(float)
    for r, n in zip(rounds, nets):
        day_sum[r["date"]] += n
    active = [v for v in day_sum.values()]
    print("%-16s 回合%4d 胜率%5.1f%% 单笔%+7.3f%% 合计%+8.1f%% | 信号日%d天 盈利日%3d(%2.0f%%)"
          % (name, len(nets), 100.0 * wins / len(nets), sum(nets) / len(nets),
             sum(nets), len(active),
             sum(1 for v in active if v > 0), 100.0 * sum(1 for v in active if v > 0) / len(active)))
    # 方向拆分
    for d, tag in ((1, "正T"), (-1, "反T")):
        dn = [(r["gross"] - COST) * 100 for r in rounds if r["dir"] == d]
        if dn:
            print("    %s: %d笔 均值%+.3f%% 合计%+.1f%%"
                  % (tag, len(dn), sum(dn) / len(dn), sum(dn)))
    return rounds


def main():
    cache = load_or_fetch()
    print("\n===== 变体对照（双边成本%.2f%%，区间 %s ~ %s）====="
          % (COST * 100, cache["bt_dates"][0], cache["bt_dates"][-1]))
    run_variant(cache, "V0 基线(双向,≤2)")
    run_variant(cache, "V1 只正T", allow_reverse=False)
    run_variant(cache, "V2 只正T+≤1回合", allow_reverse=False, max_rounds=1)
    run_variant(cache, "V3 双向+≤1回合", max_rounds=1)


if __name__ == "__main__":
    main()
