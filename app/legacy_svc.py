"""旧系统服务（:8004）——宽表存储 + 下游正在使用的旧查询接口。

迁移期间它仍是：1) 版本号分配的事实源；2) 新旧查询对比的对照组。
接口契约（/api/v1/*）被兼容查询服务原样实现，下游可无感切换。
"""
from . import config
from .httpkit import Api, HttpError
from .store import LegacyStore

api = Api("legacy")
store = LegacyStore(config.LEGACY_DB)


@api.route("GET", "/health")
def health(q, body):
    return {"service": "legacy", "ok": True}


@api.route("POST", "/internal/upsert")
def upsert(q, body):
    for key in ("record_id", "version", "payload", "batch_id", "ingested_at"):
        if body is None or key not in body:
            raise HttpError(400, f"缺少字段: {key}")
    store.upsert(body["record_id"], body["version"], body["payload"],
                 body["batch_id"], body["ingested_at"])
    return {"record_id": body["record_id"], "version": body["version"]}


def _report_data(rec):
    return {"id": rec["record_id"], "version": rec["version"], "payload": rec["payload"]}


@api.route("GET", "/api/v1/report")
def report(q, body):
    """旧查询接口：按 id（可选版本）取整行宽表记录。"""
    rid = q.get("id")
    if not rid:
        raise HttpError(400, "缺少参数 id")
    version = int(q["version"]) if q.get("version") else None
    rec = store.get(rid, version)
    if rec is None:
        return 404, {"code": 404, "msg": f"记录不存在: {rid}", "data": None}
    return 200, {"code": 0, "msg": "ok", "data": _report_data(rec)}


@api.route("GET", "/api/v1/reports")
def reports(q, body):
    return 200, {"code": 0, "msg": "ok", "data": store.list_records()}


if __name__ == "__main__":
    api.serve(config.LEGACY_PORT)
