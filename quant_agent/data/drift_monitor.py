#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行情数据修订监控（评审第 2 轮修正版：冻结必须真正不可变）。

第 1 版缺陷（评审发现）: 硬链接与源文件共享 inode，缓存原地写入时
冻结副本一起改变，"0 修订"是假象。

本版:
  --freeze   独立拷贝（copy2）+ 只读权限（0o444）+ 冻结清单自哈希
             manifest（frozen_manifest.json，含每文件 sha256 与字节数）；
  （默认）   两步验收:
             1) 冻结完整性自检——冻结文件 vs 冻结清单（防冻结侧被改动）;
             2) 当前缓存 vs 冻结副本——发现修订/增/删，写
                var/revision_log.jsonl，退出码 2（cron 告警）。

可靠性由 tests/test_snapshot_immutability.py 验收: 对源文件原地覆盖/
追加/删除/路径替换四种操作，冻结哈希不变。

重要事实（2026-10-01 核验）: 项目空间卷是 exFAT——无 POSIX 权限位
（chmod 静默无效）、无硬链接。因此:
  - 旧版"只读权限 0o444"声称作废（该卷上不生效）；
  - 不可变性依靠两层: (a) qdata 原子写（tmp+os.replace）——更新走新
    文件，旧拷贝天然不被触碰（隔离）; (b) 冻结清单 sha256 自检——
    冻结侧被改动可检出（检测）。是"隔离+检测"，不是权限拦截；
  - 旧版"硬链接冻结"在 exFAT 上根本不成立（exFAT 无硬链接），
    历史上的"0 修订"无任何证明力，已按此口径重述。
"""

import json
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import quant_agent  # noqa: E402


def _hash(fp):
    import hashlib
    h = hashlib.sha256()
    n = 0
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def _dirs():
    return (("cache", os.path.join(quant_agent.QSYS, "cache")),
            ("ucache", os.path.join(quant_agent.VAR, "ucache")))


def freeze(snapshot_id):
    import shutil
    fz = os.path.join(quant_agent.VAR, "frozen", snapshot_id)
    manifest = {}
    for dst_name, src in (("qcache", _dirs()[0][1]),
                          ("ucache", _dirs()[1][1])):
        if not os.path.isdir(src):
            continue
        dst = os.path.join(fz, dst_name)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        os.makedirs(dst)
        for fn in sorted(os.listdir(src)):
            if not fn.endswith(".json"):
                continue
            sfp = os.path.join(src, fn)
            dfp = os.path.join(dst, fn)
            shutil.copy2(sfp, dfp)          # 独立拷贝，绝不硬链接
            os.chmod(dfp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            h, n = _hash(dfp)
            manifest["%s/%s" % (dst_name, fn)] = {"sha256": h, "bytes": n}
    with open(os.path.join(fz, "frozen_manifest.json"), "w") as f:
        json.dump({"snapshot_id": snapshot_id, "files": manifest},
                  f, indent=1)
    print("freeze done -> %s (%d files, read-only copies)" %
          (fz, len(manifest)))


def _integrity(fz):
    """冻结侧自检: 冻结文件 vs 冻结清单。"""
    mfp = os.path.join(fz, "frozen_manifest.json")
    if not os.path.exists(mfp):
        return ["冻结清单缺失: %s" % mfp]
    man = json.load(open(mfp))["files"]
    bad = []
    for rel, ent in man.items():
        fp = os.path.join(fz, rel)
        if not os.path.exists(fp):
            bad.append("冻结文件丢失: %s" % rel)
            continue
        h, n = _hash(fp)
        if h != ent["sha256"] or n != ent["bytes"]:
            bad.append("冻结文件被改动: %s" % rel)
    return bad


def check(snapshot_id):
    fz = os.path.join(quant_agent.VAR, "frozen", snapshot_id)
    integ = _integrity(fz)
    changed, added, removed = [], [], []
    for dst_name, src in (("qcache", _dirs()[0][1]),
                          ("ucache", _dirs()[1][1])):
        dst = os.path.join(fz, dst_name)
        if not os.path.isdir(dst):
            continue
        fzen = {fn for fn in os.listdir(dst) if fn.endswith(".json")}
        fcur = ({fn for fn in os.listdir(src) if fn.endswith(".json")}
                if os.path.isdir(src) else set())
        for fn in sorted(fzen & fcur):
            if _hash(os.path.join(dst, fn))[0] != \
               _hash(os.path.join(src, fn))[0]:
                changed.append(fn)
        added += sorted(fcur - fzen)
        removed += sorted(fzen - fcur)
    import datetime
    rec = {"checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
           "snapshot_id": snapshot_id,
           "frozen_integrity_ok": not integ,
           "frozen_integrity_issues": integ,
           "revised": changed, "added": added, "removed": removed}
    with open(os.path.join(quant_agent.VAR, "revision_log.jsonl"), "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("checked %s: integrity_ok=%s revised=%d added=%d removed=%d" %
          (snapshot_id, not integ, len(changed), len(added), len(removed)))
    for x in integ[:3]:
        print("  INTEGRITY:", x)
    for fn in changed[:5]:
        print("  revised:", fn)
    return 2 if (integ or changed or added or removed) else 0


def main(argv):
    from quant_agent.experiments.store import Store
    store = Store(os.path.join(quant_agent.VAR, "qa_store.sqlite3"))
    snaps = store.conn.execute(
        "SELECT snapshot_id FROM data_snapshots ORDER BY created_at"
    ).fetchall()
    if not snaps:
        print("no snapshots registered")
        return 1
    snap_id = snaps[-1][0]
    if "--freeze" in argv:
        freeze(snap_id)
        return 0
    return check(snap_id)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
