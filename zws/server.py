"""生成器的本机网页服务（只用标准库）。

只监听 127.0.0.1。接口必须带页面里的令牌；Host 和 Origin 必须是本机地址，
别的网站没法借浏览器调这里的接口（防 CSRF 和 DNS 重绑定）。
"""
import argparse
import hmac
import json
import secrets
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, claude_cli, form as F, generate, homes, schema

WEB = Path(__file__).resolve().parent.parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_BODY = 512 * 1024
CSP = "default-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class App:
    """服务的状态：令牌、生成任务、Claude Code 检测结果的缓存。"""

    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.jobs = {}
        self.lock = threading.Lock()
        self._env = None
        self._env_at = 0.0

    def env(self, fresh=False):
        if fresh or self._env is None or time.time() - self._env_at > 60:
            self._env = {"claude": claude_cli.check(), "python": sys.version.split()[0], "platform": sys.platform,
                         "assistants_dir": str(homes.base_dir())}
            self._env_at = time.time()
        return self._env

    def start_job(self, raw_form, engine, model, allow_cost):
        with self.lock:
            if any(j["state"] == "running" for j in self.jobs.values()):
                raise ApiError(409, "已经有一个生成任务在跑，等它结束或先取消")
            for k in sorted(self.jobs, key=lambda k: self.jobs[k]["t0"])[:-4]:
                del self.jobs[k]
            job = {"id": uuid.uuid4().hex[:12], "state": "running", "t0": time.time(), "t1": None, "log": [],
                   "cancel": threading.Event(), "result": None, "error": ""}
            self.jobs[job["id"]] = job

        def work():
            try:
                res = generate.run(raw_form, engine, model=model, allow_cost=allow_cost, cancel=job["cancel"],
                                   log=job["log"].append)
                res["preview"] = generate.preview(res["plan"])
                job["result"], job["state"] = res, "done"
            except generate.Cancelled:
                job["state"] = "cancelled"
            except generate.GenError as e:
                job["error"], job["state"] = str(e), "error"
            except Exception as e:                                  # 兜底：界面上要有话可说
                traceback.print_exc()
                job["error"], job["state"] = f"内部错误：{e}", "error"
            job["t1"] = time.time()

        threading.Thread(target=work, daemon=True).start()
        return job["id"]

    def job_view(self, jid):
        job = self.jobs.get(jid)
        if not job:
            raise ApiError(404, "没有这个任务")
        return {"id": job["id"], "state": job["state"], "log": list(job["log"]), "error": job["error"],
                "elapsed": round((job["t1"] or time.time()) - job["t0"], 1), "result": job["result"]}


# ---------------- 接口 ----------------

def _need(body, key, kind):
    v = body.get(key)
    if not isinstance(v, kind):
        raise ApiError(400, f"缺少参数 {key}")
    return v


def examples():
    """examples/ 下的示例表单（都是虚构人物），给页面上的「载入示例」用。"""
    out = []
    for p in sorted((WEB.parent / "examples").glob("form-*.json")):
        try:
            form = F.clean(json.loads(p.read_text(encoding="utf-8")))[0]
        except (OSError, ValueError):
            continue
        out.append({"key": p.stem[5:], "label": F.ROLES.get(form["role"], p.stem[5:]), "form": form})
    order = list(F.ROLES)
    out.sort(key=lambda e: order.index(e["form"]["role"]))
    return out


def api_get(app, path, q):
    if path == "/api/boot":
        return {"version": __version__, "spec": schema.spec(), "roles": [[k, v] for k, v in F.ROLES.items()],
                "blank": F.blank(), "about_max": F.ABOUT_MAX, "models": list(claude_cli.MODELS),
                "examples": examples(), "assistants": homes.listing()}
    if path == "/api/env":
        return app.env(fresh=q.get("fresh") == ["1"])
    if path == "/api/assistants":
        return {"assistants": homes.listing()}
    if path == "/api/job":
        return app.job_view((q.get("id") or [""])[0])
    raise ApiError(404, "没有这个接口")


def api_post(app, path, body):
    if path == "/api/preview":
        if isinstance(body.get("plan"), dict):
            plan, warn = schema.normalize(body["plan"])
            return {"plan": plan, "warnings": warn, "preview": generate.preview(plan)}
        form, plan, warn = generate.baseline(body.get("form"))
        return {"form": form, "plan": plan, "warnings": warn, "preview": generate.preview(plan)}
    if path == "/api/prompt":
        return {"prompt": generate.manual_prompt(body.get("form"))}
    if path == "/api/generate":
        engine = body.get("engine")
        if engine not in ("offline", "claude"):
            raise ApiError(400, "不认识的生成方式")
        model = body.get("model") if body.get("model") in claude_cli.MODELS else ""
        return {"job": app.start_job(body.get("form"), engine, model, body.get("allow_cost") is True)}
    if path == "/api/cancel":
        job = app.jobs.get(str(body.get("job")))
        if job:
            job["cancel"].set()
        return {"ok": True}
    if path == "/api/import":
        res = generate.import_reply(body.get("form"), _need(body, "reply", str))
        res["preview"] = generate.preview(res["plan"])
        return res
    if path == "/api/create":
        plan, _ = schema.normalize(_need(body, "plan", dict))
        mode = body.get("mode")
        if mode not in ("new", "update"):
            if homes.exists(plan["assistant"]["name"]):
                return {"exists": True, "name": plan["assistant"]["name"]}
            mode = "new"
        form = F.clean(body["form"])[0] if isinstance(body.get("form"), dict) else None
        home = homes.create(plan, form, mode)
        return {"slug": home.name, "path": str(home)}
    if path == "/api/launch":
        return {"state": homes.launch(_need(body, "slug", str))}
    if path == "/api/shortcut":
        where = body.get("where") if body.get("where") in ("desktop", "startup") else "desktop"
        return {"path": homes.shortcut(_need(body, "slug", str), where)}
    if path == "/api/open":
        return {"path": homes.open_folder(_need(body, "slug", str))}
    if path == "/api/delete":
        return {"moved_to": homes.trash(_need(body, "slug", str))}
    if path == "/api/load":
        slug = _need(body, "slug", str)
        plan, warn = schema.normalize(homes.read_plan(slug) or {})
        form = homes.read_form(slug)
        return {"form": F.clean(form)[0] if isinstance(form, dict) else None, "plan": plan, "warnings": warn,
                "preview": generate.preview(plan)}
    raise ApiError(404, "没有这个接口")


class Handler(BaseHTTPRequestHandler):
    server_version = "zws"
    protocol_version = "HTTP/1.1"
    app = None
    quiet = True

    def log_message(self, fmt, *args):
        if not self.quiet:
            super().log_message(fmt, *args)

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _origins(self):
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _guard(self, api):
        """Host 必须是本机；接口还要校验令牌和 Origin。"""
        if self.headers.get("Host", "") not in self._origins():
            raise ApiError(403, "只接受本机访问")
        if not api:
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in self._origins()}:
            raise ApiError(403, "来源不对")
        if not hmac.compare_digest(self.headers.get("X-ZWS-Token", ""), self.app.token):
            raise ApiError(403, "令牌不对，刷新页面再试")

    def _handle(self, method):
        url = urlparse(self.path)
        api = url.path.startswith("/api/")
        try:
            self._guard(api)
            if not api:
                if method != "GET" or url.path not in STATIC:
                    raise ApiError(404, "没有这个页面")
                name, ctype = STATIC[url.path]
                data = (WEB / name).read_bytes()
                if name == "index.html":
                    data = data.replace(b"__ZWS_TOKEN__", self.app.token.encode("ascii"))
                return self._send(200, data, ctype)
            if method == "GET":
                return self._json(200, api_get(self.app, url.path, parse_qs(url.query)))
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ApiError(413, "请求太大")
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except (ValueError, UnicodeDecodeError):
                raise ApiError(400, "请求不是合法的 JSON")
            if not isinstance(body, dict):
                raise ApiError(400, "请求必须是一个 JSON 对象")
            return self._json(200, api_post(self.app, url.path, body))
        except ApiError as e:
            self._json(e.code, {"error": str(e)})
        except (generate.GenError, homes.HomeError, schema.PlanError) as e:
            self._json(400, {"error": str(e)})
        except (BrokenPipeError, ConnectionError):
            pass
        except Exception as e:                                      # 兜底：把错误交给界面，不让线程静默死掉
            traceback.print_exc()
            try:
                self._json(500, {"error": f"内部错误：{e}"})
            except OSError:
                pass

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def make_server(port=8787, strict=False, app=None, quiet=True):
    """绑定 127.0.0.1。端口被占用或被系统保留时往后找，最多 20 个；strict 时不找。"""
    app = app or App()
    handler = type("ZwsHandler", (Handler,), {"app": app, "quiet": quiet})
    last = None
    for p in ([port] if strict else list(range(port, port + 20)) + [0]):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), handler)
            srv.daemon_threads = True
            return srv, app
        except OSError as e:
            last = e
    raise SystemExit(f"端口绑定失败：{last}")


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="真维斯生成器：在浏览器里填需求，生成你自己的桌面助手。")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--strict-port", action="store_true", help="端口不可用时直接报错，不换端口")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--verbose", action="store_true", help="打印每个请求")
    args = ap.parse_args(argv)
    srv, _app = make_server(args.port, args.strict_port, quiet=not args.verbose)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"真维斯生成器 {__version__} 已启动：{url}")
    print("关掉这个窗口或按 Ctrl+C 结束。已经启动的浮窗不受影响。")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已结束。")
    finally:
        srv.server_close()
