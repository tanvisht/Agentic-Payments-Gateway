"""A tiny multi-merchant catalog the demo agent shops from (stands in for ACP/UCP merchant feeds)."""

from __future__ import annotations

CATALOG: list[dict] = [
    {"sku": "nk-pegasus-41", "merchant_id": "nike.com", "category": "running", "name": "Pegasus 41 Road Running Shoe", "price": 13999, "currency": "USD"},
    {"sku": "nk-vomero-18", "merchant_id": "nike.com", "category": "running", "name": "Vomero 18 Max-Cushion Shoe", "price": 15999, "currency": "USD"},
    {"sku": "ad-adizero-sl2", "merchant_id": "adidas.com", "category": "running", "name": "Adizero SL2", "price": 11999, "currency": "USD"},
    {"sku": "nk-dri-fit-tee", "merchant_id": "nike.com", "category": "apparel", "name": "Dri-FIT Running Tee", "price": 3500, "currency": "USD"},
    {"sku": "hk-clifton-10", "merchant_id": "hoka.com", "category": "running", "name": "Clifton 10", "price": 14500, "currency": "USD"},
    {"sku": "ua-flight-boarding", "merchant_id": "united.com", "category": "travel", "name": "SFO→JFK Economy", "price": 32900, "currency": "USD"},
]


def search(query: str, max_price: int | None = None, category: str | None = None) -> list[dict]:
    words = [w for w in query.lower().split() if len(w) > 2]
    out = []
    for p in CATALOG:
        hay = f"{p['name']} {p['category']} {p['merchant_id']}".lower()
        if words and not any(w in hay for w in words):
            continue
        if max_price is not None and p["price"] > max_price:
            continue
        if category and p["category"] != category:
            continue
        out.append(p)
    return sorted(out, key=lambda p: p["price"])


def get(sku: str) -> dict | None:
    return next((p for p in CATALOG if p["sku"] == sku), None)
