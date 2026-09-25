"""Short-lived signed identity delegation between trusted Helios services."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from helios_core.authz import Principal, PrincipalKind


class DelegationError(ValueError):
    """A delegated identity assertion is missing, invalid, or expired."""


@dataclass(frozen=True)
class DelegatedContext:
    principal: Principal
    organization_id: str
    model_id: str
    expires_at: int
    request_id: str | None = None
    session_id: str | None = None


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _key(secret: str) -> bytes:
    encoded = secret.encode()
    if len(encoded) < 32:
        raise DelegationError("delegation secret must contain at least 32 bytes")
    return encoded


def issue_assertion(
    secret: str,
    principal: Principal,
    organization_id: str,
    model_id: str,
    *,
    now: int | None = None,
    lifetime_seconds: int = 60,
    request_id: str | None = None,
    session_id: str | None = None,
) -> str:
    if not organization_id or not model_id:
        raise DelegationError("organization and model context are required")
    issued_at = int(time.time() if now is None else now)
    payload = {
        "v": 1,
        "iss": "helios-api",
        "aud": "helios-mcp",
        "iat": issued_at,
        "exp": issued_at + lifetime_seconds,
        "jti": secrets.token_urlsafe(12),
        "principal": {
            "issuer": principal.issuer,
            "subject": principal.subject,
            "kind": principal.kind.value,
            "display_name": principal.display_name,
        },
        "organization_id": organization_id,
        "model_id": model_id,
        "request_id": request_id,
        "session_id": session_id,
    }
    body = _encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    signature = _encode(
        hmac.new(_key(secret), body.encode(), hashlib.sha256).digest()
    )
    return f"{body}.{signature}"


def verify_assertion(
    secret: str,
    assertion: str,
    *,
    now: int | None = None,
    clock_skew_seconds: int = 5,
) -> DelegatedContext:
    try:
        body, supplied_signature = assertion.split(".", 1)
        expected_signature = _encode(
            hmac.new(_key(secret), body.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise DelegationError("invalid delegation signature")
        payload = json.loads(_decode(body))
    except DelegationError:
        raise
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise DelegationError("malformed delegation assertion") from exc

    current = int(time.time() if now is None else now)
    if (
        payload.get("v") != 1
        or payload.get("iss") != "helios-api"
        or payload.get("aud") != "helios-mcp"
    ):
        raise DelegationError("invalid delegation issuer or audience")
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    if not isinstance(issued_at, int) or not isinstance(expires_at, int):
        raise DelegationError("invalid delegation timestamps")
    if issued_at > current + clock_skew_seconds:
        raise DelegationError("delegation assertion is not yet valid")
    if expires_at < current - clock_skew_seconds:
        raise DelegationError("delegation assertion has expired")
    principal_data = payload.get("principal")
    if not isinstance(principal_data, dict):
        raise DelegationError("delegation principal is required")
    try:
        principal = Principal(
            issuer=principal_data["issuer"],
            subject=principal_data["subject"],
            kind=PrincipalKind(principal_data["kind"]),
            display_name=principal_data.get("display_name", ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DelegationError("invalid delegated principal") from exc
    organization_id = payload.get("organization_id")
    model_id = payload.get("model_id")
    if not isinstance(organization_id, str) or not organization_id:
        raise DelegationError("delegated organization is required")
    if not isinstance(model_id, str) or not model_id:
        raise DelegationError("delegated model is required")
    request_id = _optional_correlation(payload.get("request_id"), "request")
    session_id = _optional_correlation(payload.get("session_id"), "session")
    return DelegatedContext(
        principal,
        organization_id,
        model_id,
        expires_at,
        request_id,
        session_id,
    )


def _optional_correlation(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 128:
        raise DelegationError(f"invalid delegated {label} ID")
    return value
