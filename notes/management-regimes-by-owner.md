# Management Regimes by Ownership Class — Unified Direction

One statement of which silvicultural regime each ownership class gets, why, and what
would have to be measured to change it. The machine-readable form is
[`config/management_regimes.yaml`](../config/management_regimes.yaml); this note is the
reasoning behind it.

**Status.** The config is a *direction*, not the executed rule.
`pipeline/s3_management/regime_assignment.py` still carries its own hardcoded copy of the
mapping. Tests in `tests/test_config.py` assert the two agree today and will fail if
either side moves without the other, so the direction cannot rot silently. Making the
assignment code read the YAML is a follow-up, deliberately not done here — see
[Making it live](#making-it-live).

---

## The problem this resolves

"Which regime does a state forest get?" had four answers in four files, and they did not
agree:

| Where | What it said |
|---|---|
| `PLAN.md` §3c | Seven forest ownership classes, **each its own class, no collapsing** |
| `PLAN.md` §4b | Six *policy* regimes (NIPF light, industrial pine, public conservative, …) |
| `regime_assignment.py` | Four classes **collapsed** into one `PUBLIC_OWNERS` branch |
| `config/tpo_targets.yaml` | Three owner groups (`Federal (NF)`, `Other public`, `Private`) |
| LAMPS scheduler plan | Two MHA groups (`industrial`, `public`), family unmapped |

Three of those are different owner vocabularies at different granularities (7 / 3 / 2),
and the fourth is a regime vocabulary that does not line up one-to-one with the five
templates actually implemented in `regime_templates.py`. Any of them can be right for its
own purpose; what was missing was the place where they are reconciled.

The config now holds all of it: one block per owner class carrying its default regime,
its eligible regime set, and its key in each of the other two vocabularies.

---

## The direction, in one table

Riparian geometry overrides everything below it — a buffer on corporate land is
unmanaged, not a plantation. Year values are offsets from the inventory year (2022).

| Owner class | Harris px | Default regime | TPO group | LAMPS MHA |
|---|---|---|---|---|
| `unknown_forest` | 0 | thin_from_below (+10, ≤8″, 35%) | *uncapped* | — |
| `family_forest` | 3 | thin_from_below (+10, ≤8″, 35%) | Private | — |
| `corporate_forest` | 4 | pine → plantation_rotation (thin +15, clearcut +30); else clearcut (+30) | Private | industrial |
| `tribal_forest` | 5 | selection_harvest (+10→+40, every 10 yr, 20%) | Other public | public |
| `federal_forest` | 6 | selection_harvest (+10→+40, every 10 yr, 20%) | Federal (NF) | public |
| `state_forest` | 7 | selection_harvest (+10→+40, every 10 yr, 20%) | Other public | public |
| `local_forest` | 8 | selection_harvest (+10→+40, every 10 yr, 20%) | Other public | public |

`non_forest` (1) and `water` (2) are masked out of the pipeline entirely and never
receive a regime.

---

## Three decisions worth arguing with

### 1. Separate classes, shared parameters

The four public classes carry identical parameters today. That is **not** the
`PUBLIC_OWNERS` collapse re-spelled — the difference is where the sameness lives.

Collapsing in code destroys the distinction: a tribal unit and a county park become the
same row and there is no place to put evidence when it arrives. Carrying seven classes
whose parameters currently coincide keeps every class addressable in the units table, in
the summaries, and in the config, so differentiating one is a parameter edit rather than
a refactor. `PLAN.md` §3c asks for the former; the current code does the latter.

So each class block carries a `differentiation` field stating what would have to be
measured for it to earn its own numbers. That is the honest version of "we don't know
yet" — it names the measurement instead of inventing a number that looks calibrated.

### 2. `local_forest` is the most likely over-harvest

County and municipal forest in the 5-county AOI is largely parks and watershed land where
commercial entry is rare. Giving it the public schedule means a 20% selection cut every
decade for 40 years on land that may never be entered at all.

It is left on the public default rather than quietly set to `no_management`, because
guessing downward is still guessing. The criterion is stated instead: if class-8 pixels
show a Tree Removal rate near zero in LCMS 1985–2024 over the AOI, the default becomes
`no_management`. That check is cheap and should be run before any harvest total from this
pipeline is reported.

### 3. `unknown_forest` stays out of `Private`

Class 0 is forest with unresolved ownership. The tempting move is to fold it into
`Private`, since private is ~92% of the AOI's TPO volume. That would attach the largest
cap in the table (66–71M cuft/yr) to pixels with no evidence behind them and make the
private harvest total unfalsifiable. It stays uncapped, reported on its own row, and held
to the light default so it cannot dominate a total by accident. The fix is to resolve the
pixels, not to tune their regime.

---

## What this costs in FVS runs

Run count is `unique(plot × regime × site-index bin)` (`PLAN.md` §4c). Across every
eligible set in the config there are **five** distinct parameterizations —
`no_management`, `thin_from_below`, `selection_harvest`, `plantation_rotation`,
`clearcut`. For the pilot's 693 plots that is an upper bound of **3,465 runs**, against
693 for the no-management baseline. Site index adds no multiplier while it is inherited
from the plot rather than predicted per pixel (`PLAN.md` §2d).

This is the reason the public classes share one parameter set. Three plausible-looking
variants of `selection_harvest` — one each for federal, state, and local — would cost
1,386 additional runs to encode a distinction we cannot currently defend.

---

## Known gaps in the regimes themselves

- **`plantation_rotation` does not replant.** No `PLANT`/`NATREGEN` keyword is emitted,
  so the stand regenerates by FVS default after the clearcut, which understates the next
  rotation on exactly the class where rotations matter most. Blocked on verifying the
  regeneration keyword field layout — the same constraint that keeps every regime built
  from the one verified `ThinDBH` keyword (`regime_templates.py` docstring).
- **Shelterwood is absent.** Listed in `management-pipeline-plan.md` Step 3.1, not
  implemented, for the same reason.
- **Fixed entry years are a placeholder for the harvest model.** `PLAN.md` §3c specifies
  a fitted model predicting P(harvest | features) applied pseudo-deterministically with a
  locked seed. Every entry year in this config is a stand-in until that model exists.
- **The riparian SMZ threshold is a delineation artifact.** `SMZ_Pct >= 50` is a fallback.
  Once buffers are emitted as their own units
  ([`methodology-directions.md`](methodology-directions.md) item 2), a unit is riparian by
  construction and the percentage test stops carrying the decision.

---

## Making it live

`regime_assignment.py` duplicates this mapping in Python. Two ways to close that:

1. **Loader** — `assign_regime()` reads `config/management_regimes.yaml`, resolves offsets
   against `inv_year`, and keeps the current signature. The config becomes the executed
   rule; the module keeps only the precedence logic and `is_pine`.
2. **Generated constants** — keep the module hardcoded, generate it from the config in CI.
   Cheaper to review, easy to forget to regenerate.

Option 1 is the direction. Until it lands, `tests/test_config.py` holds the two in sync:
classes marked `assignment_status: current` must be reproduced exactly by
`assign_regime()`, and any class marked `proposed` must name what it supersedes and match
that instead — so a config edit that code has not adopted fails loudly rather than
becoming a quiet lie.

---

Related: [[management-pipeline-plan]] Step 3.2 (the assignment step),
[[methodology-directions]] item 2 (riparian, non-negotiable) and item 4 (per-pixel regime,
per-plot tree list), [[terminology]] (unit vs. plot vs. stand), [[management_units]].
