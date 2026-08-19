"""Print golden-set progress against the Phase 1 targets.

Usage:  python -m evals.golden_report

Informational only — this never fails a build. Structural validity is enforced by
tests/test_golden_set.py; this just answers "how much of the set exists yet, and
how much of it actually counts".
"""

from __future__ import annotations

import sys
from collections import Counter

from evals.schema import (
    PARITY_PAIR_TARGET,
    SLICE_TARGETS,
    GoldenSetError,
    Language,
    Status,
    load_golden_set,
)


def main() -> int:
    try:
        cases = load_golden_set()
    except GoldenSetError as exc:
        print(f"golden set is invalid: {exc}", file=sys.stderr)
        return 1

    by_slice = Counter(c.slice for c in cases)
    by_status = Counter(c.status for c in cases)

    print(f"{'slice':<12} {'have':>6} {'target':>7} {'scoring':>8}")
    print("-" * 36)
    for name, target in SLICE_TARGETS.items():
        have = by_slice.get(name, 0)
        scoring = sum(1 for c in cases if c.slice == name and c.counts_in_metrics)
        print(f"{name:<12} {have:>6} {target:>7} {scoring:>8}")

    pairs = {c.pair_id for c in cases if c.pair_id}
    complete_pairs = sum(
        1
        for pid in pairs
        if {c.language for c in cases if c.pair_id == pid} == {Language.AR, Language.EN}
    )
    print("-" * 36)
    print(f"{'parity pairs':<12} {complete_pairs:>6} {PARITY_PAIR_TARGET:>7}")
    print()

    total = len(cases)
    scoring = sum(1 for c in cases if c.counts_in_metrics)
    print(f"total cases: {total}   scoring in metric runs: {scoring}")
    for status in Status:
        print(f"  {status.value:<10} {by_status.get(status, 0)}")

    if total and scoring == 0:
        print("\nAll cases are still 'drafted' — labels come after corpus ingestion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
