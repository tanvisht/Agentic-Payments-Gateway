"""Money primitives.

All amounts travel through the gateway as integer *minor units* (cents for USD,
yen for JPY). Floats never touch a balance. Conversion from human input goes
through :class:`decimal.Decimal` with explicit rounding.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# ISO-4217 currencies whose minor unit exponent is not 2.
_EXPONENTS: dict[str, int] = {
    "JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "UGX": 0,
    "BHD": 3, "KWD": 3, "OMR": 3, "JOD": 3, "TND": 3,
}

SUPPORTED_CURRENCIES = frozenset(
    {"USD", "EUR", "GBP", "CAD", "AUD", "INR", "SGD", "CHF", "JPY", "KRW", "KWD", "BHD"}
)


def exponent(currency: str) -> int:
    return _EXPONENTS.get(currency.upper(), 2)


def normalize_currency(currency: str) -> str:
    cur = (currency or "").strip().upper()
    if cur not in SUPPORTED_CURRENCIES:
        raise ValueError(f"unsupported currency: {currency!r}")
    return cur


@dataclass(frozen=True, slots=True)
class Money:
    amount: int  # minor units
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise TypeError("Money.amount must be int minor units")
        object.__setattr__(self, "currency", normalize_currency(self.currency))

    @classmethod
    def from_major(cls, value: str | int | Decimal, currency: str) -> "Money":
        cur = normalize_currency(currency)
        try:
            d = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"invalid amount: {value!r}") from exc
        if not d.is_finite():
            raise ValueError("amount must be finite")
        q = Decimal(1).scaleb(-exponent(cur))
        minor = (d.quantize(q, rounding=ROUND_HALF_UP) * (10 ** exponent(cur))).to_integral_value()
        return cls(int(minor), cur)

    def to_major(self) -> Decimal:
        return Decimal(self.amount).scaleb(-exponent(self.currency))

    def _check(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __str__(self) -> str:
        return f"{self.to_major():.{exponent(self.currency)}f} {self.currency}"
