"""Schema and loader for the golden evaluation set.

The golden set is authored by hand and read by every eval run, so validation is
strict on purpose: unknown keys are rejected rather than ignored, monetary values
must arrive as strings, and a case may only claim to be labeled if it carries the
labels. See evals/golden/README.md for the field reference.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GOLDEN_DIR = Path(__file__).parent / "golden"

# Target sizes from the Phase 1 spec. Progress is reported against these; they are
# not enforced as failures while the set is being built.
SLICE_TARGETS = {"answerable": 35, "calculator": 15, "refusal": 20}
PARITY_PAIR_TARGET = 10


class Slice(StrEnum):
    ANSWERABLE = "answerable"
    CALCULATOR = "calculator"
    REFUSAL = "refusal"


class Language(StrEnum):
    AR = "ar"
    EN = "en"


class Status(StrEnum):
    """Lifecycle of a case. See README.md — this drives what counts in a run."""

    DRAFTED = "drafted"  # question only; excluded from metric runs
    LABELED = "labeled"  # labels present; counts toward retrieval metrics
    VERIFIED = "verified"  # checked against ingested current text; counts toward CI gates


class ProvenanceSource(StrEnum):
    AUTHOR = "author"
    MHRSD_FAQ = "mhrsd_faq"
    BOE_TEXT = "boe_text"
    TESTER = "tester"


class RefusalCategory(StrEnum):
    GOSI = "gosi"
    IMPLEMENTING_REGULATIONS = "implementing_regulations"
    DOMESTIC_WORKERS = "domestic_workers"
    IMMIGRATION_IQAMA = "immigration_iqama"
    QIWA_PROCEDURES = "qiwa_procedures"
    PRE_AMENDMENT = "pre_amendment"
    LEGAL_ADVICE = "legal_advice"
    OUT_OF_CORPUS = "out_of_corpus"


class TerminationType(StrEnum):
    EMPLOYER_TERMINATION = "employer_termination"
    RESIGNATION = "resignation"
    CONTRACT_EXPIRY = "contract_expiry"
    MUTUAL_AGREEMENT = "mutual_agreement"
    ARTICLE_80 = "article_80"
    ARTICLE_81 = "article_81"


def _money(value: object) -> Decimal:
    """Parse a monetary value, rejecting floats.

    YAML turns a bare 31250.50 into a float, and float noise breaks the exact-match
    comparison this slice is scored on. Quoting is enforced here rather than left to
    whoever is editing the file at the time.
    """
    if isinstance(value, float):
        raise ValueError(
            f"monetary value {value!r} parsed as a float — quote it in YAML "
            f'(e.g. "{value:.2f}") so exact-match comparison stays exact'
        )
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"not a valid decimal amount: {value!r}") from exc


class Provenance(BaseModel):
    """Where a question came from. Required on every case."""

    model_config = ConfigDict(extra="forbid")

    source: ProvenanceSource
    url: str | None = None
    retrieved: date | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _official_sources_need_a_dated_url(self) -> Provenance:
        official = {ProvenanceSource.MHRSD_FAQ, ProvenanceSource.BOE_TEXT}
        if self.source in official and not self.url:
            raise ValueError(f"provenance.source={self.source} requires a url")
        if self.url and not self.retrieved:
            # Official pages change without notice; an undated citation cannot be
            # audited later.
            raise ValueError("provenance.url requires provenance.retrieved (the date read)")
        return self


class CalculatorInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_wage: Decimal
    start_date: date
    end_date: date
    termination_type: TerminationType

    _wage = field_validator("monthly_wage", mode="before")(_money)

    @model_validator(mode="after")
    def _dates_are_ordered(self) -> CalculatorInputs:
        if self.end_date <= self.start_date:
            raise ValueError(f"end_date {self.end_date} must fall after start_date {self.start_date}")
        return self


class CalculatorExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal
    currency: Literal["SAR"] = "SAR"

    _amount = field_validator("amount", mode="before")(_money)


class _BaseCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^GS-[ACR]-\d{3}$")
    language: Language
    status: Status
    question: str = Field(min_length=1)
    provenance: Provenance
    pair_id: str | None = None
    notes: str | None = None

    @property
    def counts_in_metrics(self) -> bool:
        """Drafted cases are excluded from runs — they carry no labels to score."""
        return self.status is not Status.DRAFTED

    @model_validator(mode="after")
    def _id_prefix_matches_slice(self) -> _BaseCase:
        expected = {"answerable": "A", "calculator": "C", "refusal": "R"}[self.slice]
        actual = self.id.split("-")[1]
        if actual != expected:
            raise ValueError(f"{self.id}: slice {self.slice!r} requires id prefix GS-{expected}-")
        return self

    @model_validator(mode="after")
    def _verified_cases_cite_a_source(self) -> _BaseCase:
        if self.status is Status.VERIFIED and not self.provenance.url:
            raise ValueError(
                f"{self.id}: status=verified requires provenance.url — "
                f"'verified' means checked against a citable source, not recalled"
            )
        return self


class AnswerableCase(_BaseCase):
    slice: Literal["answerable"]
    gold_article_ids: list[int] = Field(default_factory=list)
    answer_key: str | None = None

    @model_validator(mode="after")
    def _labeled_cases_carry_labels(self) -> AnswerableCase:
        if self.counts_in_metrics and not self.gold_article_ids:
            raise ValueError(
                f"{self.id}: status={self.status} requires gold_article_ids "
                f"(use status=drafted until the corpus is ingested)"
            )
        return self


class CalculatorCase(_BaseCase):
    slice: Literal["calculator"]
    inputs: CalculatorInputs
    expected: CalculatorExpectation | None = None
    gold_article_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _labeled_cases_carry_an_expected_amount(self) -> CalculatorCase:
        if self.counts_in_metrics and self.expected is None:
            raise ValueError(
                f"{self.id}: status={self.status} requires an expected amount "
                f"(use status=drafted until the number is derived from the current text)"
            )
        return self


class RefusalCase(_BaseCase):
    slice: Literal["refusal"]
    refusal_category: RefusalCategory
    expected_pointer: str | None = None

    @model_validator(mode="after")
    def _labeled_cases_point_somewhere(self) -> RefusalCase:
        if self.counts_in_metrics and not self.expected_pointer:
            raise ValueError(
                f"{self.id}: status={self.status} requires expected_pointer — "
                f"a refusal must redirect somewhere real, not dead-end"
            )
        return self


Case = Annotated[
    Union[AnswerableCase, CalculatorCase, RefusalCase],
    Field(discriminator="slice"),
]


class GoldenFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[Case] = Field(default_factory=list)


class GoldenSetError(Exception):
    """Raised for problems spanning more than one case or file."""


def load_file(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return GoldenFile.model_validate(raw).cases


def load_golden_set(directory: Path = GOLDEN_DIR) -> list[Case]:
    """Load and validate every slice, including cross-file integrity checks."""
    cases: list[Case] = []
    for name in ("answerable", "calculator", "refusal"):
        path = directory / f"{name}.yaml"
        if not path.exists():
            raise GoldenSetError(f"missing golden set file: {path}")
        cases.extend(load_file(path))

    _check_unique_ids(cases)
    _check_parity_pairs(cases)
    return cases


def _check_unique_ids(cases: list[Case]) -> None:
    seen: dict[str, int] = {}
    for case in cases:
        seen[case.id] = seen.get(case.id, 0) + 1
    duplicates = sorted(cid for cid, count in seen.items() if count > 1)
    if duplicates:
        raise GoldenSetError(f"duplicate case ids: {', '.join(duplicates)}")


def _check_parity_pairs(cases: list[Case]) -> None:
    """A pair_id must link exactly one Arabic and one English case of one slice.

    The parity slice measures whether an English question retrieves as well as its
    Arabic twin. A malformed pair silently drops out of that metric, so it fails
    loudly here instead.
    """
    pairs: dict[str, list[Case]] = {}
    for case in cases:
        if case.pair_id:
            pairs.setdefault(case.pair_id, []).append(case)

    problems: list[str] = []
    for pair_id, members in sorted(pairs.items()):
        ids = ", ".join(sorted(c.id for c in members))
        if len(members) != 2:
            problems.append(f"{pair_id!r} links {len(members)} case(s) ({ids}), expected exactly 2")
            continue
        if {m.language for m in members} != {Language.AR, Language.EN}:
            problems.append(f"{pair_id!r} ({ids}) must link one 'ar' case and one 'en' case")
        if len({m.slice for m in members}) != 1:
            problems.append(f"{pair_id!r} ({ids}) links cases from different slices")

    if problems:
        raise GoldenSetError("malformed parity pairs:\n  " + "\n  ".join(problems))
