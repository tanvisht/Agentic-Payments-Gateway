from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    admin_key: str = field(default_factory=lambda: os.environ.get("AGENTPAY_ADMIN_KEY", ""))
    db_path: str = field(default_factory=lambda: os.environ.get("AGENTPAY_DB_PATH", ":memory:"))
    default_provider: str = field(default_factory=lambda: os.environ.get("AGENTPAY_DEFAULT_PROVIDER", "mock"))
    providers: list[str] = field(default_factory=lambda: os.environ.get("AGENTPAY_PROVIDERS", "").split(",") if os.environ.get("AGENTPAY_PROVIDERS") else [])
    signature_tolerance_s: int = 300
    async_webhooks: bool = True
    signing_key: str = field(default_factory=lambda: os.environ.get("AGENTPAY_SIGNING_KEY", ""))
    rate_per_s: float = 20.0
    rate_burst: int = 60
