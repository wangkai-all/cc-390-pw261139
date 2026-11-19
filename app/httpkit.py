"""极简 HTTP 框架（标准库）：路由 + JSON 出入参 + 统一错误信封。

所有接口返回 {"code":0,"msg":"ok","data":...}；出错时 HTTP 状态码非 200，
body 为 {"code":<status>,"msg":...}。支持 before 钩子做故障注入。
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


class HttpError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status
        self.msg = msg


def ok(data=None, msg="ok"):
    return {"code": 0, "msg": msg, "data": data}


class Api:
    def __init__(self, name):
        self.name = name
        self.routes = []
        self.before = None  # fn(path) -> None，抛 HttpError 即拦截

    def route(self, method, pattern):
        rx = re.compile("^" + pattern + "$")

        def deco(fn):
            self.routes.append((method, rx, fn))
            return fn

        return deco

    def _handler_class(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass  # 静默访问日志，演练输出更干净

            def _send(self, status, obj):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _dispatch(self, method):
                parsed = urlparse(self.path)
                path, query = parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}
                body = None
                length = self.headers.get("Content-Length")
                if length:
                    raw = self.rfile.read(int(length))
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        return self._send(400, {"code": 400, "msg": "请求体不是合法 JSON"})
                try:
                    if api.before:
                        api.before(path)
                    for m, rx, fn in api.routes:
                        if m != method:
                            continue
                        match = rx.match(path)
                        if match:
                            result = fn(query, body, **match.groupdict())
                            if isinstance(result, tuple):
                                status, obj = result
                            else:
                                status, obj = 200, ok(result)
                            return self._send(status, obj)
                    self._send(404, {"code": 404, "msg": f"无此路由: {method} {path}"})
                except HttpError as e:
                    self._send(e.status, {"code": e.status, "msg": e.msg})
                except Exception as e:  # noqa: BLE001 - 演示服务，统一兜底
                    self._send(500, {"code": 500, "msg": f"{type(e).__name__}: {e}"})

            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

        return Handler

    def serve(self, port):
        server = ThreadingHTTPServer(("127.0.0.1", port), self._handler_class())
        print(f"[{self.name}] 监听 http://127.0.0.1:{port}", flush=True)
        server.serve_forever()
