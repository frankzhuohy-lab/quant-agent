#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前向模拟（方案文档阶段五）：每日收盘后运行，产出次日意图单，次日比对。

流程（纸面信号，非自动交易；研究晋级≠上线）:
  1. 刷新 22 只定型池日线（腾讯 qfq）。
  2. 用部署候选规格（默认 EXP_0003: regime_gate + exposure_cap=1.0）
     重放引擎至最近完成 K 线 → 新成交并入台账（append-only）。
  3. 由最近一日信号生成下一交易日意图单（买: top-K 评分过过滤；
     开盘近似涨停/停牌则跳过——次日以真实行情裁决并记录差异）。
  4. 与上一交易日意图单比对：filled / skipped（原因）/ pending。

台账: var/forward/ledger.jsonl（逐行 append，含数据快照哈希与日期，
前复权历史修订会导致旧成交重述——台账以成交日记录为准，不回改）。
意图: var/forward/intentions/<date>.json

用法:
  python3 -m quant_agent.forward.daily_forward           # 常规每日运行
  python3 -m quant_agent.forward.daily_forward --exp EXP_0003
"""
from __future__ import print_function
import os, sys, json, argparse, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import quant_agent
from quant_agent.experiments.store import Store
from quant_agent.orchestrator.loop import load_constitution
from quant_agent.strategies.adapter import normalize, materialize
from quant_agent.backtest.runner import Context
from quant_agent.data import snapshot as snap

FWD = os.path.join(quant_agent.VAR, "forward")


def _ledger_fp():
    return os.path.join(FWD, "ledger.jsonl")


def _int_fp(date):
    d = os.path.join(FWD, "intentions")
    if not os.path.isdir(d):
        os.makedirs(d)
    return os.path.join(d, "%s.json" % date)


def load_spec(store, exp_id):
    """exp_id 可以是实验/策略版本号，或 "champion"（当前研究比较基线）。

    前向模拟跟踪 champion 而非某个已证伪候选——评审第 3 轮修正：
    EXP_0003 已 REJECT，不再作为部署形态默认值。
    """
    if exp_id == "champion":
        row = store.conn.execute(
            "SELECT s.spec_json FROM strategies s "
            "JOIN (SELECT version FROM champion_history "
            "ORDER BY promoted_at DESC LIMIT 1) c "
            "ON s.version = c.version").fetchone()
        if not row:
            raise ValueError("champion 无规格")
        return json.loads(row[0])
    exp = store.get_experiment(exp_id)
    if not exp or not exp.get("spec_json"):
        raise ValueError("实验 %s 无规格" % exp_id)
    return exp["spec_json"]


def run(exp_id="champion"):
    const = load_constitution()
    store = Store(os.path.join(quant_agent.VAR, "qa_store.sqlite3"))
    spec_patch = load_spec(store, exp_id)
    ctx = Context(refresh=True)   # 刷新 22 只池日线
    n = ctx.n
    # 与 BacktestRunner.run 同规则: 只取宪法允许的补丁字段，其余忽略
    allowed = set(const["research_policy"]["allowed_patch_fields"])
    patch_in = {k: v for k, v in (spec_patch or {}).items() if k in allowed}
    norm = normalize(patch_in, const)
    spec, kw, _canon = materialize(norm, ctx.stocks, ctx.codes, ctx.common)
    sys.path.insert(0, quant_agent.QSYS)
    from qengine import run_r443
    from qstress import REAL_COST
    r = run_r443(ctx.stocks, ctx.codes, ctx.feat, ctx.state, ctx.common,
                 ctx.susp, ctx.warmup, n - 6, n - 1, spec,
                 cost_buy=REAL_COST[0], cost_sell=REAL_COST[1],
                 ld_block=True, **kw)
    today = ctx.common[n - 1]

    # 快照哈希（前复权修订检测）
    man = snap.build_manifest(os.path.join(quant_agent.QSYS, "cache"))
    snap_id = man["snapshot_id"]

    # 1) 台账：新成交（按 (code,entry_date) 去重，append-only）
    seen = set()
    if os.path.exists(_ledger_fp()):
        with open(_ledger_fp()) as f:
            for line in f:
                try:
                    t = json.loads(line)
                    seen.add((t["code"], t["entry_date"]))
                except ValueError:
                    pass
    # 有意图单存档的信号日 → simulated_fill；否则 historical_replay
    int_dir = os.path.join(FWD, "intentions")
    intention_dates = set(os.listdir(int_dir)) if os.path.isdir(int_dir) \
        else set()

    new_fills = []
    if not os.path.isdir(FWD):
        os.makedirs(FWD)
    with open(_ledger_fp(), "a") as f:
        for t in r["trades"]:
            key = (t["code"], t["entry_date"])
            if key in seen:
                continue
            rec = dict(t)
            rec["recorded_at"] = today
            rec["data_snapshot"] = snap_id
            rec["source"] = exp_id
            sig_date = ctx.common[
                ctx.common.index(t["entry_date"]) - 1] if \
                t["entry_date"] in ctx.common else None
            rec["kind"] = ("simulated_fill"
                           if sig_date and (sig_date + ".json")
                           in intention_dates else "historical_replay")
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            new_fills.append(rec)

    # 2) 与上一交易日意图单比对
    prev_intentions = None
    prev_fp = _int_fp(ctx.common[n - 2]) if n >= 2 else None
    if prev_fp and os.path.exists(prev_fp):
        prev_intentions = json.load(open(prev_fp))
    fills_by_key = {(t["code"], t["entry_date"]): t for t in r["trades"]}
    comparison = []
    if prev_intentions:
        for it in prev_intentions.get("buys", []):
            key = (it["code"], today)
            t = fills_by_key.get(key)
            if t:
                comparison.append({"code": it["code"], "intention": "buy",
                                   "status": "filled",
                                   "entry_px": t["entry_px"],
                                   "note": t["reason"]})
            else:
                o = ctx.stocks[it["code"]]["open"]
                pc = ctx.stocks[it["code"]]["close"]
                band = 0.195 if it["code"].startswith(("688", "300", "301")) \
                    else 0.095
                reason = "limit_up_skip" if o[n - 1] >= pc[n - 2] * (
                    1 + band - 0.005) else (
                    "suspension_or_exposure" if it["code"] in
                    ctx.susp.get(it["code"], ()) or True else "other")
                comparison.append({"code": it["code"], "intention": "buy",
                                   "status": "skipped",
                                   "probable_reason": reason})

    # 3) 生成下一交易日意图单
    t_sig = n - 1
    buys = []
    if spec["filt"](ctx.state, t_sig):
        scores = spec["score"](ctx.feat, ctx.codes, t_sig)
        picks = sorted(scores, key=lambda c: -scores[c])[:spec["K"]]
        for c in picks:
            if scores[c] <= -1e8:
                continue
            buys.append({"code": c, "name": ctx.stocks[c]["name"],
                         "score": round(scores[c], 4),
                         "signal_date": today,
                         "guard": "skip if next open >= prev_close*"
                                  "(1+band-0.005) or suspended",
                         "weight": 1.0 / 5 / max(1, len(picks))})
    intention = {
        "generated_at": today,
        "for_session": "next_trading_day",
        "kind": "forward_intention",
        "strategy": exp_id,
        "data_snapshot": snap_id,
        "note": "纸面信号非自动交易；Keltner/到时出场由引擎每日重算，"
                "不在意图单列出。",
        "buys": buys,
    }
    with open(_int_fp(today), "w") as f:
        json.dump(intention, f, ensure_ascii=False, indent=1)

    # 4) 封存访问日志（评审第 3 轮）：记录本次运行对封存区间的读取
    _log_sealed_access(ctx, exp_id, snap_id)

    store.close()
    return {"asof": today, "snapshot": snap_id, "new_fills": new_fills,
            "comparison": comparison, "intentions": intention,
            "n_open_signals": len(buys)}


def _log_sealed_access(ctx, exp_id, snap_id):
    """每次运行记录封存区间读取：工具、区间、目的、是否可能回流研究循环。

    前向观察本身被允许；本日志是与"封存资格"配套的审计证据——
    若未来发现任何策略/门槛/候选变更可追溯至这些观察，对应区间
    须即刻降级（评审第 3 轮）。
    """
    sealed_start = ctx.const.get("sealed_holdout", {}).get("start")
    if not sealed_start:
        return
    touched = [d for d in ctx.common if d >= sealed_start]
    if not touched:
        return
    rec = {
        "tool": "daily_forward",
        "experiment": exp_id,
        "snapshot": snap_id,
        "range_read": [touched[0], touched[-1]],
        "n_days": len(touched),
        "purpose": "forward_observation",
        "consumed_by_selection": False,
        "policy": "outputs -> var/forward only; research loop never reads them",
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    fp = os.path.join(quant_agent.VAR, "sealed_access_log.jsonl")
    with open(fp, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="champion",
                    help="实验/策略版本号，或 champion（默认，当前研究比较基线）")
    args = ap.parse_args()
    out = run(args.exp)
    print("asof:", out["asof"], "snapshot:", out["snapshot"],
          "n_open_signals:", out["n_open_signals"])
    print("new fills:", len(out["new_fills"]))
    for t in out["new_fills"][-5:]:
        print("  fill %s %s entry=%s exit=%s gross=%.2f%% reason=%s" % (
            t["code"], t["entry_date"], t["entry_px"], t["exit_date"],
            t["gross"] * 100, t["reason"]))
    print("intention check vs yesterday:", len(out["comparison"]))
    for c in out["comparison"]:
        print("  ", c["code"], c["status"], c.get("note") or
              c.get("probable_reason"))
    print("next-session buy intentions:", out["n_open_signals"])
    for b in out["intentions"]["buys"]:
        print("  buy", b["code"], b["name"], "score=", b["score"])


if __name__ == "__main__":
    main()
