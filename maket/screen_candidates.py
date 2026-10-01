# -*- coding: utf-8 -*-
"""动态候选筛选器：全市场找"上升趋势+健康回踩"的可做T标的（路径B结论的横截面应用）。

假设来源（bt_model_report.md / R443 同源）:
  上升趋势日内的回踩才值得低吸 —— 昨收>MA20 且 20日涨幅>0;
  H3: 昨日大涨(>=5%)次日低吸更差 → 当日涨幅>=5%剔除;
  波动率必须够付成本: ATR14/收盘 ∈ [1.2%, 6.5%];
  流动性: 当日成交额>=10亿（妙想预筛）。
产出: ~/.maket/whitelist_dynamic.json（前4名, 7个自然日≈5个交易日过期）,
      ~/.maket/reports/screen_YYYYMMDD.txt
用法: python3 screen_candidates.py [--dry]   (需要环境变量 MX_APIKEY, 由 run_maket.sh source)
"""
import os
import re
import csv
import json
import glob
import subprocess
import statistics
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
MX_SCRIPT = "/Users/francishy/.agents/skills/mx-xuangu/mx_xuangu.py"
MX_OUTDIR = "/Users/francishy/.agents/skills/mx-output"
QUERY = "今日成交额大于10亿元，涨跌幅在负6到2之间，股价大于3元的A股"
MAX_DYN = 4          # 动态池容量（+石药常驻 = 5只）
EXPIRE_DAYS = 10     # 自然日过期（2026-09-30修: 7天会在国庆长假中过期, 10天覆盖长假+5交易日）
TOPN_FETCH = 60      # 只对预筛综合分前60名拉日线细算

QUERY_KEYS = {"code": "代码", "name": "名称", "mkt": "市场代码简称",
              "px": "最新价", "chg": "涨跌幅", "amt": "成交额"}


def fetch_universe():
    """妙想预筛 → [{code,name,mkt,px,chg,amt}]"""
    subprocess.run(["python3", MX_SCRIPT, "--query", QUERY],
                   capture_output=True, timeout=120)
    csvs = sorted(glob.glob(os.path.join(MX_OUTDIR, "mx_xuangu_%s.csv" % QUERY)),
                  key=os.path.getmtime)
    if not csvs:
        raise RuntimeError("妙想预筛无输出")
    rows = []
    with open(csvs[-1], encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            item = {}
            for k, prefix in QUERY_KEYS.items():
                for ck in r:
                    if ck.startswith(prefix):
                        item[k] = r[ck].strip()
                        break
            if item.get("code"):
                rows.append(item)
    return rows


def parse_amt(s):
    s = s.replace(",", "")
    if s.endswith("亿"):
        return float(s[:-1]) * 1e8
    if s.endswith("万"):
        return float(s[:-1]) * 1e4
    try:
        return float(s)
    except ValueError:
        return 0.0


def sym_of(code, mkt):
    return ("sh" if mkt == "SH" else "sz") + code


def fetch_daily(sym, n=140):
    import maket
    return maket.fetch_daily(sym, n)


def analyze(code, name, mkt):
    """返回候选dict或None。全部用已完成日线（今日收盘后运行）。"""
    try:
        bars = fetch_daily(sym_of(code, mkt))
    except Exception:
        return None
    if len(bars) < 30:
        return None
    closes = [float(b[2]) for b in bars]
    highs = [float(b[3]) for b in bars]
    o_today = float(bars[-1][1])
    c_today = closes[-1]
    if c_today <= 0 or o_today <= 0:
        return None
    name = name if isinstance(name, str) else name
    if "ST" in (name or "") or "退" in (name or ""):
        return None
    ma20 = sum(closes[-20:]) / 20.0
    ret20 = c_today / closes[-21] - 1.0 if len(closes) > 21 else 0.0
    chg_today = c_today / closes[-2] - 1.0
    trs = [max(highs[i] - float(bars[i][4]),
               abs(highs[i] - closes[i - 1]),
               abs(float(bars[i][4]) - closes[i - 1]))
           for i in range(len(bars) - 14, len(bars))]
    atr = sum(trs) / 14.0 / c_today
    hi20 = max(highs[-20:])
    dist20h = c_today / hi20 - 1.0
    # —— 硬条件 ——
    if not (c_today > ma20 and ret20 > 0):
        return None                       # 非上升趋势
    if chg_today >= 0.05:
        return None                       # H3: 单日过热剔除
    if not (0.012 <= atr <= 0.065):
        return None                       # 波动率不够/过大
    red_candle = c_today < o_today        # 今日收阴=日内回踩状态
    healthy_pb = -0.10 <= dist20h <= -0.02
    if not (red_candle or healthy_pb):
        return None
    # —— 打分（透明加权, 无拟合参数）——
    score = (min(ret20, 0.30) / 0.30 * 40          # 趋势强度
             + (-dist20h - 0.02) / 0.08 * 30       # 回踩深度(区间内越深越接近支撑)
             + (1 - abs(atr - 0.025) / 0.025) * 15  # ATR靠近2.5%甜点
             + min(chg_today + 0.06, 0.06) / 0.06 * 15)  # 当日相对抗跌
    return {"code": code, "name": name, "symbol": sym_of(code, mkt),
            "close": c_today, "ret20": round(ret20 * 100, 1),
            "chg_today": round(chg_today * 100, 1),
            "atr_pct": round(atr * 100, 1),
            "dist20h": round(dist20h * 100, 1),
            "red_candle": red_candle, "score": round(score, 1)}


def main():
    dry = "--dry" in os.sys.argv
    uni = fetch_universe()
    print("妙想预筛: %d 只" % len(uni))
    uni.sort(key=lambda r: -parse_amt(r.get("amt", "0")))
    cands = []
    for r in uni[:TOPN_FETCH]:
        c = analyze(r.get("code"), r.get("name"), r.get("mkt"))
        if c:
            cands.append(c)
    cands.sort(key=lambda c: -c["score"])
    print("通过硬条件: %d 只" % len(cands))
    today = datetime.date.today()
    expire = (today + datetime.timedelta(days=EXPIRE_DAYS)).isoformat()
    picks = cands[:MAX_DYN]
    out = {"generated": today.isoformat(), "expire": expire,
           "query": QUERY, "universe_n": len(uni), "pass_n": len(cands),
           "stocks": picks}
    rep = ["🔍 动态候选筛选 %s（假设: 上升趋势+健康回踩+未过热）" % today.isoformat(),
           "预筛%d只 → 细算%d只通过 → 入池%d只（有效期至%s）"
           % (len(uni), len(cands), len(picks), expire), ""]
    for i, c in enumerate(picks, 1):
        rep.append("%d. %s(%s) 综合分%.1f | 20日%+.1f%% 今日%+.1f%% 距20日高%+.1f%% ATR%.1f%% %s"
                   % (i, c["name"], c["code"], c["score"], c["ret20"],
                      c["chg_today"], c["dist20h"], c["atr_pct"],
                      "今日收阴" if c["red_candle"] else "回踩区间"))
        rep.append("   明日规则: 开盘-0.6%低吸 → 开盘+0.6%高抛, 止损-1.5%, 14:55强平")
    if not dry:
        with open(os.path.join(HERE, "whitelist_dynamic.json"), "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        # 新池票并入明日zones(收盘结算先于筛选运行, 新票当时不在池内无价位区)
        try:
            import maket as _M
            from datetime import datetime as _dt
            _nd = _M.next_weekday(_dt.now())
            _nkey = _nd.strftime("%Y%m%d")
            _zfp = os.path.join(HERE, "zones_%s.json" % _nkey)
            _z = json.load(open(_zfp)) if os.path.exists(_zfp) else {
                "date": _nd.strftime("%Y-%m-%d"),
                "generated_at": _dt.now().isoformat(), "stocks": {}}
            for c in picks:
                _bars = fetch_daily(c["symbol"])
                if len(_bars) >= 25:
                    _z["stocks"][c["code"]] = _M.build_zones(c["code"], _bars)
            _z["generated_at"] = _dt.now().isoformat()
            with open(_zfp, "w") as f:
                json.dump(_z, f, ensure_ascii=False, indent=1)
            print("zones_%s 已并入新池价位区: %s" % (_nkey, [c["name"] for c in picks]))
        except Exception as e:
            print("zones合并失败(不阻塞):", e)
        rp = os.path.join(HERE, "reports", "screen_%s.txt" % today.strftime("%Y%m%d"))
        with open(rp, "w") as f:
            f.write("\n".join(rep) + "\n")
    print("\n".join(rep))
    # 淘汰名单提示
    old_fp = os.path.join(HERE, "whitelist_dynamic.json")
    if dry and os.path.exists(old_fp):
        old = json.load(open(old_fp))
        keep = {c["code"] for c in picks}
        gone = [c["name"] for c in old.get("stocks", []) if c["code"] not in keep]
        if gone:
            print("将淘汰: %s" % "、".join(gone))


if __name__ == "__main__":
    main()
