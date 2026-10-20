"""FastAPI dependencies: authentication, role-based authorization, rate limiting."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import UserRole
from app.core.security import decode_token
from app.db.session import get_session

oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/token")


@dataclass
class Principal:
    user_id: str
    role: str
    participant_id: uuid.UUID | None

    @property
    def actor(self) -> str:
        return f"{self.role}:{self.user_id}"

    @property
    def is_privileged(self) -> bool:
        return self.role in (UserRole.ADMIN.value, UserRole.GRID_OPERATOR.value)


async def current_principal(token: str = Depends(oauth2)) -> Principal:
    try:
        claims = decode_token(token)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    pid = claims.get("pid")
    return Principal(claims["sub"], claims["role"], uuid.UUID(pid) if pid else None)


def require_roles(*roles: UserRole):
    allowed = {r.value for r in roles}

    async def dep(p: Principal = Depends(current_principal)) -> Principal:
        if p.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires one of: {sorted(allowed)}")
        return p
    return dep


reader = require_roles(UserRole.ADMIN, UserRole.GRID_OPERATOR, UserRole.PARTICIPANT, UserRole.AUDITOR)
trader = require_roles(UserRole.ADMIN, UserRole.GRID_OPERATOR, UserRole.PARTICIPANT)
operator = require_roles(UserRole.ADMIN, UserRole.GRID_OPERATOR)
admin = require_roles(UserRole.ADMIN)
auditor = require_roles(UserRole.ADMIN, UserRole.AUDITOR, UserRole.GRID_OPERATOR)


def assert_can_act_for(p: Principal, participant_id: uuid.UUID) -> None:
    """PARTICIPANT principals may only act for themselves; operators/admins for anyone."""
    if p.is_privileged:
        return
    if p.participant_id != participant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot act on behalf of another participant")


class RateLimiter:
    """In-process sliding-window limiter keyed by client. For multi-instance deployments put the
    same logic behind Redis INCR/EXPIRE; the interface (`hit`) is deliberately tiny."""

    def __init__(self) -> None:
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


limiter = RateLimiter()


async def rate_limit_middleware(request: Request, call_next):
    from fastapi.responses import JSONResponse
    limit = get_settings().rate_limit_per_minute
    if limit > 0 and not request.url.path.startswith("/ws"):
        key = request.client.host if request.client else "anon"
        if request.url.path.startswith("/auth/"):
            if not limiter.hit("auth:" + key, max(limit // 10, 5)):
                return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        elif not limiter.hit(key, limit):
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
    return await call_next(request)


SessionDep = Depends(get_session)
get_db = get_session
get_current_user = current_principal
