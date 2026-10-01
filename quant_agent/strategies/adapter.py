#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""StrategyAdapter：声明式规格 + 白名单函数（方案文档第 3 章接口落地）。

Agent 只能提交纯 JSON 规格（allowed_patch），不允许提交可执行代码、
模块路径或函数字符串以外的引用。规格经严格 schema 校验后由本模块
物化为可执行的 (spec, engine_kwargs)。

R443 基线映射:
  scores()        -> q44.score_r117_upvar   （上行波动占比评分）
  target_weights()-> K 只等权, weight_mode=equal
  exit_signals()  -> Keltner(1.5×ATR20) 或 max_hold 到时，引擎负责真实成交修正
"""
from __future__ import print_function
import os, sys, json, math, hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import quant_agent
sys.path.insert(0, quant_agent.QSYS)

from qdata import r443_spec  # noqa: E402


class SpecError(ValueError):
    """规格不合法：schema 拒绝（拒绝额外字段/非有限值/越界/路径注入）。"""


# ---------- 严格 schema 校验（constitution research_policy.allowed_patch_fields） ----------

def _is_finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and \
        math.isfinite(x)


def validate_patch(patch, allowed):
    if not isinstance(patch, dict):
        raise SpecError("patch must be object")
    for k in patch:
        if k not in allowed:
            raise SpecError("field not allowed: %r" % k)
    out = {}
    for k, rule in allowed.items():
        if k not in patch:
            continue
        v = patch[k]
        t = rule["type"]
        if t == "int":
            if isinstance(v, bool) or not isinstance(v, int):
                raise SpecError("%s must be int" % k)
            if not (rule["min"] <= v <= rule["max"]):
                raise SpecError("%s=%r out of range" % (k, v))
        elif t == "float":
            if not _is_finite(v):
                raise SpecError("%s must be finite number" % k)
            if not (rule["min"] <= v <= rule["max"]):
                raise SpecError("%s=%r out of range" % (k, v))
        elif t == "float_or_null":
            if v is not None:
                if not _is_finite(v):
                    raise SpecError("%s must be finite or null" % k)
                if not (rule["min"] <= v <= rule["max"]):
                    raise SpecError("%s=%r out of range" % (k, v))
        elif t == "bool":
            if not isinstance(v, bool):
                raise SpecError("%s must be bool" % k)
        elif t == "enum":
            if v not in rule["values"]:
                raise SpecError("%s=%r not in %r" % (k, v, rule["values"]))
        elif t == "object":
            if not isinstance(v, dict):
                raise SpecError("%s must be object" % k)
            for fk in v:
                if fk not in rule["fields"]:
                    raise SpecError("nested field not allowed: %r.%s" % (k, fk))
            sub = {}
            for fk, fr in rule["fields"].items():
                if fk in v:
                    _check_scalar(k + "." + fk, v[fk], fr)
                elif "default" in fr:
                    sub[fk] = fr["default"]
            sub.update({fk: v[fk] for fk in v})
            out[k] = sub
            continue
        else:
            raise SpecError("unknown rule type %r" % t)
        out[k] = v
    return out


def _check_scalar(name, v, rule):
    t = rule["type"]
    if t == "int":
        if isinstance(v, bool) or not isinstance(v, int) or \
                not (rule["min"] <= v <= rule["max"]):
            raise SpecError("%s bad int value %r" % (name, v))
    elif t == "float":
        if not _is_finite(v) or not (rule["min"] <= v <= rule["max"]):
            raise SpecError("%s bad float value %r" % (name, v))
    else:
        raise SpecError("%s unknown nested rule %r" % (name, t))


def _no_injection(patch):
    """路径注入防护：任何字符串值不得含路径分隔/上级引用/盘符。"""
    def bad(s):
        return ("/" in s) or ("\\" in s) or (".." in s) or (":" in s)
    for k, v in patch.items():
        if isinstance(v, str) and bad(v):
            raise SpecError("suspicious string in %s" % k)
        if isinstance(v, dict):
            for fk, fv in v.items():
                if isinstance(fv, str) and bad(fv):
                    raise SpecError("suspicious string in %s.%s" % (k, fk))


# ---------- 白名单函数注册表 ----------

_REGISTRY = None


def _registry():
    global _REGISTRY
    if _REGISTRY is None:
        import quant_iter44 as q44
        import qanalyze
        _REGISTRY = {
            "score": {"score_r117_upvar": q44.score_r117_upvar},
            "filt": {"R316_FLR": lambda: q44.make_filt_flr("R316_FLR"),
                     # E5 预注册消融开关（constitution_008 E5）: 关闭日闸，
                     # 仅用于候选数据集逐层过滤证据定位后的单条件消融。
                     "R316_FLR_off": lambda: (lambda state, t: True)},
            "_regime_labels": qanalyze.regime_labels,
        }
    return _REGISTRY


# ---------- 物化 ----------

BASE_PATCH = {
    "K": 3,
    "params": {"keltner_mult": 1.5, "max_hold": 10},
    "weight_mode": "equal",
    "allow_fewer": True,
    "regime_gate": False,
    "exposure_cap": None,
    "filt": "R316_FLR",
    "filt_regime_off": None,
}


def normalize(patch, constitution):
    """patch(可部分) → 完整合法规格。不合法抛 SpecError。

    深合并（第五轮核验修正）: 嵌套 dict（如 params）按键填充默认值，
    而非浅覆盖——此前 patch={'params': {'max_hold': 8}} 会丢掉
    keltner_mult 默认值导致 materialize KeyError。
    """
    allowed = constitution["research_policy"]["allowed_patch_fields"]
    p = dict(BASE_PATCH)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(p.get(k), dict):
            merged = dict(p[k])
            merged.update(v)
            p[k] = merged
        else:
            p[k] = v
    _no_injection(p)
    return validate_patch(p, allowed)


def materialize(norm_patch, stocks, codes, common, universe=None):
    """纯 JSON 规格 → (engine_spec, engine_kwargs, canonical_json)。

    所有函数引用只来自白名单注册表；regime_gate 是文档批准的
    唯一 filt 修饰符，其牛市集合由 regime_labels 确定性推导。
    universe=(matrix, ucodes): PIT 宇宙掩码（批次二）——在 score 层
    将非当日宇宙成员的评分置为 -1e8（引擎据此排除），无宇宙信息的
    证券或日期一律保守排除。Agent 不可控制此参数。
    """
    reg = _registry()
    approved = {"score", "filt"}
    spec = r443_spec()
    spec["score"] = reg["score"]["score_r117_upvar"]
    spec["filt"] = reg["filt"][norm_patch.get("filt", "R316_FLR")]()
    spec["K"] = norm_patch["K"]
    spec["filt_regime_off"] = norm_patch.get("filt_regime_off")
    spec["params"] = {"keltner_mult": norm_patch["params"]["keltner_mult"],
                      "max_hold": norm_patch["params"]["max_hold"]}
    spec["weight_mode"] = norm_patch["weight_mode"]
    spec["allow_fewer"] = norm_patch["allow_fewer"]
    # 实验开关透传（constitution_007 预注册；缺省 False = 基线语义）
    if norm_patch.get("exit_keltner"):
        spec["exit_keltner"] = True
    if norm_patch.get("block_reentry"):
        spec["block_reentry"] = True
    kw = {}
    if norm_patch.get("exposure_cap") is not None:
        kw["exposure_cap"] = norm_patch["exposure_cap"]
    if norm_patch.get("regime_gate"):
        labels = reg["_regime_labels"](stocks, codes, common)
        bull = set(t for t, (tr, _vo) in enumerate(labels) if tr == "上涨")
        orig = spec["filt"]

        def filt(state, t, _bull=bull, _orig=orig):
            return _orig(state, t) and t in _bull
        spec["filt"] = filt
    universe_tag = None
    if universe is not None:
        matrix, ucodes = universe
        ucol = {c: j for j, c in enumerate(ucodes)}
        col_of = tuple(ucol.get(c) for c in codes)
        orig_score = spec["score"]

        def score_uni(feat, codes_, t, _o=orig_score, _m=matrix,
                      _col=col_of):
            s = _o(feat, codes_, t)
            if t >= _m.shape[0]:
                return {c: -1e8 for c in codes_}
            row = _m[t]
            out = {}
            for c, j in zip(codes_, _col):
                v = s.get(c, -1e8)
                out[c] = v if (j is not None and row[j]) else -1e8
            return out
        spec["score"] = score_uni
        from quant_agent.data.universe import load_rule
        rule_id = load_rule()["rule_id"]
        reg_fp = os.path.join(quant_agent.VAR, "universe_registry.json")
        universe_tag = rule_id
        if os.path.exists(reg_fp):
            reg = json.load(open(reg_fp))
            for key, ent in reg.items():
                if ent.get("rule_id") == rule_id:
                    universe_tag = "%s@%s" % (key, ent["snapshot_id"][:8])
                    break
    canonical = {
        "score": "score_r117_upvar",
        "filt": norm_patch.get("filt", "R316_FLR") +
                ("+regime_gate" if norm_patch["regime_gate"] else ""),
        "K": norm_patch["K"],
        "params": norm_patch["params"],
        "weight_mode": norm_patch["weight_mode"],
        "allow_fewer": norm_patch["allow_fewer"],
        "regime_gate": norm_patch["regime_gate"],
        "exposure_cap": norm_patch.get("exposure_cap"),
        "universe": universe_tag,
        "execution": ("cash" if json.load(open(quant_agent.CONFIG))
                      ["execution_rules"].get("cash_account") else "weight"),
        "_approved_functions": sorted(approved),
    }
    return spec, kw, canonical


def spec_hash(canonical):
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def code_hash():
    """引擎与策略代码的内容哈希（可复现性：代码变化即新基线）。"""
    h = hashlib.sha256()
    base = quant_agent.QSYS
    root = quant_agent.ROOT
    for fp in (os.path.join(base, "qengine.py"),
               os.path.join(base, "qdata.py"),
               os.path.join(root, "quant_iter26.py"),
               os.path.join(root, "quant_iter44.py")):
        h.update(os.path.basename(fp).encode())
        with open(fp, "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:16]
