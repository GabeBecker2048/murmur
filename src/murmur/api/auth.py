"""Bearer token on mutating routes. Reads need nothing (LAN/tailnet boundary)."""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


def require_token(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    token = request.app.state.provider.get().api.token
    if not token:
        raise HTTPException(503, "writes are disabled: MURMUR_API_TOKEN is not set on the server")
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(401, "bearer token required", headers={"WWW-Authenticate": "Bearer"})
    if not hmac.compare_digest(creds.credentials.encode(), token.encode()):
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})


def has_token(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> bool:
    """True when the request carries the valid API token (reads that unlock private data)."""
    token = request.app.state.provider.get().api.token
    if not token or creds is None or creds.scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(creds.credentials.encode(), token.encode())
