#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BacktestRunner：固定执行逻辑（方案文档第 3 章）。

run() 输入: 规格(canonical json) + 数据快照 + 窗口 + 执行配置。
输出 RunArtifacts: trades/daily/metrics 落盘为带 sha256 的产物文件，
指标一律来自 qengine.metrics 实际计算，本模块不生成任何收益数字。
"""
from __future__ import print_function
import os, sys, json, hashlib, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import quant_agent
sys.path.insert(0, quant_agent.QSYS)

from qengine import run_r443, metrics          # noqa: E402
from qdata import load_panel, build_all        # noqa: E402
from quant_agent.strategies.adapter import normalize, materialize  # noqa


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


class Context(object):
    """数据面板 + 特征 + 窗口定义。构造即冻结（同批实验共享一个 Context）。

    use_universe=True: PIT 宇宙模式（批次二）——面板为候选超集并集，
    板块聚合特征(flr5/breadth)按 22 只定型池冻结计算，宇宙掩码供
    adapter 在 score 层收窄可交易候选。
    窗口来自 constitution["windows"] 的日期边界；SEALED 只供独立评估。
    """

    def __init__(self, refresh=False, warmup=80, constitution=None,
                 use_universe=None):
        self.warmup = warmup
        self.const = constitution or _default_constitution()
        if use_universe is None:
            use_universe = "windows" in self.const
        self.use_universe = use_universe
        if use_universe:
            from quant_agent.data import universe as U
            names = {}
            cf = os.path.join(quant_agent.VAR, "universe_candidates.json")
            if os.path.exists(cf):
                names = json.load(open(cf))
            self.stocks, self.codes, self.common, self.susp = \
                U.build_panel(names)
            import quant_iter26 as q26
            import quant_iter44 as q44
            q44.R316_D = -0.08
            q44.R316_RSI = 58.0
            q44.R316_FLR = 0.3
            q44.R443_UV = 0.8
            self.feat, state0 = q26.build_features(self.stocks, self.codes,
                                                   self.common)
            board = [c for c in U.BOARD_CODES if c in self.stocks]
            # 两次调用:
            #  1) 定型池子集 → 板块聚合特征(flr5/breadth, 冻结语义)
            #  2) 全并集 → 每只股票的批次AP特征(dist20h/upvar20 等)
            # 注: score 的 z 值在传入 codes 横截面上标准化，
            #     宇宙变宽会改变 z 分布——这是横截面策略的本意语义。
            import copy
            self.state = q44.add_batchAP_features(
                {c: self.stocks[c] for c in board}, board, self.common,
                {c: self.feat[c] for c in board}, copy.deepcopy(state0))
            q44.add_batchAP_features(self.stocks, self.codes, self.common,
                                     self.feat, state0)
            matrix, counts, udates, ucodes, umeta = U.load_universe()
            self.universe = (matrix, ucodes)
            self.universe_meta = umeta
        else:
            self.stocks, self.codes, self.common, self.susp = \
                load_panel(refresh=refresh)
            self.feat, self.state = build_all(self.stocks, self.codes,
                                              self.common)
            self.universe = None
            self.universe_meta = None
        self.n = len(self.common)
        self.windows = self._windows_from_const()

    def _windows_from_const(self):
        spec = self.const.get("windows")
        w = {}
        if not spec:
            split = self.warmup + int((self.n - 1 - self.warmup) * 0.6)
            w = {"IS": (self.warmup, split - 6, split - 1),
                 "OOS": (split, self.n - 6, self.n - 1)}
            self.split = split
            return w
        for label, b in spec.items():
            start = b.get("start")
            end = b.get("end")
            si = 0
            if start:
                si = next((i for i, d in enumerate(self.common)
                           if d >= start), self.n)
            if end:
                ei = next((i for i in range(self.n - 1, -1, -1)
                           if self.common[i] <= end), -1)
            else:
                ei = self.n - 1
            si = max(si, self.warmup)
            if label == "SEALED":
                w[label] = (si, self.n - 6, self.n - 1)
            else:
                w[label] = (si, max(si, ei - 6), ei)
        return w

    def window_dates(self, label):
        s, _e, w = self.windows[label]
        return self.common[s], self.common[w]

    def dev_labels(self):
        return [k for k in self.windows if k != "SEALED"]


def _default_constitution():
    import json as _json
    with open(quant_agent.CONFIG) as f:
        return _json.load(f)


class RunArtifacts(object):
    def __init__(self, exp_id, artifact_dir):
        self.exp_id = exp_id
        self.dir = artifact_dir
        if not os.path.isdir(artifact_dir):
            os.makedirs(artifact_dir)
        self.hashes = {}

    def write(self, kind, obj):
        path = os.path.join(self.dir, kind + ".json")
        with open(path, "w") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        self.hashes[kind] = _sha256_file(path)
        return path


def run_window(ctx, spec, kw, e0, e1, w2, er, intent_rank=None):
    """统一执行入口：引擎产出意图订单，现金口径时经 execute_cash 执行。

    er 含 "cash_account" 键 → 现金账户执行（评审 P0：不产生未建模融资）；
    否则权重口径（历史批次可复现）。

    intent_rank（批次八 E2/E3）: (code, e) -> 质量评分，传给执行器
    做当日意图降序（默认 None=基线语义不变）。

    封存硬约束（constitution_004）: 窗口末端日期 ≥ 封存起点时拒跑，
    除非 er["_allow_sealed"]=True（仅 sealed_eval 独立评估显式放行）。
    """
    import qengine
    sealed = (ctx.const.get("sealed_holdout") or {}).get("start")
    authorized = bool(er.get("_allow_sealed"))
    if sealed and not authorized:
        if w2 < len(ctx.common) and ctx.common[w2] >= sealed:
            raise RuntimeError(
                "run_window 拒绝越界: 窗口末端 %s 触及封存区间(起点 %s)；"
                "研究流程不得计算封存数据" % (ctx.common[w2], sealed))
    run = qengine.run_r443(
        ctx.stocks, ctx.codes, ctx.feat, ctx.state, ctx.common, ctx.susp,
        e0, e1, w2, spec,
        cost_buy=er["cost_buy"], cost_sell=er["cost_sell"],
        exposure_cap=kw.get("exposure_cap"))
    run["sealed_eval_authorized"] = authorized
    if er.get("cash_account"):
        ca = er["cash_account"]
        from quant_agent.backtest.executor import execute_cash
        ex = execute_cash(run["trades"], ctx.stocks, ctx.common, ctx.susp,
                          er["cost_buy"], er["cost_sell"],
                          cap=ca.get("max_exposure", 1.0),
                          cash0=ca.get("initial_cash_cny", 1_000_000.0),
                          lot=ca.get("lot_size", 100),
                          min_fee_buy=er.get("min_fee_buy", 5.0),
                          min_fee_sell=er.get("min_fee_sell", 5.0),
                          check_limit_at_fill=er.get(
                              "limit_check_at_fill", True),
                          end_day=w2, start_day=e0,
                          per_stock_cap=ca.get("per_stock_cap"),
                          intent_rank=intent_rank)
        ex["engine_avg_exposure"] = run["avg_exposure"]
        ex["sig_diag"] = run.get("sig_diag", {})
        ex["candidates"] = run.get("candidates", [])
        ex["sealed_eval_authorized"] = authorized
        return ex
    return run


class BacktestRunner(object):
    """确定性回测器：给定相同输入必然产生相同产物。"""

    def __init__(self, ctx, constitution):
        self.ctx = ctx
        self.const = constitution
        self.er = constitution["execution_rules"]

    def run(self, canonical_spec, artifact_dir, costs=None,
            windows=None, cost_override=None):
        """canonical_spec: 物化后的纯 JSON 规格。

        cost_override 仅供压力测试使用（确定性验证工具），
        正式实验一律用 constitution 冻结成本。
        返回 {window: {"metrics":..., "artifact_hash":...}}
        """
        canonical_spec = dict(canonical_spec or {})
        allowed = set(self.const["research_policy"]["allowed_patch_fields"])
        patch_in = {k: v for k, v in canonical_spec.items() if k in allowed}
        norm = normalize(patch_in, self.const)
        spec, kw, _canon = materialize(norm, self.ctx.stocks, self.ctx.codes,
                                       self.ctx.common,
                                       universe=getattr(self.ctx,
                                                        "universe", None))
        art = RunArtifacts(canonical_spec.get("_exp_id", "adhoc"),
                           artifact_dir)
        cb, cs = cost_override or (self.er["cost_buy"], self.er["cost_sell"])
        if windows is None:
            windows = self.ctx.dev_labels()
        out = {}
        er_full = dict(self.er)
        if cost_override:
            er_full["cost_buy"], er_full["cost_sell"] = cost_override
        for label in windows:
            s, ee, we = self.ctx.windows[label]
            run = run_window(self.ctx, spec, kw, s, ee, we, er_full)
            m = metrics(run, self.ctx.common)
            if m is None:
                raise RuntimeError("empty run on window %s" % label)
            curve = m.pop("equity_curve")
            d0, d1 = self.ctx.window_dates(label)
            meta = {"window": label, "window_start": d0, "window_end": d1,
                    "costs": {"buy": cb, "sell": cs},
                    "generated": datetime.datetime.now().isoformat(
                        timespec="seconds"),
                    "spec": canonical_spec}
            art.write("trades_%s" % label, run["trades"])
            art.write("equity_%s" % label,
                      {"dates": self.ctx.common[s:we + 1], "curve": curve})
            art.write("metrics_%s" % label, {"meta": meta, "metrics": m})
            out[label] = {"metrics": m,
                          "metrics_artifact": "metrics_%s" % label,
                          "n_trades": m.get("n_trades")}
        art.write("spec_canonical", canonical_spec)
        out["_artifact_hashes"] = art.hashes
        out["_artifact_dir"] = artifact_dir
        return out
