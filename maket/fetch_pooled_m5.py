# -*- coding: utf-8 -*-
"""抓取自选股池全部票的 5 分钟线到免费源最深（约125个交易日），存 bt_cache_pooled.json。
供 bt_hypo.py / bt_model_r1.py 假设检验与建模用。只读数据源，不碰引擎。"""
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "bt_cache_pooled.json")
DEEP_ANCHOR = "20260301"   # 抓到这日期之前为止（免费源上限约125天）


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def fetch_m5_deep(sym):
    allb = {}
    anchor = ""
    for _ in range(30):
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
        new = 0
        for b in bars:
            if b[0] not in allb:
                new += 1
            allb[b[0]] = [b[0], float(b[1]), float(b[2]), float(b[3]),
                          float(b[4]), float(b[5])]
        anchor = min(b[0] for b in bars)
        if anchor[:8] < DEEP_ANCHOR or new == 0:
            break
        time.sleep(0.15)
    return [allb[k] for k in sorted(allb)]


def main():
    pool = json.load(open(os.path.join(HERE, "pool.json")))
    out = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "start": DEEP_ANCHOR, "data": {}}
    for p in pool:
        sym = p["symbol"]
        try:
            bars = fetch_m5_deep(sym)
        except Exception as e:
            print("FAIL", sym, e)
            continue
        days = sorted({b[0][:8] for b in bars})
        out["data"][sym] = {"name": p["name"], "code": p["code"], "bars": bars}
        print("%s %s bars=%d days=%d %s -> %s"
              % (sym, p["name"], len(bars), len(days),
                 days[0] if days else "-", days[-1] if days else "-"))
    with open(OUT, "w") as f:
        json.dump(out, f)
    print("saved", OUT)


if __name__ == "__main__":
    main()
