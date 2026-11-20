from agentpay.storage.base import Store
from agentpay.storage.sqlite import SqliteStore


def open_store(url: str) -> Store:
    """`:memory:`, a file path, `sqlite:///path`, or `postgresql://...`."""
    if url.startswith(("postgres://", "postgresql://")):
        from agentpay.storage.postgres import PostgresStore
        return PostgresStore(url)
    if url.startswith("sqlite:///"):
        url = url[len("sqlite:///"):]
    return SqliteStore(url)


__all__ = ["Store", "SqliteStore", "open_store"]
