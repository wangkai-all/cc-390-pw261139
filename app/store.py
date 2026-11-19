"""两套存储的持久层（SQLite）。

NewStore（新版存储，三类表）：
  masters         主记录：非数组顶层字段（标量/对象）的 JSON
  sub_item_groups 子项分组：每个数组字段一行，count=0 表示空数组（与缺失区分）
  sub_items       子项行：每个数组元素一行，seq 保序，重复项各占一行
  revisions       历史修订：每次改版一行，含批次号与内容校验和
  batches         已应用批次注册表：重放幂等的依据

LegacyStore（旧宽表，迁移期保留）：
  wide_rows       整行 JSON 原文，(record_id, version) 为主键
"""
import os
import sqlite3
import threading

from . import core

NEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS masters(
  record_id TEXT NOT NULL, version INTEGER NOT NULL, fields TEXT NOT NULL,
  PRIMARY KEY(record_id, version));
CREATE TABLE IF NOT EXISTS sub_item_groups(
  record_id TEXT NOT NULL, version INTEGER NOT NULL, field TEXT NOT NULL,
  item_count INTEGER NOT NULL,
  PRIMARY KEY(record_id, version, field));
CREATE TABLE IF NOT EXISTS sub_items(
  record_id TEXT NOT NULL, version INTEGER NOT NULL, field TEXT NOT NULL,
  seq INTEGER NOT NULL, item TEXT NOT NULL,
  PRIMARY KEY(record_id, version, field, seq));
CREATE TABLE IF NOT EXISTS revisions(
  record_id TEXT NOT NULL, version INTEGER NOT NULL, batch_id TEXT NOT NULL,
  checksum TEXT NOT NULL, ingested_at TEXT NOT NULL,
  PRIMARY KEY(record_id, version));
CREATE TABLE IF NOT EXISTS batches(
  batch_id TEXT PRIMARY KEY, kind TEXT NOT NULL, record_count INTEGER NOT NULL,
  checksum TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
"""

LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS wide_rows(
  record_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
  batch_id TEXT NOT NULL, ingested_at TEXT NOT NULL,
  PRIMARY KEY(record_id, version));
"""


def _connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class NewStore:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(NEW_SCHEMA)

    def _conn(self):
        return _connect(self.path)

    def apply_batch(self, line):
        """应用一条日志批次。幂等：同 (record_id, version) 先删后插，批次行覆盖写。"""
        batch_id = line["batch_id"]
        records = line["records"]
        batch_checksum = core.checksum([core.checksum(r["payload"]) for r in records])
        with self._lock, self._conn() as conn:
            for rec in records:
                rid, ver = rec["record_id"], rec["version"]
                master, groups, items = core.split(rec["payload"])
                for table in ("masters", "sub_item_groups", "sub_items", "revisions"):
                    conn.execute(f"DELETE FROM {table} WHERE record_id=? AND version=?", (rid, ver))
                conn.execute("INSERT INTO masters VALUES(?,?,?)", (rid, ver, core.canon(master)))
                for g in groups:
                    conn.execute("INSERT INTO sub_item_groups VALUES(?,?,?,?)",
                                 (rid, ver, g["field"], g["count"]))
                for it in items:
                    conn.execute("INSERT INTO sub_items VALUES(?,?,?,?,?)",
                                 (rid, ver, it["field"], it["seq"], core.canon(it["item"])))
                conn.execute("INSERT INTO revisions VALUES(?,?,?,?,?)",
                             (rid, ver, batch_id, core.checksum(rec["payload"]), line["ts"]))
            conn.execute(
                "INSERT OR REPLACE INTO batches VALUES(?,?,?,?,?,?)",
                (batch_id, line.get("kind", "import"), len(records), batch_checksum,
                 "applied", line["ts"]))
        return {"batch_id": batch_id, "records": len(records), "checksum": batch_checksum}

    def applied_batch_ids(self):
        with self._conn() as conn:
            return {r["batch_id"] for r in conn.execute("SELECT batch_id FROM batches")}

    def list_batches(self):
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM batches ORDER BY created_at, batch_id")]

    def get_record(self, record_id, version=None):
        """重组某版本宽表记录；version 为空取最新。不存在返回 None。"""
        with self._conn() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT MAX(version) AS v FROM revisions WHERE record_id=?",
                    (record_id,)).fetchone()
                if not row or row["v"] is None:
                    return None
                version = row["v"]
            m = conn.execute("SELECT fields FROM masters WHERE record_id=? AND version=?",
                             (record_id, version)).fetchone()
            if not m:
                return None
            groups = conn.execute(
                "SELECT field, item_count FROM sub_item_groups WHERE record_id=? AND version=?",
                (record_id, version)).fetchall()
            items = conn.execute(
                "SELECT field, seq, item FROM sub_items WHERE record_id=? AND version=?"
                " ORDER BY field, seq", (record_id, version)).fetchall()
            rev = conn.execute(
                "SELECT batch_id, checksum, ingested_at FROM revisions WHERE record_id=? AND version=?",
                (record_id, version)).fetchone()
        import json
        master = json.loads(m["fields"])
        payload = core.assemble(
            master,
            [{"field": g["field"], "count": g["item_count"]} for g in groups],
            [{"field": i["field"], "seq": i["seq"], "item": json.loads(i["item"])} for i in items])
        return {"record_id": record_id, "version": version, "payload": payload,
                "checksum": rev["checksum"], "batch_id": rev["batch_id"],
                "ingested_at": rev["ingested_at"]}

    def list_records(self):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT record_id, version, batch_id, checksum, ingested_at"
                " FROM revisions ORDER BY record_id, version").fetchall()
        records = {}
        for r in rows:
            rec = records.setdefault(r["record_id"], {"record_id": r["record_id"], "versions": []})
            rec["versions"].append({"version": r["version"], "batch_id": r["batch_id"],
                                    "checksum": r["checksum"], "ingested_at": r["ingested_at"]})
        for rec in records.values():
            rec["latest_version"] = rec["versions"][-1]["version"]
        return list(records.values())

    def stats(self):
        counts = {}
        with self._conn() as conn:
            for table in ("masters", "sub_item_groups", "sub_items", "revisions", "batches"):
                counts[table] = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        size = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        return {"tables": counts, "db_bytes": size}


class LegacyStore:
    """旧宽表：整行 JSON 原文存储，迁移期仍是排序事实源与查询对照组。"""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(LEGACY_SCHEMA)

    def _conn(self):
        return _connect(self.path)

    def upsert(self, record_id, version, payload, batch_id, ingested_at):
        with self._lock, self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO wide_rows VALUES(?,?,?,?,?)",
                         (record_id, version, core.canon(payload), batch_id, ingested_at))

    def get(self, record_id, version=None):
        import json
        with self._conn() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT * FROM wide_rows WHERE record_id=? ORDER BY version DESC LIMIT 1",
                    (record_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM wide_rows WHERE record_id=? AND version=?",
                    (record_id, version)).fetchone()
        if not row:
            return None
        return {"record_id": row["record_id"], "version": row["version"],
                "payload": json.loads(row["payload"]), "batch_id": row["batch_id"],
                "ingested_at": row["ingested_at"]}

    def latest_version(self, record_id):
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM wide_rows WHERE record_id=?",
                               (record_id,)).fetchone()
        return row["v"] or 0

    def list_records(self):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT record_id, version, batch_id, ingested_at FROM wide_rows"
                " ORDER BY record_id, version").fetchall()
        records = {}
        for r in rows:
            rec = records.setdefault(r["record_id"], {"record_id": r["record_id"], "versions": []})
            rec["versions"].append({"version": r["version"], "batch_id": r["batch_id"],
                                    "ingested_at": r["ingested_at"]})
        for rec in records.values():
            rec["latest_version"] = rec["versions"][-1]["version"]
        return list(records.values())
