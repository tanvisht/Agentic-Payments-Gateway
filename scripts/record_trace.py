"""Regenerate web/trace.json (the site's offline fallback) from a real run."""

import json
from pathlib import Path

from agentpay.agent.trace import run_trace

if __name__ == "__main__":
    t = run_trace()
    t["live"] = False
    Path("web/trace.json").write_text(json.dumps(t, indent=1, default=str))
    print(f"wrote web/trace.json ({len(t['events'])} events): {t['result']}")
