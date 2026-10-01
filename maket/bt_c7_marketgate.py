# -*- coding: utf-8 -*-
"""R5 紧急任务 C7：市场级门控检验——上证指数 9:45 时点指标能否改善纸面做T系统。

背景: 2026-09-24 上证 -1.22% 系统性下跌日, 做T系统全池5票接刀, 单日 -7.73%
      （历史最差）, 账户首次跌破100万。系统是纯日内多头回归策略, 无市场级门控。
      本脚本回答: 一个基于上证指数的门控能否显著改善历史表现?

预注册设计（事前设定, 非扫描最优）:
  基线重放: 与 bt_c5_entrytest.py 的 V0_poll 口径逐 bit 一致——
            开盘锚网格 d=0.6% (买入线=开盘×0.994, 目标=开盘×1.006),
            止损=入场价×0.985 (m5 收盘价判定, 60秒轮询口径),
            14:50 起强平, 回合成本 0.22% (双边0.12%+滑点0.1%),
            入场窗口 09:45 起, 每票每日最多1回合, 入场bar次bar起判离场。
            复用 C5.load_days()/C5.replay(), 不重写重放引擎。
  日PnL:    每个交易日 = 池内全部票当日回合 pnl%(含成本) 之和 (单位 pp, 非资金加权)。
  指数指标:  全部为 9:45 时点可观测——
            g1 = 上证当日开盘较昨收缺口% (只需日线, 免费源m5上限前的日子也从日线补算)
            g2 = 上证 9:45 时点价较昨收涨跌% (用 9:45 及之前 bar 收盘, 缺bar则取此前最近bar;
                 缺m5的日子为 None, 该类门控当日不触发——口径偏保守)
            g3 = 上证 9:45 时点较高开已下跌% = (开盘-9:45价)/开盘
  门控变体 (触发=当日停止开新仓; 已有持仓照常管理到强平/止损。V0 口径下
  09:45 前不可能有持仓, 故触发日 PnL 恒为 0):
    G-a: g2 <= -0.5% 不做
    G-b: g2 <= -1.0% 不做
    G-c: g2 <= -0.5% 且 g3 >= 0.3% (双确认: 低开且较高开已续跌) 不做
    G-d: g1 <= -1.0% (跳空急杀) 不做
  关键指标: 门控净改善 = 门控后总PnL - 基线总PnL (正值=改善;
            等价于 被跳过日基线合计PnL 的相反数), 及 IS/OOS 是否同向。
  IS/OOS:   前 60 个交易日为 IS (至 cut), 其余 OOS, 与 bt_c5/bt_model_r1 一致。

诚实性（详见报告）:
  - 触发天数 < 10 → 明确声明"样本不足, 无法按协议采纳", 并给补样本方案。
  - 阈值 -0.5%/-1.0% 是事前设定的合理值而非扫描最优; 附加的阈值扫描仅作
    稳健性描述, 是曲线拟合风险, 只给"稳健区间"不给单点最优。
  - 9:45 时点判定在 60 秒轮询下的可实现性在报告中单独讨论。

数据: 股票 m5 = bt_cache_pooled.json + bt_cache_3y_300765.json (经 bt_hypo.load_universe);
      指数 sh000001 m5+日线 自行抓取 (ifzq.gtimg.cn, 失败回退 web.ifzq.gtimg.cn,
      每请求间隔≥0.15秒), 缓存 bt_cache_index_m5.json (--refresh 强制重抓)。
      指数 m5 同样跑不变式自验 low<=min(o,c)<=max(o,c)<=high。

用法: python3 bt_c7_marketgate.py [--refresh]
"""
import os
import sys
import json
import time
import urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bt_hypo as H            # 只读复用: END_DATE / load_universe / in_sample_date
import bt_c5_entrytest as C5   # 只读复用: load_days / replay (V0_poll 口径逐 bit 一致)

INDEX_SYM = "sh000001"
INDEX_CACHE = os.path.join(HERE, "bt_cache_index_m5.json")
NEED_FROM = "20260310"         # 指数 m5 抓到该日之前为止（覆盖窗口首日 20260316 与其昨收日）

# 事前设定的门控阈值（合理值, 非扫描最优; 扫描仅作稳健性描述, 见 scan_thresholds）
GATES = [
    ("G-a", "g2<=-0.5 (9:45上证较昨收-0.5%)",
     lambda g: g["g2"] <= -0.5),
    ("G-b", "g2<=-1.0 (9:45上证较昨收-1.0%)",
     lambda g: g["g2"] <= -1.0),
    ("G-c", "g2<=-0.5 且 g3>=0.3 (双确认: 低开且较高开已续跌0.3%)",
     lambda g: g["g2"] <= -0.5 and g["g3"] >= 0.3),
    ("G-d", "g1<=-1.0 (跳空急杀: 开盘缺口-1%)",
     lambda g: g["g1"] <= -1.0),
]
MIN_TRIGGER_DAYS = 10          # 协议采纳门槛: 触发天数下限


# ---------- 指数数据抓取 ----------
def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _get_json_two_hosts(path):
    """主源 ifzq.gtimg.cn, 失败回退 web.ifzq.gtimg.cn 同路径。"""
    last = None
    for host in ("ifzq.gtimg.cn", "web.ifzq.gtimg.cn"):
        try:
            return json.loads(http_get("https://%s%s" % (host, path)))
        except Exception as e:            # 网络异常换源
            last = e
            time.sleep(1.0)
    raise RuntimeError("两个行情源都失败: %r" % (last,))


def fetch_index_m5(sym=INDEX_SYM, need_from=NEED_FROM):
    """翻页抓 m5 到 need_from 之前（参考 fetch_pooled_m5.py 模式, 间隔0.2s≥0.15s）。"""
    allb, anchor = {}, ""
    for page in range(60):
        d = _get_json_two_hosts("/appstock/app/kline/mkline?param=%s,m5,%s,320"
                                % (sym, anchor))
        bars = (d.get("data", {}).get(sym, {}) or {}).get("m5") or []
        if not bars:
            break
        new = 0
        for b in bars:
            if b[0] not in allb:
                new += 1
            allb[b[0]] = [b[0], float(b[1]), float(b[2]), float(b[3]),
                          float(b[4]), float(b[5])]
        anchor = min(b[0] for b in bars)
        print("    m5 page%d: %d根 锚=%s 累计%d" % (page, len(bars), anchor, len(allb)))
        if anchor[:8] < need_from or new == 0:
            break
        time.sleep(0.2)                   # ≥0.15s
    return [allb[k] for k in sorted(allb)]


def fetch_index_daily(sym=INDEX_SYM, n=320):
    """抓日线（算昨收/前一日跌幅）。返回 [[date8,open,close,high,low], ...] 升序。"""
    d = _get_json_two_hosts("/appstock/app/kline/kline?param=%s,day,,,%d" % (sym, n))
    node = d.get("data", {}).get(sym, {}) or {}
    bars = node.get("day") or node.get("qfqday") or []
    out = [[b[0].replace("-", ""), float(b[1]), float(b[2]), float(b[3]), float(b[4])]
           for b in bars]
    return sorted(out, key=lambda x: x[0])


def load_or_fetch_index(refresh=False):
    if os.path.exists(INDEX_CACHE) and not refresh:
        with open(INDEX_CACHE) as f:
            c = json.load(f)
        print("指数缓存: %s (fetched_at=%s, m5=%d根, day=%d根)"
              % (INDEX_CACHE, c.get("fetched_at"), len(c["m5"]), len(c["day"])))
        return c["m5"], c["day"]
    print("抓取指数 %s (m5 翻页 + 日线)..." % INDEX_SYM)
    m5 = fetch_index_m5()
    time.sleep(0.2)
    day = fetch_index_daily()
    cache = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sym": INDEX_SYM,
             "m5": m5, "day": day}
    with open(INDEX_CACHE, "w") as f:
        json.dump(cache, f)
    print("已存 %s (m5=%d根 %s~%s, day=%d根 %s~%s)"
          % (INDEX_CACHE, len(m5), m5[0][0][:8] if m5 else "-", m5[-1][0][:8] if m5 else "-",
             len(day), day[0][0] if day else "-", day[-1][0] if day else "-"))
    return m5, day


# ---------- 指标计算 ----------
def index_invariant_check(m5):
    """指数 m5 同样自验字段序 [t,open,close,high,low,vol]。"""
    ok = bad = 0
    for b in m5:
        o, c, h_, l_ = b[1], b[2], b[3], b[4]
        if l_ <= min(o, c) <= max(o, c) <= h_:
            ok += 1
        else:
            bad += 1
    ratio = ok / max(1, ok + bad)
    print("指数m5自验: low<=min(o,c)<=max(o,c)<=high 占比 %.5f%% (ok=%d bad=%d) %s"
          % (ratio * 100, ok, bad, "OK" if ratio >= 0.99 else "!! <99%"))
    if ratio < 0.99:
        sys.exit(2)


def build_index_metrics(m5, day, start_date, end_date):
    """逐日指标 {date8: {g1,g2,g3,open,prev_close,p945,prev_ret}}; 全部9:45时点可观测。
    g1/prev_ret 只需日线, 对缺 m5 的日子也从日线补算 (g2/g3 置 None, 不可门控)。"""
    day_map = {r[0]: r for r in day}                       # date8 -> [d,o,c,h,l]

    by_day = defaultdict(list)
    for b in m5:
        by_day[b[0][:8]].append(b)
    for k in by_day:
        by_day[k].sort(key=lambda b: b[0])

    met, open_diffs = {}, []
    for i, r in enumerate(day):
        d8 = r[0]
        if d8 < start_date or d8 > end_date or i < 1:
            continue
        pc = day[i - 1][2]                                 # 昨收(日线)
        g1 = (r[1] / pc - 1.0) * 100.0
        prev_ret = (pc / day[i - 2][2] - 1.0) * 100.0 if i >= 2 else None
        bars = by_day.get(d8)
        if bars:
            cand = [b for b in bars if b[0][8:] <= "0945"]  # 9:45及之前的bar
            p945 = cand[-1][2] if cand else bars[0][1]      # 缺bar回退: 首bar收盘(即开盘)
            g2 = (p945 / pc - 1.0) * 100.0
            g3 = (r[1] - p945) / r[1] * 100.0
            open_diffs.append(abs(r[1] - bars[0][1]))       # m5首bar开盘 vs 官方开盘
        else:                                               # 免费源m5上限, 当日无9:45价
            p945 = g2 = g3 = None
        met[d8] = {"g1": g1, "g2": g2, "g3": g3,
                   "open": r[1], "prev_close": pc, "p945": p945,
                   "prev_ret": prev_ret, "has_early_m5": bars is not None}
    if open_diffs:
        print("开盘交叉核对: m5首bar开盘 vs 日线官方开盘 最大差 %.4f 点 (%d天)"
              % (max(open_diffs), len(open_diffs)))
    return met


# ---------- 基线重放 ----------
def baseline_replay(days_by_sym, syms):
    """V0_poll 逐票逐日重放; 返回 (day_pnl{d:pp合计}, day_rounds{d:n}, rounds明细)。"""
    day_pnl, day_rounds = defaultdict(float), defaultdict(int)
    rounds = []
    for sym in sorted(syms):
        for d in sorted(days_by_sym[sym]):
            r, _fr, _diag = C5.replay(days_by_sym[sym][d], "V0_poll")
            if r:
                r["sym"], r["date"] = sym, d
                r["pnl"] = (r["exit"] / r["entry"] - 1.0 - C5.COST) * 100.0
                day_pnl[d] += r["pnl"]
                day_rounds[d] += 1
                rounds.append(r)
    return dict(day_pnl), dict(day_rounds), rounds


def crosscheck_vs_c5(rounds, cal, cut):
    """复现校验: 与 bt_c5_entrytest_result.json 的 V0 池化 IS/OOS 合计对账。"""
    fp = os.path.join(HERE, "bt_c5_entrytest_result.json")
    if not os.path.exists(fp):
        print("!! 未找到 bt_c5_entrytest_result.json, 跳过对账")
        return None
    ref = json.load(open(fp))["sets"]["池化13只"]
    out = {}
    for ph, sel in (("IS", lambda d: d <= cut), ("OOS", lambda d: d > cut)):
        mine = sum(r["pnl"] for r in rounds if sel(r["date"]))
        n_mine = sum(1 for r in rounds if sel(r["date"]))
        refv = ref[ph]["V0_poll"]
        match = (abs(mine - refv["total"]) < 1e-6) and (n_mine == refv["n"])
        out[ph] = {"my_total": mine, "my_n": n_mine,
                   "c5_total": refv["total"], "c5_n": refv["n"], "match": match}
        print("对账[%s] 本脚本合计%+.6f%%/%d笔 vs C5结果 %+.6f%%/%d笔 → %s"
              % (ph, mine, n_mine, refv["total"], refv["n"],
                 "一致✓" if match else "!!不一致, 中止"))
        if not match:
            sys.exit(3)
    return out


# ---------- 门控统计 ----------
def eval_gate(name, desc, cond, day_pnl, day_rounds, met, cal, cut):
    trig, no_early = [], 0
    for d in cal:
        m = met.get(d)
        if m is None:
            continue
        if m.get("g2") is None:
            no_early += 1                        # 缺9:45数据, g2/g3类门控无法判定(不触发)
        try:
            if cond(m):
                trig.append(d)
        except TypeError:                        # 指标为None(如g2缺) → 不触发
            pass
    skipped_sum = sum(day_pnl.get(d, 0.0) for d in trig)
    retained_sum = sum(v for d, v in day_pnl.items() if d not in trig)
    base_total = sum(day_pnl.get(d, 0.0) for d in cal)
    gated_total = base_total - skipped_sum              # 触发日回合清零
    net = gated_total - base_total                      # 净改善(门控后-基线, 正=改善)
    out = {"name": name, "desc": desc,
           "trigger_days": len(trig), "trigger_dates": trig,
           "skipped_sum": skipped_sum,                  # 被跳过日"若不门控"合计PnL
           "saved": -skipped_sum,                       # 门控省下的钱(被跳过日亏损的相反数)
           "retained_sum": retained_sum,
           "baseline_total": base_total, "gated_total": gated_total,
           "net_improvement": net,                      # 门控后 - 基线 (正=改善)
           "missed_profit": sum(day_pnl.get(d, 0.0) for d in trig if day_pnl.get(d, 0.0) > 0),
           "avoided_loss": sum(day_pnl.get(d, 0.0) for d in trig if day_pnl.get(d, 0.0) <= 0),
           "days_no_early_data": no_early}
    for ph, sel in (("IS", lambda d: d <= cut), ("OOS", lambda d: d > cut)):
        t = [d for d in trig if sel(d)]
        s = sum(day_pnl.get(d, 0.0) for d in t)
        b = sum(v for d, v in day_pnl.items() if sel(d))
        out[ph] = {"trigger_days": len(t), "skipped_sum": s, "baseline_total": b,
                   "gated_total": b - s, "net_improvement": (b - s) - b}
    out["same_direction"] = (
        "同向(两段均改善)" if out["IS"]["net_improvement"] > 0 and out["OOS"]["net_improvement"] > 0
        else "同向(两段均变差)" if out["IS"]["net_improvement"] < 0 and out["OOS"]["net_improvement"] < 0
        else "不同向(IS/OOS变号)")
    # 最差单日
    act = [d for d in cal if d in day_pnl]
    bw = min(act, key=lambda d: day_pnl[d])
    ret_act = [d for d in act if d not in trig]
    gw = min(ret_act, key=lambda d: day_pnl[d]) if ret_act else None
    out["baseline_worst"] = {"date": bw, "pnl": day_pnl[bw]}
    out["gated_worst"] = ({"date": gw, "pnl": day_pnl[gw]} if gw else None)
    out["worst_improvement"] = (day_pnl[gw] - day_pnl[bw]) if gw else 0.0
    out["worst_day_gated"] = bw in trig
    out["trigger_detail"] = [{"date": d, "g1": round(met[d]["g1"], 3),
                              "g2": round(met[d]["g2"], 3) if met[d]["g2"] is not None else None,
                              "g3": round(met[d]["g3"], 3) if met[d]["g3"] is not None else None,
                              "day_pnl": round(day_pnl.get(d, 0.0), 3),
                              "rounds": day_rounds.get(d, 0)} for d in trig]
    return out


# ---------- 阈值扫描（稳健性描述, 曲线拟合风险, 只看区间） ----------
def scan_thresholds(day_pnl, met, cal, cut, key, ths):
    rows = []
    for t in ths:
        trig = []
        for d in cal:
            v = met.get(d, {}).get(key)
            if v is not None and v <= t:
                trig.append(d)
        s = sum(day_pnl.get(d, 0.0) for d in trig)
        b = sum(day_pnl.get(d, 0.0) for d in cal)
        net = -s
        s_is = sum(day_pnl.get(d, 0.0) for d in trig if d <= cut)
        s_oos = sum(day_pnl.get(d, 0.0) for d in trig if d > cut)
        rows.append({"threshold": t, "trigger_days": len(trig),
                     "net_improvement": net, "net_IS": -s_is, "net_OOS": -s_oos,
                     "both_pos": (-s_is > 0) and (-s_oos > 0)})
    return rows


def robust_range(rows):
    """扫描行中 IS/OOS 净改善同时为正的阈值集合（如不连续, 列出各段）。"""
    pos = [r["threshold"] for r in rows if r["both_pos"]]
    if not pos:
        return None
    segs, cur = [], [pos[0]]
    for t in pos[1:]:
        if abs(t - cur[-1]) < 1e-9 + (ths_step(rows)):
            cur.append(t)
        else:
            segs.append(cur)
            cur = [t]
    segs.append(cur)
    return [{"from": s[0], "to": s[-1], "n": len(s)} for s in segs]


def ths_step(rows):
    ds = [abs(rows[i + 1]["threshold"] - rows[i]["threshold"])
          for i in range(len(rows) - 1) if abs(rows[i + 1]["threshold"] - rows[i]["threshold"]) > 1e-12]
    return min(ds) if ds else 0.1


# ---------- 反弹桶（防一刀切: 昨日急跌, 次日做T是否反而为正） ----------
def rebound_bucket(day_pnl, met, cal):
    bucket, rest = [], []
    for d in cal:
        pr = met.get(d, {}).get("prev_ret")
        (bucket if (pr is not None and pr <= -1.0) else rest).append(d)
    def stat(ds):
        pn = [day_pnl.get(d, 0.0) for d in ds]
        return {"n": len(ds), "sum": sum(pn),
                "mean": (sum(pn) / len(pn)) if pn else None,
                "win_days": sum(1 for x in pn if x > 0),
                "dates": ds}
    return {"bucket_prev_ret<=-1%": stat(bucket), "rest": stat(rest),
            "bucket_sum": stat(bucket)["sum"], "bucket_n": stat(bucket)["n"],
            "bucket_dates": bucket, "bucket_detail": [
                {"date": d, "prev_ret": round(met[d]["prev_ret"], 3),
                 "g1": round(met[d]["g1"], 3),
                 "g2": round(met[d]["g2"], 3) if met[d]["g2"] is not None else None,
                 "day_pnl": round(day_pnl.get(d, 0.0), 3)} for d in bucket]}


def main():
    refresh = "--refresh" in sys.argv
    print("=" * 76)
    print("===== bt_c7 市场级门控检验 (基线=bt_c5 V0_poll 口径, 窗口 %s~%s) ====="
          % (C5.START_DATE, C5.END_DATE))

    # 1) 股票数据与票池（与 C5 完全一致）
    days_by_sym, names = C5.load_days()
    n_days = {s: len(days_by_sym[s]) for s in days_by_sym}
    pool_syms = sorted(s for s in days_by_sym if n_days[s] >= C5.POOL_MIN_DAYS)
    print("票池: %d只 (有效日>=%d); 剔除: %s"
          % (len(pool_syms), C5.POOL_MIN_DAYS,
             ",".join("%s(%d)" % (s, n_days[s]) for s in days_by_sym if s not in pool_syms) or "无"))
    cal = sorted({d for s in pool_syms for d in days_by_sym[s]})
    cut = cal[C5.IS_DAYS - 1] if len(cal) >= C5.IS_DAYS else cal[-1]
    print("交易日历: %d天, IS前%d天(至%s), OOS %d天(%s~)"
          % (len(cal), C5.IS_DAYS, cut, len(cal) - C5.IS_DAYS, cal[C5.IS_DAYS]))

    # 2) 指数数据
    print("\n-- 指数数据 --")
    m5, day = load_or_fetch_index(refresh)
    index_invariant_check(m5)
    met = build_index_metrics(m5, day, C5.START_DATE, C5.END_DATE)
    miss_early = [d for d in cal if not met.get(d, {}).get("has_early_m5")]
    print("窗口内指数指标: g1可算 %d/%d 天; g2/g3可算 %d/%d 天 (缺9:45数据: %s)"
          % (len(met), len(cal), len(cal) - len(miss_early), len(cal),
             ",".join(miss_early) if miss_early else "无"))

    # 3) 基线重放 + 对账
    print("\n-- 基线重放 (V0_poll, 与bt_c5口径一致) --")
    day_pnl, day_rounds, rounds = baseline_replay(days_by_sym, pool_syms)
    print("回合 %d 笔, 基线日PnL合计 %+.2fpp, 最差日 %s (%+.2fpp), 最佳日 %s (%+.2fpp)"
          % (len(rounds), sum(day_pnl.values()),
             min(day_pnl, key=day_pnl.get), day_pnl[min(day_pnl, key=day_pnl.get)],
             max(day_pnl, key=day_pnl.get), day_pnl[max(day_pnl, key=day_pnl.get)]))
    ck = crosscheck_vs_c5(rounds, cal, cut)

    base_total = sum(day_pnl.get(d, 0.0) for d in cal)
    base_is = sum(v for d, v in day_pnl.items() if d <= cut)
    base_oos = sum(v for d, v in day_pnl.items() if d > cut)
    bw_date = min(day_pnl, key=day_pnl.get)

    # 4) 门控
    print("\n" + "=" * 76)
    print("===== 门控变体 (净改善=门控后-基线, 正=改善; 单位pp) =====")
    print("基线: 总 %+8.2f | IS %+8.2f | OOS %+8.2f | 最差日 %s %+8.2f"
          % (base_total, base_is, base_oos, bw_date, day_pnl[bw_date]))
    gate_results = []
    for name, desc, cond in GATES:
        g = eval_gate(name, desc, cond, day_pnl, day_rounds, met, cal, cut)
        gate_results.append(g)
        print("\n[%s] %s" % (name, desc))
        print("  触发 %d 天 (IS %d / OOS %d)%s | 缺9:45数据日(无法判定) %d"
              % (g["trigger_days"], g["IS"]["trigger_days"], g["OOS"]["trigger_days"],
                 "  <<样本不足(<%d天), 无法按协议采纳>>" % MIN_TRIGGER_DAYS
                 if g["trigger_days"] < MIN_TRIGGER_DAYS else "",
                 g["days_no_early_data"]))
        print("  被跳过日合计(若不门控) %+8.2f → 省下的钱 %+8.2f | 保留日合计 %+8.2f"
              % (g["skipped_sum"], g["saved"], g["retained_sum"]))
        print("  门控后总PnL %+8.2f vs 基线 %+8.2f | 净改善 %+8.2f (IS %+6.2f / OOS %+6.2f, %s)"
              % (g["gated_total"], g["baseline_total"], g["net_improvement"],
                 g["IS"]["net_improvement"], g["OOS"]["net_improvement"],
                 g["same_direction"]))
        print("  触发日机会成本: 其中盈利日合计 %+6.2f, 亏损日合计 %+6.2f (被免)"
              % (g["missed_profit"], g["avoided_loss"]))
        print("  最差单日: 基线 %s %+6.2f → 门控后 %s %+6.2f (改善 %+6.2f)%s"
              % (g["baseline_worst"]["date"], g["baseline_worst"]["pnl"],
                 g["gated_worst"]["date"] if g["gated_worst"] else "-",
                 g["gated_worst"]["pnl"] if g["gated_worst"] else 0.0,
                 g["worst_improvement"],
                 " | 基线最差日被本门控触发" if g["worst_day_gated"] else ""))
        if g["trigger_dates"]:
            print("  触发日明细: " + " ".join(
                "%s(g2=%s,pnl%+.2f)" % (t["date"],
                                        "%+.2f" % t["g2"] if t["g2"] is not None else "NA",
                                        t["day_pnl"])
                for t in g["trigger_detail"]))

    # 5) 反弹桶
    print("\n" + "=" * 76)
    print("===== 反弹桶检验 (上证昨日跌幅<=-1%的次日, 做T是否反而为正) =====")
    rb = rebound_bucket(day_pnl, met, cal)
    b, r = rb["bucket_prev_ret<=-1%"], rb["rest"]
    print("  桶内: %d天 合计%+.2fpp 均值%+.3fpp 日胜率%d/%d | 桶外: %d天 合计%+.2fpp 均值%+.3fpp"
          % (b["n"], b["sum"], b["mean"] or 0.0, b["win_days"], b["n"],
             r["n"], r["sum"], r["mean"] or 0.0))
    print("  桶内日期: " + (" ".join("%s(pret%+.2f%%,pnl%+.2f)" %
          (x["date"], x["prev_ret"], x["day_pnl"]) for x in rb["bucket_detail"]) or "无"))
    if b["n"] < MIN_TRIGGER_DAYS:
        print("  <<反弹桶样本不足(<%d天), 只作方向性描述, 不构成结论>>" % MIN_TRIGGER_DAYS)

    # 6) 阈值扫描（稳健性描述, 曲线拟合风险）
    print("\n===== 阈值扫描 (描述性; 事前阈值才可用于决策, 扫描=曲线拟合风险) =====")
    ths = [-(x / 10.0) for x in range(20, 2, -1)]          # -2.0 .. -0.3
    scan_g2 = scan_thresholds(day_pnl, met, cal, cut, "g2", ths)
    scan_g1 = scan_thresholds(day_pnl, met, cal, cut, "g1", ths)
    rr_g2, rr_g1 = robust_range(scan_g2), robust_range(scan_g1)
    print("  g2<=t (G-a/G-b族): IS/OOS同正的稳健区间: %s"
          % (", ".join("[%+.1f, %+.1f]" % (s["from"], s["to"]) for s in (rr_g2 or [])) or "无"))
    print("  g1<=t (G-d族):     IS/OOS同正的稳健区间: %s"
          % (", ".join("[%+.1f, %+.1f]" % (s["from"], s["to"]) for s in (rr_g1 or [])) or "无"))
    for r_ in scan_g2:
        print("    g2<=%+.1f 触发%3d天 净改善%+8.2f (IS %+7.2f / OOS %+7.2f)%s"
              % (r_["threshold"], r_["trigger_days"], r_["net_improvement"],
                 r_["net_IS"], r_["net_OOS"], "  ←双正" if r_["both_pos"] else ""))

    # 7) 今日(窗口外)情境核对: 教训日按各门控是否触发
    ctx = {}
    last_d8 = max(d8 for d8 in (b[0][:8] for b in m5))
    if last_d8 in {r[0] for r in day} and last_d8 > C5.END_DATE:
        pc = [r[2] for r in day if r[0] < last_d8][-1]
        dly = {r[0]: r for r in day}[last_d8]
        bars = [b for b in m5 if b[0][:8] == last_d8]
        p945 = ([b for b in bars if b[0][8:] <= "0945"] or [bars[0]])[-1][2]
        g1 = (dly[1] / pc - 1) * 100
        g2 = (p945 / pc - 1) * 100
        g3 = (dly[1] - p945) / dly[1] * 100
        ctx = {"date": last_d8, "g1": g1, "g2": g2, "g3": g3,
               "index_close_ret": (dly[2] / pc - 1) * 100,
               "gates_triggered": [n for n, _d, c in GATES if c({"g1": g1, "g2": g2, "g3": g3})]}
        print("\n[情境核对-窗口外] %s: 上证收跌 %+.2f%% | g1=%+.2f g2=%+.2f g3=%+.2f → 触发: %s"
              % (last_d8, ctx["index_close_ret"], g1, g2, g3,
                 ",".join(ctx["gates_triggered"]) or "无"))
    print("\n结果已存 bt_c7_marketgate_result.json")

    # ---------- 结果 JSON ----------
    result = {
        "params": {"D": C5.D, "STOP_PCT": C5.STOP_PCT, "COST": C5.COST,
                   "IS_DAYS": C5.IS_DAYS, "START": C5.START_DATE, "END": C5.END_DATE,
                   "cut": cut, "pool_syms": pool_syms, "calendar_days": len(cal),
                   "gates": [{"name": n, "desc": d} for n, d, _ in GATES],
                   "min_trigger_days": MIN_TRIGGER_DAYS,
                   "net_improvement_def": "门控后总PnL - 基线总PnL (正=改善); "
                                          "等价于被跳过日基线合计PnL的相反数"},
        "baseline_check_vs_c5": ck,
        "baseline": {"total": base_total, "IS": base_is, "OOS": base_oos,
                     "rounds": len(rounds),
                     "worst_day": {"date": bw_date, "pnl": day_pnl[bw_date]},
                     "best_day": {"date": max(day_pnl, key=day_pnl.get),
                                  "pnl": day_pnl[max(day_pnl, key=day_pnl.get)]},
                     "day_pnl": {d: round(day_pnl[d], 4) for d in sorted(day_pnl)},
                     "day_rounds": {d: day_rounds[d] for d in sorted(day_rounds)}},
        "index_metrics": {d: {k: round(v, 4) if isinstance(v, float) else v
                              for k, v in met[d].items()} for d in sorted(met)},
        "gates": gate_results,
        "rebound_bucket": rb,
        "scan": {"g2": scan_g2, "g1": scan_g1,
                 "robust_range_g2": rr_g2, "robust_range_g1": rr_g1,
                 "note": "描述性扫描=曲线拟合风险; 只看区间, 不给单点最优"},
        "context_out_of_window": ctx,
    }
    with open(os.path.join(HERE, "bt_c7_marketgate_result.json"), "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, default=float)

    # ---------- 诚实性打印 ----------
    print("\n诚实性提示:")
    print(" 1) 触发天数<%d的门控: 样本不足, 无法按协议采纳 (补样本方案见报告)。"
          % MIN_TRIGGER_DAYS)
    print(" 2) 阈值-0.5/-1.0为事前设定; 报告中的扫描只是稳健性区间描述, 有曲线拟合风险。")
    print(" 3) V0口径下09:45前不可能有持仓, 触发日PnL恒为0; 若未来引入09:35入场,")
    print("    需改为'9:45时已有持仓照常管理'的口径, 本结论不自动外推。")
    print(" 4) 本窗口(20260316~0911)上证整体上行(牛市回调少), 系统性急跌日本身稀缺,")
    print("    门控改善的天花板受样本量限制; 2026-09-24教训日在窗口之外, 仅作情境核对。")
    print(" 5) 免费源指数m5上限为20260324: 窗口头6天(0316~0323)无9:45数据, g2/g3类门控")
    print("    对其不触发(偏保守); 其中20260323低开-1.32%且收跌-3.63%, 大概率本会触发")
    print("    G-a(当日PnL -15.82pp), 若计入则G-a净改善更大——即G-a数字偏保守。")
    print("    g1类门控(G-d)对这6天仍可判定(日线补算)。")


if __name__ == "__main__":
    main()
