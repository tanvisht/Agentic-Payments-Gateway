"""Typed gateway errors. Every error maps to a stable `code` and an HTTP status."""

from __future__ import annotations


class GatewayError(Exception):
    code = "gateway_error"
    http_status = 400

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, **({"details": self.details} if self.details else {})}}


class AuthenticationError(GatewayError):
    code, http_status = "authentication_failed", 401


class SignatureError(GatewayError):
    code, http_status = "invalid_signature", 401


class ReplayError(GatewayError):
    code, http_status = "replay_detected", 409


class NotFound(GatewayError):
    code, http_status = "not_found", 404


class ValidationFailed(GatewayError):
    code, http_status = "validation_failed", 422


class MandateViolation(GatewayError):
    code, http_status = "mandate_violation", 403


class RiskDeclined(GatewayError):
    code, http_status = "risk_declined", 402


class InvalidStateTransition(GatewayError):
    code, http_status = "invalid_state", 409


class IdempotencyConflict(GatewayError):
    code, http_status = "idempotency_conflict", 409


class ProviderError(GatewayError):
    """Raised by provider adapters. `retryable` drives router failover."""

    code, http_status = "provider_error", 502

    def __init__(self, message: str, *, retryable: bool = False, decline_code: str | None = None, details: dict | None = None):
        super().__init__(message, details=details)
        self.retryable = retryable
        self.decline_code = decline_code


class CardDeclined(ProviderError):
    code, http_status = "card_declined", 402

    def __init__(self, message: str, *, decline_code: str = "generic_decline", details: dict | None = None):
        super().__init__(message, retryable=False, decline_code=decline_code, details=details)


class NoProviderAvailable(GatewayError):
    code, http_status = "no_provider_available", 503


class RateLimited(GatewayError):
    code, http_status = "rate_limited", 429
