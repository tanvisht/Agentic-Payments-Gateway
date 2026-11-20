"""`agentpay serve` / `agentpay demo`."""

from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser(prog="agentpay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    d = sub.add_parser("demo", help="run an end-to-end agent purchase in-process")
    d.add_argument("goal", nargs="?", default="Buy running shoes under $150 from nike.com")
    args = ap.parse_args()
    if args.cmd == "serve":
        import uvicorn
        from agentpay.api.app import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
    else:
        from agentpay.agent.demo import run_demo
        run_demo(args.goal)


if __name__ == "__main__":
    main()
