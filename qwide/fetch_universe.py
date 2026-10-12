#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qwide Phase 0：全 A 股日频数据地基（XTX 路线的第一步：广度）。

与 quant_agent（22 只半导体池）完全隔离的新数据层，不复用其缓存。
腾讯 qfq 日线（proxy.finance.qq.com，与 qdata 同通道），800 根 ≈ 2023-06 起，
覆盖 IS/DEV/降级区/封存全窗口。增量可续传：已存在且非空的缓存跳过。

用法:
  python3 qwide/fetch_universe.py            # 全量（增量）
  python3 qwide/fetch_universe.py --limit 50 # 冒烟测试
输出:
  qwide/cache/k_<code>.json   每票原子写（tmp+replace）
  qwide/fetch_report.json     成功/失败/跳过计数
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "cache")
REPORT = os.path.join(HERE, "fetch_report.json")
BARS = 800
sys.path.insert(0, os.path.join(ROOT, "qsys"))  # qdata.http_get


def load_codes():
    """全 A 代码表。主源=交易所直连（SSE/SZSE，push2 对本机封锁后的可用通道）。

    旧 push2 路径保留作回退（本机当前被断连，仅未来恢复时使用）。
    """
    fp = os.path.join(HERE, "code_list.json")
    if os.path.exists(fp):
        return json.load(open(fp))
    try:
        from exchange_codes import load_codes_exchange
        return load_codes_exchange()
    except Exception:
        pass
    return _load_codes_push2()
def _load_codes_push2():
    fp = os.path.join(HERE, "code_list.json")
    import urllib.request
    out = {}
    pn = 1
    consec_fail = 0
    total = None
    while pn < 80 and consec_fail < 15:
        url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100"
               "&po=1&np=1&fltt=2&invt=2&fid=f3&"
               "fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14" % pn)
        d = None
        for i in range(8):
            try:
                req = urllib.request.Request(url, headers={
                    "Referer": "https://quote.eastmoney.com/",
                    "User-Agent": "Mozilla/5.0"})
                d = json.loads(urllib.request.urlopen(req, timeout=20)
                               .read().decode())
                break
            except Exception:
                time.sleep(2 * (i + 1))
        if d is None or not d.get("data"):
            consec_fail += 1
            time.sleep(3)
            continue
        consec_fail = 0
        rows = d["data"]["diff"]
        total = d["data"].get("total", total)
        for r in rows:
            code = str(r["f12"])
            mkt = "SH" if code.startswith("6") else (
                "SZ" if code.startswith(("0", "3")) else None)
            if mkt is None:
                continue
            out["%s.%s" % (code, mkt)] = str(r["f14"])
        if total and len(out) >= total * 0.98:
            break
        pn += 1
        time.sleep(0.5)
    if not out or (total and len(out) < total * 0.5):
        raise RuntimeError("code list incomplete: got %d (total=%s)"
                           % (len(out), total))
    with open(fp, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    return out


def fetch_one(sysm, retries=4):
    from qdata import fetch_kline_q, FETCH_DAYS
    last = None
    for i in range(retries):
        try:
            return fetch_kline_q(sysm, BARS)
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    if last is None:
        raise RuntimeError("fetch_kline_q returned without data")
    raise last


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    if not os.path.isdir(CACHE):
        os.makedirs(CACHE)
    codes = load_codes()
    items = sorted(codes.items())
    if limit:
        items = items[:limit]
    ok = skip = fail = 0
    fails = []
    for i, (code, name) in enumerate(items):
        fp = os.path.join(CACHE, "k_%s.json" % code)
        if os.path.exists(fp) and os.path.getsize(fp) > 100:
            skip += 1
            continue
        c, m = code.split(".")
        sysm = ("sh" if m == "SH" else "sz") + c
        try:
            bars = fetch_one(sysm)
            tmp = fp + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"code": code, "name": name, "bars": bars}, f)
            os.replace(tmp, fp)
            ok += 1
        except Exception as e:
            fail += 1
            fails.append((code, str(e)[:60]))
        if (i + 1) % 100 == 0:
            rep = {"done": i + 1, "total": len(items), "ok": ok,
                   "skip": skip, "fail": fail}
            with open(REPORT + ".tmp", "w") as f:
                json.dump(rep, f)
            os.replace(REPORT + ".tmp", REPORT)
            print(rep, flush=True)
        time.sleep(0.22)
    rep = {"done": len(items), "total": len(items), "ok": ok, "skip": skip,
           "fail": fail, "fails_sample": fails[:20]}
    with open(REPORT, "w") as f:
        json.dump(rep, f, ensure_ascii=False)
    print("FINAL", rep, flush=True)


if __name__ == "__main__":
    main()
