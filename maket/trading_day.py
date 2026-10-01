#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易日探针（借鉴 tick-stock-panel 的交易日历探针模式）。

判定链（fail-open）:
  1. 周末 -> False（不查网络）
  2. 拉取上证指数 sh000001 日K（主源 ifzq.gtimg.cn，失败回退 web.ifzq.gtimg.cn 同路径）:
       最新bar日期 == 目标日 -> True（今日已有K线 = 交易日）
       最新bar日期 <  目标日 -> False（节假日/休市，bar 仍停在上一交易日）
  3. 网络异常/超时(每次请求上限3秒) -> 按工作日 True（fail-open，宁可空跑不可漏跑）

模块级缓存: 同一目标日多次调用只查一次网络。
"""
import datetime
import json
import sys
import urllib.request

_TIMEOUT = 3.0  # 单次请求超时（秒），触发即走 fail-open
_URLS = (
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,5,qfq",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,5,qfq",
)

# 模块级缓存: {"YYYY-MM-DD": {"result": bool, "bar": str|None, "src": str, "at": str}}
_CACHE = {}


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_date(d):
    """把 None / date / datetime / 'YYYY-MM-DD' / 'YYYYMMDD' 归一为 date。"""
    if d is None:
        return datetime.date.today()
    if isinstance(d, datetime.datetime):
        return d.date()
    if isinstance(d, datetime.date):
        return d
    s = str(d).strip()[:10]
    if len(s) == 8 and s.isdigit():
        return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:]))
    return datetime.date.fromisoformat(s)


def _fetch_latest_bar():
    """返回 (最新bar日期原始串, 来源URL)；两个源都失败时抛 RuntimeError。"""
    last_err = None
    for url in _URLS:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (trading-day-probe)"})
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            node = ((data or {}).get("data") or {}).get("sh000001") or {}
            bars = node.get("qfqday") or node.get("day") or []
            if bars and bars[-1] and bars[-1][0]:
                return str(bars[-1][0]), url
            last_err = RuntimeError("empty kline payload from %s" % url)
        except Exception as e:  # URLError/socket.timeout/JSON/KeyError 一律记下换下一源
            last_err = e
    raise RuntimeError("all kline sources failed; last error: %r" % (last_err,))


def is_trading_day(d=None):
    """判断 d（默认今天，本地时区）是否为A股交易日。网络全失败时按工作日 fail-open 返回 True。"""
    target = _to_date(d)
    key = target.isoformat()
    hit = _CACHE.get(key)
    if hit is not None:
        return hit["result"]

    # 1. 周末直接 False
    if target.weekday() >= 5:
        _CACHE[key] = {"result": False, "bar": None, "src": "weekend", "at": _now()}
        return False

    # 2. 探针: 上证指数当日是否有K线
    try:
        bar_raw, src = _fetch_latest_bar()
    except Exception as e:
        # 3. fail-open: 网络异常/超时 -> 按工作日 True（宁可空跑不可漏跑）
        sys.stderr.write("[trading_day] probe failed (%s); fail-open -> treat %s as trading day\n"
                         % (e, key))
        _CACHE[key] = {"result": True, "bar": None, "src": "fail-open", "at": _now(),
                       "err": str(e)}
        return True

    bar = bar_raw.replace("-", "")   # 兼容 "2026-09-22" / "20260922"
    key_n = key.replace("-", "")
    if bar == key_n:
        result = True                # 最新bar==目标日: 今日开市
    elif bar < key_n:
        # 盘前分支: 交易日9:25集合竞价撮合后才生成当日bar——
        # 9:20的plan运行时必然bar=昨日。工作日+上午11:35前 → 判True(宁可空跑不可漏跑)
        # (盘后仍无bar才是真节假日; 周末已在更早分支短路)
        import datetime as _dt
        _now_dt = _dt.datetime.now()
        if _now_dt.hour < 11 or (_now_dt.hour == 11 and _now_dt.minute < 35):
            result = True
            _CACHE[key] = {"result": True, "bar": bar, "src": "pre-market-open", "at": _now()}
            return True
        result = False               # 盘后仍无bar: 节假日/休市
    else:
        # 最新bar晚于目标日（如查过去日期但当日无bar，无法确证）: fail-open
        sys.stderr.write("[trading_day] latest bar %s > target %s; fail-open -> True\n"
                         % (bar_raw, key))
        result = True
    _CACHE[key] = {"result": result, "bar": bar_raw, "src": src, "at": _now()}
    return result


if __name__ == "__main__":
    def _report(label, d):
        r = is_trading_day(d)
        key = _to_date(d).isoformat()
        info = _CACHE.get(key, {})
        print("[%s] is_trading_day(%s) = %s   (bar=%s, src=%s, at=%s)"
              % (label, key, r, info.get("bar"), info.get("src"), info.get("at")))
        return r

    print("trading_day self-test @ %s" % _now())
    today = datetime.date.today()
    sat = today + datetime.timedelta(days=(5 - today.weekday()) % 7 or 7)

    _report("1 今天", today)                 # 走真实网络探针
    _report("2 今天(缓存)", today)           # 第二次调用应命中缓存，不查网络
    _report("3 周六", sat)                   # 周末短路，预期 False
    _report("4 国庆2026-10-01", "2026-10-01")  # 法定节假日，预期 False（bar < 目标日）

    print("\n[5] 网络全失败 fail-open 模拟（指向不可达地址）:")
    _URLS = ("https://127.0.0.1:9/no-such-endpoint",)   # __main__ 即模块作用域，直接重绑
    _CACHE.clear()
    probe_day = today + datetime.timedelta(days=1)
    if probe_day.weekday() >= 5:            # 避开周末短路，保证走到网络分支
        probe_day += datetime.timedelta(days=2)
    _report("6 fail-open", probe_day)       # 预期 True 且 stderr 有提示

    print("\ncache dump:")
    for k in sorted(_CACHE):
        print("  %s -> %s" % (k, _CACHE[k]))
    print("self-test done.")
