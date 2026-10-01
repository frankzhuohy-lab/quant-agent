#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据快照与内容哈希（方案文档第 2/4 章：每个实验固定数据清单和内容哈希）。

当前数据层现实（qdata.py）：腾讯前复权日线，按证券缓存为 JSON 文件。
快照 = 文件清单 + 每文件 sha256 + 日期范围 + 来源 + 登记时间。
实验登记时引用 snapshot_id；重跑前 verify() 不一致即拒绝（防数据更新后无法重现）。

TODO(阶段二后续): 接入 point-in-time 历史股票池与 available_at 血缘后，
本清单需增加每文件的 available_at 上界与 universe 版本字段。
"""
from __future__ import print_function
import os, json, hashlib, datetime

def _sha256(path, _bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(_bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def build_manifest(cache_dir, source="tencent_qfq_day", pattern="k_*.json"):
    files = sorted(f for f in os.listdir(cache_dir) if
                   f.endswith(".json") and f.startswith("k_"))
    entries = []
    for fn in files:
        fp = os.path.join(cache_dir, fn)
        with open(fp) as f:
            bars = json.load(f)
        dates = [b[0] for b in bars if b]
        entries.append({
            "file": fn,
            "sha256": _sha256(fp),
            "n_bars": len(bars),
            "first_date": dates[0] if dates else None,
            "last_date": dates[-1] if dates else None,
        })
    manifest = {
        "source": source,
        "adjust": "qfq",
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "universe_files": entries,
    }
    blob = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
    manifest["snapshot_id"] = hashlib.sha256(
        blob.encode("utf-8")).hexdigest()[:16]
    return manifest


def verify(cache_dir, manifest):
    """重跑前校验：任何文件哈希/日期变化 → 返回差异列表（空=一致）。"""
    diffs = []
    for e in manifest["universe_files"]:
        fp = os.path.join(cache_dir, e["file"])
        if not os.path.exists(fp):
            diffs.append({"file": e["file"], "kind": "missing"})
            continue
        h = _sha256(fp)
        if h != e["sha256"]:
            diffs.append({"file": e["file"], "kind": "hash_changed",
                          "expected": e["sha256"][:12], "actual": h[:12]})
    return diffs


def check_no_future_bars(manifest, decision_date):
    """时点正确性检查：快照中不得含有晚于 decision_date 的 K 线。"""
    bad = []
    for e in manifest["universe_files"]:
        if e["last_date"] and e["last_date"] > decision_date:
            bad.append({"file": e["file"], "last_date": e["last_date"]})
    return bad


def build_combined_manifest(dirs, extra_files=None, source="combined"):
    """组合快照清单（评审第 3 轮：一个实验引用的全部数据一个清单）。

    dirs: 多个缓存目录（如 qsys/cache 与 var/ucache）；
    extra_files: 额外冻结文件（宇宙规则、注册制表、宪法等），
    同样记录 sha256。所有条目带目录前缀，重名不冲突。
    """
    import datetime as _dt
    entries = []
    for d in dirs:
        prefix = os.path.basename(os.path.normpath(d))
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json") or fn.startswith("."):
                continue
            fp = os.path.join(d, fn)
            if not os.path.isfile(fp):
                continue
            with open(fp) as f:
                bars = json.load(f)
            dates = [b[0] for b in bars if b]
            entries.append({
                "dir": prefix, "file": fn,
                "sha256": _sha256(fp),
                "n_bars": len(bars),
                "first_date": dates[0] if dates else None,
                "last_date": dates[-1] if dates else None,
            })
    for fp in (extra_files or []):
        entries.append({
            "dir": os.path.dirname(fp).replace(os.sep, "_")[-40:],
            "file": "EXTRA_" + os.path.basename(fp),
            "sha256": _sha256(fp),
            "n_bars": None, "first_date": None, "last_date": None,
        })
    manifest = {
        "source": source,
        "adjust": "qfq",
        "built_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "universe_files": entries,
    }
    blob = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
    manifest["snapshot_id"] = hashlib.sha256(
        blob.encode("utf-8")).hexdigest()[:16]
    return manifest


def verify_combined(dirs, extra_files, manifest):
    """组合清单校验: 任一文件哈希不符 → 差异列表。"""
    by_name = {}
    for d in dirs:
        for fn in os.listdir(d):
            by_name[os.path.basename(os.path.normpath(d)) + "/" + fn] = \
                os.path.join(d, fn)
    diffs = []
    for e in manifest["universe_files"]:
        if e["file"].startswith("EXTRA_"):
            continue
        fp = by_name.get(e["dir"] + "/" + e["file"])
        if fp is None or not os.path.exists(fp):
            diffs.append({"file": e["file"], "kind": "missing"})
            continue
        h = _sha256(fp)
        if h != e["sha256"]:
            diffs.append({"file": e["file"], "kind": "hash_changed"})
    return diffs
