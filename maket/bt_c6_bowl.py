# -*- coding: utf-8 -*-
"""bt_c6_bowl.py — 预注册因子测试 R2: 碗口条件(C6) vs 现行筛选条件（次日做T表现）

假设（来源: GitHub碗口反弹策略, 事后检验——原仓库零回测证据）:
  回看20日内最大成交量日若为阴线（收盘<开盘, 大资金出逃信号）, 剔除该票。
测试问题: 碗口条件组 vs 现行筛选条件组, 次日表现是否有统计显著差异?

组定义（均以 screen_candidates.analyze() 现行硬条件为基础, anchor日=筛选日）:
  A = 现行通过 且 碗口通过（最大量日为阳线, 即"最大阴量"不出现）
  B = 现行通过 但 碗口不通过（会被C6剔除的票）
  C = 全部现行通过组（A+B, 基准）

结果变量（次日 t+1, 开盘锚网格）:
  ① 触线: 次日盘中低 <= 次日开盘*0.994
  ② 达标(触线前提下): 次日高 >= 次日开盘*1.006
  ③ T回合净收益: 触线买在 开盘*0.994, 目标卖 开盘*1.006, 止损 入场*0.985,
     14:50强平简化用次日收盘判定; 成本双边0.22%含滑点。
     同日既触目标又触止损时每日线无法分辨先后 → 同时报保守(止损优先)与乐观(目标优先)两口径。

显著性: 按日聚类的组间差 t 检验（同日各票相关, 不用 stock-day 直接 t）:
  对每个"两组都有样本"的交易日取组内均值差, 对日差序列做单样本 t 检验。

数据: 妙想选股宇宙(成交额>3亿,涨跌幅-6~6,股价>3元, API上限200行, 按成交额降序取前200)
  → 腾讯日线120根(qfq) → 缓存 bt_cache_c6_daily.json（断点续抓, 已有则跳过）。
  失败回退: bt_cache_90d.json 的 pool+daily 作降级宇宙。

诚实性: 本文件是对仓库现行策略的事后检验。方向一致率只作诊断。
  采纳与否看同目录 publish_gate.py 对预注册三指标（触线/达标/保守净收益）的 Newey-West + BH-FDR。
  本脚本不改交易规则。
用法: python3 bt_c6_bowl.py          # 抓数+回测+输出 bt_c6_result.json
      python3 bt_c6_bowl.py --skip-fetch   # 用现有缓存直接回测
"""
import os
import csv
import glob
import json
import math
import time
import statistics
import urllib.request
import datetime
from collections import defaultdict

import publish_gate

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FP = os.path.join(HERE, "bt_cache_c6_daily.json")
RESULT_FP = os.path.join(HERE, "bt_c6_result.json")
FALLBACK_FP = os.path.join(HERE, "bt_cache_90d.json")
MX_GLOB = ("/Users/francishy/.agents/skills/mx-output/"
           "mx_xuangu_今日成交额大于3亿元*负6到6*股价大于3元的A股.csv")

# ---------- 预注册参数（与 screen_candidates.analyze / 任务书对齐, 勿改） ----------
N_BARS = 120          # 每票抓取日线根数
MIN_HIST = 60         # stock-day 至少60日历史
LOOKBACK = 20         # 碗口回看窗口
DIP = 0.994           # 触线: 低 <= 开*0.994
TGT = 1.006           # 达标: 高 >= 开*1.006
STOP = 0.985          # 止损: 入场价*0.985
COST = 0.0022         # 双边成本0.22%含滑点
VOL_MULT = 2.4        # 参考条件: >=2.4倍(5日均量)放量阳线
GAP_MAX_DAYS = 7      # anchor与次日间隔超过7自然日视为停牌缺口, 剔除并计数
MIN_GROUP_N = 50      # A或B低于此数 → 如实报告+扩样建议


# ============================ 数据获取 ============================
def http_get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:      # noqa: BLE001
            last = e
            time.sleep(1.0 + i)
    raise RuntimeError("http fail %s: %s" % (url, last))


def fetch_daily(sym, n=N_BARS):
    """腾讯日线(qfq), 主域失败回退 web.ifzq。bar=[date,o,c,h,l,vol,...]"""
    for host in ("https://ifzq.gtimg.cn", "https://web.ifzq.gtimg.cn"):
        try:
            d = json.loads(http_get(
                "%s/appstock/app/fqkline/get?param=%s,day,,,%d,qfq" % (host, sym, n)))
            dd = d["data"][sym]
            bars = dd.get("qfqday") or dd.get("day") or []
            if bars:
                return bars
        except Exception:           # noqa: BLE001
            continue
    return []


def load_universe():
    """妙想CSV宇宙(按成交额降序, 前200); 失败回退 bt_cache_90d.json。"""
    csvs = sorted(glob.glob(MX_GLOB), key=os.path.getmtime)
    if csvs:
        def amt(s):
            s = str(s).replace(",", "")
            if s.endswith("亿"):
                return float(s[:-1]) * 1e8
            if s.endswith("万"):
                return float(s[:-1]) * 1e4
            try:
                return float(s)
            except ValueError:
                return 0.0

        def col(row, prefix):
            for k in row:
                if k.startswith(prefix):
                    return row[k].strip()
            return ""

        uni = []
        with open(csvs[-1], encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                code, name, mkt = col(r, "代码"), col(r, "名称"), col(r, "市场代码简称")
                if not code or mkt not in ("SH", "SZ"):
                    continue
                if "ST" in (name or "") or "退" in (name or ""):
                    continue
                uni.append({"code": code, "name": name, "symbol": mkt.lower() + code,
                            "amt": amt(col(r, "成交额"))})
        uni.sort(key=lambda x: -x["amt"])
        return uni[:200], "妙想CSV(%s), 前200只按成交额降序" % os.path.basename(csvs[-1])
    # 降级宇宙
    d = json.load(open(FALLBACK_FP))
    uni = []
    for it in d.get("pool", []):
        sym = it.get("symbol", "")
        if sym and sym in d.get("daily", {}):
            uni.append({"code": it.get("code", ""), "name": it.get("name", ""),
                        "symbol": sym, "amt": 0.0})
    return uni, "回退bt_cache_90d.json pool（%d只, 无成交额排序）" % len(uni)


def build_cache(uni):
    cache = {}
    if os.path.exists(CACHE_FP):
        try:
            cache = json.load(open(CACHE_FP))
        except Exception:           # noqa: BLE001
            cache = {}
    todo = [u["symbol"] for u in uni if not cache.get(u["symbol"])]
    print("宇宙%d只, 缓存已有%d只, 需抓%d只" % (len(uni), len(uni) - len(todo), len(todo)))
    fail = []
    for k, u in enumerate(uni):
        sym = u["symbol"]
        if cache.get(sym):
            continue
        try:
            bars = fetch_daily(sym)
        except Exception:           # noqa: BLE001
            bars = []
        if not bars:
            fail.append(sym)
        cache[sym] = bars
        time.sleep(0.16)            # >=0.15s 限速
        if (k + 1) % 10 == 0:
            json.dump(cache, open(CACHE_FP, "w"))
            print("  进度 %d/%d (失败%d)" % (k + 1, len(uni), len(fail)))
    json.dump(cache, open(CACHE_FP, "w"))
    if fail:
        print("抓取失败%d只: %s" % (len(fail), ",".join(fail[:10])))
    return cache


# ============================ 条件计算 ============================
def f_bar(b):
    return (b[0], float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5]))


def screen_pass(seg):
    """现行筛选硬条件, 完全对齐 screen_candidates.analyze()（anchor=seg最后一根）。
    返回 (pass, detail)。"""
    if len(seg) < MIN_HIST:
        return False, {}
    dates, opens, closes, highs, lows, vols = [], [], [], [], [], []
    for b in seg:
        _, o, c, h, l, v = f_bar(b)
        if o <= 0 or c <= 0 or h <= 0 or l <= 0:
            return False, {}
        dates.append(b[0]); opens.append(o); closes.append(c)
        highs.append(h); lows.append(l); vols.append(v)
    n = len(seg)
    o_today, c_today = opens[-1], closes[-1]
    ma20 = sum(closes[-20:]) / 20.0
    ret20 = c_today / closes[-21] - 1.0
    chg_today = c_today / closes[-2] - 1.0
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i - 1]),
               abs(lows[i] - closes[i - 1]))
           for i in range(n - 14, n)]
    atr = sum(trs) / 14.0 / c_today
    dist20h = c_today / max(highs[-20:]) - 1.0
    ok = (c_today > ma20 and ret20 > 0 and chg_today < 0.05
          and 0.012 <= atr <= 0.065
          and (c_today < o_today or -0.10 <= dist20h <= -0.02))
    return ok, {"ma20": ma20, "ret20": ret20, "chg": chg_today, "atr": atr,
                "dist20h": dist20h, "red": c_today < o_today}


def bowl_pass(seg):
    """C6碗口条件: 回看LOOKBACK内最大成交量日为阳线(c>o)。
    doji(c==o)不判阴, 不剔除。另给参考条件: 窗口内存在 vol>=2.4倍其前5日均量 的阳线。
    返回 (main_pass, extra_ref, mv_detail)。"""
    win = seg[-LOOKBACK:]
    mv_i, mv_v = None, -1.0
    n = len(seg)
    for k in range(n - LOOKBACK, n):
        v = float(seg[k][5])
        if v <= 0:
            return True, False, None     # 量数据缺失 → 不敢判阴, 保守放行并另行计数
        if v > mv_v:
            mv_v, mv_i = v, k
    mb = f_bar(seg[mv_i])
    main = mb[2] > mb[1]                 # 最大量日 阳线?
    extra = False
    for k in range(n - LOOKBACK, n):
        if k - 5 < 0:
            continue
        b = f_bar(seg[k])
        base = sum(float(seg[j][5]) for j in range(k - 5, k)) / 5.0
        if base > 0 and b[5] >= VOL_MULT * base and b[2] > b[1]:
            extra = True
            break
    return main, extra, {"mv_date": mb[0], "mv_red": mb[2] < mb[1], "mv_doji": mb[2] == mb[1]}


def next_day_outcome(bars, i):
    """次日结果。返回 (outcome, gap_note)。outcome=None 表示数据缺口。"""
    if i + 1 >= len(bars):
        return None, "no_next"
    a_d = bars[i][0]
    nb = f_bar(bars[i + 1])
    try:
        d0 = datetime.date(*map(int, a_d.split("-")))
        d1 = datetime.date(*map(int, nb[0].split("-")))
        gap = (d1 - d0).days
    except Exception:                   # noqa: BLE001
        gap = 0
    if gap > GAP_MAX_DAYS:
        return None, "gap%d" % gap
    if nb[1] <= 0 or nb[2] <= 0 or nb[3] <= 0 or nb[4] <= 0:
        return None, "bad_ohlc"
    n_o, n_h, n_l, n_c = nb[1], nb[3], nb[4], nb[2]
    touched = n_l <= n_o * DIP
    hit = touched and (n_h >= n_o * TGT)
    if not touched:
        net_cons = net_opt = None
    else:
        entry = n_o * DIP
        stop_hit = n_l <= entry * STOP
        tgt_hit = n_h >= n_o * TGT
        # 保守: 止损优先; 乐观: 目标优先
        if stop_hit:
            g_cons = STOP - 1.0
        elif tgt_hit:
            g_cons = n_o * TGT / entry - 1.0
        else:
            g_cons = n_c / entry - 1.0
        if tgt_hit:
            g_opt = n_o * TGT / entry - 1.0
        elif stop_hit:
            g_opt = STOP - 1.0
        else:
            g_opt = n_c / entry - 1.0
        net_cons = g_cons - COST
        net_opt = g_opt - COST
    return {"date": nb[0], "touched": touched, "hit": hit,
            "net_cons": net_cons, "net_opt": net_opt}, "ok"


# ============================ 统计 ============================
def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 300, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lb = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
          + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return math.exp(lb) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lb) * _betacf(b, a, 1 - x) / b


def t_pvalue(t, df):
    """双侧p, 用不完全beta实现t分布CDF。"""
    if df <= 0:
        return None
    return betai(df / 2.0, 0.5, df / (df + t * t))


def cluster_t(rows_a, rows_b, key):
    """按日聚类组间差t检验。key(row)->观测值或None(缺失不进均值)。"""
    by_a, by_b = defaultdict(list), defaultdict(list)
    for r in rows_a:
        v = key(r)
        if v is not None:
            by_a[r["date"]].append(v)
    for r in rows_b:
        v = key(r)
        if v is not None:
            by_b[r["date"]].append(v)
    common = sorted(set(by_a) & set(by_b))
    diffs = [statistics.mean(by_a[d]) - statistics.mean(by_b[d]) for d in common]
    n = len(diffs)
    if n < 5:
        return {"n_days": n, "mean_diff": None, "t": None, "p": None,
                "t_nw": None, "p_nw": None, "nw_lag": 0, "hac_fallback": False,
                "diffs": diffs, "pct_days_a_gt_b": None}
    m = statistics.mean(diffs)
    s = statistics.stdev(diffs) if n > 1 else 0.0
    t = (m / (s / math.sqrt(n))) if s > 0 else (0.0 if m == 0 else math.copysign(9.99, m))
    p = t_pvalue(t, n - 1) if s > 0 else None
    nw = publish_gate.newey_west_mean(diffs)
    return {"n_days": n, "mean_diff": m, "t": t, "p": p,
            "t_nw": nw["t"], "p_nw": nw["p"], "nw_lag": nw["lag"],
            "hac_fallback": nw["hac_fallback"],
            "diffs": diffs,
            "pct_days_a_gt_b": sum(1 for x in diffs if x > 0) / float(n)}


def grp_stats(rows):
    touched = [r for r in rows if r["touched"]]
    out = {
        "n": len(rows),
        "n_touched": len(touched),
        "touch_rate": (len(touched) / len(rows)) if rows else None,
        "hit_rate_given_touch": (sum(1 for r in touched if r["hit"]) / len(touched))
        if touched else None,
        "mean_net_cons_given_touch": (statistics.mean([r["net_cons"] for r in touched]))
        if touched else None,
        "mean_net_opt_given_touch": (statistics.mean([r["net_opt"] for r in touched]))
        if touched else None,
        "ev_per_signal_cons": None,
    }
    if rows and touched:
        out["ev_per_signal_cons"] = out["touch_rate"] * out["mean_net_cons_given_touch"]
    return out


# ============================ 主流程 ============================
def main():
    skip_fetch = "--skip-fetch" in os.sys.argv
    uni, uni_src = load_universe()
    print("宇宙来源:", uni_src)
    names = {u["symbol"]: u.get("name", "") for u in uni}
    if skip_fetch:
        cache = json.load(open(CACHE_FP))
    else:
        cache = build_cache(uni)

    rows, gaps = [], defaultdict(int)
    syms_used = 0
    for u in uni:
        sym = u["symbol"]
        bars = cache.get(sym) or []
        if len(bars) < MIN_HIST + 2:
            gaps["sym_lt_hist"] += 1
            continue
        syms_used += 1
        for i in range(MIN_HIST - 1, len(bars) - 1):
            seg = bars[:i + 1]
            ok, _detail = screen_pass(seg)
            if not ok:
                continue
            out, note = next_day_outcome(bars, i)
            if out is None:
                gaps["anchor_" + note] += 1
                continue
            bp, extra, mvd = bowl_pass(seg)
            if mvd is None:
                gaps["bowl_vol_missing"] += 1
            rows.append({"symbol": sym, "code": u.get("code", ""), "name": names.get(sym, ""),
                         "date": seg[-1][0], "grp": ("A" if bp else "B"),
                         "bowl_extra": extra,
                         "touched": out["touched"], "hit": out["hit"],
                         "net_cons": out["net_cons"], "net_opt": out["net_opt"]})
    grpA = [r for r in rows if r["grp"] == "A"]
    grpB = [r for r in rows if r["grp"] == "B"]
    grpC = rows
    extraA = [r for r in grpA if r["bowl_extra"]]
    extraB = [r for r in grpA if not r["bowl_extra"]]

    print("\n数据缺口计数: %s" % dict(gaps))
    print("有效股票数 %d, 现行通过 stock-day %d (A=%d B=%d), 交易日 %d"
          % (syms_used, len(grpC), len(grpA), len(grpB),
             len(set(r["date"] for r in grpC))))

    stats = {"A": grp_stats(grpA), "B": grp_stats(grpB), "C": grp_stats(grpC),
             "A_extra": grp_stats(extraA), "A_noextra": grp_stats(extraB)}
    for g in ("A", "B", "C", "A_extra", "A_noextra"):
        s = stats[g]
        print("\n组%s: n=%d 触线%d (%.1f%%)" % (g, s["n"], s["n_touched"],
                                               100 * (s["touch_rate"] or 0)))
        print("   触线后达标率 %s" % (("%.1f%%" % (100 * s["hit_rate_given_touch"]))
                                      if s["hit_rate_given_touch"] is not None else "NA"))
        print("   均T收益(保守) %s | (乐观) %s | EV/信号(保守) %s"
              % (("%.3f%%" % (100 * s["mean_net_cons_given_touch"]))
                 if s["mean_net_cons_given_touch"] is not None else "NA",
                 ("%.3f%%" % (100 * s["mean_net_opt_given_touch"]))
                 if s["mean_net_opt_given_touch"] is not None else "NA",
                 ("%.3f%%" % (100 * s["ev_per_signal_cons"]))
                 if s["ev_per_signal_cons"] is not None else "NA"))

    tests = {}
    for label, ka, kb in (
            ("A_vs_B", grpA, grpB), ("A_vs_C", grpA, grpC)):
        tests[label] = {
            "touch": cluster_t(ka, kb, lambda r: 1.0 if r["touched"] else 0.0),
            "hit": cluster_t(ka, kb, lambda r: (1.0 if r["hit"] else 0.0)
                             if r["touched"] else None),   # 达标率条件于触线
            "net_cons": cluster_t(ka, kb, lambda r: r["net_cons"]),
            "net_opt": cluster_t(ka, kb, lambda r: r["net_opt"]),
        }
        for m, res in tests[label].items():
            print("\n[%s] %s: 日数=%d 均日差=%s t=%s p=%s A>B日占比=%s"
                  % (label, m, res["n_days"],
                     ("%.5f" % res["mean_diff"]) if res["mean_diff"] is not None else "NA",
                     ("%.2f" % res["t"]) if res["t"] is not None else "NA",
                     ("%.4f" % res["p"]) if res["p"] is not None else "NA",
                     ("%.0f%%" % (100 * res["pct_days_a_gt_b"]))
                     if res["pct_days_a_gt_b"] is not None else "NA"))

    # 方向一致率(采纳证据门槛之一): 触线率/达标率/均T收益(保守) 三指标 A>B
    dirs = []
    for m in ("touch_rate", "hit_rate_given_touch", "mean_net_cons_given_touch"):
        a, b = stats["A"][m], stats["B"][m]
        if a is not None and b is not None:
            dirs.append(a > b)
    consist = (sum(dirs) / float(len(dirs))) if dirs else None

    result = {
        "generated": datetime.date.today().isoformat(),
        "universe_source": uni_src, "universe_n": len(uni), "syms_used": syms_used,
        "gap_counts": dict(gaps), "params": {"N_BARS": N_BARS, "MIN_HIST": MIN_HIST,
                                             "LOOKBACK": LOOKBACK, "DIP": DIP, "TGT": TGT,
                                             "STOP": STOP, "COST": COST,
                                             "VOL_MULT": VOL_MULT},
        "stats": stats, "tests": tests,
        "direction_consistency_3metrics": consist,
        "sample_gate": {"A_n": len(grpA), "B_n": len(grpB),
                        "enough_for_test": len(grpA) >= MIN_GROUP_N
                        and len(grpB) >= MIN_GROUP_N,
                        "enough_for_evidence": len(grpA) >= 10 and len(grpB) >= 10},
        "publish_gate": publish_gate.judge_diff_family([
            {"name": "A_vs_B.touch", "diffs": tests["A_vs_B"]["touch"]["diffs"],
             "expected_sign": 1},
            {"name": "A_vs_B.hit", "diffs": tests["A_vs_B"]["hit"]["diffs"],
             "expected_sign": 1},
            {"name": "A_vs_B.net_cons", "diffs": tests["A_vs_B"]["net_cons"]["diffs"],
             "expected_sign": 1},
        ]),
    }
    json.dump(result, open(RESULT_FP, "w"), ensure_ascii=False, indent=1)
    print("\n结果已写 %s" % RESULT_FP)


if __name__ == "__main__":
    main()
