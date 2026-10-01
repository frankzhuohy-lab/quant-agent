# -*- coding: utf-8 -*-
"""预测日志机械助手: 打分待验预测 + 生成次日预测所需的行情上下文。
用法: python3 journal_helper.py
输出: 控制台JSON文本(打分结果 + 每票上下文表), 供复盘/预测写作引用。
打分规则(预注册): P1方向=明日收盘vs今收符号(±0.3%内不计); P2触线=盘中最低<=今开*0.994;
P3目标=触线前提下当日最高>=今开*1.006。
"""
import os
import json
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def http_get(url, retries=3):
    import time as _t
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            _t.sleep(2 * (i + 1))
    raise last


def daily(sym, n=30):
    d = json.loads(http_get("https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq" % (sym, n)))
    data = d["data"][sym]
    return data.get("qfqday") or data.get("day")


def tracked():
    names = {"sz300765": "石药创新"}
    try:
        dyn = json.load(open(os.path.join(HERE, "whitelist_dynamic.json")))
        for s in dyn.get("stocks", []):
            sym = ("sh" if s["code"].startswith(("6", "9")) else "sz") + s["code"]
            names[sym] = s.get("name", s["code"])
    except (IOError, ValueError):
        pass
    return names


def score_pending():
    """对 predictions.json 里 status=pending 的预测逐条打分"""
    fp = os.path.join(HERE, "predictions.json")
    try:
        preds = json.load(open(fp))
    except (IOError, ValueError):
        return []
    out = []
    for p in preds:
        if p.get("status") != "pending" or p.get("void_reason"):
            continue
        try:
            bars = daily(p["symbol"])
        except Exception as e:
            out.append({"pred_id": p["id"], "error": "数据获取失败: %s" % e})
            continue
        tgt_day = None
        for b in bars:
            if b[0].replace("-", "") == p["target_date"].replace("-", ""):
                tgt_day = b
                break
        if tgt_day is None:
            out.append({"pred_id": p["id"], "error": "目标日无数据(未收盘?)"})
            continue
        o, c, h, l = float(tgt_day[1]), float(tgt_day[2]), float(tgt_day[3]), float(tgt_day[4])
        # 前收: 目标日在bars中的前一根
        i = bars.index(tgt_day)
        pc = float(bars[i - 1][2]) if i > 0 else o
        chg = (c / pc - 1) * 100
        line = o * 0.994
        touched = l <= line
        hit_tgt = h >= o * 1.006
        res = {"pred_id": p["id"], "name": p.get("name"), "date": p["target_date"],
               "actual_chg": round(chg, 2), "line_touched": touched, "target_hit": hit_tgt}
        s = {}
        p1 = p.get("P1")
        if p1 in ("UP", "DOWN"):
            actual = "UP" if chg > 0.3 else ("DOWN" if chg < -0.3 else "FLAT")
            s["P1"] = {"pred": p1, "actual": actual,
                       "verdict": "对" if p1 == actual else "错"} if actual != "FLAT" \
                else {"pred": p1, "actual": "FLAT", "verdict": "不计分"}
        p2 = p.get("P2")
        if p2 in ("YES", "NO"):
            s["P2"] = {"pred": p2, "actual": touched,
                       "verdict": "对" if (p2 == "YES") == touched else "错"}
        p3 = p.get("P3")
        if p3 in ("YES", "NO"):
            if touched:
                s["P3"] = {"pred": p3, "actual": hit_tgt,
                           "verdict": "对" if (p3 == "YES") == hit_tgt else "错"}
            else:
                s["P3"] = {"verdict": "未触发不计分"}
        res["score"] = s
        p["status"] = "scored"
        p["score_result"] = res
        out.append(res)
    with open(fp, "w") as f:
        json.dump(preds, f, ensure_ascii=False, indent=1)
    return out


def context():
    rows = []
    for sym, name in tracked().items():
        bars = daily(sym)
        if not bars or len(bars) < 25:
            continue
        closes = [float(b[2]) for b in bars]
        ma20 = sum(closes[-20:]) / 20.0
        ret20 = closes[-1] / closes[-21] - 1 if len(closes) > 21 else 0
        prev_ret = closes[-1] / closes[-2] - 1
        trs = [max(float(bars[i][3]) - float(bars[i][4]),
                   abs(float(bars[i][3]) - float(bars[i - 1][2])),
                   abs(float(bars[i][4]) - float(bars[i - 1][2])))
               for i in range(len(bars) - 14, len(bars))]
        atr = sum(trs) / 14 / closes[-1]
        hist = []
        for b in bars[-5:]:
            o, c, h, l = float(b[1]), float(b[2]), float(b[3]), float(b[4])
            i = bars.index(b)
            pc = float(bars[i - 1][2]) if i > 0 else o
            hist.append({"date": b[0], "chg": round((c / pc - 1) * 100, 2),
                         "line_touched": l <= o * 0.994,
                         "target_hit": h >= o * 1.006})
        rows.append({"name": name, "symbol": sym, "close": closes[-1],
                     "vs_ma20": round((closes[-1] / ma20 - 1) * 100, 1),
                     "ret20": round(ret20 * 100, 1), "prev_ret": round(prev_ret * 100, 2),
                     "atr_pct": round(atr * 100, 1), "last5": hist})
    idx = daily("sh000001")
    if idx:
        i = len(idx) - 1
        pc = float(idx[i - 1][2])
        rows.append({"name": "上证指数", "symbol": "sh000001",
                     "close": float(idx[i][2]),
                     "last_chg": round((float(idx[i][2]) / pc - 1) * 100, 2)})
    return rows


def main():
    print(json.dumps({"scored": score_pending(), "context": context()},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
