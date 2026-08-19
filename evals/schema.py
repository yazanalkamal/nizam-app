"""Schema and loader for the golden evaluation set.

The golden set is authored by hand and read by every eval run, so validation is
strict on purpose: unknown keys are rejected rather than ignored, monetary values
must arrive as strings, and a case may only claim to be labeled if it carries the
labels. See evals/golden/README.md for the field reference.

Pydantic notes for anyone reading this who hasn't used it:
  - `ConfigDict(extra="forbid")` makes an unrecognized YAML key an error instead
    of being silently ignored, so a typo fails loudly.
  - `model_validator(mode="after")` runs once every field is populated, so it can
    compare fields against each other. It must return self.
  - `field_validator(..., mode="before")` runs on the raw value BEFORE pydantic
    converts it — the only moment a float is still recognisable as a float.
  - `Annotated[Union[...], Field(discriminator="slice")]` is a tagged union:
    pydantic reads `slice` first and validates against only that model, which
    makes error messages point at the real problem.
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


class Topic(StrEnum):
    """Subject area, used for coverage reporting.

    WAGE_DEFINITION and CONTRACT_VS_LAW were added after the first research pass:
    both turned out to be among the most common real questions, and both sit
    inside topics already in scope rather than opening a new corpus area.
    """

    EOSB = "eosb"
    RESIGNATION_TERMINATION = "resignation_termination"
    NOTICE = "notice"
    PROBATION = "probation"
    ANNUAL_LEAVE = "annual_leave"
    WAGE_DEFINITION = "wage_definition"  # which allowances count toward the wage base
    CONTRACT_VS_LAW = "contract_vs_law"  # contract term contradicts the amended law
    OUT_OF_SCOPE = "out_of_scope"


class ProvenanceSource(StrEnum):
    AUTHOR = "author"
    MHRSD_FAQ = "mhrsd_faq"
    BOE_TEXT = "boe_text"
    TESTER = "tester"
    PUBLIC_FORUM = "public_forum"  # found in public discussion; needs a link


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


class CalculatorOutcome(StrEnum):
    """What the system is expected to do with a calculation question.

    Real questions usually arrive incomplete — people say "my service is 7 years"
    and never mention their salary. A model handed that will invent a wage and
    return a confident number, so asking for the missing parameter is a behavior
    the eval set has to measure, not just a nicety.
    """

    AMOUNT = "amount"  # enough information; expect an exact figure
    CLARIFICATION = "clarification"  # something is missing; expect a specific ask
    OUT_OF_SCOPE = "out_of_scope"  # e.g. wage change near termination; expect a refusal


class MissingParameter(StrEnum):
    MONTHLY_WAGE = "monthly_wage"
    START_DATE = "start_date"
    END_DATE = "end_date"
    SERVICE_DURATION = "service_duration"
    TERMINATION_TYPE = "termination_type"


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
    def _cited_sources_need_a_dated_url(self) -> Provenance:
        needs_url = {
            ProvenanceSource.MHRSD_FAQ,
            ProvenanceSource.BOE_TEXT,
            ProvenanceSource.PUBLIC_FORUM,
        }
        if self.source in needs_url and not self.url:
            raise ValueError(f"provenance.source={self.source} requires a url")
        if self.url and not self.retrieved:
            # Pages change without notice; an undated citation cannot be audited.
            raise ValueError("provenance.url requires provenance.retrieved (the date read)")
        return self


class CalculatorInputs(BaseModel):
    """Parameters as the question actually supplies them.

    Every field is optional because real questions are incomplete — that
    incompleteness is the thing being tested, not a defect in the case.
    """

    model_config = ConfigDict(extra="forbid")

    monthly_wage: Decimal | None = None
    start_date: date | None = None
    end_date: date | None = None
    stated_duration: str | None = None  # verbatim, e.g. "٦ سنين ونص" — no dates given
    termination_type: TerminationType | None = None

    # Applies _money to monthly_wage before pydantic coerces it. See module docstring.
    _wage = field_validator("monthly_wage", mode="before")(_money)

    @model_validator(mode="after")
    def _dates_are_ordered(self) -> CalculatorInputs:
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValueError(
                f"end_date {self.end_date} must fall after start_date {self.start_date}"
            )
        return self

    def supplied(self, parameter: MissingParameter) -> bool:
        return getattr(self, parameter.value) is not None


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
    topic: Topic | None = None
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
    expects: CalculatorOutcome
    inputs: CalculatorInputs
    expected_amount: CalculatorExpectation | None = None
    missing_parameters: list[MissingParameter] = Field(default_factory=list)
    gold_article_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _amount_cases_carry_an_amount(self) -> CalculatorCase:
        if self.expects is not CalculatorOutcome.AMOUNT:
            if self.expected_amount is not None:
                raise ValueError(
                    f"{self.id}: expects={self.expects} must not carry an expected_amount"
                )
            return self
        if self.counts_in_metrics and self.expected_amount is None:
            raise ValueError(
                f"{self.id}: status={self.status} with expects=amount requires "
                f"expected_amount (use status=drafted until the number is derived "
                f"from the current text)"
            )
        return self

    @model_validator(mode="after")
    def _clarification_cases_name_what_is_missing(self) -> CalculatorCase:
        if self.expects is not CalculatorOutcome.CLARIFICATION:
            if self.missing_parameters:
                raise ValueError(
                    f"{self.id}: missing_parameters only applies when expects=clarification"
                )
            return self
        if not self.missing_parameters:
            raise ValueError(
                f"{self.id}: expects=clarification requires missing_parameters — "
                f"the case has to say which question the system should ask back"
            )
        # A case claiming the wage is missing while supplying one tests nothing.
        contradictions = [p.value for p in self.missing_parameters if self.inputs.supplied(p)]
        if contradictions:
            raise ValueError(
                f"{self.id}: listed as missing but supplied in inputs: "
                f"{', '.join(contradictions)}"
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
