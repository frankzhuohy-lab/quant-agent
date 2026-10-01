#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PIT 历史股票池（方案文档第 2 章：股票池按历史日期生成，保留退市证券）。

流水线:
  fetch_candidates()  候选超集 = 东财半导体板块当前成分 + 关键词匹配退市股
                        （局限见 config/universe_rule.json，残余偏差不可宣称零）
  fetch_klines()      腾讯前复权日线缓存在 var/ucache/
  build_panel()       与 qdata.load_panel 同口径的并集日历对齐面板
  build_universe()    按冻结规则逐日判定 → 宇宙日历 npz（bool 矩阵）

板块语义冻结: 聚合特征(flr5/breadth)始终按 baseline_001 的 22 只池计算；
PIT 宇宙只通过 score 掩码决定可交易候选（adapter.materialize 的
universe_mask 参数，Agent 不可见）。
"""
from __future__ import print_function
import os, sys, json, time, hashlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import quant_agent
sys.path.insert(0, quant_agent.QSYS)

UCACHE = os.path.join(quant_agent.VAR, "ucache")
UINDEX = os.path.join(quant_agent.VAR, "universe")
RULE_PATH = os.path.join(quant_agent.HERE, "config",
                         "universe_rule.json")
BOARD_CODES = ["002156.SZ", "002185.SZ", "002371.SZ", "002409.SZ",
               "300054.SZ", "300346.SZ", "300666.SZ", "600584.SH",
               "603650.SH", "603688.SH", "603986.SH", "605358.SH",
               "605589.SH", "688012.SH", "688019.SH", "688041.SH",
               "688072.SH", "688126.SH", "688256.SH", "688268.SH",
               "688347.SH", "688981.SH"]


def load_rule():
    with open(RULE_PATH) as f:
        return json.load(f)


def rule_hash():
    with open(RULE_PATH, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def _em_get(url, retries=4):
    import urllib.request
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X "
                              "10_15_7) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/"})
            return json.loads(urllib.request.urlopen(req, timeout=20).read()
                              .decode())
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("em_get failed after %d tries: %s" % (retries, last))


def fetch_candidates():
    """东财板块成分（分页，pz 上限 100）+ 退市股关键词匹配。增量落盘。"""
    out_fp = os.path.join(quant_agent.VAR, "universe_candidates.json")
    cands = {}
    if os.path.exists(out_fp):
        cands = json.load(open(out_fp))
    cons = []
    try:
        for pn in (1, 2, 3):
            url = ("https://17.push2.eastmoney.com/api/qt/clist/get?"
                   "pn=%d&pz=100&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
                   "&fltt=2&invt=2&fid=f3&fs=b:BK1036&fields=f12,f14" % pn)
            d = _em_get(url)
            diff = (d.get("data") or {}).get("diff") or []
            cons.extend(diff)
            if len(cons) >= d["data"]["total"]:
                break
            time.sleep(0.8)
    except RuntimeError as e:
        print("EM 成分拉取失败，沿用已缓存候选文件: %s" % e)
        cons = []
    for r in cons:
        code = r["f12"]
        mkt = "SH" if code.startswith("6") else "SZ"
        cands.setdefault("%s.%s" % (code, mkt), r["f14"])
    with open(out_fp, "w") as f:
        json.dump(cands, f, ensure_ascii=False, indent=1)
    # 退市股: 关键词匹配 + 腾讯K线可取得性
    kw = ("芯", "微电", "半导", "光电", "集成")
    delisted = []
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import akshare as ak
        for fn in (ak.stock_info_sh_delist, ak.stock_info_sz_delist):
            try:
                df = fn()
                delisted.append(df)
            except Exception:
                continue
        for df in delisted:
            ncol = [c for c in df.columns if "简称" in c or "名称" in c]
            ccol = [c for c in df.columns if "代码" in c]
            if not ncol or not ccol:
                continue
            for _, row in df.iterrows():
                name = str(row[ncol[0]])
                code = str(row[ccol[0]]).zfill(6)
                if any(k in name for k in kw):
                    mkt = "SH" if code.startswith("6") else "SZ"
                    cands.setdefault("%s.%s" % (code, mkt), name)
    except Exception as e:
        print("delisted list unavailable: %s" % e)
    # 定型池 22 只并入候选超集（板块分类口径不同，EM 板块缺产业链配套）
    from paper_trade import NAMES
    for c in BOARD_CODES:
        cands.setdefault(c, NAMES.get(c, c))
    # 剔除北交所（920/4xx/8xx，腾讯前缀不同且流动性口径不适用）
    cands = {c: n for c, n in cands.items()
             if not (c.split(".")[0].startswith(("920", "4", "8")))}
    with open(out_fp, "w") as f:
        json.dump(cands, f, ensure_ascii=False, indent=1)
    return cands


def sysm_of(code):
    c, m = code.split(".")
    return ("sh" if m == "SH" else "sz") + c


def fetch_klines(cands, days=800):
    """腾讯 qfq 日线 → var/ucache/k_<code>.json。返回成功代码。

    days=800: 上市日判定用 akshare 交易所官方数据（见 fetch_listing_dates），
    K线窗口只需覆盖面板期+60日成交额中位数窗口。注意腾讯对部分证券
    仅返回约640根bar，缺数据期在宇宙中按保守规则排除（无可交易数据）。
    """
    from qdata import fetch_kline_q
    if not os.path.isdir(UCACHE):
        os.makedirs(UCACHE)
    ok, fail = [], []
    qsys_cache = os.path.join(quant_agent.QSYS, "cache")
    for i, (code, name) in enumerate(sorted(cands.items())):
        fp = os.path.join(UCACHE, "k_%s.json" % code)
        if os.path.exists(fp):
            ok.append(code)
            continue
        src = os.path.join(qsys_cache, "k_%s.json" % code)
        if os.path.exists(src):
            # 定型池数据直接复用 qsys 冻结缓存（同源同格式）
            import shutil
            shutil.copyfile(src, fp)
            ok.append(code)
            continue
        try:
            bars = fetch_kline_q(sysm_of(code), days)
            if not bars:
                raise ValueError("empty")
            with open(fp, "w") as f:
                json.dump(bars, f)
            ok.append(code)
        except Exception as e:
            fail.append((code, str(e)[:60]))
        if i % 20 == 0:
            print("fetch %d/%d ok=%d" % (i, len(cands), len(ok)),
                  flush=True)
        time.sleep(0.25)
    print("fetch done: ok=%d fail=%d" % (len(ok), len(fail)))
    for c, e in fail[:10]:
        print("  fail %s: %s" % (c, e))
    return ok


def build_panel(names=None):
    """与 qdata.load_panel 同口径（并集日历、停牌前收ffill+vol=0+标记）。"""
    names = names or {}
    raw = {}
    for fn in sorted(os.listdir(UCACHE)):
        if fn.startswith("k_") and fn.endswith(".json"):
            code = fn[2:-5]
            with open(os.path.join(UCACHE, fn)) as f:
                raw[code] = json.load(f)
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
    stocks, susp = {}, {}
    for code, rows in per.items():
        rec = {"name": names.get(code, code), "code": code,
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
                if last is None:
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


def fetch_listing_dates(cands):
    """交易所官方上市日期（akshare），缓存 var/listing_dates.json。

    失败时返回 {}，build_universe 退回'缓存首bar'启发式并注明边界。
    """
    fp = os.path.join(quant_agent.VAR, "listing_dates.json")
    if os.path.exists(fp):
        return json.load(open(fp))
    out = {}
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import akshare as ak
        for fn in (ak.stock_info_sh_name_code,
                   ak.stock_info_sz_name_code):
            try:
                df = fn()
            except Exception:
                continue
            ccol = [c for c in df.columns if "代码" in c or "code" in c.lower()]
            dcol = [c for c in df.columns if "上市日期" in c or "date" in c.lower()]
            if not ccol or not dcol:
                continue
            for _, row in df.iterrows():
                code = str(row[ccol[0]]).zfill(6)
                d = str(row[dcol[0]])[:10]
                mkt = "SH" if code.startswith("6") else "SZ"
                out["%s.%s" % (code, mkt)] = d
        with open(fp, "w") as f:
            json.dump(out, f)
    except Exception as e:
        print("listing dates unavailable: %s" % e)
    return out


def build_universe(stocks, codes, common, susp, rule=None,
                   listing_dates=None):
    """冻结规则逐日判定 → (matrix [n_dates,n_codes] bool, counts, meta)。"""
    rule = rule or load_rule()
    sc = rule["screens"]
    listing_dates = listing_dates or {}
    n, m = len(common), len(codes)
    mat = np.zeros((n, m), dtype=bool)
    idx = {d: i for i, d in enumerate(common)}
    L = sc["min_listed_days"]
    A = sc["min_median_amount60_cny"]
    kws = sc.get("exclude_name_keywords", [])
    for j, code in enumerate(codes):
        rec = stocks[code]
        o, c, v = rec["open"], rec["close"], rec["vol"]
        name = rec.get("name", "")
        if any(k in name for k in kws):
            continue  # 当前名称带风险标记 → 全程排除（局限见 rule 注记）
        ld = listing_dates.get(code)
        if ld:
            if ld <= common[0]:
                # 上市早于面板起点: 面板前已上市的交易日数按日历估算
                import datetime as _dt
                try:
                    d0 = _dt.date.fromisoformat(ld)
                    d1 = _dt.date.fromisoformat(common[0])
                    pre = int(max(0, (d1 - d0).days) * 252 / 365)
                except ValueError:
                    pre = 252   # 无法解析则按已满一年处理（老股为主）
                eff_L = max(0, L - pre)
                fi = 0
            else:
                fi = idx.get(ld, next(
                    (i for i, d in enumerate(common) if d >= ld), n))
                eff_L = L
        else:
            # 启发式: 缓存首bar（>3年上市的老股会被延迟准入，边界已注明）
            raw_first = None
            for d, val in zip(common, v):
                if d not in susp[code] and val > 0:
                    raw_first = d
                    break
            fi = idx.get(raw_first, n) if raw_first else n
            eff_L = L
        # 腾讯 vol 单位为手 → 成交额(元) = 收盘 × 成交量 × 100
        amount = [c[i] * v[i] * 100.0 for i in range(n)]
        for i in range(fi + eff_L, n):
            if c[i] < sc["min_close_cny"]:
                continue
            if sc["require_volume_positive"] and v[i] <= 0:
                continue
            lo = max(fi + eff_L, i - 59)
            if i - lo + 1 < 20:
                continue
            med = float(np.median(amount[lo:i + 1]))
            if med < A:
                continue
            mat[i, j] = True
    counts = mat.sum(axis=1)
    meta = {"rule_id": rule["rule_id"], "rule_hash": rule_hash(),
            "n_codes": m, "codes": codes,
            "avg_members": float(counts.mean()),
            "min_members": int(counts.min()),
            "max_members": int(counts.max())}
    return mat, counts, meta


def save_universe(mat, counts, meta, common):
    if not os.path.isdir(UINDEX):
        os.makedirs(UINDEX)
    fp = os.path.join(UINDEX, "universe_calendar.npz")
    np.savez_compressed(fp, matrix=mat, counts=counts,
                        dates=np.array(common), codes=np.array(meta["codes"]))
    h = hashlib.sha256(open(fp, "rb").read()).hexdigest()[:16]
    meta["calendar_hash"] = h
    with open(os.path.join(UINDEX, "universe_meta.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return h


def load_universe():
    fp = os.path.join(UINDEX, "universe_calendar.npz")
    z = np.load(fp, allow_pickle=False)
    meta = json.load(open(os.path.join(UINDEX, "universe_meta.json")))
    return z["matrix"], z["counts"], z["dates"].tolist(), \
        z["codes"].tolist(), meta


def main():
    print("== candidates ==")
    cands = fetch_candidates()
    print("candidates:", len(cands))
    with open(os.path.join(quant_agent.VAR, "universe_candidates.json"),
              "w") as f:
        json.dump(cands, f, ensure_ascii=False, indent=1)
    print("== fetch klines ==")
    fetch_klines(cands)
    print("== build panel & universe ==")
    stocks, codes, common, susp = build_panel(cands)
    print("panel: %d codes, %d days %s..%s" % (len(codes), len(common),
                                               common[0], common[-1]))
    ld = fetch_listing_dates(cands)
    print("listing dates: %d" % len(ld))
    mat, counts, meta = build_universe(stocks, codes, common, susp,
                                       listing_dates=ld)
    h = save_universe(mat, counts, meta, common)
    print("universe: avg=%.0f min=%d max=%d hash=%s" %
          (meta["avg_members"], meta["min_members"], meta["max_members"], h))
    nb = [c for c in BOARD_CODES if c in set(codes)]
    print("board22 in panel: %d/22" % len(nb))


if __name__ == "__main__":
    main()
