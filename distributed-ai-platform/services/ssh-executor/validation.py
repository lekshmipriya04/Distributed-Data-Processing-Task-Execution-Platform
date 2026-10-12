"""Execution request validation (pure, no DB) — the authorized-RCE guard.

Kept dependency-free so it is unit-testable in isolation and importable without
pulling in SQLAlchemy/models. ``execution_manager`` re-exports these.
"""
from __future__ import annotations

from typing import Iterable

DEFAULT_ALLOWED_RUNTIMES = ("python", "spark", "ray")


def validate_execution_request(
    code: str,
    inputs: list,
    *,
    runtime: str = "python",
    allowed_runtimes: Iterable[str] = DEFAULT_ALLOWED_RUNTIMES,
    max_code_bytes: int = 1_000_000,
    max_inputs: int = 10_000,
) -> None:
    """Raise ``ValueError`` if the request is unsafe/invalid.

    Minimum guard for an authorized remote-code-execution surface: non-empty
    code, a size cap, an allowed runtime, and a shard-count cap.
    """
    if not code or not code.strip():
        raise ValueError("Execution code is required")
    if len(code.encode("utf-8", errors="ignore")) > max_code_bytes:
        raise ValueError(f"Code exceeds the {max_code_bytes}-byte limit")
    if runtime not in set(allowed_runtimes):
        raise ValueError(
            f"Unsupported runtime '{runtime}' (allowed: {sorted(allowed_runtimes)})"
        )
    if inputs is not None and len(inputs) > max_inputs:
        raise ValueError(f"Too many inputs ({len(inputs)} > {max_inputs})")
