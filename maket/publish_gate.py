# -*- coding: utf-8 -*-
"""发布门禁。对照 tick-stock-panel 的显式发布线，只给判决，不改交易规则。

2026-09-23 起，优化日志里的「样本≥10 且方向一致率≥70%」只够继续观察，
不能单独采纳。采纳要本模块给出 status=eligible，再由人在变更台账手写一行。
本文件的 published 恒为 false：没有任何函数会改 maket.py、筛选器或账本。

策略发布（样本外路径，全部满足才 eligible）：
  非探索档；有效外折 ≥ 2；正收益折 ≥ 2/3；
  拼接后的样本外日夏普 ≥ 0.5（252 日年化）；最大回撤不差于 -25%；
  样本外成交 ≥ 60。
  样本不够时是 pending，不是否决，也不许据此改冻结层。

因子差分族（预注册的多指标一起判）：
  日差序列用 Newey-West（Bartlett，滞后 = min(5, n//4, floor(4*(n/100)^(2/9)))）；
  族内 p 值做 BH-FDR，q=0.10；方向必须与预注册符号一致；单条日数 < 20 则整族 pending。

因子 IC（单独一条序列时）：
  观测 ≥ 60；|IC| ≥ 0.02；|IR| ≥ 0.3；Newey-West 双侧 p < 0.05；符号与预注册一致。

活账本收益沿用已入账的 ret_net（百分数，成本 0.12%）。研究回测若要 0.22% 成本，
须在进入本模块之前扣完。本模块不重述成本。
"""
from __future__ import print_function

import datetime
import json
import math
import os
import sys

MIN_TRADES = 60
MIN_FOLDS = 2
MIN_POS_RATIO = 2.0 / 3.0
MIN_SHARPE = 0.5
MAX_DD = -0.25
IC_MIN = 0.02
IR_MIN = 0.3
IC_P_MAX = 0.05
FDR_Q = 0.10
MIN_DIFF_DAYS = 20
MIN_IC_N = 60
PERIODS_PER_YEAR = 252

HERE = os.path.dirname(os.path.abspath(__file__))


def _betacf(a, b, x):
    maxit, eps, fpmin = 300, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lb = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
          + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lb) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lb) * _betacf(b, a, 1.0 - x) / b


def t_pvalue(t, df):
    """双侧 p。df<=0 或非有限 t 返回 None。"""
    if df is None or df <= 0 or t is None or not math.isfinite(t):
        return None
    return betai(df / 2.0, 0.5, df / (df + t * t))


def nw_lag(n, lag=None):
    """Bartlett 滞后。显式 lag 夹到 [0, n-1]；默认取 Newey-West 插件值，再夹到 [1, min(5, n//4)]。"""
    n = int(n)
    if n <= 1:
        return 0
    if lag is not None:
        return max(0, min(int(lag), n - 1))
    if n < 3:
        return 0
    auto = int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))
    cap = min(5, n // 4)
    if cap < 1:
        return 0
    return max(1, min(auto, cap))


def newey_west_mean(series, lag=None):
    """均值的 Newey-West t。γ_j 用 1/n 归一，权重 1 - j/(L+1)。S<=0 时退回 iid 方差并标记。"""
    xs = [float(x) for x in series]
    n = len(xs)
    if n < 2:
        return {"n": n, "mean": (xs[0] if n == 1 else None), "se": None, "t": None,
                "p": None, "lag": 0, "df": None, "hac_fallback": False}
    mean = sum(xs) / float(n)
    e = [x - mean for x in xs]
    L = nw_lag(n, lag)
    gamma0 = sum(v * v for v in e) / float(n)
    hac = gamma0
    for j in range(1, L + 1):
        gamma = sum(e[t] * e[t - j] for t in range(j, n)) / float(n)
        hac += 2.0 * (1.0 - j / float(L + 1)) * gamma
    fallback = False
    if not math.isfinite(hac) or hac <= 0.0:
        hac = gamma0
        fallback = True
    se = math.sqrt(hac / float(n)) if hac > 0.0 else 0.0
    if se > 0.0:
        t = mean / se
        p = t_pvalue(t, n - 1)
    elif mean == 0.0:
        t, p = 0.0, 1.0
    else:
        t, p = None, 0.0
    return {"n": n, "mean": mean, "se": se, "t": t, "p": p, "lag": L,
            "df": n - 1, "hac_fallback": fallback}


def bh_fdr(p_values, q=FDR_Q):
    """Benjamini-Hochberg。返回与输入同序的 {p, q_adj, reject}。None p 不占名额。"""
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None and math.isfinite(p)]
    m = len(indexed)
    out = [{"p": p, "q_adj": None, "reject": False} for p in p_values]
    if m == 0:
        return out
    order = sorted(indexed, key=lambda ip: (ip[1], ip[0]))
    raw = [order[k][1] * m / float(k + 1) for k in range(m)]
    adj = [0.0] * m
    running = 1.0
    for k in range(m - 1, -1, -1):
        running = min(raw[k], running)
        adj[k] = min(1.0, running)
    for k, (i, _p) in enumerate(order):
        out[i]["q_adj"] = adj[k]
        out[i]["reject"] = adj[k] <= q
    return out


def ann_sharpe(returns, periods=PERIODS_PER_YEAR):
    xs = [float(x) for x in returns]
    n = len(xs)
    if n < 2:
        return None
    mean = sum(xs) / float(n)
    var = sum((x - mean) ** 2 for x in xs) / float(n - 1)
    if var <= 0.0:
        if mean > 0.0:
            return float("inf")
        if mean < 0.0:
            return float("-inf")
        return 0.0
    return mean / math.sqrt(var) * math.sqrt(periods)


def max_drawdown(returns):
    """复利净值的最大回撤，0 到 -1 之间的负数或 0。"""
    eq, peak, worst = 1.0, 1.0, 0.0
    for r in returns:
        eq *= 1.0 + float(r)
        if eq > peak:
            peak = eq
        if peak > 0.0:
            dd = eq / peak - 1.0
            if dd < worst:
                worst = dd
    return worst


def _finite(x):
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def _jsonable(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_jsonable(v) for v in x]
    return x


def judge_strategy(folds, exploratory=False):
    """folds: [{returns: 日简单收益率(小数), n_trades: int}, ...]，按时间顺序。"""
    valid = []
    for fold in folds or []:
        rets = [float(x) for x in fold.get("returns") or []]
        trades = int(fold.get("n_trades") or 0)
        if len(rets) >= 2 and trades > 0:
            valid.append({"returns": rets, "n_trades": trades})
    pooled = [r for fold in valid for r in fold["returns"]]
    n_trades = sum(fold["n_trades"] for fold in valid)
    n_folds = len(valid)
    pos = sum(1 for fold in valid if sum(fold["returns"]) > 0.0)
    pos_ratio = (pos / float(n_folds)) if n_folds else None
    sharpe = ann_sharpe(pooled)
    dd = max_drawdown(pooled) if pooled else None
    reasons = []
    insuff = []
    if exploratory:
        insuff.append("探索档结果不能发布")
    if n_folds < MIN_FOLDS:
        insuff.append("有效外折 %d < %d" % (n_folds, MIN_FOLDS))
    if n_trades < MIN_TRADES:
        insuff.append("样本外成交 %d < %d" % (n_trades, MIN_TRADES))
    sample_ok = (not exploratory) and n_folds >= MIN_FOLDS and n_trades >= MIN_TRADES
    if sample_ok:
        sharpe_ok = sharpe is not None and (math.isinf(sharpe) and sharpe > 0.0 or _finite(sharpe) and sharpe >= MIN_SHARPE)
        if not sharpe_ok:
            reasons.append("样本外夏普 %s < %.2f" % (sharpe, MIN_SHARPE))
        if dd is None or dd < MAX_DD:
            reasons.append("最大回撤 %s 差于 %.0f%%" % (dd, 100.0 * MAX_DD))
        if pos_ratio is None or pos_ratio + 1e-12 < MIN_POS_RATIO:
            reasons.append("正收益折比例 %s < 2/3" % pos_ratio)
    if reasons:
        status = "reject"
    elif insuff or not valid:
        status = "pending"
    else:
        status = "eligible"
    return {
        "kind": "strategy",
        "status": status,
        "published": False,
        "n_folds": n_folds,
        "n_trades": n_trades,
        "positive_folds": pos,
        "positive_fold_ratio": pos_ratio,
        "sharpe_ann": sharpe if _finite(sharpe) else None,
        "sharpe_infinite": bool(sharpe is not None and math.isinf(sharpe)),
        "max_drawdown": dd,
        "mean_daily": (sum(pooled) / float(len(pooled))) if pooled else None,
        "reasons": reasons + insuff,
        "action": "可以写入变更台账，但仍未发布" if status == "eligible" else "不得发布，不得据此修改冻结层",
    }


def judge_diff_family(items, q=FDR_Q):
    """items: [{name, diffs, expected_sign}]。expected_sign 为 +1 或 -1。"""
    prepared = []
    for item in items:
        diffs = [float(x) for x in item.get("diffs") or []]
        nw = newey_west_mean(diffs)
        prepared.append({
            "name": item.get("name"),
            "expected_sign": 1 if int(item.get("expected_sign") or 1) >= 0 else -1,
            "n": nw["n"],
            "mean": nw["mean"],
            "t": nw["t"],
            "p": nw["p"],
            "lag": nw["lag"],
            "hac_fallback": nw["hac_fallback"],
        })
    if any(row["n"] < MIN_DIFF_DAYS for row in prepared) or not prepared:
        return {
            "kind": "diff_family",
            "status": "pending",
            "published": False,
            "q": q,
            "tests": prepared,
            "reasons": ["预注册差分族日数不足 %d，不能采纳也不能否决" % MIN_DIFF_DAYS],
            "action": "不得发布，不得据此修改冻结层",
        }
    fdr = bh_fdr([row["p"] for row in prepared], q=q)
    reasons = []
    for row, adj in zip(prepared, fdr):
        row["q_adj"] = adj["q_adj"]
        sign_ok = (row["mean"] is not None) and (row["expected_sign"] * row["mean"] > 0.0)
        row["sign_ok"] = sign_ok
        row["pass"] = bool(sign_ok and adj["reject"])
        if not sign_ok:
            reasons.append("%s 方向与预注册相反（均值 %s）" % (row["name"], row["mean"]))
        elif not adj["reject"]:
            reasons.append("%s BH q=%s > %.2f" % (row["name"], adj["q_adj"], q))
    status = "eligible" if prepared and all(row["pass"] for row in prepared) else "reject"
    return {
        "kind": "diff_family",
        "status": status,
        "published": False,
        "q": q,
        "tests": prepared,
        "reasons": reasons,
        "action": "可以写入变更台账，但仍未发布" if status == "eligible" else "不得发布，不得据此修改冻结层",
    }


def judge_ic(series, expected_sign=1):
    nw = newey_west_mean(series)
    n = nw["n"]
    mean = nw["mean"]
    ir_infinite = False
    if n < 2 or mean is None:
        ir = None
    else:
        sd = math.sqrt(sum((float(x) - mean) ** 2 for x in series) / float(n - 1))
        if sd > 0.0:
            ir = mean / sd
        elif mean == 0.0:
            ir = 0.0
        else:
            ir = None
            ir_infinite = True
    sign = 1 if int(expected_sign) >= 0 else -1
    reasons = []
    if n < MIN_IC_N:
        status = "pending"
        reasons.append("IC 观测 %d < %d" % (n, MIN_IC_N))
    else:
        sign_ok = mean is not None and sign * mean > 0.0
        mag_ok = (mean is not None and abs(mean) >= IC_MIN
                  and (ir_infinite or (ir is not None and abs(ir) >= IR_MIN)))
        sig_ok = nw["p"] is not None and nw["p"] < IC_P_MAX
        if not sign_ok:
            reasons.append("IC 符号与预注册相反")
        if not mag_ok:
            reasons.append("|IC| 或 |IR| 低于 0.02 / 0.3")
        if not sig_ok:
            reasons.append("Newey-West p=%s 不低于 %.2f" % (nw["p"], IC_P_MAX))
        status = "eligible" if sign_ok and mag_ok and sig_ok else "reject"
    return {
        "kind": "ic",
        "status": status,
        "published": False,
        "n": n,
        "ic_mean": mean,
        "ir": ir,
        "ir_infinite": ir_infinite,
        "t": nw["t"],
        "p": nw["p"],
        "lag": nw["lag"],
        "reasons": reasons,
        "action": "可以写入变更台账，但仍未发布" if status == "eligible" else "不得发布，不得据此修改冻结层",
    }


def combine(parts):
    states = [p["status"] for p in parts]
    if "reject" in states:
        status = "reject"
    elif (not states) or ("pending" in states):
        status = "pending"
    else:
        status = "eligible"
    reasons = []
    for part in parts:
        reasons.extend(part.get("reasons") or [])
    return {
        "status": status,
        "published": False,
        "parts": parts,
        "reasons": reasons,
        "action": "可以写入变更台账，但仍未发布" if status == "eligible" else "不得发布，不得据此修改冻结层",
    }


def ledger_folds(ledger):
    by_day = {}
    order = []
    for rnd in ledger.get("rounds") or []:
        if rnd.get("ret_net") is None or not rnd.get("date"):
            continue
        day = str(rnd["date"])
        if day not in by_day:
            by_day[day] = []
            order.append(day)
        by_day[day].append(float(rnd["ret_net"]) / 100.0)
    if not order:
        return [], {"n_rounds": 0, "sum_ret_pct": 0.0, "wins": 0}
    daily = [sum(by_day[day]) / float(len(by_day[day])) for day in order]
    rets = [float(r["ret_net"]) for r in ledger["rounds"] if r.get("ret_net") is not None]
    return ([{"name": "live", "returns": daily, "n_trades": len(rets)}], {
        "n_rounds": len(rets),
        "sum_ret_pct": sum(rets),
        "wins": sum(1 for x in rets if x > 0.0),
        "n_days": len(daily),
    })


def judge_ledger(ledger):
    folds, summary = ledger_folds(ledger)
    verdict = judge_strategy(folds, exploratory=False)
    verdict["ledger"] = summary
    verdict["published"] = False
    return verdict


def write_ledger_status(directory=None):
    directory = directory or HERE
    ledger_fp = os.path.join(directory, "ledger_t.json")
    with open(ledger_fp) as fh:
        ledger = json.load(fh)
    verdict = judge_ledger(ledger)
    payload = _jsonable({
        "generated": datetime.date.today().isoformat(),
        "source": "ledger_t.json",
        "bar": {
            "min_trades": MIN_TRADES,
            "min_folds": MIN_FOLDS,
            "min_sharpe": MIN_SHARPE,
            "max_drawdown": MAX_DD,
            "note": "70%方向一致率不是发布线",
        },
        "verdict": verdict,
    })
    reports = os.path.join(directory, "reports")
    os.makedirs(reports, exist_ok=True)
    stamp = datetime.date.today().strftime("%Y%m%d")
    for name in ("publish_status.json", "publish_status_%s.json" % stamp):
        with open(os.path.join(reports, name), "w") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    return payload


def _selftest():
    nw = newey_west_mean([1.0, 2.0, 3.0, 4.0])
    assert nw["lag"] == 1, nw
    assert abs(nw["mean"] - 2.5) < 1e-12
    assert abs(nw["t"] - 4.0) < 1e-9, nw
    assert nw["p"] is not None and 0.0 < nw["p"] < 0.05

    p_norm = t_pvalue(1.95996398454, 10000)
    assert p_norm is not None and 0.049 < p_norm < 0.052, p_norm
    assert abs(t_pvalue(0.0, 10) - 1.0) < 1e-9

    adj = bh_fdr([0.04, 0.01, 0.20], q=0.10)
    assert abs(adj[1]["q_adj"] - 0.03) < 1e-12
    assert abs(adj[0]["q_adj"] - 0.06) < 1e-12
    assert adj[1]["reject"] and adj[0]["reject"] and not adj[2]["reject"]
    mono = bh_fdr([0.045, 0.01, 0.04], q=0.10)
    # 排序后 p=(0.01, 0.04, 0.045)，尾部单调：0.03, 0.045, 0.045
    by_p = sorted((row["p"], row["q_adj"]) for row in mono)
    assert abs(by_p[0][1] - 0.03) < 1e-12
    assert abs(by_p[1][1] - 0.045) < 1e-12
    assert abs(by_p[2][1] - 0.045) < 1e-12

    good = judge_strategy([
        {"returns": [0.001] * 30, "n_trades": 30},
        {"returns": [0.001] * 30, "n_trades": 30},
    ])
    assert good["status"] == "eligible" and good["published"] is False, good

    short = judge_strategy([{"returns": [0.01, -0.02, 0.005], "n_trades": 20}])
    assert short["status"] == "pending" and short["published"] is False, short

    bad = judge_strategy([
        {"returns": [-0.002] * 40, "n_trades": 40},
        {"returns": [-0.001] * 40, "n_trades": 40},
    ])
    assert bad["status"] == "reject", bad

    explore = judge_strategy([
        {"returns": [0.001] * 30, "n_trades": 30},
        {"returns": [0.001] * 30, "n_trades": 30},
    ], exploratory=True)
    assert explore["status"] == "pending", explore

    wrong = judge_diff_family([
        {"name": "a", "diffs": [-0.01] * 30, "expected_sign": 1},
        {"name": "b", "diffs": [-0.01] * 30, "expected_sign": 1},
    ])
    assert wrong["status"] == "reject", wrong

    tiny = judge_diff_family([{"name": "a", "diffs": [0.01] * 5, "expected_sign": 1}])
    assert tiny["status"] == "pending", tiny

    flat_ic = judge_ic([0.03] * 80, expected_sign=1)
    assert flat_ic["status"] == "eligible", flat_ic
    weak_ic = judge_ic([0.03] * 10, expected_sign=1)
    assert weak_ic["status"] == "pending", weak_ic

    assert max_drawdown([0.10, -0.50]) < -0.4
    print("publish_gate selftest OK")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "ledger"
    if cmd == "selftest":
        return _selftest()
    if cmd == "ledger":
        payload = write_ledger_status(HERE)
        v = payload["verdict"]
        led = v.get("ledger") or {}
        print("发布状态: %s（published=false）" % v["status"])
        print("回合 %d，胜 %d，回合收益合计 %+.3f%%，有成交日 %d"
              % (led.get("n_rounds", 0), led.get("wins", 0),
                 led.get("sum_ret_pct", 0.0), led.get("n_days", 0)))
        if v.get("sharpe_ann") is not None:
            print("活跃日年化夏普 %.3f，活跃日路径最大回撤 %.2f%%"
                  % (v["sharpe_ann"], 100.0 * (v.get("max_drawdown") or 0.0)))
        print("动作: %s" % v["action"])
        for reason in v.get("reasons") or []:
            print("- %s" % reason)
        print("已写 reports/publish_status.json")
        return 0
    print("用法: python3 publish_gate.py {ledger|selftest}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
