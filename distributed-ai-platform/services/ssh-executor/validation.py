"""Execution request validation (pure, no DB) — the authorized-RCE guard.

Kept free of SQLAlchemy/models so it is unit-testable in isolation.
``execution_manager`` re-exports these. Runtime ids are checked against the
registry in :mod:`runtimes` and the operator's ``allowed_runtimes`` setting
(``["*"]`` = every registered runtime, including ``custom``).
"""
from __future__ import annotations

from typing import Iterable, Optional

import runtimes as runtime_registry

DEFAULT_ALLOWED_RUNTIMES = ("*",)
CUSTOM_RUNTIME = "custom"


def runtime_allowed(runtime: str, allowed_runtimes: Iterable[str]) -> bool:
    allowed = set(allowed_runtimes or ())
    return "*" in allowed or runtime in allowed


def validate_runtime(
    runtime: str,
    *,
    allowed_runtimes: Iterable[str] = DEFAULT_ALLOWED_RUNTIMES,
    custom_command: Optional[str] = None,
    custom_filename: Optional[str] = None,
) -> None:
    """Raise ``ValueError`` for an unknown, disallowed or malformed runtime."""
    if not runtime or runtime_registry.get_runtime(runtime) is None:
        raise ValueError(f"Unknown runtime '{runtime}'")
    if not runtime_allowed(runtime, allowed_runtimes):
        raise ValueError(
            f"Runtime '{runtime}' is not allowed on this platform "
            f"(allowed: {sorted(set(allowed_runtimes or ()))})"
        )
    if runtime == CUSTOM_RUNTIME:
        # Checks "{file}" in the command and the filename pattern.
        runtime_registry.validate_custom(custom_command, custom_filename)
    elif custom_command or custom_filename:
        raise ValueError("custom_command/custom_filename are only valid with runtime 'custom'")


def validate_execution_request(
    code: str,
    inputs: list,
    *,
    runtime: str = "python",
    allowed_runtimes: Iterable[str] = DEFAULT_ALLOWED_RUNTIMES,
    custom_command: Optional[str] = None,
    custom_filename: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    max_timeout_seconds: Optional[int] = None,
    max_code_bytes: int = 1_000_000,
    max_inputs: int = 10_000,
    max_input_bytes: Optional[int] = None,
) -> None:
    """Raise ``ValueError`` if the request is unsafe/invalid.

    Guards an authorized remote-code-execution surface: a known and allowed
    runtime (valid custom command/filename for ``custom``), non-empty code
    under the size cap, a bounded number of inputs each under the size cap, and
    a timeout no larger than the operator's maximum.
    """
    validate_runtime(
        runtime,
        allowed_runtimes=allowed_runtimes,
        custom_command=custom_command,
        custom_filename=custom_filename,
    )
    if not code or not code.strip():
        raise ValueError("Execution code is required")
    if len(code.encode("utf-8", errors="ignore")) > max_code_bytes:
        raise ValueError(f"Code exceeds the {max_code_bytes}-byte limit")
    if inputs is not None:
        if len(inputs) > max_inputs:
            raise ValueError(f"Too many inputs ({len(inputs)} > {max_inputs})")
        if max_input_bytes:
            for i, item in enumerate(inputs):
                if len((item or "").encode("utf-8", errors="ignore")) > max_input_bytes:
                    raise ValueError(f"Input #{i} exceeds the {max_input_bytes}-byte limit")
    if timeout_seconds is not None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
        if max_timeout_seconds is not None and timeout_seconds > max_timeout_seconds:
            raise ValueError(
                f"timeout_seconds {timeout_seconds} exceeds the maximum of {max_timeout_seconds}"
            )
