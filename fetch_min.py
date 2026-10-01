#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取股票池全量 m5 分钟线（腾讯 ifzq 锚点翻页），缓存到 mindata/*.json
用法: /usr/bin/python3 fetch_min.py [起始日期 YYYY-MM-DD]  默认一年前
"""
import sys, os, json, time, urllib.request
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "mindata")
os.makedirs(OUT, exist_ok=True)

POOL = {
    "sz002156": u"通富微电", "sz002185": u"华天科技", "sz002371": u"北方华创",
    "sh600584": u"长电科技", "sh603986": u"兆易创新", "sh688012": u"中微公司",
    "sh688041": u"海光信息", "sh688126": u"沪硅产业", "sh688256": u"寒武纪",
    "sh688981": u"中芯国际", "sz300346": u"南大光电", "sh603650": u"彤程新材",
    "sh688268": u"华特气体", "sh688019": u"安集科技", "sz300054": u"鼎龙股份",
    "sz300666": u"江丰电子", "sz002409": u"雅克科技", "sh603688": u"石英股份",
    "sh605358": u"立昂微", "sh605589": u"圣泉集团", "sh688347": u"华虹公司",
    "sh688072": u"拓荆科技",
}


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def fetch_one(sym, start_ts):
    """锚点翻页拉取 sym 的全部 m5 K线（>= start_ts），返回按时间升序列表"""
    all_bars = {}
    anchor = None
    for page in range(80):  # 上限 80 页 ≈ 533 交易日
        p = "m5,%s,320" % (anchor or "")
        url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=%s,%s" % (sym, p)
        try:
            d = json.loads(http_get(url))
        except Exception as e:
            print("  %s page%d 取数失败重试: %s" % (sym, page, str(e)[:60]))
            time.sleep(2)
            try:
                d = json.loads(http_get(url))
            except Exception:
                break
        node = d.get("data", {}).get(sym, {})
        bars = node.get("m5") or []
        if not bars:
            break
        for b in bars:
            # [time, open, high, low, close, volume]
            all_bars[b[0]] = [b[0], float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])]
        oldest = min(b[0] for b in bars)
        if oldest <= start_ts:
            break
        anchor = oldest  # 下一页以此为终点锚
        time.sleep(0.15)
    out = sorted(all_bars.values(), key=lambda x: x[0])
    return [x for x in out if x[0] >= start_ts]


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else (
        datetime.now() - timedelta(days=370)).strftime("%Y-%m-%d")
    start_ts = start.replace("-", "") + "0000"
    print("m5 拉取 起点=%s 股票数=%d" % (start, len(POOL)))
    result = {}
    for i, (sym, name) in enumerate(POOL.items()):
        bars = fetch_one(sym, start_ts)
        result[sym] = {"name": name, "bars": bars}
        span = (bars[0][0], bars[-1][0]) if bars else None
        print("[%d/%d] %s %s: %d 条 %s" % (i + 1, len(POOL), sym, name, len(bars), span))
    fp = os.path.join(OUT, "m5_%s.json" % start)
    with open(fp, "w") as f:
        json.dump({"start": start, "fetched_at": datetime.now().isoformat(), "data": result}, f)
    total = sum(len(v["bars"]) for v in result.values())
    print("已缓存 %s  总条数 %d" % (fp, total))


if __name__ == "__main__":
    main()
