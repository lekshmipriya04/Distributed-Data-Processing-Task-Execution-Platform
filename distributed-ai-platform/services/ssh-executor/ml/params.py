"""Federated averaging for any JSON-shaped model.

Shared by the pure-Python and Ray paths so both produce identical models.
Imports only the standard library and numpy so Ray can ship it to workers by
value (see ray_orchestrator).
"""
from __future__ import annotations

from numbers import Number
from typing import Any, Optional

import numpy as np


def _is_number(value: Any) -> bool:
    return isinstance(value, Number) and not isinstance(value, bool)


def _merge(values: list, weights: np.ndarray, path: str) -> Any:
    first = values[0]
    if all(v == first for v in values[1:]):
        # Identical on every shard (architecture, labels, ints): keep exactly.
        return first
    if all(isinstance(v, dict) for v in values):
        keys = set(first)
        if any(set(v) != keys for v in values[1:]):
            raise ValueError(f"shards returned different keys at '{path or '/'}'")
        return {k: _merge([v[k] for v in values], weights, f"{path}/{k}") for k in first}
    if all(isinstance(v, (list, tuple)) for v in values):
        if any(len(v) != len(first) for v in values[1:]):
            raise ValueError(f"shards returned different lengths at '{path or '/'}'")
        try:
            stacked = np.asarray(values, dtype=np.float64)
        except (TypeError, ValueError):
            stacked = None
        if stacked is not None and stacked.ndim >= 2:
            flat = stacked.reshape(len(values), -1)
            return (weights @ flat).reshape(stacked.shape[1:]).tolist()
        return [_merge([v[i] for v in values], weights, f"{path}[{i}]") for i in range(len(first))]
    if all(_is_number(v) for v in values):
        return float(np.dot(weights, np.asarray(values, dtype=np.float64)))
    raise ValueError(f"shards disagree on a non-numeric value at '{path or '/'}'")


def average_params(results: list[tuple[Any, int]]) -> Optional[Any]:
    """Weighted average of ``(params, rows)`` pairs; weight = rows / total rows.

    Numbers (and lists/dicts of numbers, at any depth) are averaged. Values that
    are identical on every shard are kept unchanged. Returns None when no shard
    contributed rows.
    """
    valid = [(p, int(n)) for p, n in results if p is not None and int(n or 0) > 0]
    if not valid:
        return None
    counts = np.asarray([n for _, n in valid], dtype=np.float64)
    weights = counts / counts.sum()
    return _merge([p for p, _ in valid], weights, "")


def combine_metrics(parts: list[tuple[Optional[dict], int]]) -> dict:
    """Row-weighted mean of numeric metrics reported per shard."""
    valid = [(m, int(n)) for m, n in parts if isinstance(m, dict) and int(n or 0) > 0]
    if not valid:
        return {}
    total = sum(n for _, n in valid)
    keys = {k for m, _ in valid for k, v in m.items() if _is_number(v)}
    out: dict = {}
    for key in sorted(keys):
        pairs = [(float(m[key]), n) for m, n in valid if _is_number(m.get(key))]
        weight = sum(n for _, n in pairs)
        if weight:
            out[key] = round(sum(v * n for v, n in pairs) / weight, 6)
    out["n"] = total
    return out
