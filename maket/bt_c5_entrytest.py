# -*- coding: utf-8 -*-
"""R1 预注册回测 C5：入场口径三变体同窗口比较（只新增文件，不改任何现有引擎）。

预注册研究问题: 放宽入场窗口(V1)或改触价口径(V2)，历史表现是否显著更好？
背景: 现行系统盘中60秒轮询，只有"跌势持续到轮询时点"(bar收盘<=线)才成交，
      快速V型反弹日系统性踏空（实盘4样本: 09-18石药/剑桥, 09-21船舶/华胜）。

统一规则（三变体完全一致，仅入场判定不同）:
  网格:   买入线 = 当日开盘×(1-0.6%)，目标 = 当日开盘×(1+0.6%)
  止损:   入场价×(1-1.5%)
  强平:   hm>="1450" 的 bar 收盘价强平
  成本:   回合 = 双边0.12% + 滑点0.1% = 合计0.22%
  每票每日最多1回合; 离场判定一律用 m5 收盘价(60秒轮询口径),
  入场 bar 的次 bar 起才判离场（与 bt_model_r1/bt_hypo 重放口径一致）。

变体:
  V0_poll     现行: 收盘价<=线才成交（模拟60秒轮询采样）, 09:45起
  V1_win0935  窗口放宽: 同V0但 09:35 起
  V2_barlow   bar-low触价: 当根最低价<=线即成交, 成交价=线（限价单假设）, 09:45起

样本: m5 逐bar重放 20260316~20260911（END_DATE=20260911 复用 bt_hypo）。
      m5字段序 [time,open,close,high,low,vol]（脚本内置不变式自验, <99%则中止）。
      池化 = 有效交易日>=40 的票（13只, 含石药）; sh688825 仅35天不进池;
      石药 sz300765 另出单票表。IS = 全样本日前60个交易日, 其余OOS（与bt_model_r1一致）。

诚实性: 同窗口多变体比较存在多重比较问题, 未经校正; V2 是限价单假设,
        实盘60秒轮询无法完全复现（报告分解轮询采样损失）; 结论仅作冻结层评审证据,
        不构成采纳建议。
用法: python3 bt_c5_entrytest.py
"""
import os
import sys
import json
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bt_hypo as H          # 只读复用: load_universe / in_sample_date / END_DATE

D = 0.006                    # 开盘锚网格 d=0.6%
STOP_PCT = 0.015             # 入场价×0.985 止损
COST = 0.0022                # 双边0.12% + 滑点0.1% = 合计0.22%/回合
SLIP = 0.001                 # 其中滑点增量 0.1%（差值是否超滑点的判断阈值）
IS_DAYS = 60
START_DATE = "20260316"
END_DATE = H.END_DATE        # 20260911
MIN_BARS = 20                # 当日m5 bar数下限（与bt_model_r1一致）
POOL_MIN_DAYS = 40           # 进池门槛

VARIANTS = ["V0_poll", "V1_win0935", "V2_barlow"]
VDESC = {"V0_poll": "V0 现行(收盘触线,09:45起)",
         "V1_win0935": "V1 窗口放宽(收盘触线,09:35起)",
         "V2_barlow": "V2 bar-low触价(限价=线,09:45起)"}

ENTRY_BUCKETS = [
    ("0935-0944", lambda h: h < "0945"),
    ("0945-0959", lambda h: "0945" <= h < "1000"),
    ("1000-1029", lambda h: "1000" <= h < "1030"),
    ("1030-1059", lambda h: "1030" <= h < "1100"),
    ("1100-1130", lambda h: "1100" <= h <= "1130"),
    ("1300-1359", lambda h: "1300" <= h < "1400"),
    ("1400-末盘", lambda h: h >= "1400"),
]

FLAT_KEYS = ["未触线", "仅窗口前触线", "仅盘后触线未成交", "窗口前+盘后均触"]


# ---------- 数据 ----------
def load_days():
    """{sym: {date8: sorted bars}}, 窗口内且当日bar数>=MIN_BARS; 附不变式自验。"""
    u = H.load_universe()
    days_by_sym, names, ok, bad = {}, {}, 0, 0
    for sym, v in u.items():
        days = {}
        for d, bs in v["days"].items():
            if not H.in_sample_date(d) or d < START_DATE:
                continue
            bs = sorted(bs, key=lambda b: b[0])
            if len(bs) < MIN_BARS:
                continue
            for b in bs:                       # 字段序自验: [t,o,c,h,l,vol]
                o, c, h_, l_ = float(b[1]), float(b[2]), float(b[3]), float(b[4])
                if l_ <= min(o, c) <= max(o, c) <= h_:
                    ok += 1
                else:
                    bad += 1
            days[d] = bs
        if days:
            days_by_sym[sym] = days
            names[sym] = v["name"]
    ratio = ok / max(1, ok + bad)
    print("数据自验: low<=min(o,c)<=max(o,c)<=high 占比 %.5f%% (ok=%d bad=%d) %s"
          % (ratio * 100, ok, bad, "OK" if ratio >= 0.99 else "!! <99% 中止"))
    if ratio < 0.99:
        sys.exit(2)
    return days_by_sym, names


# ---------- 重放 ----------
def replay(bars, variant):
    """返回 (round|None, flat_reason|None, diag)。round: entry/entry_hm/exit/reason。"""
    if len(bars) < MIN_BARS:
        return None, None, {}
    o = float(bars[0][1])
    if not o:
        return None, None, {}
    line, tgt = o * (1 - D), o * (1 + D)
    win_open = "0935" if variant == "V1_win0935" else "0945"
    start_i = next((i for i, b in enumerate(bars) if b[0][8:] >= win_open), None)
    if start_i is None:
        return None, None, {}
    pre_touch = any(float(b[4]) <= line for b in bars[:start_i])
    diag = {"touch_bars": 0, "gap_touch_bars": 0, "entry_n": 0, "entry_gap": 0}
    pos = None
    for i in range(start_i, len(bars)):
        b = bars[i]
        hm = b[0][8:]
        px, lo_, op = float(b[2]), float(b[4]), float(b[1])
        if pos:
            entry = pos[0]
            if px <= entry * (1 - STOP_PCT):
                return {"entry": entry, "entry_hm": pos[2], "exit": px,
                        "reason": "止损"}, None, diag
            if px >= pos[1]:
                return {"entry": entry, "entry_hm": pos[2], "exit": px,
                        "reason": "目标"}, None, diag
            if hm >= "1450":
                return {"entry": entry, "entry_hm": pos[2], "exit": px,
                        "reason": "强平"}, None, diag
            continue
        touched = lo_ <= line
        if touched:
            diag["touch_bars"] += 1
            if op < line:
                diag["gap_touch_bars"] += 1
        if variant == "V2_barlow":
            enter, fill = touched, line              # 限价单假设: 触线即以线成交
        elif variant == "V2B_gapfill":
            # 诊断用(非预注册变体): 真实挂限价单口径, 开盘已低于线则按开盘成交(更优)
            enter, fill = touched, min(line, op)
        else:
            enter, fill = px <= line, px             # 60秒轮询: 收盘价触线才成交
        if enter:
            diag["entry_n"] += 1
            if touched and op < line:
                diag["entry_gap"] += 1
        if enter:
            pos = (fill, tgt, hm)
            continue
    if pos:
        return {"entry": pos[0], "entry_hm": pos[2], "exit": float(bars[-1][2]),
                "reason": "日终"}, None, diag
    post_touch = any(float(b[4]) <= line for b in bars[start_i:])
    if pre_touch and post_touch:
        return None, "窗口前+盘后均触", diag
    if pre_touch:
        return None, "仅窗口前触线", diag
    if post_touch:
        return None, "仅盘后触线未成交", diag
    return None, "未触线", diag


def run_variant(days_by_sym, variant):
    rounds, flats = [], {}
    diag_all = {"touch_bars": 0, "gap_touch_bars": 0, "entry_n": 0, "entry_gap": 0}
    for sym in sorted(days_by_sym):
        for d in sorted(days_by_sym[sym]):
            r, fr, diag = replay(days_by_sym[sym][d], variant)
            for k in diag_all:
                diag_all[k] += diag.get(k, 0)
            if r:
                r["sym"], r["date"] = sym, d
                r["pnl"] = (r["exit"] / r["entry"] - 1.0 - COST) * 100.0
                rounds.append(r)
            else:
                flats[(sym, d)] = fr
    return rounds, flats, diag_all


# ---------- 汇总 ----------
def summarize(rounds):
    if not rounds:
        return {"n": 0}
    pn = [r["pnl"] for r in rounds]
    day_sum = defaultdict(float)
    for r in rounds:
        day_sum[r["date"]] += r["pnl"]
    return {"n": len(rounds),
            "win": sum(1 for x in pn if x > 0) / len(pn),
            "mean": sum(pn) / len(pn),
            "total": sum(pn),
            "worst": min(pn),
            "pos_days": sum(1 for v in day_sum.values() if v > 0) / len(day_sum),
            "active_days": len(day_sum)}


def entry_dist(rounds):
    cnt = defaultdict(int)
    for r in rounds:
        for label, fn in ENTRY_BUCKETS:
            if fn(r["entry_hm"]):
                cnt[label] += 1
                break
    return {label: cnt.get(label, 0) for label, _ in ENTRY_BUCKETS}


def flat_dist(flats, days_by_sym, syms, phase_of):
    """stock-day层面空仓原因分布; 分母=该样本集在相位内的全部有效stock-day。"""
    cnt = defaultdict(int)
    denom = 0
    for sym in syms:
        for d in days_by_sym[sym]:
            if phase_of(d) is None:
                continue
            denom += 1
            fr = flats.get((sym, d))
            cnt[fr if fr else "入场"] += 1
    return denom, {k: cnt.get(k, 0) for k in FLAT_KEYS + ["入场"]}


def decompose(rounds_a, rounds_b):
    """a相对b的逐stock-day配对分解: both/only_a/only_b。"""
    ma = {(r["sym"], r["date"]): r for r in rounds_a}
    mb = {(r["sym"], r["date"]): r for r in rounds_b}
    both = [k for k in ma if k in mb]
    only_a = [k for k in ma if k not in mb]
    only_b = [k for k in mb if k not in ma]
    d_both = [ma[k]["pnl"] - mb[k]["pnl"] for k in both]
    same_bar = [k for k in both if ma[k]["entry_hm"] == mb[k]["entry_hm"]]
    d_same = [ma[k]["pnl"] - mb[k]["pnl"] for k in same_bar]
    out = {"both_n": len(both),
           "both_mean_a": (sum(ma[k]["pnl"] for k in both) / len(both)) if both else 0.0,
           "both_mean_b": (sum(mb[k]["pnl"] for k in both) / len(both)) if both else 0.0,
           "both_diff_mean": (sum(d_both) / len(d_both)) if d_both else 0.0,
           "both_diff_sum": sum(d_both),
           "both_diff_win": (sum(1 for x in d_both if x > 0) / len(d_both)) if d_both else 0.0,
           "same_bar_n": len(same_bar),
           "same_bar_diff_mean": (sum(d_same) / len(d_same)) if d_same else 0.0,
           "same_bar_diff_sum": sum(d_same),
           "only_a_n": len(only_a),
           "only_a_mean": (sum(ma[k]["pnl"] for k in only_a) / len(only_a)) if only_a else 0.0,
           "only_a_sum": sum(ma[k]["pnl"] for k in only_a),
           "only_b_n": len(only_b),
           "only_b_mean": (sum(mb[k]["pnl"] for k in only_b) / len(only_b)) if only_b else 0.0,
           "only_b_sum": sum(mb[k]["pnl"] for k in only_b)}
    out["total_diff"] = out["both_diff_sum"] + out["only_a_sum"] - out["only_b_sum"]
    return out


def paired_daily_t(rounds_a, rounds_b):
    """OOS日块配对差(描述性, 未做多重比较校正): 按日加总(池)/单值(单票)差 → t。"""
    da, db = defaultdict(float), defaultdict(float)
    for r in rounds_a:
        da[r["date"]] += r["pnl"]
    for r in rounds_b:
        db[r["date"]] += r["pnl"]
    keys = sorted(set(da) | set(db))
    if len(keys) < 5:
        return None
    diffs = [da.get(k, 0.0) - db.get(k, 0.0) for k in keys]
    n = len(diffs)
    mu = sum(diffs) / n
    var = sum((x - mu) ** 2 for x in diffs) / (n - 1) if n > 1 else 0.0
    se = var ** 0.5 / n ** 0.5
    return {"n_days": n, "mean": mu, "t": (mu / se) if se else None}


def monthly(rounds_by_var, dates_all, cut):
    out = {}
    bymv = {v: defaultdict(list) for v in VARIANTS}
    for v in VARIANTS:
        for r in rounds_by_var[v]:
            bymv[v][r["date"][:6]].append(r["pnl"])
    for m in sorted(bymv[VARIANTS[0]]):
        row = {}
        for v in VARIANTS:
            pn = bymv[v].get(m, [])
            row[v] = {"n": len(pn), "total": sum(pn)}
        ds = [d for d in dates_all if d[:6] == m]
        row["phase"] = ("IS" if ds and max(ds) <= cut else
                        "OOS" if ds and min(ds) > cut else "跨IS/OOS")
        out[m] = row
    return out


# ---------- 打印 ----------
def fmt_sum(s):
    if s.get("n", 0) == 0:
        return "回合   0   —"
    return ("回合%4d 胜率%2.0f%% 笔均%+.3f%% 合计%+7.2f%% 最差%+.2f%% 日正比例%2.0f%%(活跃%d日)"
            % (s["n"], s["win"] * 100, s["mean"], s["total"], s["worst"],
               s["pos_days"] * 100, s["active_days"]))


def print_block(title, rounds_by_var, flats_by_var, days_by_sym, syms, phase_of):
    print("\n-- %s --" % title)
    for v in VARIANTS:
        print("  %-28s %s" % (VDESC[v], fmt_sum(summarize(rounds_by_var[v]))))
    for v in VARIANTS:
        ed = entry_dist(rounds_by_var[v])
        tot = sum(ed.values())
        dist = " ".join("%s:%d(%2.0f%%)" % (k, c, c * 100.0 / tot) if tot else "%s:0" % k
                        for k, c in ed.items() if c)
        print("    入场时间 %s | %s" % (VDESC[v].split()[0], dist or "无入场"))
    for v in VARIANTS:
        denom, fd = flat_dist(flats_by_var[v], days_by_sym, syms, phase_of)
        parts = " ".join("%s=%d(%2.0f%%)" % (k, fd[k], fd[k] * 100.0 / denom)
                         for k in FLAT_KEYS)
        print("    空仓结构 %s | 分母%d个stock-day: %s"
              % (VDESC[v].split()[0], denom, parts))


def main():
    days_by_sym, names = load_days()
    n_days = {s: len(days_by_sym[s]) for s in days_by_sym}
    pool_syms = sorted(s for s in days_by_sym if n_days[s] >= POOL_MIN_DAYS)
    excl = sorted(s for s in days_by_sym if n_days[s] < POOL_MIN_DAYS)
    print("样本: %d只票, 窗口 %s~%s (END_DATE=%s复用bt_hypo)"
          % (len(days_by_sym), START_DATE, END_DATE, END_DATE))
    for s in sorted(days_by_sym):
        print("    %-10s %-8s 有效日%3d %s" % (s, names[s], n_days[s],
              "" if s in pool_syms else "(<%d天, 不进池)" % POOL_MIN_DAYS))
    print("池化样本: %d只 (>=40有效日)%s" % (len(pool_syms),
          ("; 剔除: " + ",".join("%s(%d天)" % (s, n_days[s]) for s in excl)) if excl else ""))

    cal = sorted({d for s in pool_syms for d in days_by_sym[s]})
    cut = cal[IS_DAYS - 1] if len(cal) >= IS_DAYS else cal[-1]
    print("交易日历: %d天, IS前%d天(至%s), OOS %d天(%s~)"
          % (len(cal), IS_DAYS, cut, len(cal) - IS_DAYS - 0,
             cal[IS_DAYS] if len(cal) > IS_DAYS else "-"))

    def phase_of(d):
        if d <= cut:
            return "IS"
        return "OOS"

    sets = [("池化%d只" % len(pool_syms), pool_syms), ("石药sz300765单票", ["sz300765"])]
    results = {"params": {"D": D, "STOP_PCT": STOP_PCT, "COST": COST, "IS_DAYS": IS_DAYS,
                          "START": START_DATE, "END": END_DATE, "cut": cut,
                          "pool_syms": pool_syms, "excluded": excl},
               "sets": {}}
    for set_name, syms in sets:
        sub = {s: days_by_sym[s] for s in syms}
        rounds_by_var, flats_by_var, diag_by_var = {}, {}, {}
        for v in VARIANTS:
            rv, fv, dv = run_variant(sub, v)
            rounds_by_var[v] = [r for r in rv]
            flats_by_var[v] = fv
            diag_by_var[v] = dv
        print("\n" + "=" * 76)
        print("===== %s =====" % set_name)
        for ph in ("IS", "OOS"):
            sel = (lambda d: phase_of(d) == ph)
            rv = {v: [r for r in rounds_by_var[v] if sel(r["date"])] for v in VARIANTS}
            fv = {v: {k: fr for k, fr in flats_by_var[v].items() if sel(k[1])}
                  for v in VARIANTS}
            print_block(ph, rv, fv, sub, syms, sel)
            results["sets"].setdefault(set_name, {})[ph] = {
                v: summarize(rv[v]) for v in VARIANTS}
        # 差值与分解（IS/OOS各做）
        dec_all = {}
        for ph in ("IS", "OOS"):
            sel = (lambda d: phase_of(d) == ph)
            rv = {v: [r for r in rounds_by_var[v] if sel(r["date"])] for v in VARIANTS}
            print("  [%s 相对V0差值] 笔均差判断阈值: 滑点增量%.2f%%/回合, 全成本%.2f%%/回合"
                  % (ph, SLIP * 100, COST * 100))
            for v in ("V1_win0935", "V2_barlow"):
                dec = decompose(rv[v], rv["V0_poll"])
                dec["t"] = paired_daily_t(rv[v], rv["V0_poll"])
                dec_all["%s|%s" % (ph, v)] = dec
                print("    %s vs V0: 总差%+7.2f%% | 共同成交stock-day %d天 "
                      "(笔均V0 %+.3f%% → %s %+.3f%%, 差%+.3f%%, %s占优仅%.0f%%; "
                      "同bar成交%d天 差%+.3f%%/天, 更早成交%d天 差%+.3f%%/天) | "
                      "%s独有%d天 均值%+.3f%% 合计%+.2f%% | V0独有%d天 | 日块t=%.2f(n=%d)"
                      % (v.split("_")[0], dec["total_diff"], dec["both_n"],
                         dec["both_mean_b"], v.split("_")[0], dec["both_mean_a"],
                         dec["both_diff_mean"], v.split("_")[0],
                         dec["both_diff_win"] * 100,
                         dec["same_bar_n"], dec["same_bar_diff_mean"],
                         dec["both_n"] - dec["same_bar_n"],
                         ((dec["both_diff_sum"] - dec["same_bar_diff_sum"])
                          / (dec["both_n"] - dec["same_bar_n"]))
                         if dec["both_n"] > dec["same_bar_n"] else 0.0,
                         v.split("_")[0],
                         dec["only_a_n"], dec["only_a_mean"], dec["only_a_sum"],
                         dec["only_b_n"],
                         dec["t"]["t"] if dec["t"] else 0.0,
                         dec["t"]["n_days"] if dec["t"] else 0))
            # V2诊断(本相位): 入场bar中开盘已低于线(真实限价单会以更优开盘价成交) + V2B敏感性
            sub_ph = {s: {d: bs for d, bs in sub[s].items() if sel(d)} for s in sub}
            dv2 = run_variant(sub_ph, "V2_barlow")[2]
            rvb = [r for r in run_variant(sub, "V2B_gapfill")[0] if sel(r["date"])]
            sb = summarize(rvb)
            print("    V2诊断: 本相位入场%d笔, 其中入场bar开盘<线 %d笔"
                  "(真实限价单会以开盘价成交, 预注册'成交价=线'口径对这些笔偏保守)"
                  % (dv2["entry_n"], dv2["entry_gap"]))
            decb = decompose(rvb, rv["V0_poll"])
            print("    V2B敏感性(开盘<线按开盘成交): %s | vs V0总差%+.2f%% "
                  "(共同%d天 笔均差%+.3f%%; V2B独有%d天 均值%+.3f%% 合计%+.2f%%)"
                  % (fmt_sum(sb), decb["total_diff"], decb["both_n"],
                     decb["both_diff_mean"], decb["only_a_n"],
                     decb["only_a_mean"], decb["only_a_sum"]))
            dec_all["%s|V2B_gapfill" % ph] = decb
        results["sets"][set_name]["decomp"] = dec_all

    # 分月结构（全窗口, regime依赖检查）
    print("\n" + "=" * 76)
    print("===== 分月合计%%（池化%d只, regime依赖检查; IS截止%s）=====" % (len(pool_syms), cut))
    all_rounds = {v: run_variant({s: days_by_sym[s] for s in pool_syms}, v)[0]
                  for v in VARIANTS}
    mon = monthly(all_rounds, cal, cut)
    print("  月份    相位     " + " ".join("%-16s" % VDESC[v].split()[0] for v in VARIANTS)
          + "   V1-V0    V2-V0")
    for m in sorted(mon):
        row = mon[m]
        line = "  %s %-8s" % (m, row["phase"])
        for v in VARIANTS:
            line += " %-16s" % ("n=%d %+7.2f%%" % (row[v]["n"], row[v]["total"]))
        line += " %+8.2f %+8.2f" % (row["V1_win0935"]["total"] - row["V0_poll"]["total"],
                                    row["V2_barlow"]["total"] - row["V0_poll"]["total"])
        print(line)
    results["monthly_pool"] = {m: {k: (vv if not isinstance(vv, dict) else
                                       {kk: round(x, 6) for kk, x in vv.items()})
                                   for k, vv in row.items()} for m, row in mon.items()}

    # 逐票OOS宽度（池化+不进池票）
    print("\n===== 逐票OOS笔均%%（n回合 V0/V1/V2）=====")
    per_sym = {}
    for s in sorted(days_by_sym):
        rows = {}
        for v in VARIANTS:
            rr = [r for r in run_variant({s: days_by_sym[s]}, v)[0] if r["date"] > cut]
            rows[v] = {"n": len(rr), "mean": (sum(x["pnl"] for x in rr) / len(rr))
                       if rr else 0.0}
        d1 = rows["V1_win0935"]["mean"] - rows["V0_poll"]["mean"]
        d2 = rows["V2_barlow"]["mean"] - rows["V0_poll"]["mean"]
        tag = "" if s in pool_syms else "(不进池)"
        print("  %-10s %-8s%s V0 n=%3d %+7.2f%% | V1 %+7.2f%% (%+6.2f) | V2 %+7.2f%% (%+6.2f)"
              % (s, names[s], tag, rows["V0_poll"]["n"], rows["V0_poll"]["mean"],
                 rows["V1_win0935"]["mean"], d1, rows["V2_barlow"]["mean"], d2))
        per_sym[s] = {"rows": rows, "d1": d1, "d2": d2}
    results["per_sym_oos"] = per_sym

    with open(os.path.join(HERE, "bt_c5_entrytest_result.json"), "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=float)
    print("\n结果已存 bt_c5_entrytest_result.json")
    print("诚实性提示: 同窗口三变体×2相位×2样本集=12组比较, 多重比较未校正;")
    print("V2为限价单假设(触线即以线成交), 实盘60秒轮询无法完全复现;")
    print("结论仅作冻结层评审证据, 不构成采纳建议。")


if __name__ == "__main__":
    main()
