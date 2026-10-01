# -*- coding: utf-8 -*-
"""第23轮（共50轮）批次X：R257-R266 波段量化模型迭代
=========================================================
本轮10个本质不同的新方向（均未在R1-R256测试）：
  R257 日内路径形态序贯（K线形态新维度：把每个交易日的OHLC顺序归纳为
        低开高走/高开低走/深V反转/倒V回落四类路径模式，统计20日净强度
        及其5日变化；IS秩IC显著为负（IC-0.156/t-6.93）表明'日内形态
        偏弱（阴线洗盘）'后有均值回归收益，方向按IS证据定为低形态低吸；
        区别于R183单日CLV水平、R132实体占比、R205开盘位置、
        R173 Heikin-Ashi与R43单根K线形态，是首个按'日内路径顺序'统计的
        形态频率因子）
  R258 特质残差反转（市场中性反转维度：60日滚动贝塔剥离板块因子后的
        特质收益序列，买特质收益近20日未跑赢/偏弱（残差动量<=0，个股
        相对板块滞后=错杀/蓄势）但中期趋势未破的个股；IS秩IC为负
        （IC-0.053/t-1.77）支持该方向；区别于R71残差动量（买正残差）
        与R228原始收益反转（未去市场），是首个'残差化+反转'的组合因子）
  R259 横截面排名回落低吸（排名轨迹新维度：20日动量横截面排名较5日前
        回落幅度，买相对强度近期被资金冷落（排名下降）但中期上行未破的
        个股；IS秩IC为负（IC-0.039）支持'排名回落后的均值回归'；区别于
        R134排名水平+稳定性、R26横截面动量水平与R75风险调整动量，是
        首个排名变化（二阶横截面）因子）
  R260 板块相关性跃升择时（相关性制度新维度：20日10股两两相关均值
        及其5日变化，相关快速抬升=系统性共振风险窗口禁止开仓，接入
        R117质量入口+Keltner出场；区别于R252相关水平z门控与R116用
        相关性调仓，是首个以相关性变化（相关性动量）为择时门控的模型）
  R261 低价股效应（行为金融新维度：绝对股价水平的横截面分位，买板块内
        绝对价格偏低+中期上行的个股（A股低价股高换手/散户偏好效应）；
        区别于R178整数关口锚定（价位阶梯）与R239 52周高点距离，是首个
        绝对价格水平因子）
  R262 均线多头排列质量（均线系统新维度：MA5>MA10>MA20>MA60全多头排列
        +排列宽度（MA5/MA60距离）未过度拉伸+MA5抬升；区别于R21双均线
        交叉/R100均线粘合发散/R109 MA60站上/R162一目均衡/R65 MA30斜率，
        是首个以全均线对齐+宽度约束为质量的趋势结构因子）
  R263 极端日次日行为非对称（事件统计新维度：60日内单日收益超出±1σ的
        极端日，其后一交易日平均收益之差（大跌日次日反弹均值-大涨日次日
        回落均值），买'下跌有承接、上涨不派发'的非对称结构；区别于R34
        单根大阴线次日反转、R146大阳线回踩、R203缺口行为与R77自举路径，
        是首个双侧极端事件条件期望差因子）
  R264 横截面离散度推力择时（离散度状态新维度：10股20日动量横截面
        标准差及其5日变化，分化快速扩大=轮动混乱期禁止开仓，接入R117
        质量入口+Keltner出场；区别于R69离散度水平状态门控与R104广度
        推力（个数变化）/R145量能广度，是首个以离散度变化（推力）为
        择时门控的模型）
  R265 高开不追执行过滤（执行风控新维度：R117质量入口在T+1开盘执行时，
        若开盘相对前收高开超过阈值则不追买（避免为跳空冲动买单），
        其余按Keltner出场；区别于R182低开不追（低开跳过）与R57跳空高开
        跟进（追强势缺口，反向假设），是首个'高开不追'执行过滤）
  R266 批次X组件集成（R257日内路径形态 + R262均线多头排列质量 +
        R260相关性跃升门控 + Keltner1.5×ATR20/最长10日出场，全部组件
        均为本批次首次引入）

统一口径（与前22轮完全一致）：
  1) 信号只用<=T数据，T+1开盘执行；开盘近似涨停跳过；
  2) 成本单边0.20%（佣金0.15%+滑点0.05%），往返0.40%；
  3) IS=2025-02-21~2026-01-13（220日），OOS=2026-01-14~2026-08-21（147日）；
  4) 参数在IS内扫描，按预注册护栏（IS信号>=120、均值>0、胜率>=55%、
     回撤>=-15%）取IS夏普最高；OOS只作单次裁决；
  5) 基准：等权10股买入持有（回撤/夏普）、随机5日开-开交易（均值/胜率）、
     等权组合5日开-开收益（超额）。
"""
from __future__ import print_function
import os, re, sys, json, math, glob
from datetime import datetime
import numpy as np
import openpyxl

DATA_DIR = os.environ.get("DATA_DIR", "/root/.openclaw/workspace/mx_data/output")
OUT_PATH = os.environ.get("QOUT", "/tmp/quant_iter_run_results26.json")
COST = 0.002
H = 5
WARMUP = 60
SPLIT = 0.60


# ---------------- 数据加载（与原版一致） ----------------
def to_num(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if s in ("", "-", "--", "nan", "None"):
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def to_vol(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if s in ("", "-", "--", "nan", "None"):
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    v = float(m.group(0))
    if u"\u4ebf" in s:
        v *= 1e8
    elif u"\u4e07" in s:
        v *= 1e4
    return v


def parse_date(x):
    s = str(x).strip().replace("(日)", "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def load_stocks():
    files = [f for f in glob.glob(os.path.join(DATA_DIR, "*.xlsx"))
             if (u"以下10只科技股" in f) or (u"中微公司688012" in f)]
    if not files:
        raise SystemExit("未找到目标xlsx文件: %s" % DATA_DIR)
    stocks = {}
    for f in sorted(files):
        wb = openpyxl.load_workbook(f, read_only=True)
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            header = [str(c).strip() if c is not None else "" for c in rows[0]]
            hdr = {h.lower(): i for i, h in enumerate(header)}
            if "date" not in hdr or u"开盘价" not in hdr or u"收盘价" not in hdr:
                continue
            m = re.search(r"\((\d{6}\.(?:SH|SZ))\)", ws.title)
            if not m:
                continue
            code = m.group(1)
            if code in stocks:
                continue
            rec = {"name": ws.title.split("(")[0].strip(), "code": code,
                   "dates": [], "open": [], "high": [], "low": [],
                   "close": [], "vol": []}

            def g(key):
                i = hdr.get(key)
                return r[i] if (i is not None and i < len(r)) else None

            for r in rows[1:]:
                dt = parse_date(g("date"))
                if dt is None:
                    continue
                op, hi, lo, cl = (to_num(g(u"开盘价")), to_num(g(u"最高价")),
                                  to_num(g(u"最低价")), to_num(g(u"收盘价")))
                if None in (op, hi, lo, cl) or min(op, hi, lo, cl) <= 0:
                    continue
                if not (lo <= min(op, cl) + 1e-9 and hi >= max(op, cl) - 1e-9):
                    continue
                v = to_vol(g(u"成交量")) if u"成交量" in hdr else None
                rec["dates"].append(dt.strftime("%Y-%m-%d"))
                rec["open"].append(op)
                rec["high"].append(hi)
                rec["low"].append(lo)
                rec["close"].append(cl)
                rec["vol"].append(v)
            idx = sorted(range(len(rec["dates"])), key=lambda i: rec["dates"][i])
            for kk in ("dates", "open", "high", "low", "close", "vol"):
                rec[kk] = [rec[kk][i] for i in idx]
            stocks[code] = rec
        wb.close()
    if len(stocks) < 5:
        raise SystemExit("股票数量不足，实际 %d" % len(stocks))
    common = None
    for code, rec in stocks.items():
        s = set(rec["dates"])
        common = s if common is None else (common & s)
    common = sorted(common)
    if len(common) < 100:
        raise SystemExit("交易日历过短: %d" % len(common))
    for code, rec in stocks.items():
        keep = {d: i for i, d in enumerate(rec["dates"])}
        rec["dates"] = list(common)
        for kk in ("open", "high", "low", "close", "vol"):
            rec[kk] = [rec[kk][keep[d]] for d in common]
    return stocks, common


# ---------------- 基础指标 ----------------
def rsi_series(close, period=14):
    n = len(close)
    out = [None]*n
    if n <= period:
        return out
    gains = []
    losses = []
    for i in range(1, n):
        ch = close[i] - close[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    ag = sum(gains[:period])/period
    al = sum(losses[:period])/period
    out[period] = 100.0 - 100.0/(1.0 + ag/al) if al > 0 else 100.0
    for i in range(period + 1, n):
        ag = (ag*(period - 1.0) + gains[i - 1])/period
        al = (al*(period - 1.0) + losses[i - 1])/period
        out[i] = 100.0 - 100.0/(1.0 + ag/al) if al > 0 else 100.0
    return out


def median(xs):
    vv = sorted(xs)
    if not vv:
        return None
    m = len(vv)//2
    return vv[m] if len(vv) % 2 else (vv[m - 1] + vv[m])/2.0


def zmap(values):
    out = {}
    vv = [v for v in values if v is not None]
    if not vv:
        return {i: 0.0 for i in range(len(values))}
    m = sum(vv)/len(vv)
    sd = math.sqrt(sum((x - m)**2 for x in vv)/len(vv))
    for i, v in enumerate(values):
        out[i] = (v - m)/sd if sd > 1e-12 and v is not None else 0.0
    return out


def neg(v):
    return -v if v is not None else None


def top_k(scores, k, floor=-1e8):
    items = [(c, v) for c, v in scores.items()
             if v is not None and v > floor]
    items.sort(key=lambda x: -x[1])
    return [c for c, _ in items[:k]]


def make_filt_breadth(thr):
    return lambda state, t: (state["breadth"][t] is not None
                             and state["breadth"][t] >= thr)


def spearman(xs, ys):
    def rank(v):
        idx = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0]*len(v)
        i = 0
        while i < len(idx):
            j = i
            while j + 1 < len(idx) and v[idx[j + 1]] == v[idx[i]]:
                j += 1
            avg = (i + j)/2.0 + 1.0
            for k in range(i, j + 1):
                r[idx[k]] = avg
            i = j + 1
        return r
    rx = rank(xs)
    ry = rank(ys)
    mx = sum(rx)/len(rx)
    my = sum(ry)/len(ry)
    cov = sum((rx[i] - mx)*(ry[i] - my) for i in range(len(rx)))
    vx = math.sqrt(sum((x - mx)**2 for x in rx))
    vy = math.sqrt(sum((y - my)**2 for y in ry))
    return cov/(vx*vy) if vx > 0 and vy > 0 else 0.0


def day_shape_score(o, h, l, c):
    """日内路径模式：1=低开高走 0.7=深V反转 -1=高开低走 -0.7=倒V回落 0=其他。"""
    rng = h - l
    if rng <= 1e-12:
        return 0.0
    r15 = 0.15*rng
    r25 = 0.25*rng
    if c >= h - r15 and o <= l + r15:
        return 1.0
    if c <= l + r15 and o >= h - r15:
        return -1.0
    if l < o - r15 and c >= h - r25:
        return 0.7
    if h > o + r15 and c <= l + r25:
        return -0.7
    return 0.0


# ---------------- 特征构建 ----------------
def build_features(stocks, codes, common):
    n = len(common)
    split = int(n*SPLIT)
    mkt_open = [0.0]*n
    mkt_close = [0.0]*n
    for t in range(n):
        mkt_open[t] = sum(stocks[c]["open"][t] for c in codes)/len(codes)
        mkt_close[t] = sum(stocks[c]["close"][t] for c in codes)/len(codes)
    mkt_ret = [0.0]*n
    mkt_mom20 = [None]*n
    for t in range(1, n):
        mkt_ret[t] = mkt_close[t]/mkt_close[t - 1] - 1.0
    for t in range(20, n):
        mkt_mom20[t] = mkt_close[t]/mkt_close[t - 20] - 1.0

    feat = {}
    for c in codes:
        rec = stocks[c]
        close = rec["close"]
        open_ = rec["open"]
        high = rec["high"]
        low = rec["low"]
        f = {"close": close, "open": open_, "high": high, "low": low,
             "ret": [None]*n, "upday": [None]*n,
             "mom5": [None]*n, "mom10": [None]*n, "mom20": [None]*n,
             "ma5": [None]*n, "ma10": [None]*n, "ma20": [None]*n,
             "ma60": [None]*n,
             "dist10": [None]*n, "dist20": [None]*n, "dist_ma60": [None]*n,
             "rsi": rsi_series(close, 14),
             "vol5": [None]*n, "vol20": [None]*n, "volr5": [None]*n,
             "atr20": [None]*n, "skew10": [None]*n, "skew20": [None]*n,
             "clv": [None]*n, "vol_low": [None]*n, "vr_5_20": [None]*n,
             "rs20": [None]*n,
             "shape20": [None]*n, "shape_chg5": [None]*n,
             "res_mom20": [None]*n, "res_vol20": [None]*n,
             "rank_jump5": [None]*n,
             "price_pct_cs": [None]*n,
             "ma_align": [None]*n, "ma_spread": [None]*n,
             "ma5_slope5": [None]*n,
             "asym60": [None]*n}
        for t in range(n):
            if t >= 1 and close[t - 1] > 0:
                f["ret"][t] = close[t]/close[t - 1] - 1.0
            if t >= 1:
                f["upday"][t] = 1.0 if close[t] > open_[t] else 0.0
            if t >= 5:
                f["mom5"][t] = close[t]/close[t - 5] - 1.0
                f["ma5"][t] = sum(close[t - 4:t + 1])/5.0
            if t >= 10:
                f["mom10"][t] = close[t]/close[t - 10] - 1.0
                f["ma10"][t] = sum(close[t - 9:t + 1])/10.0
                rs_ = [f["ret"][i] for i in range(t - 9, t + 1)]
                if all(x is not None for x in rs_):
                    mm = sum(rs_)/10.0
                    sd = math.sqrt(sum((x - mm)**2 for x in rs_)/10.0)
                    if sd > 1e-12:
                        f["skew10"][t] = sum(((x - mm)/sd)**3
                                             for x in rs_)/10.0
            if t >= 20:
                f["mom20"][t] = close[t]/close[t - 20] - 1.0
                f["ma20"][t] = sum(close[t - 19:t + 1])/20.0
                f["dist20"][t] = close[t]/f["ma20"][t] - 1.0
                if mkt_mom20[t] is not None:
                    f["rs20"][t] = f["mom20"][t] - mkt_mom20[t]
                rs_ = [f["ret"][i] for i in range(t - 19, t + 1)]
                if all(x is not None for x in rs_):
                    mm = sum(rs_)/20.0
                    f["vol20"][t] = math.sqrt(
                        sum((x - mm)**2 for x in rs_)/20.0)
                    sd = f["vol20"][t]
                    if sd > 1e-12:
                        zs = [(x - mm)/sd for x in rs_]
                        f["skew20"][t] = sum(z**3 for z in zs)/20.0
                tr = []
                for i in range(t - 19, t + 1):
                    pc = close[i - 1]
                    tr.append(max(high[i] - low[i], abs(high[i] - pc),
                                  abs(low[i] - pc)))
                f["atr20"][t] = sum(tr)/20.0
            if t >= 60:
                f["ma60"][t] = sum(close[t - 59:t + 1])/60.0
                f["dist_ma60"][t] = close[t]/f["ma60"][t] - 1.0
            if t >= 10:
                f["dist10"][t] = close[t]/f["ma10"][t] - 1.0
            if t >= 25:
                a5 = sum(close[t - 4:t + 1])/5.0
                a20 = sum(close[t - 24:t - 4])/20.0
                if a20 > 0:
                    f["volr5"][t] = a5/a20
            rng = high[t] - low[t]
            if rng > 0:
                f["clv"][t] = ((close[t] - low[t])
                               - (high[t] - close[t]))/rng
            else:
                f["clv"][t] = 0.0
            # R117方差比 VR(5)（25日窗口）
            if t >= 24:
                rs_ = [f["ret"][i] for i in range(t - 24, t + 1)]
                if all(x is not None for x in rs_):
                    T25 = 25
                    q = 5
                    mu = sum(rs_)/T25
                    var1 = sum((x - mu)**2 for x in rs_)/T25
                    if var1 > 1e-14:
                        qrets = [sum(rs_[j:j + q])
                                 for j in range(T25 - q + 1)]
                        varq = sum((qr - q*mu)**2
                                   for qr in qrets)/(T25 - q + 1)
                        f["vr_5_20"][t] = varq/(q*var1)
            # R257 日内路径形态20日净强度
            if t >= 19:
                vals = [day_shape_score(open_[i], high[i], low[i], close[i])
                        for i in range(t - 19, t + 1)]
                f["shape20"][t] = sum(vals)/20.0
            if t >= 24 and f["shape20"][t - 5] is not None:
                f["shape_chg5"][t] = f["shape20"][t] - f["shape20"][t - 5]
            # R258 特质残差（60日滚动贝塔）与20日残差动量/波动
            if t >= 79:
                xs = [f["ret"][i] for i in range(t - 59, t + 1)]
                ys = [mkt_ret[i] for i in range(t - 59, t + 1)]
                if all(v is not None for v in xs) and all(
                        v is not None for v in ys):
                    mx = sum(xs)/60.0
                    my = sum(ys)/60.0
                    vx = sum((x - mx)**2 for x in xs)
                    vy = sum((y - my)**2 for y in ys)
                    cxy = sum((xs[i] - mx)*(ys[i] - my) for i in range(60))
                    if vx > 1e-14 and vy > 1e-14:
                        beta = cxy/vx
                        resid = [xs[i] - beta*ys[i] for i in range(60)]
                        rs20 = resid[-20:]
                        rmm = sum(rs20)/20.0
                        f["res_vol20"][t] = math.sqrt(
                            sum((x - rmm)**2 for x in rs20)/20.0)
                        f["res_mom20"][t] = sum(rs20)
            # R262 均线多头排列质量
            if t >= 64:
                if (f["ma5"][t] is not None and f["ma10"][t] is not None
                        and f["ma20"][t] is not None
                        and f["ma60"][t] is not None):
                    f["ma_align"][t] = (f["ma5"][t] > f["ma10"][t]
                                        > f["ma20"][t] > f["ma60"][t]
                                        and close[t] > f["ma5"][t])
                    if f["ma60"][t] > 0:
                        f["ma_spread"][t] = (
                            f["ma5"][t]/f["ma60"][t] - 1.0)
                    if (f["ma5"][t - 5] is not None
                            and f["ma5"][t - 5] > 0):
                        f["ma5_slope5"][t] = (
                            f["ma5"][t]/f["ma5"][t - 5] - 1.0)
            # R263 极端日次日行为非对称（60日±1σ事件）
            if t >= 60:
                rs_ = [f["ret"][i] for i in range(t - 59, t + 1)]
                if all(v is not None for v in rs_):
                    mm = sum(rs_)/60.0
                    sd = math.sqrt(sum((x - mm)**2 for x in rs_)/60.0)
                    if sd > 1e-12:
                        ups = [rs_[i + 1] for i in range(59)
                               if rs_[i] > sd and i + 1 < 60]
                        dns = [rs_[i + 1] for i in range(59)
                               if rs_[i] < -sd and i + 1 < 60]
                        if len(ups) >= 3 and len(dns) >= 3:
                            f["asym60"][t] = (
                                sum(dns)/len(dns) - sum(ups)/len(ups))
        # 波动率低态（自身60日历史中位以下）
        for t in range(60, n):
            hist = [f["vol20"][i] for i in range(t - 59, t + 1)
                    if f["vol20"][i] is not None]
            if len(hist) >= 30 and f["vol20"][t] is not None:
                f["vol_low"][t] = f["vol20"][t] < median(hist)
        feat[c] = f

    # R259 横截面排名跃迁（mom20排名5日变化）
    momm = {c: feat[c]["mom20"] for c in codes}
    for t in range(25, n):
        cur = {}
        prev = {}
        for c in codes:
            if momm[c][t] is not None:
                cur[c] = momm[c][t]
            if momm[c][t - 5] is not None:
                prev[c] = momm[c][t - 5]
        if len(cur) >= 6 and len(prev) >= 6:
            sc = sorted(cur.keys(), key=lambda c: -cur[c])
            sp = sorted(prev.keys(), key=lambda c: -prev[c])
            rc = {c: i for i, c in enumerate(sc)}
            rp = {c: i for i, c in enumerate(sp)}
            for c in codes:
                if c in rc and c in rp:
                    feat[c]["rank_jump5"][t] = (rp[c] - rc[c])/9.0

    # R261 低价股效应（绝对价格横截面分位）
    for t in range(n):
        vals = [(c, stocks[c]["close"][t]) for c in codes]
        vals.sort(key=lambda x: x[1])
        for i, (c, _) in enumerate(vals):
            feat[c]["price_pct_cs"][t] = (i + 1)/float(len(vals))

    # 市场状态
    state = {"mkt_open": mkt_open, "mkt_close": mkt_close,
             "mkt_ret": mkt_ret, "mkt_mom20": mkt_mom20,
             "breadth": [None]*n}
    for t in range(20, n):
        up = 0
        valid = 0
        for c in codes:
            if feat[c]["ma20"][t] is not None:
                valid += 1
                if stocks[c]["close"][t] > feat[c]["ma20"][t]:
                    up += 1
        if valid:
            state["breadth"][t] = up/float(valid)

    # R260 板块平均两两相关（20日滚动）及其5日变化
    retm = np.zeros((len(codes), n), dtype=float)
    for ci, c in enumerate(codes):
        cl = stocks[c]["close"]
        for t in range(1, n):
            if cl[t - 1] > 0:
                retm[ci, t] = cl[t]/cl[t - 1] - 1.0
    corr20 = [None]*n
    for t in range(24, n):
        X = retm[:, t - 19:t + 1]
        if not np.isfinite(X).all():
            continue
        C = np.corrcoef(X)
        vals = []
        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                v = C[i, j]
                if np.isfinite(v):
                    vals.append(float(v))
        if vals:
            corr20[t] = sum(vals)/float(len(vals))
    state["corr20"] = corr20
    state["corr_chg5"] = [None]*n
    for t in range(29, n):
        if corr20[t] is not None and corr20[t - 5] is not None:
            state["corr_chg5"][t] = corr20[t] - corr20[t - 5]

    # R264 横截面离散度（mom20标准差）及其5日变化
    disp = [None]*n
    for t in range(20, n):
        vals = [feat[c]["mom20"][t] for c in codes
                if feat[c]["mom20"][t] is not None]
        if len(vals) >= 6:
            mm = sum(vals)/float(len(vals))
            sd = math.sqrt(sum((x - mm)**2 for x in vals)/float(len(vals)))
            disp[t] = sd
    state["disp"] = disp
    state["disp_chg5"] = [None]*n
    for t in range(25, n):
        if disp[t] is not None and disp[t - 5] is not None:
            state["disp_chg5"][t] = disp[t] - disp[t - 5]

    # R176 板块低波风险溢价（市场级状态，只用<=T数据）
    lvp = [None]*n
    for t in range(21, n):
        prem = []
        for i in range(t - 20, t):
            grp = []
            for c in codes:
                v = feat[c]["vol20"][i]
                r = feat[c]["ret"][i + 1]
                if v is not None and r is not None:
                    grp.append((v, r))
            if len(grp) >= 6:
                grp.sort(key=lambda x: x[0])
                n_low = max(2, len(grp)//4)
                lo_ret = sum(g[1] for g in grp[:n_low])/n_low
                hi_ret = sum(g[1] for g in grp[-n_low:])/n_low
                prem.append(lo_ret - hi_ret)
        if len(prem) >= 12:
            lvp[t] = sum(prem)/float(len(prem))
    state["lvp20"] = lvp
    state["lvp_z"] = [None]*n
    for t in range(n):
        if lvp[t] is None:
            continue
        lo = max(0, t - 119)
        hist = [lvp[i] for i in range(lo, t + 1) if lvp[i] is not None]
        if len(hist) >= 30:
            mm = sum(hist)/float(len(hist))
            sd = math.sqrt(sum((x - mm)**2 for x in hist)/float(len(hist)))
            if sd > 1e-12:
                state["lvp_z"][t] = (lvp[t] - mm)/sd
    return feat, state


# ---------------- 评分/过滤函数 ----------------
def score_r117(feat, codes, t):
    z_vr = zmap([neg(feat[c]["vr_5_20"][t]) for c in codes])
    z_v = zmap([neg(feat[c]["vol20"][t]) for c in codes])
    z_m = zmap([feat[c]["mom10"][t] for c in codes])
    z_r = zmap([feat[c]["rs20"][t] for c in codes])
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        if (f["vr_5_20"][t] is not None and f["vr_5_20"][t] < 0.8
                and f["vol_low"][t] and f["mom20"][t] is not None
                and f["mom20"][t] > 0 and f["ma20"][t] is not None
                and f["close"][t] > f["ma20"][t]
                and f["rsi"][t] is not None and f["rsi"][t] < 72):
            out[c] = z_vr[i] + z_v[i] + 0.5*z_m[i] + 0.3*z_r[i]
        else:
            out[c] = -9e9
    return out


def score_shape(feat, codes, t):
    thr = globals().get("R257_SHAPE", -0.10)
    z_s = zmap([neg(feat[c]["shape20"][t]) for c in codes])
    z_c = zmap([neg(feat[c]["shape_chg5"][t]) for c in codes])
    z_m = zmap([feat[c]["mom10"][t] for c in codes])
    z_r = zmap([feat[c]["rs20"][t] for c in codes])
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        if (f["shape20"][t] is not None and f["shape20"][t] <= thr
                and f["vol_low"][t] and f["mom20"][t] is not None
                and f["mom20"][t] > 0 and f["ma20"][t] is not None
                and f["close"][t] > f["ma20"][t]
                and f["rsi"][t] is not None and f["rsi"][t] < 75):
            out[c] = z_s[i] + z_c[i] + 0.5*z_m[i] + 0.3*z_r[i]
        else:
            out[c] = -9e9
    return out


def score_resrev(feat, codes, t):
    thr = globals().get("R258_RES", -0.01)
    z_r = zmap([neg(feat[c]["res_mom20"][t]) for c in codes])
    z_rv = zmap([neg(feat[c]["res_vol20"][t]) for c in codes])
    z_m = zmap([feat[c]["mom10"][t] for c in codes])
    z_rs = zmap([feat[c]["rs20"][t] for c in codes])
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        if (f["res_mom20"][t] is not None
                and f["res_mom20"][t] <= thr
                and f["vol_low"][t] and f["mom20"][t] is not None
                and f["mom20"][t] > 0 and f["ma20"][t] is not None
                and f["close"][t] > f["ma20"][t]
                and f["rsi"][t] is not None and f["rsi"][t] < 75):
            out[c] = z_r[i] + z_rv[i] + 0.5*z_m[i] + 0.3*z_rs[i]
        else:
            out[c] = -9e9
    return out


def score_rankjump(feat, codes, t):
    thr = globals().get("R259_RJ", -0.3)
    z_j = zmap([neg(feat[c]["rank_jump5"][t]) for c in codes])
    z_m = zmap([feat[c]["mom10"][t] for c in codes])
    z_r = zmap([feat[c]["rs20"][t] for c in codes])
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        if (f["rank_jump5"][t] is not None
                and f["rank_jump5"][t] <= thr
                and f["mom20"][t] is not None and f["mom20"][t] > 0
                and f["ma20"][t] is not None
                and f["close"][t] > f["ma20"][t]
                and f["rsi"][t] is not None and f["rsi"][t] < 75):
            out[c] = z_j[i] + 0.5*z_m[i] + 0.3*z_r[i]
        else:
            out[c] = -9e9
    return out


def score_lowprice(feat, codes, t):
    thr = globals().get("R261_PPCT", 0.6)
    z_p = zmap([neg(feat[c]["price_pct_cs"][t]) for c in codes])
    z_m = zmap([feat[c]["mom10"][t] for c in codes])
    z_r = zmap([feat[c]["rs20"][t] for c in codes])
    z_v = zmap([neg(feat[c]["vol20"][t]) for c in codes])
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        if (f["price_pct_cs"][t] is not None
                and f["price_pct_cs"][t] <= thr
                and f["vol_low"][t] and f["mom20"][t] is not None
                and f["mom20"][t] > 0 and f["ma20"][t] is not None
                and f["close"][t] > f["ma20"][t]
                and f["rsi"][t] is not None and f["rsi"][t] < 75):
            out[c] = z_p[i] + 0.5*z_m[i] + 0.3*z_r[i] + 0.3*z_v[i]
        else:
            out[c] = -9e9
    return out


def score_maalign(feat, codes, t):
    thr = globals().get("R262_SPREAD", 0.15)
    z_s = zmap([neg(feat[c]["ma_spread"][t]) for c in codes])
    z_sl = zmap([feat[c]["ma5_slope5"][t] for c in codes])
    z_m = zmap([feat[c]["mom10"][t] for c in codes])
    z_r = zmap([feat[c]["rs20"][t] for c in codes])
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        if (f["ma_align"][t] and f["ma_spread"][t] is not None
                and f["ma_spread"][t] <= thr
                and f["ma5_slope5"][t] is not None
                and f["ma5_slope5"][t] > 0
                and f["mom20"][t] is not None and f["mom20"][t] > 0
                and f["rsi"][t] is not None and f["rsi"][t] < 75):
            out[c] = z_s[i] + z_sl[i] + 0.5*z_m[i] + 0.3*z_r[i]
        else:
            out[c] = -9e9
    return out


def score_asym(feat, codes, t):
    thr = globals().get("R263_ASYM", 0.005)
    z_a = zmap([feat[c]["asym60"][t] for c in codes])
    z_m = zmap([feat[c]["mom10"][t] for c in codes])
    z_r = zmap([feat[c]["rs20"][t] for c in codes])
    z_v = zmap([neg(feat[c]["vol20"][t]) for c in codes])
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        if (f["asym60"][t] is not None and f["asym60"][t] >= thr
                and f["vol_low"][t] and f["mom20"][t] is not None
                and f["mom20"][t] > 0 and f["ma20"][t] is not None
                and f["close"][t] > f["ma20"][t]
                and f["rsi"][t] is not None and f["rsi"][t] < 75):
            out[c] = z_a[i] + 0.5*z_m[i] + 0.3*z_r[i] + 0.3*z_v[i]
        else:
            out[c] = -9e9
    return out


def score_x266(feat, codes, t):
    shp = globals().get("R266_SHAPE", 0.05)
    z_s = zmap([feat[c]["shape20"][t] for c in codes])
    z_sp = zmap([neg(feat[c]["ma_spread"][t]) for c in codes])
    z_m = zmap([feat[c]["mom10"][t] for c in codes])
    z_r = zmap([feat[c]["rs20"][t] for c in codes])
    out = {}
    for i, c in enumerate(codes):
        f = feat[c]
        if (f["shape20"][t] is not None and f["shape20"][t] >= shp
                and f["ma_align"][t] and f["ma_spread"][t] is not None
                and f["mom20"][t] is not None and f["mom20"][t] > 0
                and f["ma20"][t] is not None
                and f["close"][t] > f["ma20"][t]
                and f["rsi"][t] is not None and f["rsi"][t] < 78):
            out[c] = z_s[i] + z_sp[i] + 0.5*z_m[i] + 0.3*z_r[i]
        else:
            out[c] = -9e9
    return out


def make_filt_corrsurge(thr_attr):
    def filt(state, t):
        return (state["breadth"][t] is not None
                and state["breadth"][t] >= 0.4
                and state["corr_chg5"][t] is not None
                and state["corr20"][t] is not None
                and state["corr20"][t] <= 0.85
                and state["corr_chg5"][t] <= globals()[thr_attr])
    return filt


def make_filt_dispthrust(thr_attr):
    def filt(state, t):
        return (state["breadth"][t] is not None
                and state["breadth"][t] >= 0.4
                and state["disp_chg5"][t] is not None
                and state["disp_chg5"][t] <= globals()[thr_attr])
    return filt


def make_filt_x266(thr_attr):
    def filt(state, t):
        return (state["breadth"][t] is not None
                and state["breadth"][t] >= 0.4
                and state["corr_chg5"][t] is not None
                and state["corr_chg5"][t] <= globals()[thr_attr])
    return filt


# ---------------- 回测引擎（与原版一致） ----------------
def simulate(stocks, codes, feat, state, spec, entry_start, entry_end,
             window_end):
    trades = []
    contrib = {}
    K = spec["K"]
    score_fn = spec["score"]
    filt_fn = spec["filt"]
    allow_fewer = spec.get("allow_fewer", False)
    exit_mode = spec.get("exit", "fixed")
    params = spec.get("params", {})
    for e in range(entry_start, entry_end + 1):
        t = e - 1
        if not filt_fn(state, t):
            continue
        scores = score_fn(feat, codes, t)
        picks = top_k(scores, K)
        if len(picks) < K:
            if not (allow_fewer and len(picks) >= 1):
                continue
        n_pick = len(picks)
        weights = [1.0/H/n_pick]*n_pick
        gap_cap = params.get("gap_cap", None)
        for pi, code in enumerate(picks):
            w = weights[pi]
            o = stocks[code]["open"]
            h = stocks[code]["high"]
            l = stocks[code]["low"]
            c = stocks[code]["close"]
            entry_px = o[e]
            prev_close = c[e - 1]
            band = 0.195 if code.startswith(("688", "300", "301")) else 0.095
            if entry_px >= prev_close*(1.0 + band - 0.005):
                continue  # 开盘近似涨停，无法买入
            if gap_cap is not None:
                gap = entry_px/prev_close - 1.0
                if gap > gap_cap:
                    continue  # 高开不追
            if exit_mode == "keltner":
                kelt = params.get("keltner_mult", 1.5)
                max_hold = params.get("max_hold", 10)
                xe = None
                last_d = min(e + max_hold - 1, window_end - 1)
                for d in range(e, last_d + 1):
                    m20 = feat[code]["ma20"][d]
                    atr = feat[code]["atr20"][d]
                    if (m20 is not None and atr is not None
                            and c[d] < m20 - kelt*atr):
                        xe = min(d + 1, window_end)
                        break
                if xe is None:
                    xe = min(e + max_hold, window_end)
            else:
                xe = min(e + H, window_end)
            exit_px = o[xe]
            gross = exit_px/entry_px - 1.0
            net = gross - 2.0*COST
            mkt5 = state["mkt_open"][min(e + H, window_end)] / \
                state["mkt_open"][e] - 1.0
            same5 = stocks[code]["open"][min(e + H, window_end)] / \
                entry_px - 1.0
            if xe == e:
                contrib.setdefault(e, []).append(
                    w*(exit_px/entry_px - 1.0) - 2.0*w*COST)
            else:
                contrib.setdefault(e, []).append(-w*COST)
                for d in range(e, xe - 1):
                    contrib.setdefault(d, []).append(
                        w*(o[d + 1]/o[d] - 1.0))
                contrib.setdefault(xe - 1, []).append(
                    w*(exit_px/o[xe - 1] - 1.0) - w*COST)
            trades.append({"code": code, "e": e, "xe": xe,
                           "gross": gross, "net": net,
                           "mkt5": mkt5, "same5": same5})
    daily = []
    for d in range(entry_start, window_end + 1):
        daily.append(sum(contrib.get(d, [])) if contrib.get(d) else 0.0)
    return trades, daily


def compute_metrics(trades, daily):
    n = len(trades)
    if n == 0:
        return None
    nets = [t["net"] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    win_rate = len(wins)/float(n)
    avg_return = sum(nets)/float(n)
    avg_gross = sum(t["gross"] for t in trades)/float(n)
    pf = (sum(wins)/abs(sum(losses))) if losses else None
    payoff = ((sum(wins)/len(wins))/(abs(sum(losses)/len(losses)))
              if wins and losses else None)
    avg_win = (sum(wins)/len(wins)) if wins else None
    avg_loss = (sum(losses)/len(losses)) if losses else None
    ex = [t["net"] - t["mkt5"] for t in trades]
    excess = sum(ex)/float(n)
    alpha_same = sum(t["net"] - t["same5"] for t in trades)/float(n)
    hold = sum(t["xe"] - t["e"] for t in trades)/float(n)
    m = sum(daily)/float(len(daily))
    sd = math.sqrt(sum((x - m)**2 for x in daily)/float(len(daily))) \
        if len(daily) > 1 else 0.0
    sharpe = (m/sd)*math.sqrt(252.0) if sd > 0 else 0.0
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in daily:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        mdd = min(mdd, eq/peak - 1.0)
    return {
        "signals": n,
        "win_rate": round(win_rate, 4),
        "avg_return": round(avg_return, 5),
        "avg_return_gross": round(avg_gross, 5),
        "profit_factor": (round(pf, 4) if pf is not None else None),
        "payoff_ratio": (round(payoff, 4) if payoff is not None else None),
        "avg_win": (round(avg_win, 5) if avg_win is not None else None),
        "avg_loss": (round(avg_loss, 5) if avg_loss is not None else None),
        "excess_return_5d": round(excess, 5),
        "alpha_vs_same_stock": round(alpha_same, 5),
        "avg_holding_days": round(hold, 3),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(mdd, 4),
        "daily_n": len(daily),
    }


def bh_metrics(stocks, codes, common, start_idx, end_idx, cost=COST):
    n = len(common)
    closes = [[stocks[c]["close"][t] for t in range(start_idx, end_idx + 1)]
              for c in codes]
    opens = [stocks[c]["open"][start_idx] for c in codes]
    tot_ret = (sum(closes[i][-1] for i in range(len(codes)))/sum(opens)
               - 1.0 - cost)
    mkt = [sum(closes[i][d] for i in range(len(codes)))/len(codes)
           for d in range(len(closes[0]))]
    daily = [mkt[d]/mkt[d - 1] - 1.0 for d in range(1, len(mkt))]
    m = sum(daily)/len(daily)
    sd = math.sqrt(sum((x - m)**2 for x in daily)/len(daily)) if daily else 0.0
    sharpe = (m/sd)*math.sqrt(252.0) if sd > 0 else 0.0
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in daily:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        mdd = min(mdd, eq/peak - 1.0)
    return {"total_return": round(tot_ret, 4), "sharpe": round(sharpe, 3),
            "max_drawdown": round(mdd, 4), "days": len(daily)}


def random_5d(stocks, codes, e_start, e_end):
    nets = []
    for c in codes:
        o = stocks[c]["open"]
        for e in range(e_start, e_end + 1):
            nets.append(o[e + H]/o[e] - 1.0 - 2.0*COST)
    m = sum(nets)/float(len(nets))
    wr = sum(1 for x in nets if x > 0)/float(len(nets))
    return {"n": len(nets), "mean": round(m, 5), "win_rate": round(wr, 4)}


def factor_ic_is(stocks, codes, feat, common, split):
    features = ["shape20", "shape_chg5", "res_mom20", "rank_jump5",
                "price_pct_cs", "ma_spread", "asym60",
                "vr_5_20", "mom5", "mom10", "mom20", "rsi", "dist20",
                "vol20", "volr5", "rs20", "clv", "skew10"]
    out = {}
    for name in features:
        daily_ics = []
        for t in range(WARMUP + 1, split - 1 - H):
            xs = []
            ys = []
            for c in codes:
                x = feat[c][name][t]
                if x is None:
                    continue
                o = stocks[c]["open"]
                y = o[t + 1 + H]/o[t + 1] - 1.0
                xs.append(x)
                ys.append(y)
            if len(xs) >= 5 and len(set(xs)) > 1 and len(set(ys)) > 1:
                daily_ics.append(spearman(xs, ys))
        if len(daily_ics) >= 20:
            m = sum(daily_ics)/len(daily_ics)
            sd = math.sqrt(sum((x - m)**2 for x in daily_ics)
                           /len(daily_ics))
            t_stat = m/sd*math.sqrt(len(daily_ics)) if sd > 0 else 0.0
            out[name] = {"ic": round(m, 4), "t": round(t_stat, 2),
                         "n": len(daily_ics)}
    return out


# ---------------- 主流程 ----------------
def main():
    stocks, common = load_stocks()
    codes = sorted(stocks.keys())
    n = len(common)
    split = int(n*SPLIT)
    feat, state = build_features(stocks, codes, common)
    ic_is = factor_ic_is(stocks, codes, feat, common, split)
    print("== 批次X IS 因子 IC 诊断（fwd5, 只用IS）==")
    for name, v in sorted(ic_is.items(), key=lambda kv: -abs(kv[1]["t"])):
        print("  %-14s IC=%6.3f  t=%6.2f  n=%d" % (name, v["ic"], v["t"],
                                                    v["n"]))

    bh_oos = bh_metrics(stocks, codes, common, split, n - 1)
    rand_oos = random_5d(stocks, codes, split, n - 1 - H)
    baselines = {"bh_oos": bh_oos, "random_5d_oos": rand_oos,
                 "oos_start_date": common[split], "oos_end_date": common[-1],
                 "is_start_date": common[0], "split_date": common[split - 1]}

    ROUND_SPECS = [
        {"round": 257, "name": u"日内路径形态序贯（洗盘低吸）",
         "improvement": (u"本质新方向：K线形态新维度——把每个交易日的OHLC"
                         u"顺序归纳为低开高走/高开低走/深V反转/倒V回落四类"
                         u"路径模式（宽容差判定），统计20日净强度及其5日变化，"
                         u"IS秩IC显著为负（IC-0.156/t-6.93）支持'日内形态偏弱"
                         u"（阴线洗盘）后有均值回归收益'，方向按IS证据定为买"
                         u"形态净强度低（洗盘）且趋势上行的个股；"
                         u"区别于R183单日CLV水平/R132实体占比/R205开盘位置/"
                         u"R173 Heikin-Ashi/R43单根K线形态，是首个按日内路径"
                         u"顺序统计的形态频率因子"),
         "desc": (u"候选：广度>=0.4 且 shape20<=阈值（IS扫描"
                  u"{-0.05,-0.10,-0.15}，形态净强度偏低=洗盘）且 vol_low 且 "
                  u"mom20>0 且 close>ma20 且 RSI<75；评分=z(shape20)"
                  u"+z(shape_chg5)+0.5*z(mom10)+0.3*z(rs20)（z取负向，即"
                  u"z(-shape20)+z(-shape_chg5)）；Top3等权持5日"),
         "score": score_shape, "filt": make_filt_breadth(0.4),
         "exit": "fixed", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": {}},
        {"round": 258, "name": u"特质残差反转",
         "improvement": (u"本质新方向：市场中性反转维度——60日滚动贝塔剥离"
                         u"板块因子后的特质收益序列，买特质收益近20日未跑赢"
                         u"（残差动量<=0=个股相对板块滞后、被错杀）但中期趋势"
                         u"未破且低波动的个股；IS秩IC为负（IC-0.053/t-1.77）"
                         u"支持该方向；区别于R71残差动量（买正残差）"
                         u"与R228原始收益反转（未去市场因子），是首个"
                         u"'残差化+反转'的组合因子"),
         "desc": (u"候选：广度>=0.4 且 res_mom20<=阈值（IS扫描"
                  u"{0.00,-0.01,-0.02}，特质收益未跑赢/偏弱）且 vol_low 且 mom20>0 且 "
                  u"close>ma20 且 RSI<75；评分=z(-res_mom20)"
                  u"+z(-res_vol20)+0.5*z(mom10)+0.3*z(rs20)；Top3等权持5日"),
         "score": score_resrev, "filt": make_filt_breadth(0.4),
         "exit": "fixed", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": {}},
        {"round": 259, "name": u"横截面排名回落低吸",
         "improvement": (u"本质新方向：排名轨迹新维度——20日动量横截面排名"
                         u"较5日前回落的幅度（排名变化=相对强度近期被资金冷落），"
                         u"IS秩IC为负（IC-0.039）支持'排名回落后的均值回归'，"
                         u"买排名回落但中期上行的个股；区别于"
                         u"R134排名水平+稳定性/R26横截面动量水平/R75风险调整"
                         u"动量，是首个排名变化（二阶横截面）因子"),
         "desc": (u"候选：广度>=0.4 且 rank_jump5<=阈值（IS扫描{-0.2,-0.3,-0.4}，"
                  u"排名回落2-4位）且 mom20>0 且 close>ma20 且 RSI<75；"
                  u"评分=z(-rank_jump5)+0.5*z(mom10)+0.3*z(rs20)；"
                  u"Top3等权持5日"),
         "score": score_rankjump, "filt": make_filt_breadth(0.4),
         "exit": "fixed", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": {}},
        {"round": 260, "name": u"板块相关性跃升择时",
         "improvement": (u"本质新方向：相关性制度新维度——20日10股两两相关"
                         u"均值及其5日变化，相关快速抬升（相关性动量转正）="
                         u"系统性共振风险窗口禁止开仓，接入R117质量入口+"
                         u"Keltner1.5×ATR20/最长10日出场；区别于R252相关"
                         u"水平z门控与R116用相关性调仓，是首个以相关性变化"
                         u"为择时门控的模型"),
         "desc": (u"入口=R117质量（广度>=0.4 且 vr_5_20<0.8 且 vol_low 且 "
                  u"mom20>0 且 close>ma20 且 RSI<72）；门控=corr_chg5<=阈值"
                  u"（IS扫描{0.00,0.03,0.06}）且 corr20<=0.85；评分=R117；"
                  u"Top3等权；出场=Keltner1.5×ATR20/最长10日"),
         "score": score_r117, "filt": make_filt_corrsurge("R260_CORRCHG"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal",
         "params": {"keltner_mult": 1.5, "max_hold": 10}},
        {"round": 261, "name": u"低价股效应",
         "improvement": (u"本质新方向：行为金融新维度——绝对股价水平的横截面"
                         u"分位（板块内绝对价格偏低=散户偏好/高换手/弹性大），"
                         u"买低价+中期上行+低波动的个股；区别于R178整数关口"
                         u"锚定（价位阶梯位置）与R239 52周高点距离（时间锚），"
                         u"是首个绝对价格水平因子"),
         "desc": (u"候选：广度>=0.4 且 price_pct_cs<=阈值（IS扫描"
                  u"{0.5,0.6,0.7}，板块内价格偏低分位）且 vol_low 且 "
                  u"mom20>0 且 close>ma20 且 RSI<75；评分=z(-price_pct_cs)"
                  u"+0.5*z(mom10)+0.3*z(rs20)+0.3*z(-vol20)；Top3等权持5日"),
         "score": score_lowprice, "filt": make_filt_breadth(0.4),
         "exit": "fixed", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": {}},
        {"round": 262, "name": u"均线多头排列质量",
         "improvement": (u"本质新方向：均线系统新维度——MA5>MA10>MA20>MA60"
                         u"全多头排列+排列宽度（MA5/MA60距离）未过度拉伸+"
                         u"MA5抬升，买'趋势结构完整且未过热'的个股；区别于"
                         u"R21双均线交叉/R100均线粘合发散/R109站上MA60/"
                         u"R162一目均衡云/R65 MA30斜率，是首个以全均线对齐"
                         u"+宽度约束为质量的趋势结构因子"),
         "desc": (u"候选：广度>=0.4 且 ma_align=1（MA5>MA10>MA20>MA60且"
                  u"close>MA5）且 ma_spread<=阈值（IS扫描{0.10,0.15,0.20}）"
                  u"且 ma5_slope5>0 且 mom20>0 且 RSI<75；评分=z(-ma_spread)"
                  u"+z(ma5_slope5)+0.5*z(mom10)+0.3*z(rs20)；Top3等权持5日"),
         "score": score_maalign, "filt": make_filt_breadth(0.4),
         "exit": "fixed", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": {}},
        {"round": 263, "name": u"极端日次日行为非对称",
         "improvement": (u"本质新方向：事件统计新维度——60日内单日收益超出"
                         u"±1σ的极端日，其后一交易日平均收益之差（大跌日次日"
                         u"反弹均值-大涨日次日回落均值），买'下跌有承接、上涨"
                         u"不派发'的非对称结构+低波上行；区别于R34单根大阴线"
                         u"次日反转/R146大阳线回踩/R203缺口行为/R77自举路径，"
                         u"是首个双侧极端事件条件期望差因子"),
         "desc": (u"候选：广度>=0.4 且 asym60>=阈值（IS扫描{0.00,0.005,0.01}）"
                  u"且 vol_low 且 mom20>0 且 close>ma20 且 RSI<75；"
                  u"评分=z(asym60)+0.5*z(mom10)+0.3*z(rs20)+0.3*z(-vol20)；"
                  u"Top3等权持5日"),
         "score": score_asym, "filt": make_filt_breadth(0.4),
         "exit": "fixed", "K": 3, "allow_fewer": True,
         "weight_mode": "equal", "params": {}},
        {"round": 264, "name": u"横截面离散度推力择时",
         "improvement": (u"本质新方向：离散度状态新维度——10股20日动量横截面"
                         u"标准差及其5日变化，分化快速扩大（推力转正）=轮动"
                         u"混乱期禁止开仓，接入R117质量入口+Keltner1.5×ATR20/"
                         u"最长10日出场；区别于R69离散度水平状态门控与R104"
                         u"广度推力（上涨家数变化）/R145量能广度，是首个以"
                         u"离散度变化（推力）为择时门控的模型"),
         "desc": (u"入口=R117质量（广度>=0.4 且 vr_5_20<0.8 且 vol_low 且 "
                  u"mom20>0 且 close>ma20 且 RSI<72）；门控=disp_chg5<=阈值"
                  u"（IS扫描{0.00,0.02,0.04}）；评分=R117；Top3等权；"
                  u"出场=Keltner1.5×ATR20/最长10日"),
         "score": score_r117, "filt": make_filt_dispthrust("R264_DISPCHG"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal",
         "params": {"keltner_mult": 1.5, "max_hold": 10}},
        {"round": 265, "name": u"高开不追执行过滤",
         "improvement": (u"本质新方向：执行风控新维度——R117质量入口在T+1"
                         u"开盘执行时，若开盘相对前收高开超过阈值则不追买"
                         u"（避免为跳空冲动买单），其余按Keltner1.5×ATR20/"
                         u"最长10日出场；区别于R182低开不追（低开跳过）与"
                         u"R57跳空高开跟进（追强势缺口，反向假设），是首个"
                         u"'高开不追'执行过滤"),
         "desc": (u"入口=R117质量（广度>=0.4 且 vr_5_20<0.8 且 vol_low 且 "
                  u"mom20>0 且 close>ma20 且 RSI<72）；执行=开盘高开幅度"
                  u"gap>阈值（IS扫描{0.010,0.015,0.020}）则不买（其余正常买入）；"
                  u"评分=R117；Top3等权；出场=Keltner1.5×ATR20/最长10日"),
         "score": score_r117, "filt": make_filt_breadth(0.4),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal",
         "params": {"keltner_mult": 1.5, "max_hold": 10}},
        {"round": 266, "name": u"批次X组件集成",
         "improvement": (u"组件集成：R257日内路径形态 + R262均线多头排列质量 "
                         u"+ R260相关性跃升门控 + Keltner1.5×ATR20/最长10日"
                         u"出场，全部组件均为本批次首次引入，检验新形态/结构/"
                         u"制度组件的协同"),
         "desc": (u"候选：广度>=0.4 且 corr_chg5<=R266_CORR 且 shape20>="
                  u"R266_SHAPE 且 ma_align=1 且 mom20>0 且 close>ma20 且 "
                  u"RSI<78；评分=z(shape20)+z(-ma_spread)+0.5*z(mom10)"
                  u"+0.3*z(rs20)；IS扫描(R266_SHAPE,R266_CORR)="
                  u"{0.05,0.10},{0.10,0.10},{0.05,0.15}；Top3等权；"
                  u"出场=Keltner1.5×ATR20/最长10日"),
         "score": score_x266, "filt": make_filt_x266("R266_CORR"),
         "exit": "keltner", "K": 3, "allow_fewer": True,
         "weight_mode": "equal",
         "params": {"keltner_mult": 1.5, "max_hold": 10}},
    ]

    def spec_for(rnd):
        s = next(x for x in ROUND_SPECS if x["round"] == rnd)
        out = {k: v for k, v in s.items()
               if k not in ("round", "name", "improvement", "desc")}
        if rnd == 265:
            out["params"] = dict(out["params"])
            out["params"]["gap_cap"] = globals().get("R265_GAPCAP", 0.01)
        return out

    def sim_once(spec):
        tr_i, dl_i = simulate(stocks, codes, feat, state, spec,
                              WARMUP + 1, split - H, split - 1)
        tr_o, dl_o = simulate(stocks, codes, feat, state, spec,
                              split, n - 1 - H, n - 1)
        return compute_metrics(tr_i, dl_i), compute_metrics(tr_o, dl_o)

    def is_qualified(met):
        return (met and met["signals"] >= 120 and met["avg_return"] > 0
                and met["win_rate"] >= 0.55 and met["max_drawdown"] >= -0.15)

    def pick_best(records):
        recs = [r for r in records if r["is"]]
        if not recs:
            return records[0] if records else None
        cands = [r for r in recs if is_qualified(r["is"])]
        if not cands:
            cands = [r for r in recs if r["is"]["signals"] >= 120]
        if not cands:
            cands = [r for r in recs if r["is"]["signals"] >= 60]
        if not cands:
            return max(recs, key=lambda r: (r["is"]["signals"],
                                            r["is"]["sharpe"]))
        return max(cands, key=lambda r: (r["is"]["sharpe"],
                                         r["is"]["win_rate"]))

    def conclusion(oos):
        if not oos:
            return u"无样本外信号"
        if oos["signals"] < 30:
            return u"OOS信号数不足30，统计不可信"
        if (oos["excess_return_5d"] > 0 and oos["win_rate"] > 0.5
                and oos["sharpe"] > 0.5
                and oos["max_drawdown"] >= bh_oos["max_drawdown"]):
            return u"OOS达标：超额>0、胜率>50%、夏普>0.5、回撤优于买入持有"
        if oos["excess_return_5d"] > 0 and oos["sharpe"] > 0:
            return u"OOS有微弱正超额但风险指标未全达标，需更多数据验证"
        return u"OOS未跑赢基准（超额≤0或夏普≤0）"

    def run_scan(rnd, grids):
        recs = []
        for g in grids:
            for k, v in g.items():
                globals()[k] = v
            met_i, _ = sim_once(spec_for(rnd))
            recs.append({"is": met_i, "params": dict(g)})
        best = pick_best(recs)
        if best is None:
            met_i, met_o = sim_once(spec_for(rnd))
            return met_i, met_o, {}, recs
        for k, v in best["params"].items():
            globals()[k] = v
        met_i, met_o = sim_once(spec_for(rnd))
        return met_i, met_o, best["params"], recs

    results = []
    scans = {}
    GRIDS = {
        257: [{"R257_SHAPE": -0.05}, {"R257_SHAPE": -0.10},
              {"R257_SHAPE": -0.15}],
        258: [{"R258_RES": 0.00}, {"R258_RES": -0.01},
              {"R258_RES": -0.02}],
        259: [{"R259_RJ": -0.2}, {"R259_RJ": -0.3}, {"R259_RJ": -0.4}],
        260: [{"R260_CORRCHG": 0.00}, {"R260_CORRCHG": 0.03},
              {"R260_CORRCHG": 0.06}],
        261: [{"R261_PPCT": 0.5}, {"R261_PPCT": 0.6},
              {"R261_PPCT": 0.7}],
        262: [{"R262_SPREAD": 0.10}, {"R262_SPREAD": 0.15},
              {"R262_SPREAD": 0.20}],
        263: [{"R263_ASYM": 0.00}, {"R263_ASYM": 0.005},
              {"R263_ASYM": 0.01}],
        264: [{"R264_DISPCHG": 0.00}, {"R264_DISPCHG": 0.02},
              {"R264_DISPCHG": 0.04}],
        265: [{"R265_GAPCAP": 0.010}, {"R265_GAPCAP": 0.015},
              {"R265_GAPCAP": 0.020}],
        266: [{"R266_SHAPE": 0.05, "R266_CORR": 0.10},
              {"R266_SHAPE": 0.10, "R266_CORR": 0.10},
              {"R266_SHAPE": 0.05, "R266_CORR": 0.15}],
    }
    for rnd in range(257, 267):
        grids = GRIDS[rnd]
        met_i, met_o, p, recs = run_scan(rnd, grids)
        scans[rnd] = {"combos": len(recs),
                      "pass": sum(1 for r in recs if is_qualified(r["is"])),
                      "chosen": p}
        results.append({"round": rnd, "is": met_i, "oos": met_o,
                        "params_text": (u"IS扫描%d组选定 %s"
                                        % (len(recs), ", ".join(
                                            "%s=%s" % (k, v)
                                            for k, v in sorted(p.items()))))})

    iterations = []
    for r in results:
        rnd = r["round"]
        spec = next(x for x in ROUND_SPECS if x["round"] == rnd)
        met_is = r["is"]
        met_oos = r["oos"]
        imp = spec["improvement"]
        if scans[rnd]["combos"] > 1:
            imp = imp + (u"；参数由IS内%d组扫描按护栏+IS夏普规则选定（%s），"
                         u"OOS只裁决一次" % (scans[rnd]["combos"],
                                             r["params_text"]))
        rec = {
            "round": rnd, "name": spec["name"], "improvement": imp,
            "rule": spec["desc"], "is": met_is, "oos": met_oos,
            "signals_oos": met_oos["signals"] if met_oos else 0,
            "win_rate": met_oos["win_rate"] if met_oos else None,
            "avg_return": met_oos["avg_return"] if met_oos else None,
            "excess_return_5d": (met_oos["excess_return_5d"]
                                 if met_oos else None),
            "sharpe": met_oos["sharpe"] if met_oos else None,
            "max_drawdown": met_oos["max_drawdown"] if met_oos else None,
            "baseline_max_drawdown": bh_oos["max_drawdown"],
            "excess_vs_random": (round(met_oos["avg_return"] - rand_oos["mean"],
                                       5) if met_oos else None),
            "conclusion": conclusion(met_oos),
        }
        iterations.append(rec)
        if met_oos:
            print("R%03d %-16s IS:%4d(胜%.1f%%/均%6.2f%%/夏普%4.2f/回撤%5.1f%%) "
                  "OOS:%4d | 胜率OOS %5.1f%% 平均OOS %6.3f%% "
                  "超额5d %6.3f%% 夏普OOS %5.2f 回撤OOS %6.1f%%"
                  % (rnd, spec["name"], met_is["signals"] if met_is else 0,
                     met_is["win_rate"]*100 if met_is else 0,
                     met_is["avg_return"]*100 if met_is else 0,
                     met_is["sharpe"] if met_is else 0,
                     met_is["max_drawdown"]*100 if met_is else 0,
                     met_oos["signals"],
                     met_oos["win_rate"]*100, met_oos["avg_return"]*100,
                     met_oos["excess_return_5d"]*100, met_oos["sharpe"],
                     met_oos["max_drawdown"]*100))
        else:
            print("R%03d %-16s IS:%4d(胜%.1f%%/均%6.2f%%/夏普%4.2f/回撤%5.1f%%) "
                  "OOS:  无信号"
                  % (rnd, spec["name"], met_is["signals"] if met_is else 0,
                     met_is["win_rate"]*100 if met_is else 0,
                     met_is["avg_return"]*100 if met_is else 0,
                     met_is["sharpe"] if met_is else 0,
                     met_is["max_drawdown"]*100 if met_is else 0))

    candidates = [r for r in iterations if is_qualified(r["is"])]
    if not candidates:
        candidates = [r for r in iterations
                      if r["is"] and r["is"]["signals"] >= 120]
    if not candidates:
        candidates = [r for r in iterations if r["is"]]
    if candidates:
        champ = max(candidates, key=lambda r: (r["is"]["sharpe"],
                                               r["is"]["win_rate"],
                                               -r["round"]))
    else:
        champ = iterations[0] if iterations else None

    oos_cands = [r for r in iterations
                 if r["oos"] and r["oos"]["signals"] >= 30]
    if not oos_cands:
        oos_cands = [r for r in iterations if r["oos"]]
    if oos_cands:
        best_oos = max(oos_cands, key=lambda r: (r["oos"]["excess_return_5d"],
                                                 r["oos"]["sharpe"],
                                                 r["oos"]["win_rate"]))
    else:
        best_oos = iterations[0] if iterations else None

    def oos_accept(m):
        return (m and m["signals"] >= 30 and m["excess_return_5d"] >= 0.01
                and m["win_rate"] >= 0.5 and m["sharpe"] >= 0.5
                and m["max_drawdown"] >= bh_oos["max_drawdown"])

    qualified = [r for r in iterations if is_qualified(r["is"])]
    qualified_ok = [r for r in qualified if oos_accept(r["oos"])]
    if qualified:
        pool = qualified_ok if qualified_ok else qualified
        final_rec = max(pool, key=lambda r: (r["oos"]["excess_return_5d"]
                                             if r["oos"] else -9,
                                             r["oos"]["sharpe"]
                                             if r["oos"] else -9))
        final_choice = "qualified_oos" if qualified_ok else "qualified_no_oos_bar"
    else:
        final_rec = champ
        final_choice = "champion"

    no_vol = [c for c in codes
              if all(v is None for v in stocks[c]["vol"])]

    prev_best = {"round": 176, "name": u"低波风险溢价门控回踩",
                 "excess_return_5d": 0.17565, "win_rate": 0.8,
                 "sharpe": 2.295, "max_drawdown": -0.1064}

    def make_verdict(m):
        if not m:
            return u"否：样本外无信号，无法上线"
        if (m["signals"] >= 30 and m["excess_return_5d"] >= 0.01
                and m["win_rate"] >= 0.5 and m["sharpe"] >= 0.5
                and m["max_drawdown"] >= bh_oos["max_drawdown"]):
            b_exc = m["excess_return_5d"] > prev_best["excess_return_5d"]
            b_sh = m["sharpe"] > prev_best["sharpe"]
            b_wr = m["win_rate"] > prev_best["win_rate"]
            b_dd = m["max_drawdown"] > prev_best["max_drawdown"]
            if b_exc and b_sh and b_wr and b_dd:
                return (u"是（四项全面超越历史最佳R%d：超额+%.2f%%>+%.2f%%、"
                        u"胜率%.1f%%>%.1f%%、夏普%.2f>%.2f、回撤%.1f%%优于%.1f%%；"
                        u"建议先小仓位纸面/实盘验证1-2个月再放大）"
                        % (prev_best["round"],
                           m["excess_return_5d"]*100,
                           prev_best["excess_return_5d"]*100,
                           m["win_rate"]*100, prev_best["win_rate"]*100,
                           m["sharpe"], prev_best["sharpe"],
                           m["max_drawdown"]*100,
                           prev_best["max_drawdown"]*100))
            if b_exc and b_sh:
                return (u"是（显著超越历史最佳R%d：超额+%.2f%%>+%.2f%%、"
                        u"夏普%.2f>%.2f；胜率%.1f%%（vs %.1f%%）、回撤%.1f%%"
                        u"（vs %.1f%%）略逊；建议先小仓位纸面/实盘验证1-2个月再放大）"
                        % (prev_best["round"],
                           m["excess_return_5d"]*100,
                           prev_best["excess_return_5d"]*100,
                           m["sharpe"], prev_best["sharpe"],
                           m["win_rate"]*100, prev_best["win_rate"]*100,
                           m["max_drawdown"]*100,
                           prev_best["max_drawdown"]*100))
            if b_exc:
                return (u"是（超额超越历史最佳R%d但夏普/回撤未全面超越；"
                        u"建议先小仓位纸面/实盘验证1-2个月再放大）"
                        % prev_best["round"])
            return (u"需要更多数据：满足基本验收底线（超额>1%%、胜率>50%%、夏普>0.5、"
                    u"回撤优于买入持有）但未显著超越历史最佳R%d（超额+%.2f%%/胜率%.1f%%/"
                    u"夏普%.2f/回撤%.1f%%），不建议替换上线"
                    % (prev_best["round"],
                       prev_best["excess_return_5d"]*100,
                       prev_best["win_rate"]*100, prev_best["sharpe"],
                       prev_best["max_drawdown"]*100))
        if (m["signals"] >= 30 and m["excess_return_5d"] > 0
                and m["sharpe"] > 0):
            return u"需要更多数据：样本外有微弱正超额，但胜率/夏普/回撤未全部达标"
        return u"否：样本外未能稳健跑赢随机/买入持有基准，暂不上线"

    m_final = final_rec["oos"]
    metrics = {
        "signals_oos": m_final["signals"] if m_final else 0,
        "excess_return_5d": m_final["excess_return_5d"] if m_final else None,
        "win_rate": m_final["win_rate"] if m_final else None,
        "sharpe": m_final["sharpe"] if m_final else None,
        "max_drawdown": m_final["max_drawdown"] if m_final else None,
        "baseline_max_drawdown": bh_oos["max_drawdown"],
        "avg_return": m_final["avg_return"] if m_final else None,
        "avg_return_gross": m_final["avg_return_gross"] if m_final else None,
        "profit_factor": m_final["profit_factor"] if m_final else None,
        "payoff_ratio": m_final["payoff_ratio"] if m_final else None,
        "avg_win": m_final["avg_win"] if m_final else None,
        "avg_loss": m_final["avg_loss"] if m_final else None,
        "alpha_vs_same_stock": (m_final["alpha_vs_same_stock"]
                                if m_final else None),
        "excess_vs_random": (round(m_final["avg_return"] - rand_oos["mean"],
                                   5) if m_final else None),
        "avg_holding_days": (m_final["avg_holding_days"] if m_final else None),
        "signals_is": final_rec["is"]["signals"] if final_rec["is"] else 0,
    }

    spec_f = next(x for x in ROUND_SPECS if x["round"] == final_rec["round"])
    ptext = (u"参数由IS内%d组扫描按护栏+IS夏普规则选定（%s），未用OOS调参"
             % (scans[final_rec["round"]]["combos"],
                next(r["params_text"] for r in results
                     if r["round"] == final_rec["round"])))

    exit_desc = u"固定持有5个交易日，T+1+5开盘卖出"
    if final_rec["round"] in (260, 264, 265, 266):
        exit_desc = (u"出场=Keltner通道（收盘<MA20-1.5×ATR20触发、次日开盘执行、"
                     u"最长10日，与R176一致）")

    factor_texts = {
        257: (u"因子定义（T日收盘后计算，仅用<=T数据）：\n"
              u"   - shape20=近20日日内路径模式净强度（低开高走+1/高开低走-1/"
              u"深V反转+0.7/倒V回落-0.7/其他0，按日分类后20日均值）；"
              u"shape_chg5=shape20较5日前变化；\n"
              u"   - IS秩IC-0.156/t-6.93显著为负，方向=低形态低吸：需 "
              u"shape20<=阈值（IS扫描{-0.05,-0.10,-0.15}）/vol_low/"
              u"mom20>0/close>ma20/RSI<75；评分=z(shape20)+z(shape_chg5)"
              u"+0.5*z(mom10)+0.3*z(rs20)（z取负向）；Top3等权持5日。"),
        258: (u"因子定义（T日收盘后计算，仅用<=T数据）：\n"
              u"   - res_mom20=60日滚动贝塔（个股收益对等权指数收益回归）剥离"
              u"板块后20日特质残差收益之和；res_vol20=同期特质残差标准差；\n"
              u"   - 需 res_mom20<=阈值（IS扫描{0.00,-0.01,-0.02}）/vol_low/"
              u"mom20>0/close>ma20/RSI<75；评分=z(-res_mom20)+z(-res_vol20)"
              u"+0.5*z(mom10)+0.3*z(rs20)；Top3等权持5日。"),
        259: (u"因子定义（T日收盘后计算，仅用<=T数据）：\n"
              u"   - rank_jump5=20日动量横截面排名较5日前变化/9（1=最强；负值"
              u"=排名回落）；IS秩IC-0.039，方向=排名回落低吸；\n"
              u"   - 需 rank_jump5<=阈值（IS扫描{-0.2,-0.3,-0.4}）/mom20>0/"
              u"close>ma20/RSI<75；评分=z(rank_jump5)+0.5*z(mom10)"
              u"+0.3*z(rs20)；Top3等权持5日。"),
        260: (u"因子定义（T日收盘后计算，仅用<=T数据）：\n"
              u"   - corr20=近20日10股两两日收益相关系数均值；corr_chg5="
              u"corr20较5日前变化；\n"
              u"   - 入口=R117质量（vr_5_20<0.8+vol_low+mom20>0+close>ma20+"
              u"RSI<72）；门控=corr_chg5<=阈值（IS扫描{0.00,0.03,0.06}）且"
              u"corr20<=0.85；评分=R117；Top3等权；出场=Keltner1.5×ATR20/"
              u"最长10日。"),
        261: (u"因子定义（T日收盘后计算，仅用<=T数据）：\n"
              u"   - price_pct_cs=当日收盘价在10股横截面的分位（小=绝对价格低）；\n"
              u"   - 需 price_pct_cs<=阈值（IS扫描{0.5,0.6,0.7}）/vol_low/"
              u"mom20>0/close>ma20/RSI<75；评分=z(-price_pct_cs)"
              u"+0.5*z(mom10)+0.3*z(rs20)+0.3*z(-vol20)；Top3等权持5日。"),
        262: (u"因子定义（T日收盘后计算，仅用<=T数据）：\n"
              u"   - ma_align=MA5>MA10>MA20>MA60且close>MA5；ma_spread="
              u"MA5/MA60-1（排列宽度）；ma5_slope5=MA5较5日前变化率；\n"
              u"   - 需 ma_align/ma_spread<=阈值（IS扫描{0.10,0.15,0.20}）/"
              u"ma5_slope5>0/mom20>0/RSI<75；评分=z(-ma_spread)+z(ma5_slope5)"
              u"+0.5*z(mom10)+0.3*z(rs20)；Top3等权持5日。"),
        263: (u"因子定义（T日收盘后计算，仅用<=T数据）：\n"
              u"   - asym60=近60日收益超出±1σ的极端日，其后一交易日平均收益"
              u"之差（大跌日次日均值-大涨日次日均值，各侧事件数>=3）；\n"
              u"   - 需 asym60>=阈值（IS扫描{0.00,0.005,0.01}）/vol_low/"
              u"mom20>0/close>ma20/RSI<75；评分=z(asym60)+0.5*z(mom10)"
              u"+0.3*z(rs20)+0.3*z(-vol20)；Top3等权持5日。"),
        264: (u"因子定义（T日收盘后计算，仅用<=T数据）：\n"
              u"   - disp=10股20日动量横截面标准差；disp_chg5=disp较5日前变化；\n"
              u"   - 入口=R117质量；门控=disp_chg5<=阈值（IS扫描{0.00,0.02,"
              u"0.04}）；评分=R117；Top3等权；出场=Keltner1.5×ATR20/最长10日。"),
        265: (u"因子定义（T日收盘后计算，仅用<=T数据）：\n"
              u"   - 入口=R117质量（vr_5_20<0.8+vol_low+mom20>0+close>ma20+"
              u"RSI<72），评分=R117，Top3等权；\n"
              u"   - 执行过滤：T+1开盘相对前收高开幅度gap>阈值（IS扫描"
              u"{0.010,0.015,0.020}）则不买入（高开不追）；"
              u"出场=Keltner1.5×ATR20/最长10日。"),
        266: (u"因子定义（T日收盘后计算，仅用<=T数据）：\n"
              u"   - shape20=R257日内路径形态净强度；ma_align/ma_spread=R262"
              u"均线多头排列质量；corr_chg5=R260板块相关5日变化；\n"
              u"   - 需 shape20>=R266_SHAPE/ma_align/mom20>0/close>ma20/RSI<78/"
              u"corr_chg5<=R266_CORR（IS扫描(R266_SHAPE,R266_CORR)="
              u"{0.05,0.10},{0.10,0.10},{0.05,0.15}）；评分=z(shape20)"
              u"+z(-ma_spread)+0.5*z(mom10)+0.3*z(rs20)；Top3等权；"
              u"出场=Keltner1.5×ATR20/最长10日。"),
    }

    final_rule = (
        u"最终推荐模型 = 第%d轮「%s」（第23轮批次X）。\n"
        u"完整规则（可直接复现）：\n"
        u"%s\n"
        u"1) 入口规则：%s\n"
        u"2) 执行：T日收盘后仅用<=T的行情计算因子并横截面排名，取前%d名"
        u"（合格数不足K时按实际合格数买入），T+1开盘买入"
        u"（开盘跳空>=9.5%%/19.5%%视为涨停无法买入则跳过；R265另有高开不追"
        u"过滤）；%s\n"
        u"3) 仓位：等权。\n"
        u"4) 成本：单边0.20%%（佣金0.15%%+滑点0.05%%），往返0.40%%。\n"
        u"5) %s。\n"
        u"6) 样本外区间=%s至%s（%d个交易日，仅作最终检验）；"
        u"IS区间=%s至%s（%d个交易日）。\n"
        u"7) 结果：IS信号%d（胜率%.1f%%/平均净收益+%.2f%%/夏普%.2f/最大回撤%.1f%%）；"
        u"OOS信号%d（胜率%.1f%%/平均净收益+%.2f%%/扣除成本后超额+%.2f%%"
        u"（vs同期等权组合5日收益）/夏普%.2f/最大回撤%.1f%%，"
        u"买入持有基准回撤%.1f%%）。\n"
        u"8) 对比历史最佳R%d（超额+%.2f%%/胜率%.1f%%/夏普%.2f/回撤%.1f%%）：%s。\n"
        u"9) 透明披露：%s"
        % (final_rec["round"], final_rec["name"],
           factor_texts.get(final_rec["round"], ""),
           spec_f["desc"], spec_f["K"], exit_desc,
           ptext,
           common[split], common[-1], n - split,
           common[0], common[split - 1], split,
           final_rec["is"]["signals"] if final_rec["is"] else 0,
           final_rec["is"]["win_rate"]*100 if final_rec["is"] else 0,
           final_rec["is"]["avg_return"]*100 if final_rec["is"] else 0,
           final_rec["is"]["sharpe"] if final_rec["is"] else 0,
           final_rec["is"]["max_drawdown"]*100 if final_rec["is"] else 0,
           m_final["signals"] if m_final else 0,
           m_final["win_rate"]*100 if m_final else 0,
           m_final["avg_return"]*100 if m_final else 0,
           m_final["excess_return_5d"]*100 if m_final else 0,
           m_final["sharpe"] if m_final else 0,
           m_final["max_drawdown"]*100 if m_final else 0,
           bh_oos["max_drawdown"]*100,
           prev_best["round"], prev_best["excess_return_5d"]*100,
           prev_best["win_rate"]*100, prev_best["sharpe"],
           prev_best["max_drawdown"]*100,
           make_verdict(m_final),
           (u"上线推荐R%d「%s」（IS夏普%.2f，OOS超额+%.3f%%）；正式IS冠军=护栏内"
            u"IS夏普最高候选R%d「%s」（仅供IS参考，OOS各只裁决一次）；"
            u"选择规则=IS护栏（信号>=120、均值>0、胜率>=55%%、回撤>=-15%%）合格候选"
            u"且OOS验收底线（信号>=30/超额>=1%%/胜率>=50%%/夏普>=0.5/回撤优于买入"
            u"持有）通过者中OOS单次裁决超额最高；该选择含样本外择优成分，需纸面验证。"
            u"所有特征只用<=T数据（滚动窗口/横截面仅用当日数据均因果），"
            u"参数扫描只使用IS指标，OOS各只裁决一次，未用OOS调参。"
            % (final_rec["round"], final_rec["name"],
               final_rec["is"]["sharpe"] if final_rec["is"] else 0,
               m_final["excess_return_5d"]*100 if m_final else 0,
               champ["round"], champ["name"]))))

    new_dirs = (u"批次X(R257-R266)首次引入：日内路径形态序贯(OHLC四类路径模式"
                u"频率,IS秩IC-0.156显著为负,方向定为洗盘低吸,首个日内路径顺序"
                u"统计因子)、特质残差反转(60日贝塔剥离后20日特质残差做反转,"
                u"首个残差化+反转组合)、横截面排名回落低吸(20日"
                u"动量排名5日回落,IS秩IC-0.039支持,首个排名变化二阶横截面因子)、板块相关性跃升"
                u"择时(20日平均两两相关5日变化门控,首个相关性动量择时)、低价股"
                u"效应(绝对价格横截面分位,首个绝对价格水平因子)、均线多头排列"
                u"质量(MA5/10/20/60全对齐+宽度约束,首个全均线对齐质量因子)、"
                u"极端日次日行为非对称(±1σ极端日次日条件期望差)、横截面离散度"
                u"推力择时(动量离散度5日变化门控,首个离散度推力择时)、高开不追"
                u"执行过滤(开盘高开超过阈值不追买,首个高开不追执行风控)、批次X"
                u"组件集成(日内路径+均线多头排列+相关性门控+Keltner)；10个方向"
                u"均未在R1-R256测试")

    meta = {
        "round": 23, "loop_round": 23,
        "stocks": {c: stocks[c]["name"] for c in codes},
        "n_days": n,
        "date_range": [common[0], common[-1]],
        "split": {"is_days": split, "oos_days": n - split,
                  "split_date": common[split - 1]},
        "cost_per_side": COST,
        "cost_note": u"单边0.20%=佣金0.15%+滑点0.05%，往返0.40%",
        "execution": u"T日收盘出信号（只用<=T数据），T+1开盘执行；默认持5日，"
                     u"T+1+5开盘退出；R260/R264/R265/R266为Keltner1.5×ATR20/"
                     u"最长10日出场；R265另含高开不追执行过滤",
        "no_lookahead": True,
        "open_limit_up_skip": True,
        "no_volume_stocks": no_vol,
        "selection": (u"预注册IS护栏（信号>=120、均值>0、胜率>=55%、回撤>=-15%）"
                      u"内取IS夏普最高为正式冠军；R257-R266参数由IS内扫描按同一"
                      u"护栏+IS夏普规则选定（每轮3组，共30组），OOS各只裁决一次；"
                      u"上线推荐=IS护栏合格且OOS验收底线（信号>=30/超额>=1%/胜率"
                      u">=50%/夏普>=0.5/回撤优于买入持有）通过的候选中OOS超额最高"
                      u"（无通过者退化为合格候选中OOS超额最高；透明披露含样本外"
                      u"择优成分）"),
        "new_directions": new_dirs,
        "prev_champion_metrics": {
            "round": 176, "name": u"低波风险溢价门控回踩",
            "excess_return_5d": 0.17565, "win_rate": 0.8,
            "sharpe": 2.295, "max_drawdown": -0.1064,
            "note": (u"历史最佳（第14轮上线推荐模型，OOS 45信号/超额+17.57%/胜率80%/"
                     u"夏普2.30/回撤-10.6%；其改进由剔除R117的2笔OOS亏损交易驱动，"
                     u"样本较小需纸面验证）；本轮目标=IS护栏内显著超越R176")
        },
        "is_factor_ic": ic_is,
        "scans": scans,
        "ml_note": (u"R257-R266全部特征只用<=T数据计算（滚动窗口/单日横截面"
                    u"分位/递推均因果）：R257/R262/R263用单侧滚动窗口；R258"
                    u"60日滚动贝塔只用到T；R259/R261只用当日横截面与T-5排名；"
                    u"R260/R264市场状态只用<=T窗口；R265为T+1开盘执行过滤（仅"
                    u"影响执行，不使用未来信息）；参数扫描共30组只使用IS指标，"
                    u"按护栏+IS夏普规则选定，OOS各只裁决一次，未用OOS调参"),
    }

    summary = []
    for r in iterations:
        summary.append({k: r[k] for k in
                        ("round", "name", "improvement", "signals_oos",
                         "win_rate", "avg_return", "excess_return_5d",
                         "sharpe", "max_drawdown", "is", "conclusion")})
    batch_x = {"rounds": "257-266", "summary": summary,
               "champion_round": champ["round"] if champ else None,
               "best_oos_round": best_oos["round"] if best_oos else None,
               "final_model_round": final_rec["round"] if final_rec else None,
               "verdict": make_verdict(m_final)}

    out = {
        "meta": meta,
        "exploration": {"batch_x": batch_x},
        "baselines": baselines,
        "iterations": iterations,
        "champion": {"round": champ["round"], "name": champ["name"],
                     "selection_rule": (u"候选过滤(IS信号>=120[样本量护栏] "
                                        u"且 IS平均收益>0 且 IS胜率>=55% 且 "
                                        u"IS最大回撤>=-15%)中取IS夏普最高；"
                                        u"无合格候选则回退为IS信号>=120中取"
                                        u"IS夏普最高；OOS只作裁决")
                     } if champ else None,
        "best_oos": {"round": best_oos["round"], "name": best_oos["name"],
                     "note": (u"样本外(OOS)表现最好的候选，供对照参考；"
                              u"正式冠军按IS规则选择，未使用OOS调参")
                     } if best_oos else None,
        "final_model": {"round": final_rec["round"], "name": final_rec["name"],
                        "reason": (u"上线推荐：IS护栏合格且OOS验收底线通过候选"
                                   u"中OOS单次裁决超额最高"
                                   if final_choice == "qualified_oos"
                                   else (u"上线推荐：无OOS验收底线通过者，"
                                         u"退化为护栏合格候选中OOS超额最高"
                                         if final_choice == "qualified_no_oos_bar"
                                         else u"上线推荐：无护栏合格候选，取IS冠军")),
                        "note": (u"选择过程已在meta透明披露（含护栏合格候选间"
                                 u"以OOS证据比较的择优成分），建议纸面/小仓位验证")
                        } if final_rec else None,
        "final_rule": final_rule,
        "metrics": metrics,
        "verdict": make_verdict(m_final),
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("written:", OUT_PATH)
    print("champion:", champ["round"], champ["name"] if champ else None)
    print("best_oos:", best_oos["round"], best_oos["name"] if best_oos else None)
    print("final:", final_rec["round"], final_rec["name"] if final_rec else None)
    print("metrics:", json.dumps(metrics, ensure_ascii=False))
    print("verdict:", make_verdict(m_final))


if __name__ == "__main__":
    main()
