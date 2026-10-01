#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite 实验库（方案文档第 5 章九表 + data_snapshots）。

设计要点（对应文档 [2] SQLite WAL 说明）:
  - WAL 改善读写并发，但仍只有一个同时写事务 → 全部写操作走本类的
    单写入队列（同进程串行）；多机调度时再迁移 PostgreSQL。
  - 最终提交与 Champion 指针更新使用事务 + 状态检查，防止并发覆盖。
  - 失败实验永不删除（keep_failed_experiments）。
"""
from __future__ import print_function
import os, json, sqlite3, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies(
  version TEXT PRIMARY KEY,
  parent TEXT,
  spec_hash TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS data_snapshots(
  snapshot_id TEXT PRIMARY KEY,
  manifest TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments(
  id TEXT PRIMARY KEY,
  hypothesis TEXT,
  parent_version TEXT,
  snapshot_id TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  seed INTEGER,
  trial_index INTEGER,
  state TEXT NOT NULL DEFAULT 'PROPOSED',
  spec_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL,
  window_label TEXT NOT NULL,
  window_start TEXT,
  window_end TEXT,
  costs TEXT NOT NULL,
  metrics TEXT,
  artifact_hash TEXT,
  UNIQUE(experiment_id, window_label, costs)
);
CREATE TABLE IF NOT EXISTS audit_reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id TEXT NOT NULL,
  agent TEXT NOT NULL,
  findings TEXT NOT NULL,
  severity TEXT NOT NULL,
  evidence_locator TEXT
);
CREATE TABLE IF NOT EXISTS state_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id TEXT NOT NULL,
  previous_state TEXT NOT NULL,
  next_state TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  error TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS decisions(
  experiment_id TEXT PRIMARY KEY,
  gate_version TEXT NOT NULL,
  decision TEXT NOT NULL,
  reasons TEXT NOT NULL,
  reviewer TEXT NOT NULL DEFAULT 'code'
);
CREATE TABLE IF NOT EXISTS champion_history(
  version TEXT PRIMARY KEY,
  previous_version TEXT,
  promoted_at TEXT NOT NULL,
  rollback_reason TEXT
);
CREATE TABLE IF NOT EXISTS artifacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  UNIQUE(experiment_id, kind, path)
);
CREATE INDEX IF NOT EXISTS idx_runs_exp ON runs(experiment_id);
CREATE INDEX IF NOT EXISTS idx_events_exp ON state_events(experiment_id);
CREATE TABLE IF NOT EXISTS sealed_evaluations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target TEXT NOT NULL,
  challenger TEXT,
  window_start TEXT,
  window_end TEXT,
  metrics TEXT,
  baseline_metrics TEXT,
  verdict TEXT NOT NULL,
  reasons TEXT,
  decided_by TEXT NOT NULL,
  decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corrections(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id TEXT NOT NULL,
  original_decision TEXT NOT NULL,
  corrected_interpretation TEXT NOT NULL,
  reason TEXT NOT NULL,
  corrected_at TEXT NOT NULL,
  impact TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS data_snapshot_notes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id TEXT NOT NULL,
  note TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

TERMINAL = ("DECIDED", "REJECTED", "ERROR")


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


class Store(object):
    """单写入队列串行登记的实验库。进程内线程不安全但事件循环内串行安全。"""

    def __init__(self, path):
        self.path = path
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        # 迁移: strategies 增加 spec_json（旧库补列）
        cols = [r[1] for r in self.conn.execute(
            "PRAGMA table_info(strategies)").fetchall()]
        if "spec_json" not in cols:
            self.conn.execute(
                "ALTER TABLE strategies ADD COLUMN spec_json TEXT")
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- 基础 ----------
    def _tx(self, fn):
        """事务包装：多步写要么全成要么全回滚。"""
        cur = self.conn.cursor()
        try:
            out = fn(cur)
            self.conn.commit()
            return out
        except Exception:
            self.conn.rollback()
            raise

    # ---------- 数据快照 ----------
    def register_snapshot(self, snapshot_id, manifest):
        def f(cur):
            cur.execute("INSERT OR IGNORE INTO data_snapshots VALUES(?,?,?)",
                        (snapshot_id, json.dumps(manifest, sort_keys=True,
                                                 ensure_ascii=False), now_iso()))
        self._tx(f)

    def get_snapshot(self, snapshot_id):
        row = self.conn.execute(
            "SELECT manifest FROM data_snapshots WHERE snapshot_id=?",
            (snapshot_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def add_snapshot_note(self, snapshot_id, note):
        def f(cur):
            cur.execute(
                "INSERT INTO data_snapshot_notes(snapshot_id,note,created_at)"
                " VALUES(?,?,?)", (snapshot_id, note, now_iso()))
        self._tx(f)

    def get_snapshot_notes(self, snapshot_id=None):
        sql = "SELECT snapshot_id,note,created_at FROM data_snapshot_notes"
        if snapshot_id:
            return self.conn.execute(sql + " WHERE snapshot_id=?",
                                     (snapshot_id,)).fetchall()
        return self.conn.execute(sql).fetchall()

    # ---------- 实验与状态机 ----------
    def create_experiment(self, exp_id, hypothesis, parent_version,
                          snapshot_id, config_hash, spec_json=None,
                          seed=0, trial_index=0):
        ts = now_iso()

        def f(cur):
            cur.execute(
                "INSERT INTO experiments(id,hypothesis,parent_version,"
                "snapshot_id,config_hash,seed,trial_index,state,spec_json,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (exp_id, hypothesis, parent_version, snapshot_id, config_hash,
                 seed, trial_index, "PROPOSED",
                 json.dumps(spec_json, sort_keys=True, ensure_ascii=False)
                 if spec_json is not None else None, ts, ts))
            cur.execute(
                "INSERT INTO state_events(experiment_id,previous_state,"
                "next_state,timestamp) VALUES(?,?,?,?)",
                (exp_id, "NONE", "PROPOSED", ts))
        self._tx(f)

    def transition(self, exp_id, next_state, error=None, retry_count=0,
                   expected_from=None):
        """状态迁移：expected_from 非空时做乐观检查，防止并发/重放覆盖。"""
        def f(cur):
            row = cur.execute("SELECT state FROM experiments WHERE id=?",
                              (exp_id,)).fetchone()
            if not row:
                raise KeyError("experiment not found: %s" % exp_id)
            prev = row[0]
            if prev in TERMINAL:
                raise RuntimeError(
                    "terminal state %s cannot transition to %s" %
                    (prev, next_state))
            if expected_from is not None and prev != expected_from:
                raise RuntimeError("state mismatch: %s != %s" %
                                   (prev, expected_from))
            ts = now_iso()
            cur.execute(
                "UPDATE experiments SET state=?, updated_at=? WHERE id=?",
                (next_state, ts, exp_id))
            cur.execute(
                "INSERT INTO state_events(experiment_id,previous_state,"
                "next_state,timestamp,error,retry_count) VALUES(?,?,?,?,?,?)",
                (exp_id, prev, next_state, ts, error, retry_count))
            return prev
        return self._tx(f)

    def set_spec(self, exp_id, spec_json):
        def f(cur):
            cur.execute(
                "UPDATE experiments SET spec_json=?, updated_at=? WHERE id=?",
                (json.dumps(spec_json, sort_keys=True, ensure_ascii=False),
                 now_iso(), exp_id))
        self._tx(f)

    def get_experiment(self, exp_id):
        row = self.conn.execute(
            "SELECT id,hypothesis,parent_version,snapshot_id,config_hash,"
            "seed,trial_index,state,spec_json,created_at,updated_at "
            "FROM experiments WHERE id=?", (exp_id,)).fetchone()
        if not row:
            return None
        keys = ("id", "hypothesis", "parent_version", "snapshot_id",
                "config_hash", "seed", "trial_index", "state", "spec_json",
                "created_at", "updated_at")
        d = dict(zip(keys, row))
        if d["spec_json"]:
            d["spec_json"] = json.loads(d["spec_json"])
        return d

    def non_terminal_experiments(self):
        rows = self.conn.execute(
            "SELECT id, state FROM experiments WHERE state NOT IN "
            "('DECIDED','REJECTED','ERROR')").fetchall()
        return rows

    def count_experiments(self):
        return self.conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]

    def experiments_since_promotion(self):
        """距上次 PROMOTE 以来的实验数（停止规则用）。"""
        row = self.conn.execute(
            "SELECT e.created_at FROM experiments e JOIN decisions d "
            "ON d.experiment_id=e.id AND d.decision='PROMOTE' "
            "ORDER BY e.created_at DESC LIMIT 1").fetchone()
        if not row:
            return self.count_experiments()
        return self.conn.execute(
            "SELECT COUNT(*) FROM experiments WHERE created_at > ?",
            (row[0],)).fetchone()[0]

    def trial_index_next(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM experiments").fetchone()[0] + 1

    # ---------- 运行产物 ----------
    def add_run(self, run_id, experiment_id, window_label, window_start,
                window_end, costs, metrics, artifact_hash):
        def f(cur):
            cur.execute(
                "INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?,?,?)",
                (run_id, experiment_id, window_label, window_start,
                 window_end, json.dumps(costs, sort_keys=True), 
                 json.dumps(metrics, sort_keys=True) if metrics else None,
                 artifact_hash))
        self._tx(f)

    def get_runs(self, experiment_id):
        rows = self.conn.execute(
            "SELECT run_id,window_label,window_start,window_end,costs,"
            "metrics,artifact_hash FROM runs WHERE experiment_id=?",
            (experiment_id,)).fetchall()
        out = []
        for r in rows:
            out.append({"run_id": r[0], "window_label": r[1],
                        "window_start": r[2], "window_end": r[3],
                        "costs": json.loads(r[4]),
                        "metrics": json.loads(r[5]) if r[5] else None,
                        "artifact_hash": r[6]})
        return out

    # ---------- 审计 / 决策 / champion ----------
    def add_audit(self, experiment_id, agent, findings, severity,
                  evidence_locator=None):
        def f(cur):
            cur.execute(
                "INSERT INTO audit_reports(experiment_id,agent,findings,"
                "severity,evidence_locator) VALUES(?,?,?,?,?)",
                (experiment_id, agent,
                 json.dumps(findings, sort_keys=True, ensure_ascii=False),
                 severity, evidence_locator))
        self._tx(f)

    def add_decision(self, experiment_id, gate_version, decision, reasons,
                     reviewer="code"):
        def f(cur):
            cur.execute(
                "INSERT OR REPLACE INTO decisions VALUES(?,?,?,?,?)",
                (experiment_id, gate_version, decision,
                 json.dumps(reasons, ensure_ascii=False), reviewer))
            # 晋级指针更新与决策同事务（防并发覆盖）
            if decision == "PROMOTE":
                prev = self.conn.execute(
                    "SELECT version FROM champion_history ORDER BY "
                    "promoted_at DESC LIMIT 1").fetchone()
                prev_v = prev[0] if prev else None
                row = self.conn.execute(
                    "SELECT parent_version FROM experiments WHERE id=?",
                    (experiment_id,)).fetchone()
                cur.execute(
                    "INSERT OR IGNORE INTO champion_history VALUES(?,?,?,?)",
                    (row[0] if row else "unknown",
                     prev_v, now_iso(), None))
        self._tx(f)

    def get_decision(self, experiment_id):
        row = self.conn.execute(
            "SELECT gate_version,decision,reasons,reviewer FROM decisions "
            "WHERE experiment_id=?", (experiment_id,)).fetchone()
        if not row:
            return None
        return {"gate_version": row[0], "decision": row[1],
                "reasons": json.loads(row[2]), "reviewer": row[3]}

    def current_champion(self):
        row = self.conn.execute(
            "SELECT version, previous_version, promoted_at FROM "
            "champion_history ORDER BY promoted_at DESC LIMIT 1").fetchone()
        return dict(zip(("version", "previous_version", "promoted_at"), row)) \
            if row else None

    # ---------- sealed 评估（独立流程，结果不回传研究循环） ----------
    def add_sealed_eval(self, target, challenger, window_start, window_end,
                        metrics, baseline_metrics, verdict, reasons,
                        decided_by):
        def f(cur):
            cur.execute(
                "INSERT INTO sealed_evaluations(target,challenger,"
                "window_start,window_end,metrics,baseline_metrics,verdict,"
                "reasons,decided_by,decided_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (target, challenger, window_start, window_end,
                 json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                 json.dumps(baseline_metrics, ensure_ascii=False,
                            sort_keys=True),
                 verdict, json.dumps(reasons, ensure_ascii=False),
                 decided_by, now_iso()))
        self._tx(f)

    def get_sealed_evals(self, target=None):
        sql = ("SELECT id,target,challenger,window_start,window_end,"
               "metrics,baseline_metrics,verdict,reasons,decided_by,"
               "decided_at FROM sealed_evaluations")
        if target:
            sql += " WHERE target=?"
            rows = self.conn.execute(sql, (target,)).fetchall()
        else:
            rows = self.conn.execute(sql).fetchall()
        out = []
        for r in rows:
            out.append({"id": r[0], "target": r[1], "challenger": r[2],
                        "window_start": r[3], "window_end": r[4],
                        "metrics": json.loads(r[5]),
                        "baseline_metrics": json.loads(r[6]),
                        "verdict": r[7], "reasons": json.loads(r[8]),
                        "decided_by": r[9], "decided_at": r[10]})
        return out

    # ---------- 修正事件（评审第 3 轮：Gate 判定可追溯） ----------
    def add_correction(self, experiment_id, original_decision,
                       corrected_interpretation, reason, impact):
        def f(cur):
            cur.execute(
                "INSERT INTO corrections(experiment_id,original_decision,"
                "corrected_interpretation,reason,corrected_at,impact)"
                " VALUES(?,?,?,?,?,?)",
                (experiment_id, original_decision, corrected_interpretation,
                 reason, now_iso(), impact))
        self._tx(f)

    def get_corrections(self, experiment_id=None):
        sql = ("SELECT id,experiment_id,original_decision,"
               "corrected_interpretation,reason,corrected_at,impact"
               " FROM corrections")
        if experiment_id:
            rows = self.conn.execute(sql + " WHERE experiment_id=?",
                                     (experiment_id,)).fetchall()
        else:
            rows = self.conn.execute(sql).fetchall()
        cols = ("id", "experiment_id", "original_decision",
                "corrected_interpretation", "reason", "corrected_at", "impact")
        return [dict(zip(cols, r)) for r in rows]

    # ---------- artifacts ----------
    def add_artifact(self, experiment_id, kind, path, sha256):
        def f(cur):
            cur.execute(
                "INSERT OR IGNORE INTO artifacts(experiment_id,kind,path,"
                "sha256) VALUES(?,?,?,?)",
                (experiment_id, kind, path, sha256))
        self._tx(f)
