"""兼容查询服务（:8003）——旧查询接口在新版存储上的等价实现。

对外契约与旧系统 /api/v1/* 完全一致；内部把每次查询翻译成对三类表的读取与重组。
下游切流时只改地址不改代码。支持故障注入，演练"兼容服务晚恢复"。
"""
from . import client, config
from .httpkit import Api, HttpError

api = Api("compat")
fault = {"down": False}


def guard(path):
    if fault["down"] and not path.startswith(("/health", "/admin")):
        raise HttpError(503, "兼容查询服务故障注入中（演练开关已打开）")


api.before = guard


@api.route("GET", "/health")
def health(q, body):
    return {"service": "compat", "ok": True, "fault": fault["down"]}


@api.route("POST", "/admin/fault")
def set_fault(q, body):
    fault["down"] = bool(body and body.get("down"))
    return {"fault": fault["down"]}


@api.route("GET", "/api/v1/report")
def report(q, body):
    """与旧系统同契约：按 id（可选版本）返回整行宽表记录，数据来自三类表重组。"""
    rid = q.get("id")
    if not rid:
        raise HttpError(400, "缺少参数 id")
    params = {"record_id": rid}
    if q.get("version"):
        params["version"] = q["version"]
    try:
        resp = client.call("GET", f"{config.STORAGE_URL}/internal/record", params=params)
    except client.ServiceError as e:
        if e.status == 404:
            return 404, {"code": 404, "msg": f"记录不存在: {rid}", "data": None}
        raise HttpError(503, f"新版存储读取失败: {e.msg}") from e
    rec = resp["data"]
    return 200, {"code": 0, "msg": "ok",
                 "data": {"id": rec["record_id"], "version": rec["version"], "payload": rec["payload"]}}


@api.route("GET", "/api/v1/reports")
def reports(q, body):
    try:
        resp = client.call("GET", f"{config.STORAGE_URL}/internal/records")
    except client.ServiceError as e:
        raise HttpError(503, f"新版存储读取失败: {e.msg}") from e
    data = [{"record_id": r["record_id"], "latest_version": r["latest_version"],
             "versions": [{"version": v["version"], "batch_id": v["batch_id"],
                           "ingested_at": v["ingested_at"]} for v in r["versions"]]}
            for r in resp["data"]]
    return 200, {"code": 0, "msg": "ok", "data": data}


if __name__ == "__main__":
    api.serve(config.COMPAT_PORT)
