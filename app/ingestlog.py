"""接入日志（WAL）：转换器先写日志再双写，重放与差异定位的唯一事实源。

每行一个批次：{"seq", "batch_id", "ts", "kind", "records":[{"record_id","version","payload"}]}
- 版本号在接入时分配并落进日志 -> 重放产出与双写逐字节一致；
- batch_id = b<seq>，差异定位时用它回查本文件即可拿到原始输入。
"""
import json
import os
import threading
from datetime import datetime, timezone


class IngestLog:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._next_seq = 1
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self._next_seq += sum(1 for line in f if line.strip())

    def append(self, kind, records):
        """records: [{"record_id","version","payload"}]，返回完整日志行（已落盘并 fsync）。"""
        with self._lock:
            seq = self._next_seq
            line = {
                "seq": seq,
                "batch_id": f"b{seq:06d}",
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "kind": kind,
                "records": records,
            }
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._next_seq += 1
        return line

    def read_all(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def find_batch(self, batch_id):
        for line in self.read_all():
            if line["batch_id"] == batch_id:
                return line
        return None
