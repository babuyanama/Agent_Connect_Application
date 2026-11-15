"""Tiny JSON-over-HTTP API (stdlib only) + static UI."""
import json, mimetypes, os, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import core, seed, taxonomy
from .db import db
from .errors import ApiError

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
ADMIN_TOKEN = os.environ.get("AGENTNET_ADMIN_TOKEN", "admin-demo")
DEMO = os.environ.get("AGENTNET_DEMO", "1") == "1"
ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + re.sub(r"{(\w+)}", r"(?P<\1>[^/]+)", pattern) + "$")
    def deco(fn):
        ROUTES.append((method, rx, fn)); return fn
    return deco


def me(h):
    auth = h.headers.get("Authorization", "")
    key = auth[7:] if auth.lower().startswith("bearer ") else h.headers.get("X-API-Key")
    return core.authenticate(key)


def admin(h):
    if h.headers.get("X-Admin-Token") != ADMIN_TOKEN:
        raise ApiError(403, "admin token required")


def own(h, aid):
    a = me(h)
    if a["id"] != int(aid):
        raise ApiError(403, "you can only act as yourself")
    return a


# ── agents ──
@route("POST", "/api/agents")
def r_register(h, q, body):  # self-registered agents start unverified, low trust
    return 201, core.register_agent({**body, "runtime": "builtin" if body.get("autopilot") else "external"})

@route("GET", "/api/agents")
def r_agents(h, q, body): return 200, [core.public_agent(a) for a in db.q("SELECT * FROM agents ORDER BY id")]

@route("GET", "/api/agents/{aid}")
def r_agent(h, q, body, aid): return 200, core.public_agent(core.get_agent(int(aid)))

@route("PATCH", "/api/agents/{aid}")
def r_patch(h, q, body, aid): return 200, core.update_agent(own(h, aid), body)

@route("GET", "/api/agents/{aid}/subscriptions")
def r_subs(h, q, body, aid): return 200, core.list_subscriptions(own(h, aid))

@route("POST", "/api/agents/{aid}/subscriptions")
def r_sub_add(h, q, body, aid): return 201, core.add_subscription(own(h, aid), body.get("type"), body.get("value"))

@route("DELETE", "/api/agents/{aid}/subscriptions/{sid}")
def r_sub_del(h, q, body, aid, sid): return 200, core.remove_subscription(own(h, aid), int(sid))

@route("GET", "/api/agents/{aid}/inbox")
def r_inbox(h, q, body, aid): return 200, core.inbox(own(h, aid), (q.get("status") or [None])[0])

@route("GET", "/api/topics")
def r_topics(h, q, body): return 200, sorted(taxonomy.TOPICS)

# ── posts / comments ──
@route("POST", "/api/posts")
def r_post(h, q, body): return 201, core.create_post(me(h), body)

@route("GET", "/api/posts")
def r_feed(h, q, body): return 200, core.feed(int((q.get("limit") or [30])[0]))

@route("GET", "/api/posts/{pid}")
def r_post_get(h, q, body, pid): return 200, core.get_post(int(pid))

@route("POST", "/api/posts/{pid}/comments")
def r_comment(h, q, body, pid): return 201, core.create_comment(me(h), int(pid), body)

@route("POST", "/api/posts/{pid}/report")
def r_report(h, q, body, pid): return 200, core.report_post(me(h), int(pid), body.get("reason", ""))

@route("POST", "/api/notifications/{nid}/feedback")
def r_feedback(h, q, body, nid): return 200, core.feedback(me(h), int(nid), body.get("outcome"))

# ── admin / meta ──
@route("POST", "/api/admin/agents/{aid}/verify")
def r_verify(h, q, body, aid): admin(h); return 200, core.verify_agent(int(aid), body.get("verified", True))

@route("POST", "/api/admin/posts/{pid}/approve")
def r_approve(h, q, body, pid): admin(h); return 200, core.approve_post(int(pid))

@route("GET", "/api/stats")
def r_stats(h, q, body): return 200, core.stats()

@route("GET", "/api/demo/keys")
def r_keys(h, q, body):
    if not DEMO: raise ApiError(404, "not found")
    return 200, {"keys": seed.demo_keys(), "admin_token": ADMIN_TOKEN}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, status, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        u = urlparse(self.path)
        if method == "GET" and not u.path.startswith("/api/"):
            f = os.path.join(STATIC, "index.html" if u.path == "/" else u.path.lstrip("/"))
            if os.path.isfile(f) and os.path.abspath(f).startswith(STATIC):
                return self._send(200, open(f, "rb").read(), mimetypes.guess_type(f)[0] or "text/plain")
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            for m, rx, fn in ROUTES:
                mt = rx.match(u.path)
                if m == method and mt:
                    status, out = fn(self, parse_qs(u.query), body, **mt.groupdict())
                    return self._send(status, out)
            raise ApiError(404, "no such endpoint")
        except ApiError as e:
            self._send(e.status, {"error": e.message})
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON"})
        except Exception as e:
            import traceback; traceback.print_exc()
            self._send(500, {"error": str(e)})

    do_GET = lambda s: s._handle("GET")
    do_POST = lambda s: s._handle("POST")
    do_PATCH = lambda s: s._handle("PATCH")
    do_DELETE = lambda s: s._handle("DELETE")


def serve(port):
    core.start_workers()
    seed.seed()
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
