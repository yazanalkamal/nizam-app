"""Print golden-set progress against the Phase 1 targets.

Usage:  python -m evals.golden_report

Informational only — this never fails a build. Structural validity is enforced by
tests/test_golden_set.py; this answers "how much of the set exists, how much of it
actually counts, and where did it come from".
"""

from __future__ import annotations

import sys
from collections import Counter
from urllib.parse import urlparse

from evals.schema import (
    PARITY_PAIR_TARGET,
    SLICE_TARGETS,
    CalculatorOutcome,
    GoldenSetError,
    Language,
    Status,
    Topic,
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

    total = len(cases)
    scoring = sum(1 for c in cases if c.counts_in_metrics)
    print(f"\ntotal cases: {total}   scoring in metric runs: {scoring}")
    for status in Status:
        print(f"  {status.value:<10} {by_status.get(status, 0)}")

    _print_topics(cases)
    _print_calculator_outcomes(cases)
    _print_sources(cases)

    if total and scoring == 0:
        print("\nAll cases are still 'drafted' — labels come after corpus ingestion.")
    return 0


def _print_topics(cases) -> None:
    by_topic = Counter(c.topic for c in cases if c.topic)
    untagged = sum(1 for c in cases if not c.topic)
    print("\ntopic coverage")
    for topic in Topic:
        count = by_topic.get(topic, 0)
        marker = "  " if count else " !"  # a topic with no cases is a coverage hole
        print(f" {marker} {topic.value:<24} {count}")
    if untagged:
        print(f"    {'(untagged)':<24} {untagged}")


def _print_calculator_outcomes(cases) -> None:
    calc = [c for c in cases if c.slice == "calculator"]
    if not calc:
        return
    by_outcome = Counter(c.expects for c in calc)
    print("\ncalculator expected outcomes")
    for outcome in CalculatorOutcome:
        print(f"    {outcome.value:<16} {by_outcome.get(outcome, 0)}")
    if not by_outcome.get(CalculatorOutcome.AMOUNT):
        print("    ! no exact-amount cases yet — these must be authored by hand")


def _print_sources(cases) -> None:
    """Source concentration is a validity risk, so it is shown every run.

    A golden set drawn from one website measures that website's community, not
    the user population, and the resulting metrics inherit the bias silently.
    """
    by_source = Counter(c.provenance.source for c in cases)
    print("\nprovenance")
    for source, count in by_source.most_common():
        print(f"    {source.value:<16} {count}")

    domains = Counter(
        urlparse(c.provenance.url).netloc for c in cases if c.provenance.url
    )
    if not domains:
        return
    print("\n  source domains")
    total_linked = sum(domains.values())
    for domain, count in domains.most_common():
        share = count / total_linked
        flag = "  <-- concentration risk" if share > 0.5 and total_linked > 5 else ""
        print(f"    {domain:<28} {count:>3}  ({share:>4.0%}){flag}")


if __name__ == "__main__":
    raise SystemExit(main())
