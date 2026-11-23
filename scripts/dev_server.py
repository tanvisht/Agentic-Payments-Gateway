"""Local stand-in for the Vercel deployment: serves web/ and routes /api/run to api/run.py."""

import os
import sys
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api.run import handler as ApiHandler  # noqa: E402


class Dev(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=os.path.join(ROOT, "web"), **kw)

    def do_POST(self):  # noqa: N802
        if self.path.startswith("/api/run"):
            return ApiHandler.do_POST(self)
        self.send_error(404)

    _send = ApiHandler._send

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    ThreadingHTTPServer(("127.0.0.1", port), Dev).serve_forever()
