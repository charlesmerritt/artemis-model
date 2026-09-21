# What the 2026-09-14 timing library shows about offsets

Research note · issue #51 · 2026-09-16

Every number below was recomputed from the committed artifacts on branch
`claude/gifted-ritchie-l9lsyq` (PR #48) under `weekly-artifact/2026-09-14/`, read with
`git show` and re-aggregated; nothing is quoted from the artifact's prose without a
corresponding recount. Where the artifact README and a CSV disagree the CSV wins and the
discrepancy is stated.

Scope of the evidence: the five-county north-Florida pilot (Baker, Columbia, Hamilton,
Suwanee, Union), 11,831 stands, 5,229 upland and 6,602 riparian, 878,137 acres carrying a
cutting prescription in the published plan.

---

## 1. Which offsets the annealer chose

`timing_offsets_chosen.csv` has four rows, one per offset. Its 4,998 stands are the plan's
cutting stands: 5,229 upland stands minus the 231 that chose `no_management`. The 6,602
riparian stands have a one-element library and never enter this table.

### Landscape totals

| Offset | Stands | % of stands | Acres | % of acres | Removed ft³ | % of volume |
|---|---:|---:|---:|---:|---:|---:|
| +0 | 1,158 | 23.17% | 211,805 | 24.1% | 531.0 M | 20.6% |
| +5 | 1,464 | 29.29% | 329,325 | 37.5% | 624.9 M | 24.2% |
| +10 | 1,266 | 25.33% | 153,996 | 17.5% | 534.9 M | 20.7% |
| +15 | 1,110 | 22.21% | 183,011 | 20.8% | 887.0 M | 34.4% |
| **Total** | **4,998** | | **878,137** | | **2,577.7 M** | |

**The plan does not cluster at offset 0.** Offset 0 takes 23.2% of stands against the 25%
a uniform draw would give, and it is the *second smallest* of the four groups. Against a
uniform null the stand counts give χ²(3) = 59.3 — a real preference, but the spread is over
all four start times, not a concentration on any one. Acres tilt to +5 (37.5%) and volume
tilts to +15 (34.4%): the delayed stands are the ones carrying the large removals, which is
what the even-flow term is buying.

Corroborating counts: `prescription_mix.csv` lists all 28 `base@+offset` variants as used
(every prescription appears at every offset), and `annealed_plan.csv` reproduces the same
four totals exactly.

### By prescription (stands chosen)

| Base prescription | +0 | +5 | +10 | +15 | Total | Modal | χ²(3) vs uniform |
|---|---:|---:|---:|---:|---:|---|---:|
| `family_light_thin` | 63 | 72 | 69 | 61 | 265 | +5 (27%) | 1.2 |
| `family_uneven_aged_selection` | 239 | **484** | 101 | 103 | 927 | +5 (52%) | 420.1 |
| `hardwood_clearcut_regen` | 227 | 206 | 174 | 245 | 852 | +15 (29%) | 13.1 |
| `pine_plantation_long_rotation` | 238 | 207 | 222 | **459** | 1,126 | +15 (41%) | 150.9 |
| `pine_plantation_short_rotation` | 70 | 54 | 47 | 67 | 238 | +0 (29%) | 5.9 |
| `public_selection_light` | 225 | 354 | **563** | 98 | 1,240 | +10 (45%) | 381.0 |
| `public_thin_restore` | 96 | 87 | 90 | 77 | 350 | +0 (27%) | 2.2 |
| **All** | **1,158** | **1,464** | **1,266** | **1,110** | **4,998** | +5 (29%) | 59.3 |

Three prescriptions are close to uniform (`family_light_thin`, `public_thin_restore`,
`pine_plantation_short_rotation`, all χ² < 6 on 3 df). Three are strongly shaped: the
repeating-entry prescriptions pick a phase (`family_uneven_aged_selection` at +5,
`public_selection_light` at +10) and the long pine rotation pushes its regeneration harvest
to +15. `hardwood_clearcut_regen` is mildly tilted to +15 despite +15 being the offset at
which it loses the most variants to the horizon rule (§2).

Acres and volume behind the same cells:

| Base prescription | acres +0 | +5 | +10 | +15 |
|---|---:|---:|---:|---:|
| `family_light_thin` | 926 | 1,811 | 3,794 | 2,076 |
| `family_uneven_aged_selection` | 64,196 | 225,124 | 22,217 | 7,402 |
| `hardwood_clearcut_regen` | 17,051 | 18,864 | 9,142 | 28,489 |
| `pine_plantation_long_rotation` | 79,750 | 29,180 | 68,600 | 123,370 |
| `pine_plantation_short_rotation` | 23,324 | 2,986 | 5,308 | 7,596 |
| `public_selection_light` | 3,758 | 20,469 | 22,767 | 97 |
| `public_thin_restore` | 22,800 | 30,892 | 22,169 | 13,982 |

| Base prescription | removed M ft³ +0 | +5 | +10 | +15 |
|---|---:|---:|---:|---:|
| `family_light_thin` | 0.1 | 0.2 | 0.6 | 0.2 |
| `family_uneven_aged_selection` | 113.3 | 365.9 | 35.8 | 8.5 |
| `hardwood_clearcut_regen` | 47.6 | 55.9 | 22.9 | 96.1 |
| `pine_plantation_long_rotation` | 268.8 | 114.1 | 379.7 | 743.9 |
| `pine_plantation_short_rotation` | 76.0 | 9.0 | 18.5 | 32.4 |
| `public_selection_light` | 8.8 | 63.5 | 65.5 | 0.1 |
| `public_thin_restore` | 16.4 | 16.3 | 11.9 | 5.7 |

`pine_plantation_long_rotation@+15` alone supplies 743.9 M ft³, 28.9% of the plan's
2,577.7 M ft³.

### By owner class (the seven Harris classes; five are present)

| Owner class | +0 | +5 | +10 | +15 | Total | % at +0 |
|---|---:|---:|---:|---:|---:|---:|
| `federal` | 119 | 105 | 127 | 69 | 420 | 28.3% |
| `local` | 127 | 229 | **369** | 63 | 788 | 16.1% |
| `private_family` | 409 | 651 | 272 | 392 | 1,724 | 23.7% |
| `private_industrial` | 359 | 331 | 303 | **478** | 1,471 | 24.4% |
| `state` | 144 | 148 | 195 | 108 | 595 | 24.2% |

Acres:

| Owner class | +0 | +5 | +10 | +15 | Total |
|---|---:|---:|---:|---:|---:|
| `federal` | 24,603 | 39,385 | 29,657 | 13,984 | 107,629 |
| `local` | 1,785 | 10,992 | 13,135 | 50 | 25,962 |
| `private_family` | 129,199 | 250,143 | 74,156 | 103,140 | 556,637 |
| `private_industrial` | 53,398 | 27,283 | 34,523 | 64,331 | 179,535 |
| `state` | 2,820 | 1,522 | 2,526 | 1,507 | 8,375 |

Removed volume (M ft³):

| Owner class | +0 | +5 | +10 | +15 | Total |
|---|---:|---:|---:|---:|---:|
| `federal` | 20.2 | 40.8 | 36.3 | 5.8 | 103.0 |
| `local` | 4.6 | 36.1 | 35.9 | 0.1 | 76.6 |
| `private_family` | 322.1 | 456.1 | 280.1 | 554.5 | 1,612.7 |
| `private_industrial` | 174.8 | 87.2 | 175.3 | 317.1 | 754.4 |
| `state` | 9.3 | 4.7 | 7.3 | 9.6 | 30.9 |

### By owner group (the three TPO budget groups)

| Owner group | +0 | +5 | +10 | +15 | Total | % at +0 |
|---|---:|---:|---:|---:|---:|---:|
| Federal (NF) | 119 | 105 | 127 | 69 | 420 | 28.3% |
| Other public | 271 | 377 | 564 | 171 | 1,383 | 19.6% |
| Private | 768 | 982 | 575 | 870 | 3,195 | 24.0% |

No owner class or group clusters at offset 0; the highest share at +0 is `federal` at 28.3%,
the lowest `local` at 16.1%. The owner-class pattern is mostly inherited from the menus,
since an owner class sees only its own prescriptions:

| Owner class | prescriptions used in the plan |
|---|---|
| `federal` | `public_selection_light` (171), `public_thin_restore` (249) |
| `local` | `public_selection_light` (730), `public_thin_restore` (58) |
| `private_family` | `family_light_thin` (265), `family_uneven_aged_selection` (927), `pine_plantation_long_rotation` (532) |
| `private_industrial` | `hardwood_clearcut_regen` (852), `pine_plantation_long_rotation` (381), `pine_plantation_short_rotation` (238) |
| `state` | `pine_plantation_long_rotation` (213), `public_selection_light` (339), `public_thin_restore` (43) |

`local`'s +10 mode is `public_selection_light`'s +10 mode (730 of its 788 stands are on that
prescription); `private_industrial`'s +15 mode is `pine_plantation_long_rotation` plus
`hardwood_clearcut_regen`, both of which peak at +15.

Every cutting stand had a real timing choice. `library_size` among the 4,998 cutting stands:
924 stands with 13 options (3 cutting prescriptions × 4 offsets + `no_management`), 3,222
with 9 (2 × 4 + 1), 751 with 5 (1 × 4 + 1), and 101 with 2–4 where the horizon rule removed
variants (`options_per_stand.csv` gives the same distribution over all 11,831 stands, with
6,602 at a single option).

---

## 2. The collapse accounting

`library_expansion.csv` is 112 rows = 28 distinct `(base_prescription, template,
resolved_params)` specifications × 4 offsets. A row records the shifted entry years, how many
entries the horizon rule dropped, and whether the variant collapsed.

Two rules from `config/management_regimes.yaml` govern it, and they are the config's own,
not the artifact's:

1. An entry resolving past `LAST_ENTRY_YEAR = INV_YEAR + HORIZON_YEARS = 2022 + 50 = 2072`
   is dropped (`make_timing_library.py:114-116`).
2. A variant that loses *every* entry "resolves to `no_management` for that stand" and is not
   published as an option, because it would duplicate the `no_management` already on the
   menu (`expand_library`, `make_timing_library.py:412ff`).

**Exactly six `(prescription, params, offset)` combinations collapsed. All six are
`hardwood_clearcut_regen` on the `clearcut` template — a single-entry prescription, so
dropping its one entry drops all of them. Each reports `entries_kept = 0`,
`entries_dropped = 1`.**

| # | Base prescription | Template | Resolved params | Offset | Shifted entry year | Kept | Dropped |
|---|---|---|---|---:|---:|---:|---:|
| 1 | `hardwood_clearcut_regen` | `clearcut` | `year=2062` | +15 | 2077 | 0 | 1 |
| 2 | `hardwood_clearcut_regen` | `clearcut` | `year=2067` | +10 | 2077 | 0 | 1 |
| 3 | `hardwood_clearcut_regen` | `clearcut` | `year=2067` | +15 | 2082 | 0 | 1 |
| 4 | `hardwood_clearcut_regen` | `clearcut` | `year=2072` | +5 | 2077 | 0 | 1 |
| 5 | `hardwood_clearcut_regen` | `clearcut` | `year=2072` | +10 | 2082 | 0 | 1 |
| 6 | `hardwood_clearcut_regen` | `clearcut` | `year=2072` | +15 | 2087 | 0 | 1 |

The rule is arithmetic: a combination collapses iff `resolved_year + offset > 2072`. The
three resolved clearcut years at risk are 2062, 2067 and 2072, and the six cells above are
every `(year, offset)` pair among them that crosses the horizon.

Note (discrepancy): the artifact README's correction #2 says the collapsed rows "now report 1
or 3 dropped entries rather than 0". The committed CSV reports **1** for all six. No row in
the file reports 3.

**Three further rows dropped an entry without collapsing** — multi-entry prescriptions that
lost a later comb tooth and kept the rest:

| Base prescription | Offset | Kept | Dropped | Surviving entry years |
|---|---:|---:|---:|---|
| `family_uneven_aged_selection` | +10 | 2 | 1 | 2047; 2062 |
| `family_uneven_aged_selection` | +15 | 2 | 1 | 2052; 2067 |
| `public_selection_light` | +15 | 3 | 1 | 2047; 2057; 2067 |

So 9 of 112 rows lose something and 6 of those lose everything. The other 103 rows shift
cleanly.

**What that costs in FVS runs.** A row is a specification, not a run; each collapsed
specification removes one run per donor plot that resolved to it. Reconstructing the plot
counts from the library (`trajectory_index.csv` gives 335 / 329 / 317 / 294
`hardwood_clearcut_regen` runs at +0 / +5 / +10 / +15, where +0 includes the one SIGFPE
failure excluded from the index):

| Resolved clearcut year | Donor plots | Lost at +5 | at +10 | at +15 |
|---|---:|---:|---:|---:|
| 2072 | 6 | 6 | 6 | 6 |
| 2067 | 12 | — | 12 | 12 |
| 2062 | 23 | — | — | 23 |
| **Total lost** | | **6** | **18** | **41** |

6 + 18 + 41 = **65 lost runs**, which is exactly the 65 the module docstring states
(`make_timing_library.py:130`) and which reconciles 3 × 3,106 = 9,318 down to the measured
3,100 + 3,088 + 3,065 = 9,253.

**Why only hardwood.** `hardwood_clearcut_regen` is the only prescription whose resolved
entry year reaches the end of the horizon. It has 10 distinct resolved specifications
(`year=2027, 2032, … 2072`), driven by stand age at inventory, and its 2062/2067/2072 cohorts
are the only entries within 15 years of 2072 that have nothing else in the schedule to fall
back on. `pine_plantation_long_rotation` has 9 specifications and
`pine_plantation_short_rotation` 5, but both use the `plantation_rotation` template — a thin
plus a clearcut — so losing the clearcut still leaves the thin and the variant survives.

**What did not collapse, structurally.** `no_management` takes no offsets at all: it has no
entries, so `build_thins` returns nothing and `expand_library` passes it through unexpanded.
All 6,602 riparian stands therefore still carry a library of exactly `{no_management}` after
the other menus quadrupled. `library_expansion.csv` has no `no_management` row, and
`trajectory_index.csv` has 676 `no_management` runs, all at offset 0.

---

## 3. The cycle-1 evidence

The claim is that an offsets-only grid can delay an entry but never advance one, so it cannot
raise cycle 1's ceiling. The library proves it directly.

**(a) No variant at any non-zero offset harvests in cycle 1.** Counting runs in
`trajectory_index.csv` with `cuft_cycle_1 > 0`, broken down by `offset_years`:

| Cycle (year) | +0 | +5 | +10 | +15 | Total runs cutting |
|---|---:|---:|---:|---:|---:|
| 0 (2022) | 0 | 0 | 0 | 0 | 0 |
| **1 (2027)** | **1,113** | **0** | **0** | **0** | **1,113** |
| 2 (2032) | 1,190 | 1,122 | 0 | 0 | 2,312 |
| 3 (2037) | 736 | 1,192 | 1,126 | 0 | 3,054 |

Offsets +5, +10 and +15 contribute **exactly zero** runs to cycle 1. The totals match
`library_reach_by_cycle.csv` row by row (1,113 / 2,312 / 3,054). The general form is visible
in the table: offset *o* contributes nothing to any cycle earlier than *o*/5 + 1, because
every offset is a whole number of 5-year cycles and every entry moves forward by it.

**(b) The earliest entry year in the library, per offset.** From `library_expansion.csv`,
taking the minimum entry year over all specifications at each offset:

| Offset | +0 | +5 | +10 | +15 |
|---|---:|---:|---:|---:|
| Earliest entry year | **2027** | 2032 | 2037 | 2042 |

Cycle 1 is 2027. Only offset 0 reaches it. This is a property of the grid `OFFSETS = (0, 5,
10, 15)` (`make_timing_library.py:134`): all four are non-negative, so the offset-0 schedule
is the earliest the library can express, and cycle-1 supply is fixed at whatever the
deterministically resolved schedules already put there.

**(c) The cycle-1 ceiling is bit-identical to the pre-grid library.** The artifact's
`check_base_library` compares all 3,781 offset-0 runs against `2026-08-31/trajectory_index.csv`
and requires 3,775 of them to be identical (max absolute difference 9.1 × 10⁻¹³ ft³/ac, a CSV
round-trip) with the other 6 differing *only* by a cycle-10 removal. No cycle-1 value moved.
Since offsets > 0 add nothing to cycle 1, the cycle-1 ceiling under the expanded library is
the same number it was under the unexpanded one.

**(d) The consequence in the envelope.** `attainable_envelope.csv` leaves exactly three of 80
targets outside the relaxed envelope, and two are in cycle 1:

| Dimension | Key | Cycle | Max attainable ft³ | Target ft³ | Max as % of target |
|---|---|---:|---:|---:|---:|
| county | Suwanee | 1 (2027) | 90,876,472 | 112,373,500 | 80.87% |
| owner_group | Other public | 1 (2027) | 13,815,426 | 22,434,500 | 61.58% |
| county | Suwanee | 10 (2072) | 98,589,123 | 112,373,500 | 87.73% |

`comparison_to_20260831.csv` shows the count of cycle-1 unreachable targets going 2 → 2, the
only cycle whose count did not fall. Cycles 2 through 9 went 4, 3, 3, 8, 1, 7, 6, 5 → 0 and
cycle 10 went 8 → 1. The grid cleared 44 of 47 unreachable targets and left untouched exactly
the two it structurally cannot reach.

For completeness, full cycle-1 envelope rows (all 8 keys):

| Dimension | Key | Max attainable | Target | Within? |
|---|---|---:|---:|---|
| county | Baker | 236,285,178 | 57,256,000 | yes (412.7%) |
| county | Columbia | 162,120,213 | 98,627,500 | yes (164.4%) |
| county | Hamilton | 132,334,393 | 81,057,500 | yes (163.3%) |
| county | Suwanee | 90,876,472 | 112,373,500 | **no (80.9%)** |
| county | Union | 63,744,831 | 38,214,500 | yes (166.8%) |
| owner_group | Federal (NF) | 34,309,634 | 10,117,500 | yes (339.1%) |
| owner_group | Other public | 13,815,426 | 22,434,500 | **no (61.6%)** |
| owner_group | Private | 637,236,028 | 354,974,000 | yes (179.5%) |

The envelope's minimum is 0 for every cycle-1 key, because `no_management` is on every upland
menu; the binding side is always the maximum.

**Separate fact, often conflated with this one:** cycle 10 (2072) was empty in `2026-08-31`
for a different reason, and no offset could have fixed that either. The 2026-08-17 library
*did* schedule 2072 entries (`hardwood_clearcut_regen` resolves to `year=2072` for 6 donor
plots), but FVS does not execute an activity scheduled in the final year of a projection.
`terminal_cycle_probe.csv`, on PLT_CN 718603850290487:

| Clearcut scheduled in | Projection | `RMCuFt` reported |
|---|---|---:|
| 2067 | 10 cycles (2022→2072) | 5,061.69 |
| **2072** | **10 cycles (2022→2072)** | **0.00** |
| 2072 | 11 cycles (2022→2077) | 5,274.74 |

The fix is the eleventh carrier cycle (`NUM_CYCLE = N_OBJECTIVE_CYCLES + 1 = 11`,
`make_timing_library.py:118-120`), not timing. The probe's two non-interference cases confirm
the 2022–2072 rows of a ten- and an eleven-cycle run are identical.

---

## 4. Run cost as an equation

### The unit of cost

One FVS run = one keyfile = one `(donor plot, prescription variant)` pair. Verified three
ways:

- `render_batch` groups the expanded library by `["PLT_CN", "prescription"]` and writes one
  keyfile per group (`make_timing_library.py:560-565`, filename `S{PLT_CN}__{prescription}.key`).
- The working domain model (`CONTEXT.md`, uncommitted on `diag/statewide-repair-blockers` at
  the time of writing) defines a trajectory as "what FVS produces for one prescription at one
  offset on one donor plot".
- Measured: `trajectory_index.csv` has 13,034 rows over 676 distinct `PLT_CN`, and the
  maximum number of rows for any `(PLT_CN, prescription)` pair is 1.

**Site-index bins have multiplicity 1 in this library.** `config/scenarios.yaml`'s `run_cost`
block states the library is keyed on `unique(donor plot × regime × site-index bin)`, but in
this build site index is a per-plot attribute, not an axis: `render_batch` does
`plot_sdi = sdi.get(str(run.PLT_CN))` and injects it as `params["stand_sdi"]`, one table per
plot. The confirming count is `no_management`, which has exactly 676 runs for 676 plots — one
apiece. So the bin factor B = 1 today. It would only become a multiplier if one donor plot
were run on more than one site-index assumption.

### The equation

```
R  =  D                              (one no_management run per donor plot; it takes no offsets)
   +  Σ_{p ∈ P_cut} |A_p| × |O|      (each eligible plot × cutting prescription × offset)
   −  C                              (plot-variants whose entries all fall past 2072)

     with B = 1 site-index bins per plot folded into D.
```

where `D` = donor plots, `A_p` = donor plots eligible for cutting prescription `p`, `O` = the
offset grid, `C` = collapsed plot-variants.

Compact form, writing `K = Σ|A_p| / D` for the mean number of cutting prescriptions a donor
plot is eligible for:

```
R  ≈  D × (1 + K × |O| × B)  −  C
```

### Checked against the two measured counts

Plot × cutting-prescription pairs at offset 0, from `trajectory_index.csv` (+1 for the SIGFPE
run excluded from the index):

| Cutting prescription | Eligible donor plots `\|A_p\|` |
|---|---:|
| `family_light_thin` | 627 |
| `family_uneven_aged_selection` | 627 |
| `hardwood_clearcut_regen` | 335 |
| `pine_plantation_long_rotation` | 290 |
| `pine_plantation_short_rotation` | 271 |
| `public_selection_light` | 478 |
| `public_thin_restore` | 478 |
| **Σ\|A_p\|** | **3,106** |

D = 676, so K = 3,106 / 676 = **4.594**.

**Base library, `O = {0}`:**

```
R₀ = 676 + 3,106 × 1 − 0 = 3,782 attempted
   = 3,781 completed after the one SIGFPE exclusion   ✓ matches the reported 3,781
```

**Expanded library, `O = {0, 5, 10, 15}`:**

```
R  = 676 + 3,106 × 4 − 65
   = 676 + 12,424 − 65
   = 13,035 attempted
   = 13,034 completed after the same SIGFPE exclusion  ✓ matches the reported 13,035 / 13,034
```

Per-offset breakdown, also matching: 3,106 at +0, 3,100 at +5, 3,088 at +10, 3,065 at +15
(that is 3,106 minus the 6 / 18 / 41 collapses of §2), plus 676 `no_management`.

The excluded run is `473803917489998 / hardwood_clearcut_regen@+0`, SIGFPE in `varmrt.f:176`
(`ADJUST = TEMKIL/TEMSUM`). Only the `@+0` variant dies; the other three offsets on the same
plot complete.

**Marginal cost of one more offset:** `ΔR = Σ|A_p| − (collapses at that offset) ≈ 3,065`
today, consistent with the README's "a fifth offset would add roughly 3,000 more". The
collapse term grows with the offset, so the marginal cost is mildly decreasing: 3,100 / 3,088
/ 3,065 for +5 / +10 / +15.

### Wall-clock coefficient

13,035 runs in ~22 minutes on 4 cores (`make_timing_library.py:445`, README "Caveats"):

```
22 min × 4 cores = 88 core-min = 5,280 core-seconds
5,280 / 13,035 = 0.405 core-seconds per run
             = 1.125 × 10⁻⁴ core-hours per run
```

A run is a single donor plot over 11 cycles, so this coefficient is independent of landscape
size. FVS is embarrassingly parallel, so wall-clock = core-hours / cores.

### Statewide extrapolation

Substituting the compact form and holding K, |O| and B at their pilot values:

```
R_state ≈ D_state × (1 + 4.594 × 4 × 1) − C_state
        ≈ 19.376 × D_state − C_state
        ≈ 19.28 × D_state            (absorbing C at the pilot's 65/13,035 = 0.50% rate)

core-hours ≈ 19.28 × D_state × 1.125 × 10⁻⁴  ≈  D_state × 2.17 × 10⁻³
           ≈ 7.8 core-seconds per statewide donor plot
```

**`D_state` is not measured anywhere in this repository.** `config/scenarios.yaml` records
693 FIA plots for the five-county pilot (676 of which appear in the library), and
`notes/2026-09-14_statewide_repair_blockers.md` confirms the statewide parcel layer covers
all 67 Florida counties, but no statewide donor-plot count has been computed. The table below
is therefore parametric, not a prediction:

| `D_state` | Runs `R_state` | Core-hours | Wall-clock on 4 cores | on 64 cores |
|---:|---:|---:|---:|---:|
| 676 (pilot, measured) | 13,035 | 1.5 | 22 min | 1.4 min |
| 2,000 | ~38,600 | 4.3 | 1.1 h | 4.1 min |
| 5,000 | ~96,400 | 10.8 | 2.7 h | 10 min |
| 9,100 (naive 67/5 county scaling) | ~175,000 | 19.7 | 4.9 h | 18 min |
| 12,400 (naive forest-area scaling, see A6) | ~239,000 | 26.9 | 6.7 h | 25 min |
| 20,000 | ~386,000 | 43.4 | 10.8 h | 41 min |

#### Assumptions behind the extrapolation

**A1 — K = 4.594 cutting prescriptions per donor plot holds statewide.** K is set by the
owner-class → menu mapping in `config/management_regimes.yaml` crossed with the ownership
mix, both of which change outside north Florida. Hard bounds from the current menus: a donor
plot is eligible for at least 1 and at most 7 cutting prescriptions, so
`8 × D_state ≤ R_state ≤ 29 × D_state`. The table's 19.38 sits at 67% of that range.

**A2 — the grid stays at |O| = 4.** A fifth offset adds ≈ K × D_state ≈ 4.59 × D_state runs,
raising the multiplier from 19.38 to 23.97 (+24%).

**A3 — B = 1, no site-index binning.** If the `run_cost` block's site-index axis is ever
realised with `b` bins per plot, the multiplier becomes `1 + 4.594 × 4 × b` and cost is very
nearly linear in `b`.

**A4 — the 0.50% collapse rate carries over.** Only `hardwood_clearcut_regen` collapses, and
only for plots whose resolved clearcut year is 2062 or later. The rate is a function of the
statewide stand-age distribution against a fixed 2072 horizon, not a constant of the method.
It is a small correction either way (65 runs on 13,035).

**A5 — 0.405 core-seconds per run holds.** The coefficient was measured on four cores on one
machine on an 11-cycle SN projection of a single donor plot. It should be robust to landscape
size (a run never sees the landscape) but not to variant, cycle count, or tree-list size.
It excludes keyfile rendering, input-DB build and the FVS_Summary2 parse, and excludes the
scheduler entirely.

**A6 — `D_state` grows sub-linearly with area, so the last two table rows are upper bounds.**
TreeMap reuses donor plots, so the count of *distinct* donors saturates as area grows; a
county added next to the pilot will share many of its donors. The 9,100 row is the pilot's 676
scaled by 67/5 counties, which ignores both that Florida's counties differ in size and forest
cover and that the pilot counties are small. The 12,400 row is 676 scaled by roughly 18×, the
ratio of Florida's timberland acreage (~17 M ac, an external FIA figure not held in this
repo) to the pilot's 925,098 forested acres. Both ignore donor reuse and should be read as
ceilings. The honest statement is the coefficient, 19.3 runs per donor plot; `D_state` needs
a one-off count of distinct `TM_ID` over the repaired statewide raster.

**A7 — the scheduler is not in this equation.** Simulated annealing scales with stands
(11,831 in the pilot) and with options per stand, not with FVS runs, and it is the part that
is already showing strain: seed spread went from 0.088 to 3.48 (0.05% → 5.5% of the
objective) when the decision space grew 3.45×. A statewide run multiplies stands, not just
runs, and A7 says nothing about what that costs.

---

## Sources

All under `weekly-artifact/2026-09-14/` on `origin/claude/gifted-ritchie-l9lsyq` (PR #48):
`README.md`, `timing_offsets_chosen.csv`, `annealed_plan.csv` (11,831 rows),
`trajectory_index.csv` (13,034 rows), `library_expansion.csv` (112 rows),
`library_reach_by_cycle.csv`, `attainable_envelope.csv` (80 rows), `harvest_by_cycle.csv`,
`comparison_to_20260831.csv`, `prescription_mix.csv`, `options_per_stand.csv`,
`plan_by_dimension.csv`, `seed_spread.csv`, `solution_quality.json`,
`terminal_cycle_probe.csv`, `fvs_failures.csv`, `make_timing_library.py`.

From `main`: `config/scenarios.yaml` (`run_cost` block, lines 205–222),
`config/management_regimes.yaml`, `config/tpo_targets.yaml`.

From the `diag/statewide-repair-blockers` working tree:
`notes/2026-09-14_statewide_repair_blockers.md`, and `CONTEXT.md` (untracked at the time of
writing — quoted for its definition of "trajectory", not relied on for any number).
