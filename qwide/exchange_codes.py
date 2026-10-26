#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易所直连：全 A 代码表（SSE + SZSE），push2 挂掉时的主源。"""
import json
import time
import urllib.request

HERE = __file__.rsplit("/", 1)[0]


def _get(url, referer, retries=6, timeout=20):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Referer": referer})
            return json.loads(urllib.request.urlopen(req, timeout=timeout)
                              .read().decode())
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def fetch_sse(stock_type=1, prefixes=("60",)):
    """上交所股票列表。stock_type=1 主板 A（60），8 科创板（688/689）。"""
    out = {}
    page = 1
    while True:
        url = ("https://query.sse.com.cn/sseQuery/commonQuery.do?"
               "STOCK_TYPE=%d&REG_PROVINCE=&CSRC_CODE=&STOCK_CODE=&"
               "sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L&COMPANY_STATUS=2,4,5,7,8"
               "&type=inParams&isPagination=true&pageHelp.cacheSize=1"
               "&pageHelp.beginPage=%d&pageHelp.pageSize=200"
               "&pageHelp.pageNo=%d&pageHelp.endPage=%d"
               % (stock_type, page, page, page))
        d = _get(url, "https://www.sse.com.cn/")
        if not d:
            break
        rows = d.get("result") or []
        for r in rows:
            code = str(r.get("A_STOCK_CODE", "")).strip()
            name = str(r.get("COMPANY_ABBR", "")).strip()
            if code and code.startswith(prefixes):
                out[code + ".SH"] = name
        total = int(d["pageHelp"]["total"])
        if page * 200 >= total or not rows:
            break
        page += 1
        time.sleep(0.5)
    return out


def fetch_szse():
    """深交所主板+创业板（00/30 开头）。"""
    out = {}
    page = 1
    while True:
        url = ("https://www.szse.cn/api/report/ShowReport/data?"
               "SHOWTYPE=JSON&CATALOGID=1110&TABKEY=tab1&PAGENO=%d" % page)
        d = _get(url, "https://www.szse.cn/market/product/stock/list/index.html")
        if not d or not d[0].get("data"):
            break
        for r in d[0]["data"]:
            code = str(r.get("agdm", "")).strip()
            name = str(r.get("agjc", "")).strip()
            # agjc 形如 "<a href=...><u>平安银行</u></a>"，剥掉标签
            if "<u>" in name:
                name = name.split("<u>")[1].split("</u>")[0]
            if code.startswith(("00", "30")):
                out[code + ".SZ"] = name
        pages = int(d[0]["metadata"]["pagecount"])
        if page >= pages:
            break
        page += 1
        time.sleep(0.5)
    return out


def load_codes_exchange():
    fp = HERE + "/code_list.json"
    a = fetch_sse()
    a.update(fetch_sse(stock_type=8, prefixes=("688", "689")))  # 科创板补漏
    b = fetch_szse()
    out = dict(a)
    out.update(b)
    if not out:
        raise RuntimeError("exchange code lists both empty")
    with open(fp, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    return out


if __name__ == "__main__":
    c = load_codes_exchange()
    print("total:", len(c))
