#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flow_data.py — 龙虎榜 + 北向资金 观察性数据接入（纸面做T系统）
================================================================
性质: 纯观察/存档, 不参与任何交易决策, 不改变任何筛选条件/规则。
位置: 收盘链 run_maket.sh close 中, 位于 maket.py close 之后、screen_candidates.py 之前,
      以 `python3 flow_data.py snapshot` 运行, 任何情况下 exit 0 (fail-open, 不阻塞收盘链)。

数据源(东方财富公开接口, 无key无限额):
  - 龙虎榜  RPT_DAILYBILLBOARD_DETAILSNEW  (datacenter-web.eastmoney.com)
      实测字段: SECURITY_CODE / SECURITY_NAME_ABBR / BILLBOARD_NET_AMT(元) /
                EXPLANATION(上榜原因) / CHANGE_RATE / BILLBOARD_BUY_AMT / BILLBOARD_SELL_AMT
      注意: 同一股票可因多个上榜原因返回多行; 当日未发布时 code=9201, result=null
  - 北向    RPT_MUTUAL_DEAL_HISTORY  (MUTUAL_TYPE: 001=沪股通, 003=深股通)
      实测: 净买入(NET_DEAL_AMT)自2024-08-19起停止披露, 近期为 null;
            仅剩成交额 DEAL_AMT(百万元)/成交笔数 DEAL_NUM/指数/领涨股 → 返回近5日成交额序列并标注
语义约定:
  - 返回 []  : 请求成功但该日无数据(未发布/非交易日)
  - 返回 None: 请求失败(网络/超时/解析), 已 stderr 提示
统一: 3秒超时, 失败重试2次, 模块内缓存当日成功结果。

用法:
  python3 flow_data.py            # 自测: 打印今日龙虎榜+北向(空则回看最近发布日)
  python3 flow_data.py snapshot   # 收盘链用: 写 reports/flow_YYYYMMDD.txt + 追加 flow_history.jsonl
"""
import json
import os
import re
import sys
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

MAKET_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(MAKET_DIR, "reports")
HISTORY_PATH = os.path.join(MAKET_DIR, "flow_history.jsonl")
POOL_PATH = os.path.join(MAKET_DIR, "pool.json")

EM_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DT_REPORT = "RPT_DAILYBILLBOARD_DETAILSNEW"
_NB_REPORT = "RPT_MUTUAL_DEAL_HISTORY"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
}
TIMEOUT = 3      # 秒
RETRIES = 2      # 失败重试次数
RETRY_BACKOFF = 0.5  # 重试间隔, 秒

# 模块级缓存: key=(name, 'YYYY-MM-DD') -> 成功结果([] 也缓存; None 失败不缓存, 允许重试)
_CACHE = {}


def _log_err(msg):
    print("[flow_data] %s" % msg, file=sys.stderr)


def _norm_date(date=None):
    """统一日期入参 -> 'YYYY-MM-DD'; None=今天(本地时区)。"""
    if date is None:
        return datetime.now().strftime("%Y-%m-%d")
    if isinstance(date, datetime):
        return date.strftime("%Y-%m-%d")
    if isinstance(date, str):
        # 容错: 'YYYYMMDD' -> 'YYYY-MM-DD'
        s = date.strip()
        if re.fullmatch(r"\d{8}", s):
            return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])
        return s
    # date 对象等
    return str(date)


def _em_get(params):
    """请求东财 datacenter 接口, 返回解析后的 dict; 失败重试后仍失败返回 None(stderr提示)。"""
    url = EM_API + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(1 + RETRIES):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # URLError/timeout/JSON 等, 全部按失败处理
            last_err = e
            if attempt < RETRIES:
                time.sleep(RETRY_BACKOFF)
    _log_err("请求失败(%s): %s" % (params.get("reportName"), last_err))
    return None


def dragon_tiger(date=None) -> list:
    """当日龙虎榜上榜股票, 按净买额降序。
    返回 [{code, name, net_buy, reason, chg, buy, sell}] (net_buy/buy/sell 单位:元, 可多条同票不同原因)。
    [] = 该日无数据; None = 请求失败。
    """
    d = _norm_date(date)
    key = ("dragon_tiger", d)
    if key in _CACHE:
        return _CACHE[key]
    body = _em_get({
        "reportName": _DT_REPORT,
        "columns": "ALL",
        "filter": "(trade_date='%s')" % d,
        "pageSize": 500,
        "pageNumber": 1,
        "sortColumns": "BILLBOARD_NET_AMT",
        "sortTypes": -1,
        "source": "WEB",
        "client": "WEB",
    })
    if body is None:
        return None
    result = body.get("result") or {}
    rows = result.get("data") or []
    out = []
    for r in rows:
        code = r.get("SECURITY_CODE")
        if not code:
            continue
        try:
            net_buy = float(r.get("BILLBOARD_NET_AMT") or 0.0)
        except (TypeError, ValueError):
            net_buy = 0.0
        out.append({
            "code": code,
            "name": r.get("SECURITY_NAME_ABBR") or code,
            "net_buy": net_buy,
            "reason": r.get("EXPLANATION") or "",
            "chg": r.get("CHANGE_RATE"),
            "buy": r.get("BILLBOARD_BUY_AMT"),
            "sell": r.get("BILLBOARD_SELL_AMT"),
        })
    _CACHE[key] = out
    return out


def _nb_side(row):
    """单条北向记录 -> 展示结构; DEAL_AMT 百万元 -> 亿元。"""
    if not row:
        return None
    def _amt(v):
        try:
            return round(float(v) / 100.0, 2)  # 百万元 -> 亿元
        except (TypeError, ValueError):
            return None
    return {
        "deal_amt": _amt(row.get("DEAL_AMT")),        # 成交额(亿元)
        "deal_num": row.get("DEAL_NUM"),              # 成交笔数
        "index_close": row.get("INDEX_CLOSE_PRICE"),  # 沪:上证指数 / 深:深证成指
        "index_chg": row.get("INDEX_CHANGE_RATE"),
        "lead_stock": row.get("LEAD_STOCKS_NAME"),
        "lead_chg": row.get("LS_CHANGE_RATE"),
        "net_buy": row.get("NET_DEAL_AMT"),           # 2024-08-19 起停止披露, 近期为 None
    }


def northbound(date=None) -> dict:
    """北向资金。净买入已停止披露, 返回当日(或最近可得)两侧成交额 + 近5日序列。
    返回 {date, sh, sz, note, recent5:[{date, sh_deal_amt, sz_deal_amt}]}。
    None = 请求失败。
    """
    d = _norm_date(date)
    key = ("northbound", d)
    if key in _CACHE:
        return _CACHE[key]
    sh_rows, sz_rows = [], []
    ok = True
    for mtype, sink in (("001", sh_rows), ("003", sz_rows)):
        body = _em_get({
            "reportName": _NB_REPORT,
            "columns": "ALL",
            "filter": "(MUTUAL_TYPE=\"%s\")" % mtype,
            "pageSize": 10,
            "pageNumber": 1,
            "sortColumns": "TRADE_DATE",
            "sortTypes": -1,
            "source": "WEB",
            "client": "WEB",
        })
        if body is None:
            ok = False
            break
        result = body.get("result") or {}
        sink.extend(result.get("data") or [])
    if not ok:
        return None
    sh_by_date = {str(r.get("TRADE_DATE", ""))[:10]: r for r in sh_rows}
    sz_by_date = {str(r.get("TRADE_DATE", ""))[:10]: r for r in sz_rows}
    all_dates = sorted(set(sh_by_date) | set(sz_by_date), reverse=True)
    if not all_dates:
        _CACHE[key] = {"date": d, "sh": None, "sz": None, "note": "无数据", "recent5": []}
        return _CACHE[key]
    target = d if d in all_dates else all_dates[0]  # 当日未出则取最近可得日, 由调用方标注
    recent5 = []
    for dt_ in all_dates[:5]:
        s = sh_by_date.get(dt_)
        z = sz_by_date.get(dt_)
        s_amt = _nb_side(s)["deal_amt"] if s else None
        z_amt = _nb_side(z)["deal_amt"] if z else None
        total = round(s_amt + z_amt, 2) if (s_amt is not None and z_amt is not None) else None
        recent5.append({
            "date": dt_,
            "sh_deal_amt": s_amt,
            "sz_deal_amt": z_amt,
            "total_deal_amt": total,
        })
    out = {
        "date": target,
        "requested_date": d,
        "sh": _nb_side(sh_by_date.get(target)),
        "sz": _nb_side(sz_by_date.get(target)),
        "net_buy": None,
        "note": "北向净买入自2024-08-19起停止披露, 现仅披露成交额/笔数; 金额单位亿元",
        "recent5": recent5,
    }
    _CACHE[key] = out
    return out


def check_pool(pool_codes, date=None) -> list:
    """只读检测: 交易名单(pool_codes)中当日上榜龙虎榜的票及净买卖额。
    不接进任何交易决策, 仅供报告/人工对账参考。失败/无数据返回 []。
    """
    try:
        codes = {re.sub(r"\D", "", str(c)) for c in pool_codes}
        codes.discard("")
    except Exception as e:
        _log_err("check_pool 代码入参异常: %s" % e)
        return []
    dt = dragon_tiger(date)
    if dt is None:
        _log_err("check_pool: 龙虎榜请求失败, 返回空")
        return []
    grouped = {}
    for row in dt:
        c = row["code"]
        g = grouped.setdefault(c, {
            "code": c, "name": row["name"], "net_buy": row["net_buy"],
            "reason": [], "chg": row.get("chg"),
        })
        g["reason"].append(row["reason"])
        if abs(row["net_buy"]) > abs(g["net_buy"]):
            g["net_buy"] = row["net_buy"]  # 多榜取净买额绝对值最大一条
            g["chg"] = row.get("chg")
    hits = [g for c, g in grouped.items() if c in codes]
    for g in hits:
        g["reason"] = " / ".join([x for x in g["reason"] if x])
    hits.sort(key=lambda x: x["net_buy"], reverse=True)
    return hits


# ---------------- 报告/存档 (snapshot) ----------------

def _fmt_amt_yuan(v):
    """元 -> 可读字符串(亿/万)。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    if abs(v) >= 1e8:
        return "%+.2f亿" % (v / 1e8)
    if abs(v) >= 1e4:
        return "%+.0f万" % (v / 1e4)
    return "%+.0f元" % v


def _load_pool_codes():
    """只读加载 pool.json 的代码列表(仅供观察展示)。"""
    try:
        with open(POOL_PATH, "r", encoding="utf-8") as f:
            pool = json.load(f)
        return [p.get("code") for p in pool if p.get("code")]
    except Exception as e:
        _log_err("读取 pool.json 失败(不影响主流程): %s" % e)
        return []


def _render_flow_report(now_str, dt_date, dt_rows, dt_note, nb, pool_hits):
    lines = []
    lines.append("📊 资金流向观察 %s" % now_str[:10])
    lines.append("   —— 观察性数据, 不参与交易决策 ——")
    lines.append("")
    lines.append("🐉 龙虎榜 (%s):" % dt_date)
    lines.append("  %s" % dt_note)
    if dt_rows:
        uniq = {}
        for r in dt_rows:
            u = uniq.setdefault(r["code"], {"code": r["code"], "name": r["name"], "net_buy": r["net_buy"],
                                            "chg": r.get("chg"), "reason": []})
            u["reason"].append(r["reason"])
            if abs(r["net_buy"]) > abs(u["net_buy"]):
                u["net_buy"], u["chg"] = r["net_buy"], r.get("chg")
        rows = sorted(uniq.values(), key=lambda x: x["net_buy"], reverse=True)
        lines.append("  上榜 %d 条 / %d 只, 按净买额排序(前20):" % (len(dt_rows), len(rows)))
        for u in rows[:20]:
            chg = ("涨%+.2f%%" % u["chg"]) if isinstance(u["chg"], (int, float)) else "-"
            lines.append("  · %s(%s) 净买 %s %s | %s"
                         % (u["name"], u["code"], _fmt_amt_yuan(u["net_buy"]), chg, " / ".join(filter(None, u["reason"]))[:48]))
        if len(rows) > 20:
            lines.append("  · ... 其余 %d 只见 flow_history.jsonl" % (len(rows) - 20))
    lines.append("")
    lines.append("🌊 北向资金 (%s):" % (nb.get("date") if nb else "-"))
    if nb:
        if nb.get("requested_date") and nb.get("date") != nb.get("requested_date"):
            lines.append("  注: 当日(%s)数据未发布, 展示最近可得日" % nb["requested_date"])
        lines.append("  · 净买入: %s" % ("已停止披露(2024-08-19起), 观察成交额" if not nb.get("net_buy") else _fmt_amt_yuan(nb["net_buy"])))
        sh, sz = nb.get("sh"), nb.get("sz")
        if sh or sz:
            s_amt = sh.get("deal_amt") if sh else None
            z_amt = sz.get("deal_amt") if sz else None
            total = (s_amt + z_amt) if (s_amt is not None and z_amt is not None) else None
            lines.append("  · 沪股通成交 %s亿 | 深股通成交 %s亿 | 合计 %s亿"
                         % (s_amt if s_amt is not None else "-", z_amt if z_amt is not None else "-", total if total is not None else "-"))
        lines.append("  · 近5日合计成交(亿元):")
        for it in nb.get("recent5", []):
            t = it.get("total_deal_amt")
            lines.append("    %s 合计 %s (沪 %s / 深 %s)"
                         % (it["date"], t if t is not None else "-",
                            it.get("sh_deal_amt") if it.get("sh_deal_amt") is not None else "-",
                            it.get("sz_deal_amt") if it.get("sz_deal_amt") is not None else "-"))
    else:
        lines.append("  · 数据获取失败(fail-open, 不影响收盘链)")
    lines.append("")
    lines.append("🎯 交易名单命中龙虎榜 (只读参考, 不改变任何规则):")
    if pool_hits:
        for h in pool_hits:
            lines.append("  · %s(%s) 净买 %s | %s" % (h["name"], h["code"], _fmt_amt_yuan(h["net_buy"]), h["reason"][:60]))
    else:
        lines.append("  · 无")
    lines.append("")
    lines.append("— flow_data.py · fail-open · 数据仅存档观察 —")
    return "\n".join(lines)


def snapshot():
    """拉当日龙虎榜+北向, 写 flow 报告 + 追加 jsonl 存档。任何异常 exit 0。"""
    try:
        now = datetime.now()
        ymd = now.strftime("%Y%m%d")
        iso_date = now.strftime("%Y-%m-%d")
        status = "ok"

        dt_rows = dragon_tiger(iso_date)
        dt_date, dt_note = iso_date, ""
        if dt_rows is None:
            status = "partial"
            dt_note = "· 请求失败(fail-open)"
            dt_rows = []
        elif not dt_rows:
            # 当日未发布(15:05时通常尚未出) → 回看最近发布日, 明确标注
            for back in range(1, 8):
                d_prev = (now - timedelta(days=back)).strftime("%Y-%m-%d")
                rows_prev = dragon_tiger(d_prev)
                if rows_prev:
                    dt_date, dt_rows = d_prev, rows_prev
                    dt_note = "注: 当日(%s)数据未发布(通常盘后晚间披露), 以下为最近发布日" % iso_date
                    status = "partial"
                    break
            else:
                dt_note = "· 近7日无可龙虎榜数据(节假日/接口变更?)"
        elif dt_rows:
            dt_note = "共 %d 条记录" % len(dt_rows)

        nb = northbound(iso_date)
        if nb is None:
            status = "partial" if status == "ok" else status

        pool_codes = _load_pool_codes()
        pool_hits = check_pool(pool_codes, dt_date) if dt_rows else []

        report = _render_flow_report(now.strftime("%Y-%m-%d %H:%M"), dt_date, dt_rows, dt_note, nb, pool_hits)
        os.makedirs(REPORTS_DIR, exist_ok=True)
        report_path = os.path.join(REPORTS_DIR, "flow_%s.txt" % ymd)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report + "\n")

        record = {
            "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": iso_date,
            "dragon_tiger_date": dt_date,
            "status": status,
            "dragon_tiger": dt_rows if dt_rows else [],
            "northbound": nb,
            "pool_hits": pool_hits,
        }
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        print("[flow_data] snapshot 完成: %s (status=%s, 龙虎榜%d条, 池命中%d)"
              % (report_path, status, len(dt_rows), len(pool_hits)))
    except Exception:
        # fail-open: 任何情况下不阻塞收盘链
        _log_err("snapshot 异常(忽略, 保证收盘链继续):")
        traceback.print_exc()
    sys.exit(0)


def _selftest():
    today = datetime.now().strftime("%Y-%m-%d")
    print("== flow_data 自测 %s ==" % today)
    dt = dragon_tiger(today)
    print("dragon_tiger(%s) -> %s" % (today, "None(请求失败)" if dt is None else "%d 条" % len(dt)))
    if dt is not None and not dt:
        for back in range(1, 8):
            d_prev = (datetime.now() - timedelta(days=back)).strftime("%Y-%m-%d")
            dt_prev = dragon_tiger(d_prev)
            if dt_prev:
                print("今日未发布, 最近发布日 %s -> %d 条, 样例:" % (d_prev, len(dt_prev)))
                for r in dt_prev[:3]:
                    print("  %r" % {k: r[k] for k in ("code", "name", "net_buy", "reason")})
                dt = dt_prev
                break
    nb = northbound(today)
    print("northbound(%s) -> %s" % (today, json.dumps(nb, ensure_ascii=False)[:600] if nb else "None(请求失败)"))
    codes = _load_pool_codes()
    hits = check_pool(codes, today)
    print("check_pool(交易名单%d只) -> %d 命中: %s" % (len(codes), len(hits), json.dumps(hits, ensure_ascii=False)[:400]))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "snapshot":
        snapshot()  # 内部保证 exit 0
    else:
        try:
            sys.exit(_selftest())
        except Exception as e:
            _log_err("自测异常: %r" % e)
            sys.exit(1)
