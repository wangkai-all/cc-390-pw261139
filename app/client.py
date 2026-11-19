"""服务间 HTTP 调用：统一超时、错误包装、信封解包。"""
import json
import urllib.error
import urllib.parse
import urllib.request


class ServiceError(Exception):
    def __init__(self, url, status, msg):
        super().__init__(f"{url} -> {status}: {msg}")
        self.url = url
        self.status = status
        self.msg = msg


def call(method, url, params=None, body=None, timeout=5):
    """发起调用，返回解析后的 body（信封）。非 2xx 抛 ServiceError。"""
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(raw).get("msg", raw)
        except ValueError:
            msg = raw
        raise ServiceError(url, e.code, msg) from e
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise ServiceError(url, 503, f"连接失败: {reason}") from e


def data_of(envelope):
    """解信封；code!=0 视为错误。"""
    if envelope.get("code") != 0:
        raise ServiceError("envelope", envelope.get("code", -1), envelope.get("msg", ""))
    return envelope.get("data")
