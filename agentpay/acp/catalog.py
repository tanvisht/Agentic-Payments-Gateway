"""Merchant catalog with weighted keyword search and structured filters.

The interface (`search`, `get`) is what the ACP server depends on, so a
Postgres full-text or vector index can replace this without touching callers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Product:
    sku: str
    merchant_id: str
    name: str
    category: str
    price: int  # minor units
    currency: str = "USD"
    sizes: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    description: str = ""
    stock: int = 100

    def to_dict(self) -> dict:
        return {"sku": self.sku, "merchant_id": self.merchant_id, "name": self.name, "category": self.category,
                "price": self.price, "currency": self.currency, "sizes": list(self.sizes),
                "description": self.description, "in_stock": self.stock > 0}


_SHOE = tuple(str(s) for s in (6, 7, 8, 9, 10, 11, 12, 13))

PRODUCTS: list[Product] = [
    Product("strd-aero-2", "stride.run", "Aero Daily Trainer 2", "running", 12900, sizes=_SHOE,
            keywords=("daily", "trainer", "neutral", "versatile", "road"), description="Balanced cushioning for everyday miles"),
    Product("strd-cloudmax", "stride.run", "CloudMax Long Run", "running", 15900, sizes=_SHOE,
            keywords=("max", "cushion", "plush", "long", "marathon", "recovery"), description="Max-stack foam for high mileage"),
    Product("strd-tempo-x", "stride.run", "Tempo X Plate", "running", 19900, sizes=_SHOE,
            keywords=("race", "carbon", "plate", "fast", "tempo", "marathon"), description="Carbon-plated race day shoe"),
    Product("trl-ridge-4", "trailhead.co", "Ridge 4 Trail", "trail", 14500, sizes=_SHOE,
            keywords=("trail", "grip", "lug", "hiking", "mud"), description="Aggressive lugs for technical terrain"),
    Product("crt-hoop-elite", "courtside.shop", "Hoop Elite Mid", "basketball", 13900, sizes=_SHOE,
            keywords=("basketball", "court", "ankle", "support", "jump"), description="Mid-top court shoe with ankle lockdown"),
    Product("strd-dry-tee", "stride.run", "DryLite Run Tee", "apparel", 3500, sizes=("S", "M", "L", "XL"),
            keywords=("shirt", "tee", "breathable", "running"), description="Featherweight moisture-wicking tee"),
    Product("strd-socks-3", "stride.run", "Cushion Run Socks (3 pack)", "apparel", 1800, sizes=("S", "M", "L"),
            keywords=("socks", "running", "blister"), description="Blister-resistant running socks"),
    Product("air-sfo-jfk", "skyline.air", "SFO → JFK Economy", "travel", 32900,
            keywords=("flight", "new york", "jfk", "sfo"), description="Non-stop, carry-on included"),
    Product("stay-soho-1n", "quietstays.com", "SoHo Loft, 1 night", "lodging", 28900,
            keywords=("hotel", "new york", "soho", "night"), description="Boutique loft, late checkout"),
    Product("eat-omakase-2", "tablefor.two", "Omakase for two", "dining", 24000,
            keywords=("dinner", "sushi", "omakase", "reservation"), description="12-course chef's counter"),
]

_INDEX = {p.sku: p for p in PRODUCTS}
_WORD = re.compile(r"[a-z0-9]+")


def get(sku: str) -> Product | None:
    return _INDEX.get(sku)


def search(query: str = "", *, max_price: int | None = None, category: str | None = None, size: str | None = None,
           merchants: list[str] | None = None, limit: int = 5) -> list[Product]:
    terms = set(_WORD.findall(query.lower()))
    scored: list[tuple[float, Product]] = []
    for p in PRODUCTS:
        if max_price is not None and p.price > max_price:
            continue
        if category and p.category != category:
            continue
        if size and p.sizes and size not in p.sizes:
            continue
        if merchants and p.merchant_id not in merchants:
            continue
        if p.stock <= 0:
            continue
        name_terms = set(_WORD.findall(p.name.lower()))
        kw = set(" ".join(p.keywords).split())
        score = 3 * len(terms & name_terms) + 2 * len(terms & kw) + (2 if p.category in terms else 0)
        if terms and score == 0:
            continue
        scored.append((score, p))
    scored.sort(key=lambda sp: (-sp[0], sp[1].price))
    return [p for _, p in scored[:limit]]
