"""Vercel serverless function: POST /api/run {"goal": str, "budget": dollars} -> live trace.

Runs the real gateway in-process (fresh in-memory store per request, mock
card processor), so visitors can watch an actual mandate-bound purchase.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentpay.agent.trace import run_trace  # noqa: E402

MAX_BODY = 2048


class handler(BaseHTTPRequestHandler):  # noqa: N801 - Vercel's required name
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        try:
            n = int(self.headers.get("content-length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"error": "body too large"})
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            goal = str(body.get("goal", ""))
            budget = float(body.get("budget", 500))
            return self._send(200, run_trace(goal, budget_cents=int(budget * 100)))
        except (ValueError, TypeError):
            return self._send(400, {"error": "invalid request"})
        except Exception:  # noqa: BLE001 - never leak internals from a public endpoint
            return self._send(500, {"error": "demo run failed"})

    def do_GET(self):  # noqa: N802
        # GET /api/run?goal=...&budget=... runs the same side-effect-free demo (handy for
        # link sharing and health checks); bare GET just describes the endpoint.
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(getattr(self, "path", "") or "").query)
        if "goal" not in q:
            return self._send(200, {"ok": True, "usage": "POST {goal, budget} or GET ?goal=&budget="})
        try:
            budget = float(q.get("budget", ["500"])[0])
            return self._send(200, run_trace(q["goal"][0], budget_cents=int(budget * 100)))
        except (ValueError, TypeError):
            return self._send(400, {"error": "invalid request"})
        except Exception:  # noqa: BLE001
            return self._send(500, {"error": "demo run failed"})
