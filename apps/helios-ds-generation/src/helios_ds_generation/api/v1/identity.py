"""Who is calling: the authenticated Workbench user (ADR 0001).

Cloudera AI Workbench puts Applications behind its authenticating proxy, which
passes the signed-in user to the application in a request header. Review marks
and approvals record that user and are refused when no identity is present;
they are never recorded anonymously.

``GET /v1/whoami`` reports which identity header (by name, never other header
values) this deployment receives, so the mechanism can be confirmed in place.
HELIOS_DS_DEV_USER sets an identity for local development only.
"""

import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

# Checked in order; the first non-empty one is the user.
IDENTITY_HEADERS = (
    "remote-user",
    "x-remote-user",
    "x-forwarded-user",
    "x-auth-request-user",
    "x-auth-request-email",
    "x-forwarded-email",
)

router = APIRouter()


def current_user(request: Request) -> Optional[str]:
    for header in IDENTITY_HEADERS:
        value = request.headers.get(header, "").strip()
        if value:
            return value
    return os.environ.get("HELIOS_DS_DEV_USER") or None


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=(
                "no authenticated Workbench user on this request; review actions are "
                "never recorded anonymously (see GET /v1/whoami)"
            ),
        )
    return user


class WhoAmI(BaseModel):
    user: Optional[str]
    identity_header: Optional[str]
    headers_seen: List[str]  # header names only


@router.get("/whoami", response_model=WhoAmI)
def whoami(request: Request) -> WhoAmI:
    header = next((h for h in IDENTITY_HEADERS if request.headers.get(h, "").strip()), None)
    return WhoAmI(
        user=current_user(request),
        identity_header=header or ("HELIOS_DS_DEV_USER" if current_user(request) else None),
        headers_seen=sorted(request.headers.keys()),
    )
