"""Web 门面（:8000）——静态页面 + 编排 API。

页面只跟本服务通信（无跨域）；本服务把请求编排到转换器/新版存储/兼容服务/旧系统。
对账（reconcile）在这里执行：同一输入版本分别走旧接口与兼容服务，逐版本比对校验和与内容。
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import client, config, core

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}


def _get(url, params=None):
    return client.call("GET", url, params=params)


def _post(url, body):
    return client.call("POST", url, body=body)


def _legacy_report(rid, version=None):
    params = {"id": rid}
    if version:
        params["version"] = str(version)
    return _get(f"{config.LEGACY_URL}/api/v1/report", params)


def _compat_report(rid, version=None):
    params = {"id": rid}
    if version:
        params["version"] = str(version)
    return _get(f"{config.COMPAT_URL}/api/v1/report", params)


def _compare_one(rid, version):
    """同一输入版本：旧接口 vs 兼容服务。返回对比明细（含逐路径差异）。"""
    row = {"record_id": rid, "version": version}
    old = new = None
    try:
        old = _legacy_report(rid, version)["data"]
        row["legacy_checksum"] = core.checksum(old["payload"]) if old else None
    except client.ServiceError as e:
        row["legacy_error"] = f"{e.status} {e.msg}"
    try:
        new = _compat_report(rid, version)["data"]
        row["new_checksum"] = core.checksum(new["payload"]) if new else None
    except client.ServiceError as e:
        row["new_error"] = f"{e.status} {e.msg}"
    if old and new:
        row["equal"] = row["legacy_checksum"] == row["new_checksum"]
        row["diffs"] = [] if row["equal"] else core.diff_paths(old["payload"], new["payload"])
    else:
        row["equal"] = False
        row["diffs"] = []
    return row


def _reconcile():
    """全量对账：以新版存储的修订清单为基准，逐版本三方校验。

    stored_checksum   接入时写入修订表的校验和（日志批次的确定性产物）
    legacy_checksum   旧宽表现存内容的校验和
    new_checksum      兼容服务实时重组内容的校验和
    三者一致 -> ok；不一致 -> mismatch 并附差异路径与批次号（可回查原批次）。
    """
    records = _get(f"{config.STORAGE_URL}/internal/records")["data"]
    rows = []
    for rec in records:
        for v in rec["versions"]:
            row = _compare_one(rec["record_id"], v["version"])
            row["batch_id"] = v["batch_id"]
            row["stored_checksum"] = v["checksum"]
            if row.get("equal") and row["legacy_checksum"] == v["checksum"]:
                row["status"] = "ok"
            elif row.get("legacy_error") or row.get("new_error"):
                row["status"] = "error"
            else:
                row["status"] = "mismatch"
            rows.append(row)
    by_batch = {}
    for row in rows:
        b = by_batch.setdefault(row["batch_id"], {"batch_id": row["batch_id"], "total": 0, "ok": 0})
        b["total"] += 1
        b["ok"] += 1 if row["status"] == "ok" else 0
    for b in by_batch.values():
        b["status"] = "ok" if b["ok"] == b["total"] else "bad"
    return {"rows": rows, "batches": sorted(by_batch.values(), key=lambda b: b["batch_id"]),
            "summary": {"total": len(rows),
                        "ok": sum(1 for r in rows if r["status"] == "ok"),
                        "bad": sum(1 for r in rows if r["status"] != "ok")}}


def _service_health(name, url):
    started = time.time()
    try:
        resp = _get(f"{url}/health")
        return {"name": name, "ok": True, "ms": round((time.time() - started) * 1000, 1),
                "detail": resp.get("data", {})}
    except client.ServiceError as e:
        return {"name": name, "ok": False, "ms": round((time.time() - started) * 1000, 1),
                "error": f"{e.status} {e.msg}"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        if path in ("/", "/index.html"):
            path = "/index.html"
        full = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not full.startswith(WEB_DIR) or not os.path.isfile(full):
            return self._json(404, {"code": 404, "msg": "not found"})
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if parsed.path == "/health":
            return self._json(200, {"code": 0, "msg": "ok", "data": {"service": "web", "ok": True}})
        if not parsed.path.startswith("/api/"):
            return self._static(parsed.path)
        try:
            return self._json(200, {"code": 0, "msg": "ok", "data": self._api_get(parsed.path, q)})
        except client.ServiceError as e:
            return self._json(200, {"code": e.status, "msg": f"依赖服务异常: {e.msg}", "data": None})
        except Exception as e:  # noqa: BLE001
            return self._json(200, {"code": 500, "msg": f"{type(e).__name__}: {e}", "data": None})

    def do_POST(self):
        parsed = urlparse(self.path)
        length = self.headers.get("Content-Length")
        body = None
        if length:
            try:
                body = json.loads(self.rfile.read(int(length)).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._json(200, {"code": 400, "msg": "请求体不是合法 JSON", "data": None})
        try:
            return self._json(200, {"code": 0, "msg": "ok", "data": self._api_post(parsed.path, body)})
        except client.ServiceError as e:
            return self._json(200, {"code": e.status, "msg": f"依赖服务异常: {e.msg}", "data": None})
        except Exception as e:  # noqa: BLE001
            return self._json(200, {"code": 500, "msg": f"{type(e).__name__}: {e}", "data": None})

    # ---------------- GET ----------------
    def _api_get(self, path, q):
        if path == "/api/status":
            services = [
                _service_health("transformer", config.TRANSFORMER_URL),
                _service_health("storage", config.STORAGE_URL),
                _service_health("compat", config.COMPAT_URL),
                _service_health("legacy", config.LEGACY_URL),
            ]
            try:
                pending = _get(f"{config.STORAGE_URL}/pending")["data"]
            except client.ServiceError:
                pending = {"pending": [], "count": None}
            try:
                stats = _get(f"{config.STORAGE_URL}/internal/stats")["data"]
            except client.ServiceError:
                stats = None
            return {"services": services, "pending": pending, "storage_stats": stats}

        if path == "/api/records":
            return _get(f"{config.STORAGE_URL}/internal/records")["data"]

        if path == "/api/record":
            params = {"record_id": q["record_id"]}
            if q.get("version"):
                params["version"] = q["version"]
            return _get(f"{config.STORAGE_URL}/internal/record", params)["data"]

        if path == "/api/revisions":
            return _get(f"{config.STORAGE_URL}/internal/revisions",
                        {"record_id": q["record_id"]})["data"]

        if path == "/api/compare":
            version = int(q["version"]) if q.get("version") else None
            if version is None:
                rec = _get(f"{config.STORAGE_URL}/internal/record",
                           {"record_id": q["record_id"]})["data"]
                version = rec["version"]
            row = _compare_one(q["record_id"], version)
            old = _legacy_report(q["record_id"], version)["data"]
            new = _compat_report(q["record_id"], version)["data"]
            row["legacy_payload"] = old["payload"] if old else None
            row["new_payload"] = new["payload"] if new else None
            return row

        if path == "/api/reconcile":
            return _reconcile()

        if path == "/api/batches":
            return _get(f"{config.STORAGE_URL}/internal/batches")["data"]

        if path == "/api/batch_source":
            return _get(f"{config.TRANSFORMER_URL}/log/batch", {"batch_id": q["batch_id"]})["data"]

        if path == "/api/log":
            return _get(f"{config.TRANSFORMER_URL}/log", {"n": q.get("n", "20")})["data"]

        return _err(404, f"无此接口: {path}")

    # ---------------- POST ----------------
    def _api_post(self, path, body):
        if path == "/api/import":
            records = body.get("records") if body else None
            return _post(f"{config.TRANSFORMER_URL}/ingest",
                         {"records": records, "kind": "import"})["data"]

        if path == "/api/revise":
            payload = dict(body["payload"])
            payload["id"] = body["record_id"]
            return _post(f"{config.TRANSFORMER_URL}/ingest",
                         {"records": [payload], "kind": "revise"})["data"]

        if path == "/api/preview_split":
            master, groups, items = core.split(body["record"])
            return {"master": master, "groups": groups,
                    "items": items[:20], "items_total": len(items),
                    "checksum": core.checksum(body["record"])}

        if path == "/api/fault":
            target, down = body["target"], bool(body["down"])
            url = {"storage": config.STORAGE_URL, "compat": config.COMPAT_URL}[target]
            return _post(f"{url}/admin/fault", {"down": down})["data"]

        if path == "/api/replay":
            return _post(f"{config.STORAGE_URL}/replay", {})["data"]

        raise ValueError(f"无此接口: {path}")


def _err(status, msg):
    raise client.ServiceError("web", status, msg)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", config.WEB_PORT), Handler)
    print(f"[web] 页面与编排 API: http://127.0.0.1:{config.WEB_PORT}", flush=True)
    server.serve_forever()
