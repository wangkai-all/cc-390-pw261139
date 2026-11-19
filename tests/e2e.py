"""端到端验收：真实启动五个服务，跑完整迁移流程。

python3 tests/e2e.py

覆盖：
1. 导入 -> 旧接口与兼容服务对同一输入版本返回等价结果（逐字节）；
2. 改版 -> 新旧两版各自等价，历史修订可查；
3. 新版存储故障 -> 双写部分失败 -> 待重放 -> 恢复重放 -> 追平且幂等；
4. 兼容服务故障（晚恢复）-> 旧接口不受影响 -> 恢复后等价；
5. 全量对账三方校验和一致；差异可按批次号定位原始输入；
6. 空数组/重复项/顺序在整条管道中不丢失。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import client, core  # noqa: E402

PORTS = {"web": 18200, "transformer": 18201, "storage": 18202, "compat": 18203, "legacy": 18204}
URL = {k: f"http://127.0.0.1:{p}" for k, p in PORTS.items()}

EDGE_RECORD = {
    "id": "rpt-edge",
    "report_date": "2026-11-18",
    "pv": 100,
    "tags": ["a", "b", "a"],
    "hourly": [{"hour": 1, "pv": 5}, {"hour": 1, "pv": 5}, {"hour": 0, "pv": 3}],
    "top_pages": [],
    "attr": {"model": "last_click", "rules": [{"w": 0.5}, {"w": 0.5}]},
}
SECOND = {"id": "rpt-plain", "pv": 7, "rows": [{"k": 1}, {"k": 2}, {"k": 3}]}
THIRD = {"id": "rpt-empty-only", "idempotent_check": True, "empty": [], "one": [None]}

passed = 0


def check(name, cond, extra=""):
    global passed
    if not cond:
        print(f"  ✘ {name} {extra}")
        raise SystemExit(1)
    passed += 1
    print(f"  ✔ {name}")


def wait_health(url, name, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            client.call("GET", url + "/health", timeout=2)
            return
        except client.ServiceError:
            time.sleep(0.2)
    raise SystemExit(f"服务未就绪: {name}")


def legacy_payload(rid, version=None):
    params = {"id": rid}
    if version:
        params["version"] = str(version)
    return client.call("GET", URL["legacy"] + "/api/v1/report", params)["data"]["payload"]


def compat_payload(rid, version=None):
    params = {"id": rid}
    if version:
        params["version"] = str(version)
    return client.call("GET", URL["compat"] + "/api/v1/report", params)["data"]["payload"]


def main():
    tmp = tempfile.mkdtemp(prefix="e2e-data-")
    env = dict(os.environ)
    env.update({
        "DATA_DIR": tmp,
        "WEB_PORT": str(PORTS["web"]), "TRANSFORMER_PORT": str(PORTS["transformer"]),
        "STORAGE_PORT": str(PORTS["storage"]), "COMPAT_PORT": str(PORTS["compat"]),
        "LEGACY_PORT": str(PORTS["legacy"]),
        "TRANSFORMER_URL": URL["transformer"], "STORAGE_URL": URL["storage"],
        "COMPAT_URL": URL["compat"], "LEGACY_URL": URL["legacy"],
    })
    procs = []
    for mod in ("storage_svc", "legacy_svc", "transformer", "compat_svc", "web_svc"):
        procs.append(subprocess.Popen(
            [sys.executable, "-m", f"app.{mod}"], cwd=BASE, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    try:
        for name in ("storage", "legacy", "transformer", "compat", "web"):
            wait_health(URL[name], name)
        print("五个服务已就绪\n[1] 导入与等价性")

        r = client.call("POST", URL["transformer"] + "/ingest",
                        body={"records": [EDGE_RECORD, SECOND, THIRD], "kind": "import"})["data"]
        check("批次已双写", r["storage"] == "applied", json.dumps(r, ensure_ascii=False))
        batch1 = r["batch_id"]

        for rec in (EDGE_RECORD, SECOND, THIRD):
            rid = rec["id"]
            old, new = legacy_payload(rid), compat_payload(rid)
            check(f"{rid} 旧接口==原始输入", core.canon(old) == core.canon(rec))
            check(f"{rid} 兼容服务==原始输入", core.canon(new) == core.canon(rec))
            check(f"{rid} 旧==新（同一输入版本等价）", core.canon(old) == core.canon(new))

        rebuilt = compat_payload("rpt-edge")
        check("空数组保留", rebuilt["top_pages"] == [])
        check("重复子项保留", rebuilt["hourly"].count({"hour": 1, "pv": 5}) == 2)
        check("子项顺序保留", [h["hour"] for h in rebuilt["hourly"]] == [1, 1, 0])
        check("重复标量子项保留", rebuilt["tags"] == ["a", "b", "a"])

        print("[2] 改版与历史修订")
        revised = dict(EDGE_RECORD, pv=101, hourly=EDGE_RECORD["hourly"] + [{"hour": 3, "pv": 9}])
        r2 = client.call("POST", URL["web"] + "/api/revise",
                         body={"record_id": "rpt-edge", "payload": revised})["data"]
        check("改版生成 v2", r2["records"][0]["version"] == 2)
        check("v1 仍等价", core.canon(compat_payload("rpt-edge", 1)) == core.canon(EDGE_RECORD))
        check("v2 新内容等价", core.canon(compat_payload("rpt-edge", 2)) == core.canon(
            client.call("GET", URL["legacy"] + "/api/v1/report",
                        params={"id": "rpt-edge", "version": "2"})["data"]["payload"]))
        revs = client.call("GET", URL["storage"] + "/internal/revisions",
                           params={"record_id": "rpt-edge"})["data"]["revisions"]
        check("历史修订两行且批次不同", len(revs) == 2 and revs[0]["batch_id"] != revs[1]["batch_id"])

        print("[3] 新版存储故障 -> 待重放 -> 重放追平")
        client.call("POST", URL["storage"] + "/admin/fault", body={"down": True})
        r3 = client.call("POST", URL["transformer"] + "/ingest",
                         body={"records": [dict(SECOND, pv=8)], "kind": "revise"})["data"]
        check("故障期批次标记待重放", r3["storage"] == "pending_replay")
        pending = client.call("GET", URL["storage"] + "/pending")["data"]
        check("待重放列表含该批次", r3["batch_id"] in pending["pending"])
        try:
            compat_payload("rpt-plain", 2)
            check("故障期兼容查询应失败", False)
        except client.ServiceError as e:
            check("故障期兼容查询 503", e.status == 503)
        check("故障期旧接口仍可读", legacy_payload("rpt-plain", 1)["pv"] == 7)

        client.call("POST", URL["storage"] + "/admin/fault", body={"down": False})
        rep = client.call("POST", URL["storage"] + "/replay", body={})["data"]
        check("重放补上了落后批次", r3["batch_id"] in rep["applied"])
        rep2 = client.call("POST", URL["storage"] + "/replay", body={})["data"]
        check("重放幂等（二次无新增）", rep2["applied"] == [])
        check("追平后 v2 等价", core.canon(compat_payload("rpt-plain", 2))
              == core.canon(legacy_payload("rpt-plain", 2)))

        print("[4] 兼容服务故障（晚恢复）")
        client.call("POST", URL["compat"] + "/admin/fault", body={"down": True})
        try:
            compat_payload("rpt-edge")
            check("兼容故障期应 503", False)
        except client.ServiceError as e:
            check("兼容故障期 503，旧接口不受影响", e.status == 503
                  and legacy_payload("rpt-edge", 1)["pv"] == 100)
        client.call("POST", URL["compat"] + "/admin/fault", body={"down": False})
        check("恢复后兼容服务等价", core.canon(compat_payload("rpt-edge", 2))
              == core.canon(legacy_payload("rpt-edge", 2)))

        print("[5] 全量对账与批次定位")
        rec = client.call("GET", URL["web"] + "/api/reconcile")["data"]
        check("对账全部一致", rec["summary"]["bad"] == 0 and rec["summary"]["ok"] == rec["summary"]["total"],
              json.dumps(rec["summary"]))
        src = client.call("GET", URL["web"] + "/api/batch_source", params={"batch_id": batch1})["data"]
        check("按批次号可取回原始输入", core.canon(src["records"][0]["payload"]) == core.canon(EDGE_RECORD))
        check("原始批次带版本分配", src["records"][0]["version"] == 1)

        status = client.call("GET", URL["web"] + "/api/status")["data"]
        check("状态面板四服务在线", all(s["ok"] for s in status["services"]))
        check("三类表确有数据（非改名字段）",
              status["storage_stats"]["tables"]["sub_items"] > 0
              and status["storage_stats"]["tables"]["sub_item_groups"] > 0
              and status["storage_stats"]["tables"]["revisions"] >= 4)

        print(f"\n全部通过：{passed} 项断言 ✔")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=5)


if __name__ == "__main__":
    main()
