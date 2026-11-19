"""新版存储服务（:8002）——三类表的独立运行载体。

职责：应用批次（拆分写入）、日志重放（幂等追平）、重组读取、故障注入演练。
"""
from . import config, core
from .httpkit import Api, HttpError
from .ingestlog import IngestLog
from .store import NewStore

api = Api("storage")
store = NewStore(config.NEW_DB)
log = IngestLog(config.INGEST_LOG)
fault = {"down": False}


def guard(path):
    # /pending 是带外运维通道（类比消费者滞后监控），故障期仍可观察积压
    if fault["down"] and not path.startswith(("/health", "/admin", "/pending")):
        raise HttpError(503, "新版存储故障注入中（演练开关已打开）")


api.before = guard


@api.route("GET", "/health")
def health(q, body):
    return {"service": "storage", "ok": True, "fault": fault["down"]}


@api.route("POST", "/admin/fault")
def set_fault(q, body):
    fault["down"] = bool(body and body.get("down"))
    return {"fault": fault["down"]}


@api.route("POST", "/apply")
def apply(q, body):
    """应用一条日志批次（双写路径由转换器调用，重放路径由 /replay 调用）。"""
    if not body or "batch_id" not in body or "records" not in body:
        raise HttpError(400, "需要日志行 {batch_id, records, ...}")
    return store.apply_batch(body)


@api.route("POST", "/replay")
def replay(q, body):
    """重放日志：把尚未应用的批次按序补齐。幂等，可反复调用。"""
    applied_ids = store.applied_batch_ids()
    applied, skipped = [], []
    for line in log.read_all():
        if line["batch_id"] in applied_ids:
            skipped.append(line["batch_id"])
            continue
        store.apply_batch(line)
        applied.append(line["batch_id"])
    return {"applied": applied, "skipped": skipped,
            "pending_after": sorted(set(_log_batch_ids()) - store.applied_batch_ids())}


def _log_batch_ids():
    return [line["batch_id"] for line in log.read_all()]


@api.route("GET", "/pending")
def pending(q, body):
    ids = sorted(set(_log_batch_ids()) - store.applied_batch_ids())
    return {"pending": ids, "count": len(ids)}


@api.route("GET", "/internal/records")
def records(q, body):
    return store.list_records()


@api.route("GET", "/internal/record")
def record(q, body):
    rid = q.get("record_id")
    if not rid:
        raise HttpError(400, "缺少 record_id")
    version = int(q["version"]) if q.get("version") else None
    rec = store.get_record(rid, version)
    if rec is None:
        raise HttpError(404, f"记录不存在: {rid}" + (f" v{version}" if version else ""))
    return rec


@api.route("GET", "/internal/revisions")
def revisions(q, body):
    rid = q.get("record_id")
    if not rid:
        raise HttpError(400, "缺少 record_id")
    for rec in store.list_records():
        if rec["record_id"] == rid:
            return {"record_id": rid, "revisions": rec["versions"]}
    raise HttpError(404, f"记录不存在: {rid}")


@api.route("GET", "/internal/batches")
def batches(q, body):
    return store.list_batches()


@api.route("GET", "/internal/stats")
def stats(q, body):
    return store.stats()


@api.route("GET", "/internal/checksum")
def checksum(q, body):
    """某版本的重组内容校验和，供对账快速比对。"""
    rid = q.get("record_id")
    version = int(q["version"]) if q.get("version") else None
    rec = store.get_record(rid, version)
    if rec is None:
        raise HttpError(404, "记录不存在")
    return {"record_id": rid, "version": rec["version"],
            "stored_checksum": rec["checksum"],
            "assembled_checksum": core.checksum(rec["payload"])}


if __name__ == "__main__":
    api.serve(config.STORAGE_PORT)
