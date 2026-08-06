# Management Regimes by Ownership Class — Unified Direction

One statement of which silvicultural regime each ownership class gets, why, and what
would have to be measured to change it. The machine-readable form is
[`config/management_regimes.yaml`](../config/management_regimes.yaml); this note is the
reasoning behind it.

**Vocabulary: LETO, not Harris.** The config is keyed on the **LETO ownership classes**
carried by `FVS_StandInit.csv` — the table the FVS runs actually consume — not on the
Harris RDS-2025-0045 raster values. The two systems use the same column name (`OWN_CODE`)
with different meanings, which is a live bug ([#20](#the-own_code-collision)). The Harris
raster remains the per-pixel ownership source and is crosswalked, never substituted.

---

## Source

`r2://artemis-r2/data/20260804_095846_Hard_Ownership_Boundaries/` — a LETO FVS database
run, 2026-08-04. Confirmed against `Inputs/FVS_StandInit.csv` (57,527 stands, 1,052,306
acres, 9,199,539 tree records, variant SN, FIPS 12, inventory year 2022, 529 retained
unique `PLT_CN` donor plots).

The stand table carries **two independent axes**, which is exactly the structure the
precedence ladder needs:

| Axis | Columns | Values |
|---|---|---|
| Ownership | `OWN_CODE`, `OWN_TYPE` | 0 Unknown, 1 Private, 2 Corporate, 3 Federal, 4 State, 5 County, 6 NGO, 7 Other |
| Management | `MGMT_CLASS`, `MGMT_TYPE` | 0 Upland, 1 Riparian |

`DB_GROUP` (the 9 FVS database groups) is **not** a third vocabulary — it is
`OWN_TYPE` for upland stands and `Riparian` for every riparian stand regardless of owner.
Verified: the identity holds for all 39,824 upland and all 17,703 riparian stands. It is a
run-packaging key for splitting FVS databases, and assigning regimes off it would silently
treat a geometry class as an ownership class.

There is **no NWOS dataset on R2**. The only `RDS-*` directories in the bucket are
`RDS-2025-0045` (Harris ownership), `RDS-2025-0031`/`RDS-2025-0032` (TreeMap). The LETO
run above is the ownership vocabulary the project actually has.

---

## The direction, in one table

Riparian geometry overrides everything below it. Acreages are **upland** stands only,
since riparian stands are taken by rank-1 precedence before ownership applies. Year values
are offsets from the inventory year (2022).

| LETO class | code | Upland acres | Default regime | TPO group | LAMPS MHA |
|---|---:|---:|---|---|---|
| `unknown` | 0 | 2,122 | thin_from_below (+10, ≤8″, 35%) | *uncapped* | — |
| `private` | 1 | 269,312 | thin_from_below (+10, ≤8″, 35%) | Private | — |
| `corporate` | 2 | 212,814 | pine → plantation_rotation (thin +15, cc +30); else clearcut (+30) | Private | industrial |
| `federal` | 3 | 213,546 | selection_harvest (+10→+40, every 10 yr, 20%) | Federal (NF) | public |
| `state` | 4 | 74,398 | selection_harvest | Other public | public |
| `county` | 5 | 3,875 | selection_harvest | Other public | public |
| `ngo` | 6 | 15,508 | selection_harvest *(proposed)* | Private | public |
| `other` | 7 | 234,245 | thin_from_below *(holding position)* | *uncapped* | — |
| **riparian** | — | 26,485 | **no_management, unconditional** | — | never scheduled |

---

## The `OWN_CODE` collision

**This is the most important thing in this note.** Two ownership code systems share one
column name and agree on nothing:

| Code | LETO `OWN_TYPE` | Harris class |
|---:|---|---|
| 0 | Unknown | unknown_forest |
| 1 | Private | non_forest |
| 2 | Corporate | water |
| 3 | **Federal** | **family_forest** |
| 4 | **State** | **corporate_forest** |
| 5 | County | tribal_forest |
| 6 | NGO | federal_forest |
| 7 | Other | state_forest |
| 8 | — | local_forest |

Only code 0 means the same thing in both. `pipeline/s3_management/regime_assignment.py`
reads `OWN_CODE` and interprets it as Harris. `FVS_StandInit.csv` — the table that feeds
the FVS runs — populates that column with LETO codes. Running the current assignment code
against the real stand table gives every stand the wrong regime, and the failure is
silent because both systems are small integers in the same range:

- Federal stands (LETO 3) get `thin_from_below`, the family-forest regime
- State stands (LETO 4) get `plantation_rotation` or `clearcut`, the corporate regime
- County stands (LETO 5) get the tribal branch
- NGO stands (LETO 6) get the federal regime

`tests/test_config.py::test_leto_own_code_fed_to_assignment_code_gives_the_wrong_regime`
asserts the *current wrong behaviour* on purpose, so the bug cannot be fixed by accident
without the test failing and being deliberately removed. The docstring at
`regime_assignment.py:15` describing "the LETO / RDS-2025-0045 lookup" is where the two
systems got conflated — they are different lookups.

---

## Four decisions worth arguing with

### 1. `other` is the biggest open problem

15,190 upland stands and 234,245 acres — 22% of upland acreage, the second-largest class —
sit in a bucket with no owner semantics. Every harvest total this pipeline produces is
sensitive to what it turns out to be.

The light-thin default is a holding position chosen so the class cannot dominate a total,
not a claim about management. It is left **uncapped** on the TPO side deliberately:
assigning 22% of the landscape to an owner group's volume ceiling before knowing what it
is would corrupt that group's constraint.

The parcel summary (`data/Owner_Summaries_FL5Co_Parcels.txt`) suggests the decomposition:
`Other-Misc` is 702,605 parcel acres at a 1.61 ac median, plus `Other-State`,
`Other-Private`, `Other-County`, `Other-Federal`. That reads as mostly small
non-industrial parcels plus the non-forest-classified holdings of owners already named. If
most of it is really private, it belongs in `private` and under the Private cap.

### 2. NGO is why LETO wins over Harris

Harris has no NGO class at all — those 15,508 acres land in family or corporate by
default, and a land trust gets a commercial thin. LETO names them. This is the single
clearest argument for keying regimes off the stand table rather than the raster, and it
generalizes: the raster's seven classes are a national product, while LETO's classes come
from the parcels in this AOI.

`ngo` is the one class marked `assignment_status: proposed` on its merits — the direction
(public conservative) differs from what the code can currently produce (private light
thin), because the code has no NGO branch to reach.

### 3. Corporate hides the distinction that matters most

LETO collapses REIT, TIMO, and operating company into one `Corporate` class. The parcel
summary keeps them apart — Forest-REIT 179,723 ac, Forest-TIMO 45,848 ac, Forest-Company
45,235 ac — and they do not manage alike. REIT and TIMO are the most rotation-driven
owners in the AOI, which is exactly the behaviour `plantation_rotation` encodes.

Splitting this class is the highest-value refinement available, and unusually cheap: the
data already exists one layer upstream.

### 4. `county` is the most likely over-harvest

Largely parks and watershed land, given the public schedule: a 20% selection cut every
decade for 40 years on land that may never be entered. Left on the public default rather
than quietly set to `no_management`, because guessing downward is still guessing. The
criterion: if these stands show a Tree Removal rate near zero in LCMS 1985–2024, the
default flips. At 3,875 acres the acreage bounds the damage — unlike `other`.

---

## Riparian, as the data confirms it

17,703 of 57,527 stands (31%) are riparian, carrying 26,485 acres — **2.5% of the
acreage**. Mean riparian stand is 1.50 ac against 25.76 ac for upland.

The two-axis structure vindicates the precedence ladder: riparian is a `MGMT_CLASS`, not
an owner, and it crosses every ownership class (2,654 corporate riparian stands, 1,758
federal, 4,954 private, and so on). The config's rank-1 override matches how the source
data is actually shaped, and the `SMZ_Pct >= 50` test drops to a fallback for units that
predate LETO segmentation.

The sliver problem is real and visible here — 31% of stands for 2.5% of area — but it is a
delineation artifact for `pipeline/s3_management/sliver_merge.py` to handle, and it must
not be resolved by dissolving buffers into neighbours
([`methodology-directions.md`](methodology-directions.md) item 2 is explicit that buffer
polygons keep their own identity).

---

## What this costs in FVS runs

Run count is `unique(plot × regime × site-index bin)` (`PLAN.md` §4c). The LETO run
resolves 57,527 stands from **529 retained unique `PLT_CN` donors**, so the library is
keyed on donors, not stands. Five distinct parameterizations across every eligible set
gives an upper bound of **529 × 5 = 2,645 runs**.

That is the whole argument for keeping the regime library small and discrete. Splitting
`corporate` into REIT/TIMO/Company with distinct parameters, or giving each public class
its own numbers, multiplies this directly.

---

## Known gaps in the regimes themselves

- **`plantation_rotation` does not replant** ([#18](https://github.com/charlesmerritt/artemis-model/issues/18)) — no `PLANT`/`NATREGEN` keyword, so the stand regenerates by FVS default after clearcut. Blocked on [#17](https://github.com/charlesmerritt/artemis-model/issues/17).
- **Shelterwood is absent** — listed in `management-pipeline-plan.md` Step 3.1, unimplemented, same blocker.
- **Fixed entry years are a placeholder** for the fitted harvest model (`PLAN.md` §3c).
- **No tribal class in this AOI.** Harris carries one (value 5); LETO's FL 5-county run has
  none. Kept visible in `harris_classes_absent_from_leto` so the crosswalk is total in both
  directions rather than quietly lossy — it matters when the pipeline expands.

---

## Making it live

`regime_assignment.py` hardcodes the mapping *and* speaks the wrong vocabulary. Both are
fixed by the same change ([#16](https://github.com/charlesmerritt/artemis-model/issues/16),
[#20](https://github.com/charlesmerritt/artemis-model/issues/20)): have `assign_regime()`
read this config, key off `OWN_TYPE` (the string, which cannot collide) rather than a bare
integer, and take the riparian test from `MGMT_CLASS`.

Keying off the string is the durable fix. Two integer vocabularies in one column will
collide again; `"Federal"` and `"family_forest"` cannot be silently swapped.

Until then `tests/test_config.py` holds the line: classes marked
`assignment_status: current` must be reproduced by `assign_regime()` *through the Harris
crosswalk*, `proposed` classes must name what they supersede, and the collision itself is
pinned by its own test.

---

Related: [[management-pipeline-plan]] Step 3.2, [[methodology-directions]] item 2
(riparian) and item 4 (per-pixel regime, per-plot tree list), [[terminology]],
[[management_units]].
