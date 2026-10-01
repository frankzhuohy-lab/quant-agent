#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纸面交易系统 —— 冠军模型「上行波动占比门控」(R443)
支持三种时点：开盘执行(9:31) / 盘中监控 / 收盘结算(15:10)
设计原则：全量重放（无状态机漂移），信号/执行逻辑与回测 quant_iter44 完全一致

EXP_0003 部署形态（2026-09-30 qsys ACCEPT，写入每日信号链）：
  H2 趋势门控 —— 仅等权指数 > MA60×1.01 且 mom20 > +3%（上涨市）才开新仓；
  H1 敞口上限 —— 现金约束 exposure_cap=1.0，持仓权重合计不得超 100%（消除原
  回测 ~200% 隐含杠杆）。OOS 证据：回撤 -23.7%→-10.5%，年化 220%→67%（去杠杆后
  真实可复制口径），Sharpe 1.84、成本×2 压力下仍 1.74。
  回退开关：环境变量 EXP0003=0 恢复旧逻辑（仅排障用，日常勿关）。
"""
from __future__ import print_function
import sys, os, json, math
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from urllib.request import urlopen, Request
except Exception:
    from urllib2 import urlopen, Request

import quant_iter26 as q26
import quant_iter44 as q44

# ---------------- 冻结参数（与回测完全一致，不得修改） ----------------
Q26_COST = q26.COST          # 0.002 单边
KELT = {"keltner_mult": 1.5, "max_hold": 10}
R316_D = -0.08
R316_RSI = 58.0
R316_FLR = 0.3
R443_UV = 0.8
K = 3
ALLOW_FEWER = True

QMAP = {
    # 原池：封测/设计/设备龙头
    "002156.SZ": "sz002156", "002185.SZ": "sz002185", "002371.SZ": "sz002371",
    "600584.SH": "sh600584", "603986.SH": "sh603986", "688012.SH": "sh688012",
    "688041.SH": "sh688041", "688126.SH": "sh688126", "688256.SH": "sh688256",
    "688981.SH": "sh688981",
    # 扩展池：半导体材料/零部件/气体/光刻胶
    "300346.SZ": "sz300346", "603650.SH": "sh603650", "688268.SH": "sh688268",
    "688019.SH": "sh688019", "300054.SZ": "sz300054", "300666.SZ": "sz300666",
    "002409.SZ": "sz002409", "603688.SH": "sh603688", "605358.SH": "sh605358",
    "605589.SH": "sh605589",
    # 扩展池：晶圆代工/封测/AI 芯片
    "688347.SH": "sh688347", "688072.SH": "sh688072", "002156.SZ_bak": "sz002156",
}
# 去重（002156 出现两次）
QMAP = {k: v for k, v in QMAP.items() if not k.endswith('_bak')}

# ---------------- EXP_0003 部署参数（qsys ACCEPT，勿随意改动） ----------------
# 回退开关：EXP0003=0 时恢复旧逻辑（无趋势门控、无敞口上限）
EXP0003_ON = os.environ.get("EXP0003", "1") != "0"
REGIME_MA = 60          # 指数均线窗口
REGIME_MA_BAND = 0.01   # 指数 > MA60×1.01
REGIME_MOM = 20         # 动量窗口
REGIME_MOM_MIN = 0.03   # mom20 > +3%
EXPOSURE_CAP = 1.0      # 现金约束：任意持仓日敞口合计 ≤ 100%

NAMES = {
    "002156.SZ": u"通富微电", "002185.SZ": u"华天科技", "002371.SZ": u"北方华创",
    "600584.SH": u"长电科技", "603986.SH": u"兆易创新", "688012.SH": u"中微公司",
    "688041.SH": u"海光信息", "688126.SH": u"沪硅产业", "688256.SH": u"寒武纪",
    "688981.SH": u"中芯国际",
    "300346.SZ": u"南大光电", "603650.SH": u"彤程新材", "688268.SH": u"华特气体",
    "688019.SH": u"安集科技", "300054.SZ": u"鼎龙股份", "300666.SZ": u"江丰电子",
    "002409.SZ": u"雅克科技", "603688.SH": u"石英股份", "605358.SH": u"立昂微",
    "605589.SH": u"圣泉集团",
    "688347.SH": u"华虹公司", "688072.SH": u"拓荆科技",
}
FETCH_DAYS = 420
PAPER_START = "2026-08-24"      # 纸面账本起点
STATE_FILE = os.path.join(HERE, "paper_state.json")


def http_get(url, timeout=25):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urlopen(req, timeout=timeout).read()


def fetch_kline(sym, n=FETCH_DAYS):
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           "?param=%s,day,,,%d,qfq" % (sym, n))
    d = json.loads(http_get(url).decode("utf-8", "ignore"))
    dd = d["data"][sym]
    return dd.get("qfqday") or dd.get("day")


def fetch_realtime():
    """实时行情（盘中/盘后快照）：返回 {code: {open,price,high,low,vol,date}}"""
    qs = ",".join(QMAP.values())
    raw = http_get("https://qt.gtimg.cn/q=" + qs).decode("gbk", "ignore")
    out = {}
    for line in raw.strip().split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        sym = key.split("_")[-1]
        f = val.strip('"').split("~")
        if len(f) < 35:
            continue
        code = next((c for c, s in QMAP.items() if s == sym), None)
        if not code:
            continue
        try:
            out[code] = {
                "price": float(f[3]), "prev_close": float(f[4]),
                "open": float(f[5]), "high": float(f[33]),
                "low": float(f[34]),
                "quote_date": f[30][:8] if len(f[30]) >= 8 else "",
            }
        except (ValueError, IndexError):
            continue
    return out


def load_clean(live_bar=None):
    """拉取全部股票日线。
    live_bar: {code: {...}} 若今日K线缺失但实时行情有效，注入合成今日bar"""
    stocks = {}
    today = datetime.now().strftime("%Y-%m-%d")
    for code, sym in QMAP.items():
        bars = fetch_kline(sym)
        rec = {"name": NAMES[code], "code": code, "dates": [], "open": [],
               "high": [], "low": [], "close": [], "vol": []}
        for b in bars:
            o, c, h, l = float(b[1]), float(b[2]), float(b[3]), float(b[4])
            v = float(b[5]) if len(b) > 5 else None
            if min(o, h, l, c) <= 0:
                continue
            rec["dates"].append(b[0]); rec["open"].append(o)
            rec["high"].append(h); rec["low"].append(l)
            rec["close"].append(c); rec["vol"].append(v)
        # 注入实时合成bar（今日K线未出但实时行情有效时）
        lb = (live_bar or {}).get(code)
        if (lb and lb.get("quote_date") == today.replace("-", "")
                and (not rec["dates"] or rec["dates"][-1] < today)
                and min(lb["open"], lb["price"]) > 0):
            rec["dates"].append(today)
            rec["open"].append(lb["open"])
            rec["high"].append(max(lb["high"], lb["open"], lb["price"]))
            rec["low"].append(min(lb["low"], lb["open"], lb["price"]))
            rec["close"].append(lb["price"])
            rec["vol"].append(None)
        stocks[code] = rec
    if len(stocks) < 5:
        raise SystemExit("股票数不足: %d" % len(stocks))
    common = None
    for code, rec in stocks.items():
        s = set(rec["dates"])
        common = s if common is None else (common & s)
    common = sorted(common)
    if len(common) < 120:
        raise SystemExit("交易日历过短: %d" % len(common))
    for code, rec in stocks.items():
        idx = {d: i for i, d in enumerate(rec["dates"])}
        for k in ("open", "high", "low", "close", "vol"):
            rec[k] = [rec[k][idx[d]] for d in common]
        rec["dates"] = list(common)
    return stocks, common


def regime_bull_days(stocks, codes, common):
    """EXP_0003 H2 趋势门控：返回可开仓日下标集合。

    条件（与 qsys/qanalyze.regime_labels 判定逐字一致，全因果无未来函数）：
    等权指数收盘 > MA60×1.01 且 mom20 > +3% 记为「上涨」可开仓日，
    其余（震荡/下跌）一律不开新仓；持仓的出场不受影响。
    """
    n = len(common)
    idx = [sum(stocks[c]["close"][t] for c in codes) / len(codes)
           for t in range(n)]
    ma60 = [None] * n
    for t in range(REGIME_MA, n):
        ma60[t] = sum(idx[t - REGIME_MA + 1:t + 1]) / float(REGIME_MA)
    mom20 = [None] * n
    for t in range(REGIME_MOM, n):
        mom20[t] = idx[t] / idx[t - REGIME_MOM] - 1.0
    bull = set()
    for t in range(n):
        if ma60[t] is not None and idx[t] > ma60[t] * (1.0 + REGIME_MA_BAND) \
                and (mom20[t] or 0) > REGIME_MOM_MIN:
            bull.add(t)
    return bull


def regime_diagnostics(stocks, codes, common, t):
    """t 日趋势门控诊断信息（写进报告，便于每日信号链可读）。"""
    n = len(common)
    if t < 0 or t >= n:
        return {}
    idx = sum(stocks[c]["close"][t] for c in codes) / len(codes)
    ma60 = mom20 = None
    if t >= REGIME_MA:
        ma60 = (sum(sum(stocks[c]["close"][t - REGIME_MA + 1:t + 1])
                    for c in codes)
                / len(codes) / float(REGIME_MA))
    if t >= REGIME_MOM:
        now = sum(stocks[c]["close"][t] for c in codes)
        past = sum(stocks[c]["close"][t - REGIME_MOM] for c in codes)
        mom20 = now / past - 1.0
    gate = (ma60 is not None and idx > ma60 * (1.0 + REGIME_MA_BAND)
            and (mom20 or 0) > REGIME_MOM_MIN)
    return {"gate_pass": gate,
            "idx_vs_ma60": (round(idx / ma60 - 1.0, 4) if ma60 else None),
            "mom20": (round(mom20, 4) if mom20 is not None else None)}


def build_spec():
    q44.R316_D = R316_D
    q44.R316_RSI = R316_RSI
    q44.R316_FLR = R316_FLR
    q44.R443_UV = R443_UV
    return {"score": q44.score_r117_upvar, "filt": q44.make_filt_flr("R316_FLR"),
            "exit": "keltner", "K": K, "allow_fewer": ALLOW_FEWER,
            "params": dict(KELT)}


def top_k(scores, k):
    items = [(c, v) for c, v in scores.items() if v > -9e8]
    items.sort(key=lambda x: (-x[1], x[0]))
    return [c for c, _ in items[:k]]


def replay(stocks, codes, feat, state, common, spec, entry_start_idx,
           data_end, today_finalized=True, clamp_end=None, entry_end=None,
           exposure_cap=None):
    """全量重放回测执行逻辑。
    data_end: 最新K线下标（盘中=今日未完成bar；收盘后=今日已完成bar）
    today_finalized: data_end 那根bar的收盘价是否已定型（收盘后为True）
    clamp_end: 历史验证用，强制 xe<=clamp_end（对齐回测窗口截断）
    entry_end: 入场日上限（历史验证用，回测为 n-1-H）
    exposure_cap: EXP_0003 H1 现金约束——入场日敞口合计+w 超过上限则跳过
    """
    K_ = spec["K"]
    score_fn = spec["score"]
    filt_fn = spec["filt"]
    allow_fewer = spec["allow_fewer"]
    params = spec["params"]
    kelt = params.get("keltner_mult", 1.5)
    max_hold = params.get("max_hold", 10)
    H = q26.H
    if entry_end is None:
        entry_end = data_end
    scan_last = data_end if today_finalized else data_end - 1
    closed, opens = [], []
    exposure = {}  # day -> 当日持仓权重合计（EXP_0003 H1）
    for e in range(entry_start_idx, entry_end + 1):
        t = e - 1
        if t < 0 or not filt_fn(state, t):
            continue
        picks = top_k(score_fn(feat, codes, t), K_)
        if len(picks) < K_ and not (allow_fewer and len(picks) >= 1):
            continue
        w = 1.0 / H / len(picks)
        for code in picks:
            o = stocks[code]["open"]
            c = stocks[code]["close"]
            entry_px = o[e]
            prev_close = c[e - 1]
            band = 0.195 if code.startswith(("688", "300", "301")) else 0.095
            if entry_px >= prev_close * (1.0 + band - 0.005):
                continue  # 开盘触涨停线 → 买不进（与回测一致）
            if exposure_cap is not None and exposure.get(e, 0.0) + w > exposure_cap:
                continue  # EXP_0003 H1: 现金约束，敞口≤100%
            xe = None
            for d in range(e, min(e + max_hold - 1, scan_last) + 1):
                m20 = feat[code]["ma20"][d]
                atr = feat[code]["atr20"][d]
                if (m20 is not None and atr is not None
                        and c[d] < m20 - kelt * atr):
                    xe = d + 1
                    break
            if xe is None:
                xe = e + max_hold
            if clamp_end is not None:
                xe = min(xe, clamp_end)
            # 登记持仓敞口（出场日前含当日；未平仓则计至 data_end）
            for d in range(e, min(xe, data_end + 1)):
                exposure[d] = exposure.get(d, 0.0) + w
            rec = {"code": code, "e": e, "xe": xe, "entry_px": entry_px,
                   "weight": w, "entry_date": common[e]}
            if xe <= data_end:
                exit_px = o[xe]
                gross = exit_px / entry_px - 1.0
                rec.update({"exit_px": exit_px, "gross": gross,
                            "net": gross - 2.0 * Q26_COST,
                            "exit_date": common[xe], "open": False})
                closed.append(rec)
            else:
                mark = c[data_end]
                rec.update({"mark": mark, "unreal": mark / entry_px - 1.0,
                            "open": True, "hold_days": data_end - e})
                opens.append(rec)
    return closed, opens


def nav_of(closed, opens):
    """近似净值：已平仓按净收益，未平仓按现价结算并扣双边成本（保守）"""
    nav = 1.0
    for t in closed:
        nav += t["weight"] * t["net"]
    for p in opens:
        nav += p["weight"] * (p["unreal"] - 2.0 * Q26_COST)
    return nav


def tomorrow_signal(stocks, codes, feat, state, common, spec, today_idx):
    """今日收盘的信号 → 明日开盘买入候选（仅收盘后有效）"""
    t = today_idx
    if not spec["filt"](state, t):
        return {"filter_pass": False, "picks": []}
    scores = spec["score"](feat, codes, t)
    picks = top_k(scores, spec["K"])
    out = []
    for c in picks:
        band = 0.195 if c.startswith(("688", "300", "301")) else 0.095
        out.append({"code": c, "name": NAMES.get(c, c), "score": scores[c],
                    "close": stocks[c]["close"][t],
                    "limit_up_threshold": round(
                        stocks[c]["close"][t] * (1 + band - 0.005), 3)})
    return {"filter_pass": True, "picks": out}


def exit_due_tomorrow(opens, feat, data_end, params):
    """持仓中明日开盘需卖出的（今日收盘触发Keltner 或 持满最长天数）"""
    kelt = params.get("keltner_mult", 1.5)
    max_hold = params.get("max_hold", 10)
    due = []
    for p in opens:
        code, e = p["code"], p["e"]
        m20 = feat[code]["ma20"][data_end]
        atr = feat[code]["atr20"][data_end]
        trig = (m20 is not None and atr is not None and feat[code]
                ["close"][data_end] < m20 - kelt * atr)
        maxed = (e + max_hold == data_end + 1)
        if trig or maxed:
            reason = u"Keltner触发" if trig else u"持满%d日" % max_hold
            due.append({"code": code, "name": NAMES.get(code, code),
                        "reason": reason, "entry_px": p["entry_px"],
                        "mark": p["mark"]})
    return due


def run_engine(live=False, asof=None, finalized=True):
    """主计算。
    live=True(盘中): 注入实时bar。finalized 控制最新bar是否已定型：
      盘中监控 → finalized=False（Keltner出场判断不扫描今日）
      收盘结算 → finalized=True（今日K线以收盘价定型，参与出场判断）
    asof='YYYY-MM-DD': 截断到该日。
    """
    rt = fetch_realtime() if live else None
    stocks, common = load_clean(live_bar=rt)
    codes = sorted(stocks.keys())
    n = len(common)
    data_end = n - 1
    if asof is not None:
        for i, d in enumerate(common):
            if d > asof:
                data_end = i - 1
                break
    feat, state = q26.build_features(stocks, codes, common)
    state = q44.add_batchAP_features(stocks, codes, common, feat, state)
    spec = build_spec()
    # EXP_0003 H2：趋势门控包裹原 filt（出场逻辑不受影响；signal 日 t 判定，
    # 全因果）。replay 与 tomorrow_signal 共用 spec["filt"]，一处生效。
    bull = regime_bull_days(stocks, codes, common) if EXP0003_ON else None
    if bull is not None:
        orig_filt = spec["filt"]

        def _filt(state, t, _bull=bull, _orig=orig_filt):
            return _orig(state, t) and t in _bull
        spec["filt"] = _filt
    paper_idx = None
    for i, d in enumerate(common):
        if d >= PAPER_START:
            paper_idx = i
            break
    if paper_idx is None:
        paper_idx = data_end
    closed, opens = replay(stocks, codes, feat, state, common, spec,
                           paper_idx, data_end,
                           today_finalized=finalized,
                           exposure_cap=EXPOSURE_CAP if EXP0003_ON else None)
    return {"stocks": stocks, "common": common, "codes": codes, "n": n,
            "data_end": data_end, "feat": feat, "state": state,
            "spec": spec, "paper_idx": paper_idx, "closed": closed,
            "opens": opens, "realtime": rt, "bull": bull}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="盘中模式：注入实时行情，最新bar视为未定型")
    ap.add_argument("--close", action="store_true",
                    help="收盘模式：注入实时行情，bar视为已定型，计算明日计划")
    args = ap.parse_args()
    if args.close:
        eng = run_engine(live=True, finalized=True)
    elif args.live:
        eng = run_engine(live=True, finalized=False)
    else:
        eng = run_engine(live=False, finalized=True)
    common, data_end = eng["common"], eng["data_end"]
    closed, opens = eng["closed"], eng["opens"]
    nav = nav_of(closed, opens)
    closed_nets = [t["net"] for t in closed]
    wins = sum(1 for x in closed_nets if x > 0)
    today_buys = [x for x in closed + opens if x["e"] == data_end]
    today_sells = [x for x in closed if x["xe"] == data_end]
    report = {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "live": args.live,
        "data_end": common[data_end],
        "paper_start": PAPER_START,
        "exp0003": EXP0003_ON,
        "regime": regime_diagnostics(eng["stocks"], eng["codes"],
                                     common, data_end),
        "nav": nav,
        "closed_trades": len(closed),
        "win_rate": (wins / len(closed_nets)) if closed_nets else None,
        "today_buys": [{
            "name": NAMES.get(t["code"], t["code"]), "code": t["code"],
            "entry_px": t["entry_px"]} for t in today_buys],
        "today_sells": [{
            "name": NAMES.get(t["code"], t["code"]), "code": t["code"],
            "exit_px": t["exit_px"], "net_pct": round(t["net"] * 100, 2),
            "reason": (u"Keltner触发" if t["xe"] - t["e"] < 10
                       else u"持满10日")} for t in today_sells],
        "open_positions": [{
            "name": NAMES.get(p["code"], p["code"]), "code": p["code"],
            "entry_date": p["entry_date"], "entry_px": p["entry_px"],
            "mark": p["mark"], "unreal_pct": round(p["unreal"] * 100, 2),
            "hold_days": p["hold_days"]} for p in opens],
        "recent_closed": [{
            "name": NAMES.get(t["code"], t["code"]), "code": t["code"],
            "entry_date": t["entry_date"], "exit_date": t["exit_date"],
            "net_pct": round(t["net"] * 100, 2)} for t in closed[-8:]],
    }
    if not args.live:
        # 收盘模式和默认模式都计算明日计划（bar已定型）
        report["tomorrow"] = tomorrow_signal(
            eng["stocks"], eng["codes"], eng["feat"], eng["state"],
            common, eng["spec"], data_end)
        report["exit_due_tomorrow"] = exit_due_tomorrow(
            opens, eng["feat"], data_end, KELT)
    else:
        if not args.close:
            # 开盘模式：展示昨日信号中今日因触涨停线被跳过的标的
            y_idx = data_end - 1
            if y_idx >= 0:
                sig = tomorrow_signal(eng["stocks"], eng["codes"],
                                      eng["feat"], eng["state"], common,
                                      eng["spec"], y_idx)
                bought = {t["code"] for t in today_buys}
                report["yesterday_filter_pass"] = sig["filter_pass"]
                report["skipped_at_open"] = [
                    {"name": p["name"], "code": p["code"],
                     "close": p["close"],
                     "limit_up_threshold": p["limit_up_threshold"]}
                    for p in sig["picks"] if p["code"] not in bought]
    with open(STATE_FILE, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
