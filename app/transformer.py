"""接入转换器（:8001）——管道写路径的唯一入口。

写入顺序（防偏离的关键）：
  1. 预检旧系统可用（版本号由旧系统分配，迁移期它是排序事实源）；
  2. 追加接入日志并 fsync（此后无论哪边失败都可重放恢复）；
  3. 双写：旧宽表 + 新版存储（同一拆分纯函数，两条路径产出一致）；
  4. 新版存储失败不阻断批次：标记 pending，待恢复后由 /replay 追平。
"""
from . import client, config
from .httpkit import Api, HttpError
from .ingestlog import IngestLog

api = Api("transformer")
log = IngestLog(config.INGEST_LOG)


@api.route("GET", "/health")
def health(q, body):
    return {"service": "transformer", "ok": True, "log": config.INGEST_LOG}


@api.route("POST", "/ingest")
def ingest(q, body):
    if not body or not isinstance(body.get("records"), list) or not body["records"]:
        raise HttpError(400, "请求体需为 {records: [...]} 且非空")
    kind = body.get("kind", "import")

    # 1. 校验 + 分配版本（旧系统是迁移期的排序事实源）
    staged = []
    for i, payload in enumerate(body["records"]):
        if not isinstance(payload, dict):
            raise HttpError(400, f"第 {i} 条记录不是 JSON 对象")
        rid = payload.get("id")
        if rid is None or str(rid) == "":
            raise HttpError(400, f"第 {i} 条记录缺少 id 字段")
        rid = str(rid)
        try:
            resp = client.call("GET", f"{config.LEGACY_URL}/api/v1/report", params={"id": rid})
            latest = resp["data"]["version"]
        except client.ServiceError as e:
            if e.status == 404:
                latest = 0
            else:
                raise HttpError(503, f"旧系统不可用，批次未接入: {e.msg}") from e
        staged.append({"record_id": rid, "version": latest + 1, "payload": payload})

    # 2. 先写日志（WAL），此后任何下游失败都能重放补齐
    line = log.append(kind, staged)

    # 3a. 双写 - 旧宽表（失败则整批失败：日志已留底，可安全重试）
    try:
        for rec in staged:
            client.call("POST", f"{config.LEGACY_URL}/internal/upsert", body={
                "record_id": rec["record_id"], "version": rec["version"],
                "payload": rec["payload"], "batch_id": line["batch_id"],
                "ingested_at": line["ts"]})
    except client.ServiceError as e:
        raise HttpError(503, f"旧宽表写入失败，批次未生效（日志已留底 {line['batch_id']}）: {e.msg}") from e

    # 3b. 双写 - 新版存储（失败不阻断：标记待重放）
    storage_status = "applied"
    storage_error = None
    try:
        client.call("POST", f"{config.STORAGE_URL}/apply", body=line)
    except client.ServiceError as e:
        storage_status = "pending_replay"
        storage_error = e.msg

    return {
        "batch_id": line["batch_id"],
        "kind": kind,
        "records": [{"record_id": r["record_id"], "version": r["version"]} for r in staged],
        "storage": storage_status,
        "storage_error": storage_error,
    }


@api.route("GET", "/log")
def log_tail(q, body):
    lines = log.read_all()
    n = int(q.get("n", "20"))
    return {"total": len(lines), "lines": lines[-n:]}


@api.route("GET", "/log/batch")
def log_batch(q, body):
    """差异定位：按批次号取回原始输入（原批次）。"""
    batch_id = q.get("batch_id")
    if not batch_id:
        raise HttpError(400, "缺少 batch_id")
    line = log.find_batch(batch_id)
    if line is None:
        raise HttpError(404, f"批次不存在: {batch_id}")
    return line


if __name__ == "__main__":
    api.serve(config.TRANSFORMER_PORT)
