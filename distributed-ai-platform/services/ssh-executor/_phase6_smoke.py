"""Phase 6 smoke test — partitioning + execution-request validation (offline, pure)."""
from __future__ import annotations

import partition_manager as PM
from validation import validate_execution_request


def test_roundrobin() -> None:
    shards = PM.partition_roundrobin(list(range(10)), 3)
    assert [len(s) for s in shards] == [4, 3, 3]
    assert sorted(x for s in shards for x in s) == list(range(10))  # no loss
    assert shards[0] == [0, 3, 6, 9]
    print("[ok] partition_roundrobin")


def test_chunks() -> None:
    shards = PM.partition_chunks(list(range(10)), 3)
    assert shards == [[0, 1, 2, 3], [4, 5, 6], [7, 8, 9]]
    print("[ok] partition_chunks")


def test_normalize_and_empty() -> None:
    assert PM.normalize_k(5, 10) == 5          # never more shards than items
    assert PM.normalize_k(0, 3) == 0
    assert PM.partition_roundrobin([], 4) == []
    assert PM.partition_chunks([1], 4) == [[1]]
    print("[ok] normalize_k + empty handling")


def test_by_size() -> None:
    items = [10, 1, 1, 1]
    bins = PM.partition_by_size(items, 2, cost=lambda x: x)
    assert len(bins) == 2
    # the big item sits alone; the three small ones balance into the other bin
    assert [10] in bins
    assert sorted(len(b) for b in bins) == [1, 3]
    print("[ok] partition_by_size (greedy LPT balances work, not count)")


def test_validation() -> None:
    # valid
    validate_execution_request("print(1)", ["a", "b"], runtime="python")
    for bad, kw in [
        (("", []), {}),                                   # empty code
        (("x" * 50, []), {"max_code_bytes": 10}),         # too big
        (("print(1)", []), {"runtime": "julia"}),         # bad runtime
        (("print(1)", [1, 2, 3]), {"max_inputs": 2}),     # too many inputs
    ]:
        try:
            validate_execution_request(bad[0], bad[1], **kw)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad} {kw}")
    print("[ok] validate_execution_request guards (empty/size/runtime/inputs)")


if __name__ == "__main__":
    test_roundrobin()
    test_chunks()
    test_normalize_and_empty()
    test_by_size()
    test_validation()
    print("\nALL PHASE 6 CHECKS PASSED")
