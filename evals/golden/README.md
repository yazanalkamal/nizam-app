# Golden set — format

The golden set is the project's scoreboard. It is built **before** the retrieval
pipeline: every component downstream exists to move these numbers.

Three files, one per slice:

| File | Slice | n (target) | Measures |
|---|---|---|---|
| `answerable.yaml` | `answerable` | 35 | recall@5, MRR, LLM-judge grade |
| `calculator.yaml` | `calculator` | 15 | exact match, no judge |
| `refusal.yaml` | `refusal` | 20 | refusal recall |

The **parity slice** (10 Arabic/English pairs) is not a fourth file. A parity pair
is any two cases linked by the same `pair_id` — one `ar`, one `en`, same slice.
Pairs live in whichever file their slice belongs to.

Validate at any time:

```
.venv/Scripts/python -m pytest tests/test_golden_set.py -q     # structure, must pass
.venv/Scripts/python -m evals.golden_report                    # progress vs targets
```

---

## The status lifecycle

This is the part to understand before writing anything.

Questions are written **before the corpus is ingested**, so a new case cannot yet
name the article that answers it. The schema models that explicitly rather than
letting half-finished cases masquerade as real ones:

| Status | Means | Requires | Counts in evals |
|---|---|---|---|
| `drafted` | The question exists. Nothing is labeled. | question + provenance | **No** |
| `labeled` | Gold article IDs (and for calculator cases, the expected number) are filled in. | + `gold_article_ids` / `expected` | Yes — retrieval metrics |
| `verified` | Cross-checked against the **ingested current text**, post-M/44. | + `provenance.url` | Yes — including hard CI thresholds |

A `drafted` case is invisible to metric runs. That is deliberate: an unlabeled
question silently scoring 0 for recall would make the pipeline look broken, and an
unlabeled question silently *skipped* without a status field would make it look
better than it is. The status makes the distinction explicit and countable.

Nothing reaches `verified` from memory. Not yours, not a model's, not a law-firm
blog post's. The Labor Law was materially amended by Royal Decree M/44 (effective
Feb 2025); pre-amendment knowledge is presumed wrong. `verified` means someone
opened the ingested article text and looked.

---

## Fields

Every case, regardless of slice:

```yaml
- id: GS-A-001            # GS-<A|C|R>-NNN, prefix must match the slice
  slice: answerable       # answerable | calculator | refusal
  language: ar            # ar | en
  status: drafted         # drafted | labeled | verified
  question: "..."         # the question exactly as a user would type it
  topic: eosb             # see the topic list below; optional but used for coverage
  pair_id: null           # set to a shared string to link an ar/en pair
  notes: null             # free text, optional
  provenance:
    source: author        # author | mhrsd_faq | boe_text | tester | public_forum
    url: null             # required for mhrsd_faq, boe_text, public_forum
    retrieved: null       # date the URL was read; required with url
    note: null
```

Long questions read better as a literal block, which also avoids escaping quotes:

```yaml
  question: |-
    نص السؤال هنا كما كتبه صاحبه
```

**Topics** — `eosb`, `resignation_termination`, `notice`, `probation`,
`annual_leave`, `wage_definition`, `contract_vs_law`, `out_of_scope`.

The last two were added after the first research pass showed they were among the
most common real questions. `wage_definition` is which allowances count toward
the wage base; `contract_vs_law` is a contract term that contradicts the amended
law. Both sit inside topics already in scope — no new corpus area.

`extra` fields are forbidden — a typo'd key fails validation rather than being
silently ignored.

### `answerable` cases

```yaml
  gold_article_ids: [84]  # required once status is labeled/verified
  answer_key: "..."       # what a correct answer must convey, from the source
```

### `calculator` cases

Every calculator case declares what the system is expected to *do*:

| `expects` | Meaning | Requires |
|---|---|---|
| `amount` | Enough information given; produce an exact figure | `expected_amount` once labeled |
| `clarification` | Something is missing; ask for it | `missing_parameters` |
| `out_of_scope` | e.g. wage change near termination; say so | neither |

```yaml
  expects: amount
  inputs:
    monthly_wage: "12000.00"        # STRING, not a bare decimal — see below
    start_date: 2020-03-01
    end_date: 2024-06-30
    stated_duration: null           # verbatim, when the user gives no dates
    termination_type: resignation   # employer_termination | resignation |
                                    # contract_expiry | mutual_agreement |
                                    # article_80 | article_81
  expected_amount:
    amount: "31250.00"              # STRING. Exact match, no tolerance.
    currency: SAR
  gold_article_ids: [84, 85]
```

A clarification case instead names what is missing, and validation rejects it if
`inputs` actually supplies one of them — a case claiming the wage is missing while
supplying a wage tests nothing:

```yaml
  expects: clarification
  inputs:
    stated_duration: "٦ سنين ونص"   # what the user actually wrote
    termination_type: resignation
  missing_parameters: [monthly_wage]
```

**Why this type exists.** Of ~40 real questions collected in the first research
pass, not one supplied a wage, both dates and a termination type together. People
write "خدمتي 7 سنوات" and never mention their salary. A model handed that will
invent a wage and answer confidently, so asking for the missing parameter is
behavior the eval set has to measure.

**Quote decimal money.** YAML parses a bare `31250.50` as a float, and float noise
breaks exact-match comparison. The validator rejects any monetary value arriving
as a float. Whole numbers (`12000`) are exact and are accepted unquoted, though
quoting everything is one less rule to remember.

### `refusal` cases

```yaml
  refusal_category: gosi   # gosi | implementing_regulations | domestic_workers |
                           # immigration_iqama | qiwa_procedures | pre_amendment |
                           # legal_advice | out_of_corpus
  expected_pointer: "..."  # the official source the refusal should redirect to
```

---

## Worked example

**Illustrative formatting only.** The article IDs below are placeholders copied
from the project notes; every one of them must be checked against the ingested
text before any case reaches `verified`.

```yaml
cases:
  - id: GS-R-001
    slice: refusal
    language: ar
    status: labeled
    question: "كم مكافأة نهاية الخدمة اللي بتوصلني من التأمينات الاجتماعية؟"
    pair_id: pair-gosi-eosb
    notes: >
      The signature trap: end-of-service and GOSI are different systems under
      different laws. Must refuse the GOSI half by name rather than answering
      around it or blending the two.
    provenance:
      source: author
      url: null
      retrieved: null
      note: "Written before corpus ingestion."
    refusal_category: gosi
    expected_pointer: "GOSI (التأمينات الاجتماعية) — gosi.gov.sa"

  - id: GS-R-002
    slice: refusal
    language: en
    status: labeled
    question: "How much end-of-service pay will I get from social insurance?"
    pair_id: pair-gosi-eosb      # same pair_id, other language → a parity pair
    notes: null
    provenance:
      source: author
      url: null
      retrieved: null
      note: null
    refusal_category: gosi
    expected_pointer: "GOSI (التأمينات الاجتماعية) — gosi.gov.sa"
```

---

## Writing the first 20

Author homework H3: **write them in Arabic, without opening the corpus.**

The exercise is a test of the corpus choice, not of the schema. Questions derived
from reading the law will be answerable by construction — which tells you nothing.
Questions written from what people actually ask will include ones the corpus
cannot answer, and *that* is the finding worth having early.

Practical approach: draft them as plain text first, set `status: drafted` and
`source: author` for all of them, and leave every label empty. Labels come after
ingestion.
