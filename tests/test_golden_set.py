"""Validation of the golden set and of the schema that guards it.

Two groups of tests:

* the real files in evals/golden/ parse and satisfy every cross-file invariant
* the schema actually rejects the mistakes it claims to reject

The second group matters while the set is still empty: a validator nothing has
been run through is an assumption, not a guarantee.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import yaml
from pydantic import ValidationError

from evals.schema import (
    GOLDEN_DIR,
    SLICE_TARGETS,
    GoldenFile,
    GoldenSetError,
    Status,
    load_golden_set,
)

# --------------------------------------------------------------------------
# The real files
# --------------------------------------------------------------------------


def test_golden_set_loads_and_is_internally_consistent():
    """Every slice parses; ids are unique; parity pairs are well formed."""
    load_golden_set()


@pytest.mark.parametrize("slice_name", sorted(SLICE_TARGETS))
def test_every_slice_file_exists(slice_name: str):
    assert (GOLDEN_DIR / f"{slice_name}.yaml").exists()


def test_no_case_exceeds_its_slice_target():
    """Targets are a ceiling, not just an aspiration — an oversized slice skews
    the metric weighting the spec assumes."""
    cases = load_golden_set()
    for slice_name, target in SLICE_TARGETS.items():
        have = sum(1 for c in cases if c.slice == slice_name)
        assert have <= target, f"{slice_name}: {have} cases exceeds target of {target}"


# --------------------------------------------------------------------------
# Schema behaviour
# --------------------------------------------------------------------------


def _case(**overrides) -> dict:
    base = {
        "id": "GS-A-001",
        "slice": "answerable",
        "language": "ar",
        "status": "drafted",
        "question": "سؤال",
        "provenance": {"source": "author"},
    }
    return base | overrides


def test_drafted_case_needs_no_labels():
    """H3 questions are written before ingestion — they must be storable."""
    parsed = GoldenFile.model_validate({"cases": [_case()]})
    assert parsed.cases[0].status is Status.DRAFTED
    assert parsed.cases[0].counts_in_metrics is False


def test_labeled_answerable_case_requires_gold_articles():
    with pytest.raises(ValidationError, match="requires gold_article_ids"):
        GoldenFile.model_validate({"cases": [_case(status="labeled")]})


def test_labeled_refusal_case_requires_a_pointer():
    with pytest.raises(ValidationError, match="requires expected_pointer"):
        GoldenFile.model_validate(
            {
                "cases": [
                    _case(
                        id="GS-R-001",
                        slice="refusal",
                        status="labeled",
                        refusal_category="gosi",
                    )
                ]
            }
        )


def test_verified_case_requires_a_citable_source():
    with pytest.raises(ValidationError, match="requires provenance.url"):
        GoldenFile.model_validate(
            {"cases": [_case(status="verified", gold_article_ids=[84])]}
        )


def test_id_prefix_must_match_slice():
    with pytest.raises(ValidationError, match="requires id prefix GS-R-"):
        GoldenFile.model_validate(
            {"cases": [_case(id="GS-A-002", slice="refusal", refusal_category="gosi")]}
        )


def test_unknown_field_is_rejected():
    """A typo'd key must fail rather than silently do nothing."""
    with pytest.raises(ValidationError):
        GoldenFile.model_validate({"cases": [_case(gold_articles=[84])]})


def test_official_provenance_requires_a_dated_url():
    with pytest.raises(ValidationError, match="requires a url"):
        GoldenFile.model_validate({"cases": [_case(provenance={"source": "mhrsd_faq"})]})

    with pytest.raises(ValidationError, match="requires provenance.retrieved"):
        GoldenFile.model_validate(
            {
                "cases": [
                    _case(provenance={"source": "mhrsd_faq", "url": "https://example.gov.sa"})
                ]
            }
        )


# --------------------------------------------------------------------------
# Calculator slice: money and dates
# --------------------------------------------------------------------------


def _calc_case(**overrides) -> dict:
    base = {
        "id": "GS-C-001",
        "slice": "calculator",
        "language": "ar",
        "status": "drafted",
        "question": "كم مكافأة نهاية الخدمة؟",
        "provenance": {"source": "author"},
        "inputs": {
            "monthly_wage": "12000.00",
            "start_date": "2020-03-01",
            "end_date": "2024-06-30",
            "termination_type": "resignation",
        },
    }
    return base | overrides


def test_quoted_money_parses_as_exact_decimal():
    parsed = GoldenFile.model_validate({"cases": [_calc_case()]})
    assert parsed.cases[0].inputs.monthly_wage == Decimal("12000.00")


def test_unquoted_money_in_yaml_is_rejected():
    """A bare YAML decimal becomes a float, which breaks exact-match scoring."""
    document = yaml.safe_load(
        """
        cases:
          - id: GS-C-001
            slice: calculator
            language: ar
            status: drafted
            question: "كم مكافأة نهاية الخدمة؟"
            provenance:
              source: author
            inputs:
              monthly_wage: 12000.50
              start_date: 2020-03-01
              end_date: 2024-06-30
              termination_type: resignation
        """
    )
    with pytest.raises(ValidationError, match="parsed as a float"):
        GoldenFile.model_validate(document)


def test_labeled_calculator_case_requires_an_expected_amount():
    with pytest.raises(ValidationError, match="requires an expected amount"):
        GoldenFile.model_validate({"cases": [_calc_case(status="labeled")]})


def test_end_date_must_follow_start_date():
    inputs = _calc_case()["inputs"] | {"start_date": "2024-06-30", "end_date": "2020-03-01"}
    with pytest.raises(ValidationError, match="must fall after start_date"):
        GoldenFile.model_validate({"cases": [_calc_case(inputs=inputs)]})


# --------------------------------------------------------------------------
# Cross-file invariants
# --------------------------------------------------------------------------


def test_duplicate_ids_are_rejected(tmp_path):
    _write_slices(tmp_path, answerable=[_case(), _case()])
    with pytest.raises(GoldenSetError, match="duplicate case ids: GS-A-001"):
        load_golden_set(tmp_path)


def test_parity_pair_must_span_two_languages(tmp_path):
    _write_slices(
        tmp_path,
        answerable=[
            _case(id="GS-A-001", pair_id="pair-1", language="ar"),
            _case(id="GS-A-002", pair_id="pair-1", language="ar"),
        ],
    )
    with pytest.raises(GoldenSetError, match="must link one 'ar' case and one 'en' case"):
        load_golden_set(tmp_path)


def test_dangling_parity_pair_is_rejected(tmp_path):
    """A half-written pair would silently vanish from the parity metric."""
    _write_slices(tmp_path, answerable=[_case(pair_id="pair-1")])
    with pytest.raises(GoldenSetError, match="links 1 case"):
        load_golden_set(tmp_path)


def test_well_formed_parity_pair_is_accepted(tmp_path):
    _write_slices(
        tmp_path,
        answerable=[
            _case(id="GS-A-001", pair_id="pair-1", language="ar"),
            _case(id="GS-A-002", pair_id="pair-1", language="en"),
        ],
    )
    assert len(load_golden_set(tmp_path)) == 2


def _write_slices(directory, **slices) -> None:
    for name in ("answerable", "calculator", "refusal"):
        payload = {"cases": slices.get(name, [])}
        (directory / f"{name}.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
        )
