#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自选股同步：从东方财富妙想 API 拉取用户自选股列表 → pool.json
数据源: 妙想自选股接口（MX_APIKEY 认证），失败时回退上次缓存。
用法: python3 pool_sync.py   （需要 source ~/.mx_env 或已 export MX_APIKEY）
"""
import os, sys, json, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
POOL_FP = os.path.join(HERE, "pool.json")
URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/get"


def http_post_json(url, payload, apikey, timeout=30):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "apikey": apikey,
                 "User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def market_of(code):
    """6位代码 → 腾讯行情市场前缀"""
    if code.startswith(("6", "9", "5")):
        return "sh"
    if code.startswith(("0", "2", "3")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    return "sh"


def parse_data_list(d):
    """优先解析结构化 dataList（完整14条；partialResults 表格会截断到10行）"""
    out = []
    try:
        rows = d["data"]["allResults"]["result"]["dataList"]
    except Exception:
        return out
    for rec in rows:
        code = (rec.get("SECURITY_CODE") or "").strip()
        if not code.isdigit():
            continue
        mkt = (rec.get("MARKET_SHORT_NAME") or "").strip().lower() or market_of(code)
        px = rec.get("NEWEST_PRICE")
        out.append({
            "code": code,
            "symbol": "%s%s" % (mkt, code),
            "name": (rec.get("SECURITY_SHORT_NAME") or "").strip(),
            "last_px": float(px) if px and px.replace(".", "").isdigit() else None,
        })
    return out


def parse_md_table(table):
    """回退: 解析 markdown 表格"""
    out = []
    for line in table.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or not cells[0].isdigit() or not cells[1].isdigit():
            continue
        code = cells[1]
        out.append({
            "code": code, "symbol": market_of(code) + code,
            "name": cells[2],
            "last_px": float(cells[3]) if cells[3].replace(".", "").isdigit() else None,
        })
    return out


def fetch_watchlist():
    apikey = os.environ.get("MX_APIKEY", "")
    if not apikey:
        env_fp = os.path.expanduser("~/.mx_env")
        if os.path.exists(env_fp):
            with open(env_fp) as f:
                for line in f:
                    if line.strip().startswith("export MX_APIKEY="):
                        apikey = line.split("=", 1)[1].strip().strip('"')
    if not apikey:
        raise RuntimeError("MX_APIKEY 未设置")
    raw = http_post_json(URL, {}, apikey)
    d = json.loads(raw)
    if d.get("code") != 0:
        raise RuntimeError("妙想API返回异常: %s" % d.get("message"))
    out = parse_data_list(d) or parse_md_table(d["data"]["partialResults"])
    return out


def main():
    try:
        pool = fetch_watchlist()
        if not pool:
            raise RuntimeError("自选股列表为空")
        prev_codes = None
        if os.path.exists(POOL_FP):
            try:
                prev_codes = [s["code"] for s in json.load(open(POOL_FP))]
            except Exception:
                pass
        with open(POOL_FP, "w") as f:
            json.dump(pool, f, ensure_ascii=False, indent=1)
        codes = [s["code"] for s in pool]
        changed = (prev_codes is not None and prev_codes != codes)
        print("✅ 自选股同步成功: %d 只%s" % (len(pool), "（清单有变动）" if changed else ""))
        for s in pool:
            print("  · %s %s (%s)" % (s["code"], s["name"], s["symbol"]))
        if changed:
            print("⚠️ 自选股清单与上次不同，做T计划池已更新")
    except Exception as e:
        if os.path.exists(POOL_FP):
            print("⚠️ 同步失败(%s)，沿用上次缓存 pool.json" % str(e)[:120])
        else:
            print("❌ 同步失败且无缓存: %s" % str(e)[:200])
            sys.exit(1)


if __name__ == "__main__":
    main()
