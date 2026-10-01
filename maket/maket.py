#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自选股每日高抛低吸/做T 引擎（纸面信号 + 纸面账本）
模式:
  plan     盘前计划（9:15-9:25）: 按昨日日线算枢轴/ATR/均线 → 每票做T价位区 + regime 分类
  monitor  盘中监控（9:35-15:00 每10分钟）: 实时价+5分钟动量确认 → 触发正T/反T信号并记账
  close    收盘结算（15:05）: 平掉未了结回合 → 结算账本 → 生成明日计划区
数据源: 腾讯日/分钟线（与主系统一致）+ 妙想自选股清单（pool.json, pool_sync.py 维护）
纪律: 「一直大涨」只持有不做T（STRONG regime 仅回调低吸/极端冲高回落提示）; 每票每日最多2回合;
      9:45前不进场、14:30后不开新仓、14:55强制平纸面回合; 双边成本默认0.12%。
所有信号均为纸面决策支持，非自动交易。
"""
from __future__ import print_function
import sys, os, json, time, urllib.request
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
import account as ACCT
REPORTS = os.path.join(HERE, "reports")
os.makedirs(REPORTS, exist_ok=True)
LEDGER_FP = os.path.join(HERE, "ledger_t.json")
POSITIONS_FP = os.path.join(HERE, "positions.json")   # 底仓配置(T+1回转前提)

RT_COST = 0.0012          # 双边成本: 佣金0.025%*2 + 印花0.05% + 滑点缓冲
STOP_PCT = 0.015          # 反向止损 1.5%
MAX_ROUNDS = 2            # 每票每日最多T回合
LATE_OPEN = "1430"        # 14:30后不开新仓
FORCE_EXIT = "1455"       # 14:55 强制平仓
EARLY_OK = "0945"         # 9:45 前不进场
# 90日回测(2026-05-07~09-10, 632回合): 反T每笔-0.228%是全部亏损来源(-87%)，
# 正T打平(+0.4%)；只正T合计-1.3%≈打平。故默认关闭反T开仓（bt_variants.py V1/V2）
ALLOW_REVERSE = False
# —— 每日必做模式（2026-09-14改版: 信号优先的网格+止损）——
# 规则: 只正T网格 — 开盘(1-d)低吸买入 → 开盘(1+d)高抛卖出; 入场价-1.5%日度止损; 14:55强平
#       无信号日不交易（取消14:30兜底, 用户2026-09-14授权按数据裁决）
# 石药创新(300765)修正口径回测(125天, 收盘价成交, 成本0.12%): 全窗约+14.7%
#       (IS段-17.7% / OOS段+32.4%); 止损使最差单笔-5.4%→-2.4%(OOS), 去兜底笔均+0.30%→+0.63%
#       旧数字(+44.3%等)因m5字段序bug作废, 详见 bt_model_report.md
DAILY_ALWAYS = True
ALWAYS_D = 0.006
ALWAYS_WHITELIST = ["300765"]   # 石药创新专属; 留空=全部自选股每天必做
# 动态筛选池（screen_candidates.py 每日收盘后产出, 7自然日过期强制复筛）
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "whitelist_dynamic.json")) as _fdyn:
        _dyn = json.load(_fdyn)
    _exp = str(_dyn.get("expire", "0000"))
    DYNAMIC_CODES = [s["code"] for s in _dyn.get("stocks", [])
                     if _exp >= datetime.now().date().isoformat()]
    _dyn_name = {s["code"]: s.get("name", s["code"]) for s in _dyn.get("stocks", [])}
except (IOError, ValueError):
    DYNAMIC_CODES = []
    _dyn_name = {}
ALWAYS_ALL = list(ALWAYS_WHITELIST) + [c for c in DYNAMIC_CODES if c not in ALWAYS_WHITELIST]
# 即时提醒: 每条信号弹 macOS 通知中心 + 提示音（launchd 自驱）
NOTIFY = True
NOTIFY_SOUND = "Glass"

ZK_HI = 0.6               # 高抛/低吸基准带 = 昨收 ± 0.6*ATR（再与枢轴对齐）
ZK_W = 0.35               # 区间半宽 = 0.35*ATR


# ---------------- 基础数据 ----------------
def http_get(url, timeout=20, gbk=False):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return raw.decode("gbk" if gbk else "utf-8", "ignore")


def load_pool():
    fp = os.path.join(HERE, "pool.json")
    with open(fp) as f:
        pool = json.load(f)
    _have = {s["code"] for s in pool}
    for code in DYNAMIC_CODES:
        if code in _have:
            continue
        pool.append({"code": code, "symbol": ("sh" if code.startswith(("6", "9")) else "sz") + code,
                     "name": _dyn_name.get(code, code), "_dyn": True})
    return pool



def fetch_daily(sym, n=260):
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           "?param=%s,day,,,%d,qfq" % (sym, n))
    try:
        d = json.loads(http_get(url))
    except Exception:
        # web.ifzq 偶发501限流, 回退同结构主域
        d = json.loads(http_get(
            "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
            "?param=%s,day,,,%d,qfq" % (sym, n)))
    dd = d["data"][sym]
    return dd.get("qfqday") or dd.get("day") or []


def fetch_m5(sym, pages=1):
    """最近 N 页 m5（每页320根 ≈ 6.7个交易日），升序返回 [time,o,h,l,c,vol]"""
    allb = {}
    anchor = ""
    for _ in range(pages):
        url = ("https://ifzq.gtimg.cn/appstock/app/kline/mkline"
               "?param=%s,m5,%s,320" % (sym, anchor))
        try:
            d = json.loads(http_get(url))
        except Exception:
            time.sleep(1.5)
            d = json.loads(http_get(url))
        bars = d.get("data", {}).get(sym, {}).get("m5") or []
        if not bars:
            break
        for b in bars:
            allb[b[0]] = [b[0], float(b[1]), float(b[2]), float(b[3]),
                          float(b[4]), float(b[5])]
        anchor = min(b[0] for b in bars)
        time.sleep(0.12)
    return [allb[k] for k in sorted(allb)]


def fetch_realtime(symbols):
    """qt.gtimg.cn 实时快照 {sym: {price,prev_close,open,high,low,date}}"""
    raw = http_get("https://qt.gtimg.cn/q=" + ",".join(symbols), gbk=True)
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
        try:
            out[sym] = {
                "price": float(f[3]), "prev_close": float(f[4]),
                "open": float(f[5]), "high": float(f[33]),
                "low": float(f[34]), "date": f[30][:8],
                "name": f[1],
            }
        except (ValueError, IndexError):
            continue
    return out


# ---------------- 指标与状态 ----------------
def atr14(bars):
    """bars: [[date,open,close,high,low,vol],...] 腾讯日线顺序"""
    trs = []
    for i in range(1, len(bars)):
        h, l = float(bars[i][3]), float(bars[i][4])
        pc = float(bars[i - 1][2])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    return sum(trs[-14:]) / float(min(14, len(trs)))


def limit_pct(code):
    return 0.20 if code.startswith(("300", "301", "688")) else 0.10


def classify(code, bars):
    """regime 分类（只用已完成日线）。STRONG=强势单边(不做T) / WEAK / NORMAL"""
    c = [float(b[2]) for b in bars]
    o = [float(b[1]) for b in bars]
    n = len(c)
    ma20 = sum(c[-20:]) / 20.0 if n >= 20 else c[-1]
    ret5 = c[-1] / c[-6] - 1 if n >= 6 else 0.0
    ret20 = c[-1] / c[-21] - 1 if n >= 21 else 0.0
    up3 = all(c[-i] > c[-i - 1] for i in (1, 2, 3))
    ret3 = c[-1] / c[-4] - 1 if n >= 4 else 0.0
    lp = limit_pct(code)
    prev_c = c[-2] if n >= 2 else c[-1]
    limit_up_px = round(prev_c * (1 + lp), 2)
    hit_limit = abs(c[-1] - limit_up_px) < 0.005 * prev_c or c[-1] / prev_c - 1 >= lp - 0.002
    if hit_limit or ret5 >= 0.12 or (up3 and ret3 >= 0.10):
        return "STRONG", {"ret5": ret5, "ret3": ret3, "up3": up3, "ret20": ret20,
                          "ma20": ma20, "hit_limit": hit_limit}
    if c[-1] < ma20 and ret20 < -0.15:
        return "WEAK", {"ret5": ret5, "ret3": ret3, "up3": up3, "ret20": ret20,
                        "ma20": ma20, "hit_limit": False}
    return "NORMAL", {"ret5": ret5, "ret3": ret3, "up3": up3, "ret20": ret20,
                      "ma20": ma20, "hit_limit": False}


def build_zones(code, bars):
    """由最近一根已完成日线（昨日）计算今日做T价位区"""
    b = bars[-1]
    o, c, h, l = float(b[1]), float(b[2]), float(b[3]), float(b[4])
    atr = atr14(bars[:-1]) or (h - l) or c * 0.02
    atr = max(atr, c * 0.008)
    p = (h + l + c) / 3.0
    r1, s1 = 2 * p - l, 2 * p - h
    r2, s2 = p + (h - l), p - (h - l)
    regime, meta = classify(code, bars)
    hi_k = min(max(ZK_HI * atr / c, 0.008), 0.04)
    s_lo = max(r1, c * (1 + hi_k))
    s_lo = min(s_lo, c * 1.055)          # 单日高抛不过分
    s_hi = s_lo + ZK_W * atr
    b_hi = min(s1, c * (1 - hi_k))
    b_hi = max(b_hi, c * 0.95)           # 低吸不至于深坑
    b_lo = b_hi - ZK_W * atr
    return {
        "regime": regime, "atr": round(atr, 3),
        "prev_high": h, "prev_low": l, "prev_close": c, "pivot": round(p, 2),
        "sell_lo": round(s_lo, 2), "sell_hi": round(s_hi, 2),
        "buy_lo": round(b_lo, 2), "buy_hi": round(b_hi, 2),
        "sell_ext": round(max(r2, s_hi), 2), "buy_ext": round(min(s2, b_lo), 2),
        "meta": {k: (round(v, 4) if isinstance(v, float) else v)
                 for k, v in meta.items()},
    }


def next_weekday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d = d + timedelta(days=1)
    return d


def target_date(today=None):
    """plan 生成的目标交易日: 周一~周五=当天，周末=下一周一"""
    d = today or datetime.now()
    if d.weekday() >= 5:
        return next_weekday(d)
    return d


def fmt_zone(a, b):
    return "%.2f-%.2f" % (a, b)


# ---------------- 账本 ----------------
def load_ledger():
    if os.path.exists(LEDGER_FP):
        with open(LEDGER_FP) as f:
            return json.load(f)
    return {"config": {"rt_cost": RT_COST, "stop_pct": STOP_PCT,
                       "max_rounds": MAX_ROUNDS},
            "rounds": [], "open": {}}


def save_ledger(lg):
    with open(LEDGER_FP, "w") as f:
        json.dump(lg, f, ensure_ascii=False, indent=1)


def load_positions():
    """底仓配置: {code: {name, shares, avg_cost}}。A股T+1下做T的前提是有底仓。
    未在文件中的股票默认视为有底仓(向后兼容); shares=0 表示明确无底仓, 不可做T。"""
    if os.path.exists(POSITIONS_FP):
        with open(POSITIONS_FP) as f:
            return json.load(f)
    return {}


def base_shares(code, positions):
    """返回底仓股数: None=未配置(默认有底仓, 兼容旧行为); 0=明确无底仓。"""
    p = positions.get(code)
    if p is None:
        return None
    try:
        return int(p.get("shares", 0) or 0)
    except (TypeError, ValueError):
        return 0


def round_pnl(r, rt_cost):
    gross = (r["exit_px"] / r["entry_px"] - 1.0) * r["dir"]
    return gross - rt_cost


# ---------------- 模式1: 盘前计划 ----------------
def run_plan(force=False):
    import trading_day as _td
    if not force and not _td.is_trading_day():
        return
    tdate = target_date()
    tkey = tdate.strftime("%Y%m%d")
    pool = load_pool()
    zones = {"date": tdate.strftime("%Y-%m-%d"),
             "generated_at": datetime.now().isoformat(), "stocks": {}}
    lines = ["📋 自选股高抛低吸 · 盘前计划 %s（生成 %s）"
             % (tdate.strftime("%Y-%m-%d"), datetime.now().strftime("%H:%M"))]
    if DAILY_ALWAYS:
        lines += [
            "⚙️ 模式: 每日必做（信号优先）— 开盘锚网格+止损:",
            "   开盘价-%.1f%% 低吸买入 → 开盘价+%.1f%% 高抛卖出 · 入场价-1.5%%止损 · 14:55强平"
            % (ALWAYS_D * 100, ALWAYS_D * 100),
            "   （无信号日不交易, 已取消14:30兜底）",
            "   （以下枢轴/区间为条件模式参考位）", "",
            "🔄 T+1底仓回转: 正T=先加仓买入(新仓T+1才可卖)→卖等量底仓 · 反T=先卖底仓→买回(新仓T+1才可卖)",
            "   ⚠️ 无底仓不可做T(当天买当天卖=违规T+0)。底仓配置 positions.json（未配置默认视为有底仓）", ""]
        if ALWAYS_ALL:
            names = {s["code"]: s["name"] for s in pool}
            focus = "、".join("%s(%s)%s" % (names.get(c, "?"), c,
                                            "🔍" if c in DYNAMIC_CODES else "")
                              for c in ALWAYS_ALL)
            lines.insert(1, "🎯 做T标的(常驻+筛选🔍): %s — 其余自选股仅列参考位" % focus)
    else:
        lines += ["纪律(T+1底仓回转): 正T=先加仓买入(新仓T+1)→卖等量底仓 · "
                  "反T=先卖底仓→买回(新仓T+1) · 止损1.5% · 每票每日≤2回合 · 14:30后不进场",
                  "⚠️ 无底仓不可做T(当天买当天卖=违规T+0)", ""]
    n_strong = 0
    for s in pool:
        sym, code, name = s["symbol"], s["code"], s["name"]
        try:
            bars = fetch_daily(sym)
            if len(bars) < 25:
                lines.append("· %s(%s) 历史数据不足，跳过" % (name, code))
                continue
            z = build_zones(code, bars)
        except Exception as e:
            lines.append("· %s(%s) 取数失败: %s" % (name, code, str(e)[:60]))
            continue
        zones["stocks"][code] = dict(z, name=name, symbol=sym)
        meta = z["meta"]
        if z["regime"] == "STRONG":
            n_strong += 1
            lines += [
                "· %s(%s) 🚀 强势单边%s" % (
                    name, code,
                    "（每日必做模式下网格照常执行）" if DAILY_ALWAYS
                    else "——只持有不做T"),
                "   近5日%+.1f%% 昨日%s。除非冲高回落(较开盘+5%%后连续2根5分收阴)才提示反T，"
                "回调至低吸区才提示低吸:" % (meta["ret5"] * 100,
                                            "涨停" if meta["hit_limit"] else "连续大涨"),
                "   低吸区 %s | 极端高抛参考 %.2f" % (fmt_zone(z["buy_lo"], z["buy_hi"]),
                                                    z["sell_ext"]),
            ]
        else:
            tag = "震荡双向做T" if z["regime"] == "NORMAL" else "弱势——正T优先,高抛收紧"
            amp = (z["sell_hi"] - z["buy_lo"]) / z["prev_close"] * 100
            lines += [
                "· %s(%s) 🔄 %s（参考日内振幅约%.1f%%）" % (name, code, tag, amp),
                "   反T: 冲至高抛区 %s 且5分钟连续2根收阴 → 卖出, 回落至 %s 买回"
                % (fmt_zone(z["sell_lo"], z["sell_hi"]), fmt_zone(z["buy_lo"], z["buy_hi"])),
                "   正T: 探至低吸区 %s 且5分钟连续2根收阳 → 买入, 反弹至高抛区卖出"
                % fmt_zone(z["buy_lo"], z["buy_hi"]),
                "   极端位: 上 %.2f / 下 %.2f · 枢轴 %.2f · 止损: 进场点反向%.1f%%"
                % (z["sell_ext"], z["buy_ext"], z["pivot"], STOP_PCT * 100),
            ]
        lines.append("")
    lines.insert(lines.index(""), "🚀 强势持有: %d 只 | 🔄 做T: %d 只" %
                 (n_strong, len(zones["stocks"]) - n_strong))
    lines += ["— 纸面信号仅供参考，下单前确认流动性，竞价异动（高开>3%不追高、低开>2%等企稳）"]
    fp = os.path.join(REPORTS, "plan_%s.txt" % tkey)
    with open(fp, "w") as f:
        f.write("\n".join(lines))
    with open(os.path.join(HERE, "zones_%s.json" % tkey), "w") as f:
        json.dump(zones, f, ensure_ascii=False, indent=1)
    print("\n".join(lines))
    return fp


# ---------------- 模式2: 盘中监控 ----------------
def _asof():
    """测试钩子: MAKET_ASOF=YYYYMMDD 可用历史日回归测试"""
    return os.environ.get("MAKET_ASOF") or datetime.now().strftime("%Y%m%d")


def _now_hm():
    return os.environ.get("MAKET_NOW") or datetime.now().strftime("%H%M")


def now_hm():
    return _now_hm()


def is_trading_now():
    h, m = divmod(int(now_hm()), 100)
    mins = h * 60 + m
    return (570 <= mins <= 690) or (780 <= mins <= 900)  # 9:30-11:30 / 13:00-15:00


def vwap_today(bars_today):
    pv = sum(b[2] * b[5] for b in bars_today)
    v = sum(b[5] for b in bars_today)
    return pv / v if v else None


def momo(bars):
    """连续2根5分钟收阳/收阴（m5字段序: [time,open,close,high,low,vol]）"""
    if len(bars) < 3:
        return 0
    c = [b[2] for b in bars[-3:]]
    if c[-1] > c[-2] > c[-3]:
        return 1
    if c[-1] < c[-2] < c[-3]:
        return -1
    return 0




# ---------------- 自选股做T提醒（不自动开仓, 用户手动执行） ----------------
def _remind_state(today):
    fp = os.path.join(HERE, "remind_state.json")
    try:
        st = json.load(open(fp))
    except (IOError, ValueError):
        st = {}
    if st.get("date") != today:
        st = {"date": today, "fired": {}}
    return st, fp


def check_remind(code, name, z, px, today_open, mo, today):
    """触及价位区+5分钟动量确认 → 提醒（每票每侧每日至多一次）"""
    if now_hm() < EARLY_OK or now_hm() >= LATE_OPEN:
        return []
    st, fp = _remind_state(today)
    fired = st["fired"].get(code, [])
    out = []
    if z["regime"] == "STRONG":
        rally = px / today_open - 1 if today_open else 0
        if rally >= 0.05 and mo == -1 and "rally" not in fired:
            out.append("🔔 提醒 %s(%s) 冲高回落(较开盘+%.1f%%)且5分钟2连阴 — 强势票仅反向高抛参考"
                       % (name, code, rally * 100))
            fired.append("rally")
    else:
        if mo == 1 and z["buy_lo"] <= px <= z["buy_hi"] and "buy" not in fired:
            out.append("🔔 提醒 %s(%s) 触及低吸区 %s~%s 且5分钟2连阳 — 正T低吸参考"
                       % (name, code, z["buy_lo"], z["buy_hi"]))
            fired.append("buy")
        if mo == -1 and px >= z["sell_lo"] and "sell" not in fired:
            out.append("🔔 提醒 %s(%s) 冲至高抛区 %s~%s 且5分钟2连阴 — 反T高抛/持仓高抛参考"
                       % (name, code, z["sell_lo"], z["sell_hi"]))
            fired.append("sell")
    if out:
        st["fired"][code] = fired
        with open(fp, "w") as f:
            json.dump(st, f)
    return out


def run_monitor(force=False):
    today = _asof()
    tkey = today
    if not force and not is_trading_now():
        return
    pool = load_pool()
    lg = load_ledger()
    positions = load_positions()
    rt_cost = lg["config"].get("rt_cost", RT_COST)
    zfp = os.path.join(HERE, "zones_%s.json" % tkey)
    zones = {}
    if os.path.exists(zfp):
        with open(zfp) as f:
            zones = json.load(f).get("stocks", {})
    syms = [s["symbol"] for s in pool]
    rt = fetch_realtime(syms + ["sh000001"])
    if not force and not any(v.get("date") == today for v in rt.values()):
        return  # 休市
    alerts = []
    # 市场门控 G-a（R5校准采纳, 9:45时点版）: 上证较昨收<=-0.5% → 当日暂停开新仓(持仓照常管理)
    market_halt = False
    _ms_fp = os.path.join(HERE, "market_state_%s.json" % tkey)
    _ms = {}
    if os.path.exists(_ms_fp):
        try:
            _ms = json.load(open(_ms_fp))
        except ValueError:
            _ms = {}
    if "decision" not in _ms and "0945" <= now_hm() <= "0950":
        # 审计修复(P1): 校准时点是9:45——9:30早判会错过"开盘微跌+盘中阴跌"型(0928实测: 9:30判pass, 9:45实为-0.81%应HALT, 损失-4.93%可避免)
        _iq = rt.get("sh000001")
        if _iq and _iq.get("prev_close") and _iq.get("price"):
            _g2 = _iq["price"] / _iq["prev_close"] - 1
            _ms["decision"] = "halt" if _g2 <= -0.005 else "pass"
            _ms["g2"] = round(_g2 * 100, 2)
            _ms["at"] = now_hm()
            with open(_ms_fp, "w") as _mf:
                json.dump(_ms, _mf)
            if _ms["decision"] == "halt":
                alerts.append("🏛 市场门控(G-a)触发: 上证9:45较昨收%.2f%%<=-0.5%%, 今日暂停开新仓(持仓照常管理)" % _ms["g2"])
    market_halt = (_ms.get("decision") == "halt")
    acc = ACCT.load()
    acc_dirty = False
    # 底仓轮换: 不在当前交易名单的底仓, 开盘即卖出(石药常驻除外; 底仓均已满T+1)
    exited = [c for c in acc["bases"] if c != "300765" and c not in ALWAYS_ALL]
    if exited:
        ex_syms = [s for s in [("sh" if c.startswith(("6", "9")) else "sz") + c for c in exited]
                   if s not in syms]
        if ex_syms:
            try:
                rt.update(fetch_realtime(ex_syms))
            except Exception:
                pass
    for c in exited:
        sym_s = ("sh" if c.startswith(("6", "9")) else "sz") + c
        p = (rt.get(sym_s) or {}).get("price")
        r_sold = ACCT.sell_base(acc, c, p, today)
        if r_sold:
            acc_dirty = True
            alerts.append("📤 底仓轮换卖出: %s(%s) %d股@%.2f —— 资金转入新池"
                          % (r_sold["name"], r_sold["code"], r_sold["shares"], r_sold["px"]))
    for s in pool:
        sym, code, name = s["symbol"], s["code"], s["name"]
        q = rt.get(sym)
        if not q or (not force and q.get("date") != today):
            continue
        # 交易标的(白名单/动态筛选)或已有持仓; 其余自选股走提醒模式
        tradeable = (not DAILY_ALWAYS or not ALWAYS_ALL
                     or code in ALWAYS_ALL or bool(lg["open"].get(code)))
        if tradeable and code in ALWAYS_ALL and code not in acc["bases"]:
            if ACCT.ensure_base(acc, code, name, q.get("open") or q.get("price"), today):
                acc_dirty = True
        z = zones.get(code)
        if not z:
            try:
                bars = fetch_daily(sym)
                if len(bars) < 25:
                    continue
                z = build_zones(code, bars)
            except Exception:
                continue
        px = q["price"]
        today_open = q["open"] or px
        o = lg["open"].get(code)
        # 分钟线按需拉取(动量/VWAP确认时才要)——1分钟轮询的成本前提
        need_m5 = bool(o and not o.get("always"))
        if not tradeable:
            near_buy = z["buy_lo"] * 0.997 <= px <= z["buy_hi"] * 1.003
            need_m5 = near_buy or px >= z["sell_lo"] * 0.997
        mbars = [b for b in fetch_m5(sym, 1) if b[0].startswith(today)] if need_m5 else []
        if need_m5 and len(mbars) < 8:
            continue
        mo = momo(mbars) if mbars else 0
        vwap = vwap_today(mbars) if mbars else None
        if not tradeable:
            for _msg in check_remind(code, name, z, px, today_open, mo, today):
                alerts.append(_msg)
            continue
        rounds_today = [r for r in lg["rounds"]
                        if r["date"] == today and r["code"] == code]
        # —— 持仓回合的退出检查 ——
        if o:
            done = False
            if o.get("always"):
                # 网格回合: 网格目标 / 日度止损1.5% / 强平
                if o.get("target") and ((o["dir"] == 1 and px >= o["target"])
                                        or (o["dir"] == -1 and px <= o["target"])):
                    done = ("到达网格目标", px)
                elif o["dir"] == 1 and px <= o["entry_px"] * (1 - STOP_PCT):
                    done = ("止损", px)
                elif o["dir"] == -1 and px >= o["entry_px"] * (1 + STOP_PCT):
                    done = ("止损", px)
            elif o["dir"] == 1:  # 正T: 目标高抛区/VWAP上方, 止损下方
                if px >= o["entry_px"] * (1 + 0.010) and vwap and vwap >= o["entry_px"] * (1 + 0.006):
                    done = ("反弹达标", px)
                elif o.get("target") and px >= o["target"]:
                    done = ("到达高抛目标", px)
                elif px <= o["entry_px"] * (1 - STOP_PCT):
                    done = ("止损", px)
            else:              # 反T: 目标低吸区/VWAP下方, 止损上方
                if px <= o["entry_px"] * (1 - 0.010) and vwap and vwap <= o["entry_px"] * (1 - 0.006):
                    done = ("回落达标", px)
                elif o.get("target") and px <= o["target"]:
                    done = ("到达低吸目标", px)
                elif px >= o["entry_px"] * (1 + STOP_PCT):
                    done = ("止损", px)
            if now_hm() >= FORCE_EXIT and not done:
                done = ("尾盘强制平", px)
            if done:
                reason, exit_px = done
                r = {"date": today, "code": code, "name": name,
                     "dir": o["dir"], "entry_px": o["entry_px"],
                     "entry_time": o["entry_time"], "exit_px": round(exit_px, 3),
                     "exit_time": now_hm(), "reason": reason,
                     "entry_act": o.get("entry_act", "加仓买入(新仓T+1)" if o["dir"] == 1 else "卖出底仓"),
                     "exit_act": o.get("exit_act", "卖出底仓" if o["dir"] == 1 else "买回(新仓T+1)"),
                     "t1": True}
                r["ret_net"] = round(round_pnl(r, rt_cost) * 100, 3)
                lg["rounds"].append(r)
                del lg["open"][code]
                flag = "+" if r["ret_net"] >= 0 else ""
                alerts.append("✅ %s(%s) T回合了结 [%s] %s%.2f%%（%s%.2f→%s%.2f）"
                              % (name, code, r["reason"], flag, r["ret_net"],
                                 r["entry_act"], r["entry_px"],
                                 r["exit_act"], r["exit_px"]))
            continue
        # —— 每日必做模式（无条件, 优先于条件模式）——
        if DAILY_ALWAYS and (not ALWAYS_ALL or code in ALWAYS_ALL):
            if rounds_today or now_hm() < EARLY_OK:
                continue
            if code not in acc["bases"]:
                continue          # T+1合规: 无底仓(如高价股建不满100股)则回合不可完成, 禁止开仓
            if market_halt:
                continue          # 市场门控G-a: 大盘急跌日暂停开新仓
            topen = q["open"] or px
            if px <= topen * (1 - ALWAYS_D):
                open_round(lg, code, name, 1, px, today, alerts,
                           target=topen * (1 + ALWAYS_D), always=True,
                           positions=positions)
            continue
        # —— 新开仓触发 ——
        if len(rounds_today) >= MAX_ROUNDS or now_hm() >= LATE_OPEN \
                or (mbars and mbars[-1][0][8:] < EARLY_OK):
            continue
        if market_halt:
            continue              # 市场门控G-a: 大盘急跌日暂停开新仓
        if code not in acc["bases"]:
            continue              # T+1合规: 条件模式同样要求底仓（卖出腿需要）
        m = z["meta"]
        if z["regime"] == "STRONG":
            # 强势股: 仅「冲高回落」反T 或 回调低吸
            rally = px / today_open - 1 if today_open else 0
            if ALLOW_REVERSE and rally >= 0.05 and mo == -1 and px >= z["sell_ext"]:
                open_round(lg, code, name, -1, px, today, alerts,
                           target=max(z["buy_hi"], px * (1 - 0.02)),
                           positions=positions)
            elif px <= z["buy_hi"] and mo == 1:
                open_round(lg, code, name, 1, px, today, alerts,
                           target=z["sell_lo"] if z["sell_lo"] > px else px * (1 + 0.012),
                           positions=positions)
            continue
        # NORMAL / WEAK
        sell_trig = z["sell_lo"] if z["regime"] == "NORMAL" \
            else min(z["sell_lo"], z["prev_close"] * (1 + 0.005))
        floor = z["buy_ext"] * 0.997
        low_today = q["low"] or px
        if ALLOW_REVERSE and px >= sell_trig and mo == -1:
            open_round(lg, code, name, -1, px, today, alerts,
                       target=max(z["buy_hi"], px * (1 - min(0.012, 0.6 * z["atr"] / z["prev_close"]))),
                       positions=positions)
        elif px <= z["buy_hi"] and px >= floor and mo == 1:
            open_round(lg, code, name, 1, px, today, alerts,
                       target=max(z["sell_lo"], (vwap or 0)) if (vwap or 0) > px * 1.004
                       else px * (1 + min(0.012, 0.6 * z["atr"] / z["prev_close"])),
                       positions=positions)
        elif low_today < floor and px > floor and mo == 1:
            open_round(lg, code, name, 1, px, today, alerts,
                       target=max(z["sell_lo"], (vwap or 0)) if (vwap or 0) > px * 1.004
                       else px * (1 + min(0.012, 0.6 * z["atr"] / z["prev_close"])),
                       positions=positions)
    if lg["open"]:
        for code, o in lg["open"].items():
            if o["date"] == today:
                alerts.append("⏳ 持仓中: %s(%s) %s@%.2f %s"
                              % (o["name"], code,
                                 ("网格低吸" if o.get("always") else
                                  ("正T买入" if o["dir"] == 1 else "反T卖出")),
                                 o["entry_px"], o["entry_time"]))
    save_ledger(lg)
    if acc_dirty:
        ACCT.save(acc)
    if alerts:
        afp = os.path.join(REPORTS, "alerts_%s.log" % tkey)
        with open(afp, "a") as f:
            for a in alerts:
                f.write("%s %s\n" % (datetime.now().strftime("%H:%M"), a))
        print("\n".join(alerts))
        if NOTIFY:
            for a in alerts:
                notify_send(a)


def notify_send(text):
    """macOS 通知中心弹窗+提示音（launchd 自驱即时提醒）"""
    try:
        import notify
        notify.notify("做T信号", text, NOTIFY_SOUND)
    except Exception:
        pass


def open_round(lg, code, name, d, px, today, alerts, target=None, always=False,
               positions=None):
    # T+1 底仓回转语义: 正T=先加仓买入(新仓T+1才可卖)后卖底仓; 反T=先卖底仓后买回(新仓T+1才可卖)
    sh = base_shares(code, positions) if positions is not None else None
    if sh == 0:
        alerts.append("⚠️ %s(%s) 无底仓，跳过做T（A股T+1需底仓回转）" % (name, code))
        return
    entry_act = "加仓买入(新仓T+1)" if d == 1 else "卖出底仓"
    exit_act = "卖出底仓" if d == 1 else "买回(新仓T+1)"
    lg["open"][code] = {
        "date": today, "code": code, "name": name, "dir": d,
        "entry_px": round(px, 3), "entry_time": now_hm(),
        "target": round(target, 3) if target else None, "always": always,
        "entry_act": entry_act, "exit_act": exit_act, "t1": True,
    }
    flag = ("网格低吸买入" if always else "正T低吸买入") if d == 1 else "反T高抛卖出"
    if always and d == 1:
        alerts.append("🔔 %s(%s) %s @%.2f 目标%.2f 14:55强平"
                      % (name, code, flag, px, target or 0))
    else:
        alerts.append("🔔 %s(%s) %s @%.2f 目标%.2f 止损%.2f"
                      % (name, code, flag, px, target or 0,
                         px * (1 - STOP_PCT) if d == 1 else px * (1 + STOP_PCT)))


# ---------------- 模式3: 收盘结算 ----------------
def run_close(force=False):
    import trading_day as _td
    if not force and not _td.is_trading_day():
        return
    today = _asof()
    today_s = "%s-%s-%s" % (today[:4], today[4:6], today[6:])
    pool = load_pool()
    lg = load_ledger()
    rt_cost = lg["config"].get("rt_cost", RT_COST)
    rt = fetch_realtime([s["symbol"] for s in pool])
    if not force and not any(v.get("date") == today for v in rt.values()):
        return  # 休市静默
    settled = []
    for code in list(lg["open"].keys()):
        o = lg["open"][code]
        if o["date"] != today:
            continue
        sym = next((s["symbol"] for s in pool if s["code"] == code), None)
        px = (rt.get(sym) or {}).get("price")
        if not px:
            mb = [b for b in fetch_m5(sym or "", 1) if b[0].startswith(today)]
            px = mb[-1][2] if mb else o["entry_px"]   # m5: b[2]=close
        r = {"date": today, "code": code, "name": o["name"], "dir": o["dir"],
             "entry_px": o["entry_px"], "entry_time": o["entry_time"],
             "exit_px": round(px, 3), "exit_time": now_hm(), "reason": "收盘结算",
             "entry_act": o.get("entry_act", "加仓买入(新仓T+1)" if o["dir"] == 1 else "卖出底仓"),
             "exit_act": o.get("exit_act", "卖出底仓" if o["dir"] == 1 else "买回(新仓T+1)"),
             "t1": True}
        r["ret_net"] = round(round_pnl(r, rt_cost) * 100, 3)
        lg["rounds"].append(r)
        del lg["open"][code]
        settled.append(r)
    save_ledger(lg)

    acc = ACCT.load()
    for r in [x for x in lg["rounds"] if x["date"] == today]:
        ACCT.apply_round(acc, r)
    for s in pool:
        if s["code"] not in acc["bases"]:
            q = rt.get(s["symbol"]) or {}
            sym_s = s["symbol"]
            if not q.get("date"):
                continue
            if sym_s in ALWAYS_ALL or s["code"] in ALWAYS_ALL:
                if ACCT.ensure_base(acc, s["code"], s["name"], q.get("open") or q.get("price"), today):
                    pass
    close_px = {}
    for code, b in acc["bases"].items():
        sym_s = next((s["symbol"] for s in pool if s["code"] == code), None)
        p = (rt.get(sym_s) or {}).get("price")
        if p:
            close_px[code] = p
    ACCT.mark(acc, close_px, today)
    ACCT.save(acc)

    lines = ["📊 自选股做T · 收盘复盘 %s" % today_s, ""]
    today_rounds = [r for r in lg["rounds"] if r["date"] == today]
    if today_rounds:
        wins = [r for r in today_rounds if r["ret_net"] > 0]
        tot = sum(r["ret_net"] for r in today_rounds)
        lines.append("今日纸面T回合: %d 笔 | 胜 %d 亏 %d | 当日净利 %+.2f%%"
                     % (len(today_rounds), len(wins),
                        len(today_rounds) - len(wins), tot))
        for r in today_rounds:
            flag = "+" if r["ret_net"] >= 0 else ""
            lines.append("  · %s(%s) %s %s%.2f→%s%.2f 净%s%.2f%% [%s]"
                         % (r["name"], r["code"],
                            "正T" if r["dir"] == 1 else "反T",
                            r.get("entry_act", "加仓买入" if r["dir"] == 1 else "卖出底仓"), r["entry_px"],
                            r.get("exit_act", "卖出底仓" if r["dir"] == 1 else "买回"), r["exit_px"],
                            flag, r["ret_net"], r["reason"]))
    else:
        lines.append("今日无T回合成交（未触及低吸触发线, 开盘价-%.1f%%）" % (ALWAYS_D * 100)
                     if DAILY_ALWAYS else "今日无T回合成交（未触发价位+动量确认条件）")
    # 累计战绩
    allr = lg["rounds"]
    if allr:
        days = sorted(set(r["date"] for r in allr))
        wins = sum(1 for r in allr if r["ret_net"] > 0)
        tot = sum(r["ret_net"] for r in allr)
        per_day = len(allr) / float(len(days))
        # 滚动regime监控（walk-forward实验结论: 策略盈利依赖行情结构, 逆风期需显著提示）
        recent = allr[-20:]
        r_wins = sum(1 for r in recent if r["ret_net"] > 0)
        r_tot = sum(r["ret_net"] for r in recent)
        r_wr = 100.0 * r_wins / len(recent)
        regime_ok = r_wr >= 50.0 and r_tot > 0
        lines += ["", "滚动20回合: 胜率%.0f%% 净利%+.2f%% —— %s"
                  % (r_wr, r_tot,
                     "顺风期" if regime_ok else "⚠️ 逆风期(策略与当前行情结构不匹配, 回测参照2026年3-5月全参数皆亏)"),
                  "累计: %d笔 · 胜率%.0f%% · 合计%+.2f%% · 日均%.1f笔（%d个交易日）"
                  % (len(allr), 100.0 * wins / len(allr), tot, per_day, len(days))]
        # 实盘 vs 回测基准追踪（125天修正口径回测[止损+去兜底]: 胜率48%, 单笔+0.14%）
        if len(allr) >= 5:
            live_wr = 100.0 * wins / len(allr)
            live_avg = tot / len(allr)
            trig = [r for r in allr if (r.get("entry_time") or "0000") < LATE_OPEN]
            fb = [r for r in allr if (r.get("entry_time") or "0000") >= LATE_OPEN]
            drift_wr = live_wr - 48.0
            drift_avg = live_avg - 0.14
            flag = "正常" if (abs(drift_wr) <= 15 and abs(drift_avg) <= 0.30) else "⚠️ 偏离回测，检查执行"
            lines += ["实盘vs回测: 胜率%.0f%%(回测48%%, %+.0fpp) · 单笔%+.2f%%(回测+0.14%%, %+.2fpp) —— %s"
                      % (live_wr, drift_wr, live_avg, drift_avg, flag)]
            rsn = {}
            for r in allr:
                rsn.setdefault(r.get("reason") or "?", []).append(r["ret_net"])
            lines.append("  离场结构: " + " | ".join(
                "%s %d笔 %+.2f%%" % (k, len(v), sum(v) / len(v))
                for k, v in sorted(rsn.items(), key=lambda kv: -len(kv[1]))))
    lines.append("")
    # 明日计划（用今日已收盘日线重算 regime 与价位区）
    nd = next_weekday(datetime.now())
    nkey = nd.strftime("%Y%m%d")
    zones = {"date": nd.strftime("%Y-%m-%d"),
             "generated_at": datetime.now().isoformat(), "stocks": {}}
    lines.append("🎯 明日 %s 计划预览:" % nd.strftime("%Y-%m-%d"))
    for s in pool:
        try:
            bars = fetch_daily(s["symbol"])
            if len(bars) < 25:
                continue
            z = build_zones(s["code"], bars)
        except Exception as e:
            lines.append("  · %s 取数失败 %s" % (s["name"], str(e)[:40]))
            continue
        zones["stocks"][s["code"]] = dict(z, name=s["name"], symbol=s["symbol"])
        if z["regime"] == "STRONG":
            lines.append("  · %s(%s) 🚀 持有不T（低吸区 %s）"
                         % (s["name"], s["code"], fmt_zone(z["buy_lo"], z["buy_hi"])))
        else:
            lines.append("  · %s(%s) 高抛 %s / 低吸 %s"
                         % (s["name"], s["code"],
                            fmt_zone(z["sell_lo"], z["sell_hi"]),
                            fmt_zone(z["buy_lo"], z["buy_hi"])))
    with open(os.path.join(HERE, "zones_%s.json" % nkey), "w") as f:
        json.dump(zones, f, ensure_ascii=False, indent=1)
    lines += ["", "— 纸面账本，信号仅供参考"]
    fp = os.path.join(REPORTS, "close_%s.txt" % today)
    with open(fp, "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    # 逆风期收盘弹窗（滚动20回合胜率<50%或净利<0）
    try:
        recent = lg["rounds"][-20:]
        if recent and (100.0 * sum(1 for r in recent if r["ret_net"] > 0) / len(recent) < 50.0
                       or sum(r["ret_net"] for r in recent) < 0):
            notify_send("⚠️ 做T系统进入逆风期",
                        "近20回合胜率/净利低于阈值，行情结构对网格不友好，请降低做T资金预期")
    except Exception:
        pass
    return fp


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    force = "--force" in sys.argv
    if mode == "plan":
        run_plan(force)
    elif mode == "monitor":
        run_monitor(force)
    else:
        run_close(force)


if __name__ == "__main__":
    main()
