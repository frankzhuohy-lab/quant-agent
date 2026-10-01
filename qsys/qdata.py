#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qsys 数据层：腾讯前复权日线缓存 + 对齐面板构建。

对齐口径（比 quant_iter26.load_stocks 更诚实）：
- 交易日历取股票池并集（而非交集），停牌日以前收 forward-fill、vol=0，
  并打 suspended 标记 —— 回测层据此禁止成交，避免「交集日历」静默抹掉停牌。
"""
from __future__ import print_function
import os, sys, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from paper_trade import QMAP, NAMES, http_get  # noqa: E402

CACHE = os.path.join(HERE, "cache")
FETCH_DAYS = int(os.environ.get("QSYS_DAYS", "800"))


def _atomic_write_json(fp, obj):
    """先写临时文件再 os.replace 原子替换。

    原地 open(fp,'w') 会截断旧 inode——硬链接/快照共享同一底层文件，
    会破坏不可变快照（2026-09-30 评审发现）。替换路径产生新 inode，
    旧快照天然保留旧内容。
    """
    tmp = fp + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, fp)


def fetch_kline_q(sysm, n=FETCH_DAYS):
    # host 用 proxy.finance.qq.com：web.ifzq.gtimg.cn 对本机已触发限流(501)，
    # 两 host 同路径同数据（ifzqgtimg 镜像）。
    url = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get"
           "?param=%s,day,,,%d,qfq" % (sysm, n))
    d = json.loads(http_get(url).decode("utf-8", "ignore"))
    dd = d["data"][sysm]
    return dd.get("qfqday") or dd.get("day") or []


def load_panel(refresh=False, days=FETCH_DAYS):
    """返回 stocks{code:rec}, codes, common, susp{code:set(date)}
    rec 额外带 suspended: [bool]*n"""
    if not os.path.isdir(CACHE):
        os.makedirs(CACHE)
    raw = {}
    for code, sysm in sorted(QMAP.items()):
        fp = os.path.join(CACHE, "k_%s.json" % code)
        if os.path.exists(fp) and not refresh:
            with open(fp) as f:
                bars = json.load(f)
        else:
            bars = fetch_kline_q(sysm, days)
            _atomic_write_json(fp, bars)
            time.sleep(0.3)
        raw[code] = bars
    # 交易日历 = 并集
    cal = set()
    per = {}
    for code, bars in raw.items():
        rows = {}
        for b in bars:
            try:
                o, c, h, l = (float(b[1]), float(b[2]), float(b[3]),
                              float(b[4]))
                v = float(b[5]) if len(b) > 5 else 0.0
            except (ValueError, IndexError):
                continue
            if min(o, h, l, c) <= 0:
                continue
            rows[b[0]] = (o, h, l, c, v)
        per[code] = rows
        cal.update(rows.keys())
    common = sorted(cal)
    idx = {d: i for i, d in enumerate(common)}
    stocks, susp = {}, {}
    for code, rows in per.items():
        rec = {"name": NAMES.get(code, code), "code": code,
               "dates": list(common),
               "open": [], "high": [], "low": [], "close": [], "vol": []}
        sset = set()
        ds = sorted(rows.keys())
        last = None
        for d in common:
            if d in rows:
                last = rows[d]
                rec["open"].append(last[0]); rec["high"].append(last[1])
                rec["low"].append(last[2]); rec["close"].append(last[3])
                rec["vol"].append(last[4])
                if last[4] <= 0:
                    sset.add(d)
            else:
                # 停牌：forward-fill 价格，vol=0
                if last is None:
                    # 上市前：用首个可用值回填（仅影响热身期）
                    first = rows[ds[0]]
                    rec["open"].append(first[0]); rec["high"].append(first[1])
                    rec["low"].append(first[2]); rec["close"].append(first[3])
                    rec["vol"].append(0.0)
                else:
                    rec["open"].append(last[2]); rec["high"].append(last[2])
                    rec["low"].append(last[2]); rec["close"].append(last[2])
                    rec["vol"].append(0.0)
                sset.add(d)
        stocks[code] = rec
        susp[code] = sset
    codes = sorted(stocks.keys())
    return stocks, codes, common, susp


def build_all(stocks, codes, common):
    """复用官方特征管线（quant_iter26.build_features + quant_iter44 批次AP特征）"""
    import quant_iter26 as q26
    import quant_iter44 as q44
    q44.R316_D = -0.08
    q44.R316_RSI = 58.0
    q44.R316_FLR = 0.3
    q44.R443_UV = 0.8
    feat, state = q26.build_features(stocks, codes, common)
    state = q44.add_batchAP_features(stocks, codes, common, feat, state)
    return feat, state


def r443_spec(**over):
    """冻结基线 spec（参数与 quant_iter44/paper_trade 完全一致）"""
    import quant_iter44 as q44
    spec = {"score": q44.score_r117_upvar, "filt": q44.make_filt_flr("R316_FLR"),
            "exit": "keltner", "K": 3, "allow_fewer": True,
            "weight_mode": "equal",
            "params": {"keltner_mult": 1.5, "max_hold": 10}}
    spec.update(over)
    if "params" in over:
        p = {"keltner_mult": 1.5, "max_hold": 10}
        p.update(over["params"])
        spec["params"] = p
    return spec
