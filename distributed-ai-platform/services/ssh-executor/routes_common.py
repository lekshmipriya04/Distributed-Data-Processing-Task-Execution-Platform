"""Shared FastAPI dependencies for the ssh-executor routers.

Every endpoint requires an authenticated user (``get_current_user``) and every
resource is scoped to ``owner_id = user["sub"]``.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db_manager
from shared.common.auth import get_current_user


async def get_db_session() -> AsyncSession:
    db_manager = get_db_manager()
    async with db_manager.session() as session:
        yield session


# --- per-owner rate limiting --------------------------------------------
# In-process sliding window. Guards the expensive / abuse-prone endpoints
# (outbound SSH connects and remote code-exec batches) so a single account
# cannot hammer the service — important once it faces a public network. The
# service runs with a single worker, so this shared state is consistent.
_RATE_WINDOW_SECONDS = 60
_rate_state: dict[tuple[str, str], deque] = defaultdict(deque)


def rate_limit(bucket: str, max_calls: int):
    async def _dep(user: dict = Depends(get_current_user)) -> None:
        owner = user.get("sub") or "anonymous"
        key = (bucket, owner)
        now = time.monotonic()
        window = _rate_state[key]
        while window and now - window[0] > _RATE_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= max_calls:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({max_calls}/min); slow down",
            )
        window.append(now)

    return _dep
