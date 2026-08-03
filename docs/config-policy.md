# ARTEMIS config and policy directions

Three config files set the modelling policy that everything downstream of stand
delineation depends on. This document says what each decides, why it decides it that way,
and what is still an assumption rather than a measurement.

| File | Decides | Read by |
|---|---|---|
| [`config/ownership_policy.yaml`](../config/ownership_policy.yaml) | Who owns each unit, and which TPO volume budget it charges against | `pipeline/s3_management/owner_classes.py` |
| [`config/management_regimes.yaml`](../config/management_regimes.yaml) | The prescription library, and 2–3 eligible prescriptions per owner class | `pipeline/s3_management/regime_assignment.py` |
| [`config/fallback_treelists.yaml`](../config/fallback_treelists.yaml) | What a stand is initialized from when it has no tree list of its own | `pipeline/s4_fvs/fallback_treelists.py` |

They interlock: ownership picks the owner class, the owner class picks the prescription,
and a prescription that removes the stand names the fixed tree list that regenerates it.

```text
Harris ownership raster ─┐
                         ├─► owner class ─► default prescription ─► FVS keyfile
parcel DORUC / acreage ──┘        │              │
                                  │              └─► regen slot ─┐
                                  └─► eligible menu              ├─► fixed tree list
                                       (scheduler chooses)       │      (pinned PLT_CN)
TreeMap hole / no live trees ────────────────────────────────────┘
```

---

## 1. Ownership

### The finding that shapes the design

**The parcel layer has no ownership-class column.** The attributes carried through
`sketch_management_units.py` are `CNTYNAME, PARCELID, NPARNO, DORUC, PARUSEDESC, ACRES`.
The only ownership signal is `DORUC` — the Florida Department of Revenue *land use* code —
and a use code is not an owner. DOR_UC 82, "forest, parks, recreational areas", is applied
to federal, state, county, and municipal conservation land alike, so it can tell you a
parcel is public and nothing more.

That asymmetry sets the precedence:

1. **The Harris raster assigns the class.** It is 30 m, circa 2022, and co-registered with
   TreeMap 2022 — the two products were built to be used together. Area-majority over a
   unit's forested pixels, not the centroid: units are irregular and a centroid can land
   on a road or a hole.
2. **The parcel layer refines within private only.** The raster has one
   `corporate_forest` class that mixes industrial timberland with small corporate
   holdings; DORUC plus acreage is the only thing that can split them.
3. **Disagreements are flagged, never resolved.** A parcel that says government under a
   raster pixel that says private sets `owner_conflict`, and the raster class stands.
   Parcel and raster vintages differ; a conflict is information about that, not a
   correction to either.

### The eight classes

Seven owner classes plus `unknown`, mapped onto the three TPO owner groups that
`config/tpo_targets.yaml` budgets in:

| Owner class | Harris value | TPO group |
|---|---|---|
| `private_industrial` | 4 | Private |
| `private_corporate_other` | 4 (demoted by parcel evidence) | Private |
| `private_family` | 3 | Private |
| `tribal` | 5 | Private |
| `federal` | 6 | Federal (NF) |
| `state` | 7 | Other public |
| `local` | 8 | Other public |
| `unknown` | 0 | Private |

Corporate defaults to **industrial** and is demoted on evidence, rather than the reverse,
because the pilot's corporate forest class is dominated by managed pine plantation — so
defaulting to industrial misclassifies less area. That is a stated assumption; the audit
command below reports the demoted fraction so it can be checked.

Tribal maps to the Private TPO group to match FIA's owner grouping (OWNGRPCD 40).

### Before these numbers appear in a result

`ownership_policy.yaml` is `verified: false`. The DOR_UC table is transcribed from the
published FDOR land-use codes and has not been checked against the actual parcel layer,
because the data drive was not mounted when it was written. Run:

```bash
uv run python -m pipeline.s3_management.owner_classes --audit-parcels \
    --parcels data/raw/FL_5_Co_Parcels.gdb --layer FL_5_Co_Parcels
```

It prints every observed DORUC value with its `PARUSEDESC` text, parcel count, and
acreage, marks which config signal claims it, and lists any candidate owner-name column.
Codes with no signal are the ones to look at. Flip `verified: true` in the same commit
that records the result.

### The tunable that deserves a sensitivity test

`private_refinement.industrial_min_acres` (default 1000) is where "industrial" starts. It
is a policy knob, not a measured constant. Re-run the owner-class assignment at 500, 1000,
and 2500 acres and report the area that changes class — if the industrial share moves a
lot, the threshold is doing more work than the evidence supports.

---

## 2. Management regimes

### Library versus menu

The file separates two things that are easy to conflate:

- **`prescriptions`** — the library. Eight distinct prescriptions, each renderable through
  the verified `ThinDBH` templates in `pipeline/s4_fvs/regime_templates.py`. Library size is
  the cost driver: the FVS trajectory library is keyed by
  `(plot_id, prescription, site_index_bin)`, so 8 prescriptions × 3 SI bins × 693 pilot
  plots bounds the pilot at 16,632 runs. Every addition multiplies that, and the config
  carries the arithmetic so it cannot drift.
- **`owner_classes`** — the policy. Two or three eligible prescriptions per owner class,
  one declared default per forest-type branch. Prescriptions are **shared** across owner
  classes: `public_selection_light` is eligible for four classes and still costs one
  column in the library. That is how every owner gets a real menu without the library
  growing owner-by-owner.

The default is what gets assigned deterministically today. The eligible menu is what the
landscape scheduler will choose among once the trajectory library exists — declaring it
now means the library is generated for the right set from the start.

### The eight prescriptions

| Prescription | Template | Shape |
|---|---|---|
| `no_management` | `no_management` | Grow only |
| `pine_plantation_short_rotation` | `plantation_rotation` | Thin at 15, clearcut at 25 |
| `pine_plantation_long_rotation` | `plantation_rotation` | Thin at 18, clearcut at 35 |
| `hardwood_clearcut_regen` | `clearcut` | Stand-replacing at 50 |
| `family_light_thin` | `thin_from_below` | One entry |
| `family_uneven_aged_selection` | `selection_harvest` | 15% every 15 years |
| `public_selection_light` | `selection_harvest` | 20% every 10 years |
| `public_thin_restore` | `thin_from_below_repeated` | 30% below 10" every 15 years |

`no_management` is universally eligible — declining to harvest is always a legal choice —
and for riparian units it is the only one.

### The menus

| Owner class | Default (pine / hardwood) | Also eligible |
|---|---|---|
| `private_industrial` | short rotation / hardwood clearcut | long rotation |
| `private_corporate_other` | long rotation / light thin | uneven-aged selection |
| `private_family` | light thin | uneven-aged selection, long rotation |
| `tribal` | public selection | light thin |
| `federal` | public selection | restoration thin |
| `state` | restoration thin / public selection | long rotation |
| `local` | no management | public selection, restoration thin |
| `unknown` | light thin | *(minimal menu — see below)* |

`unknown` is deliberately narrower than the 2–3 every real owner class gets. It is missing
information, not an owner type: with no owner known, letting the scheduler place a rotation
clearcut there would be inventing behaviour to hit a volume target. The shortfall lands on
known private units instead, where it is attributable.

### Deliberate changes from the previous behaviour

The pre-config `regime_assignment.py` sent Federal, State, Tribal, and Local alike to
`selection_harvest`, and any corporate owner to plantation-or-clearcut. Under the config:

- **Federal and tribal** — unchanged (`public_selection_light` has exactly the old
  `selection_harvest` parameters).
- **State pine** — now `public_thin_restore`, not selection. State forests carry an active
  timber program.
- **Local** — now `no_management`. County and municipal forest is predominantly parks,
  watershed, and school land.
- **Corporate** — still industrial by default, but demotable by parcel evidence, which
  changes both the default prescription and the eligible menu for demoted units.

Everything else resolves to the same template and the same parameters as before, which
`tests/test_s4_regime_templates.py` pins.

### Scheduling: age-based where it matters

Prescriptions declare entries either by **stand age** or by **fixed offsets** from the
inventory year. Age-based is the default for rotations, because a fixed offset gets the
forestry wrong: a 22-year-old plantation on a 25-year rotation should be cut in 3 years,
not in 30. Entry years snap up to the next 5-year cycle and never land sooner than one
cycle out.

Without a stand age, a prescription falls back to its offsets — which is also what
reproduces the pre-config behaviour exactly, so `stand_age` is an improvement rather than a
requirement. An overmature stand whose thin would land at or after its rotation harvest
loses the thin and is simply harvested. Entries past `inventory_year + horizon_years` are
dropped, and a prescription with no entry left inside the horizon resolves to
`no_management` with a note, rather than emitting a keyfile entry FVS never reaches.

### Riparian is absolute

`overrides.riparian` fires before ownership and cannot be overturned: `SMZ_Pct >= 50` means
no entry of any kind, no buffer class exempted. It is assigned by geometry, so no
ownership or forest-type rule can reach it. Riparian units are still grown through FVS on
the same cycles and still reported as their own polygons — see
[`notes/methodology-directions.md`](../notes/methodology-directions.md) item 2.

---

## 3. Fixed fallback tree lists

### The two gaps

**Initialization (year 0).** The pixel is forest under the mask but TreeMap gives it
nothing usable: nodata under the mask, a TM_ID with no crosswalk row, or a donor plot with
no live tree records. Today `build_fvs_inputs.impute_nearest_runnable` hands such a unit
the nearest runnable unit's tree list at *any* distance and with no forest-type test — a
bottomland hardwood unit can inherit a pine plantation from 40 km away, and nothing in the
output records that it happened.

**Regeneration (mid-run).** The scheduler applies a stand-replacing entry and the stand is
empty; FVS will grow nothing for the rest of the horizon. Regeneration is **not** expressed
with the FVS `PLANT`/`NATREGEN` keywords — their field layouts are unverified in this
project. It is expressed by restarting the stand from a fixed tree list, which is the
mechanism these slots provide. External cut injection and restart are verified exact on
stand values ([`gate_cut_injection.txt`](../research/restart_fidelity/outputs/gate_cut_injection.txt)).

### Every fallback is a real FIA plot

A fallback tree list is never hand-written. Each slot is filled by **one real, unmodified
FIA tree list**, chosen by a deterministic rule and pinned by `PLT_CN` in
`config/fallback_treelists.lock.yaml`.

This is the same principle as
[`notes/methodology-directions.md`](../notes/methodology-directions.md) item 1 — "every FVS
run is initialized from a real, unmodified FIA tree list" — applied to the gap cases. A
synthetic list would break `TPA_UNADJ` expansion semantics, could not be checked against
FIA, and would put invented numbers into a reported result.

The selection rule is the **median live basal area** plot among the slot's candidates,
ties broken by ascending `PLT_CN` as a string. Reproducible from the FIA database alone; a
mean would be no plot at all, and a random draw would need its own seed. Pinning matters as
much as the rule: an unpinned "median plot" is recomputed whenever the FIA vintage changes,
which would silently move every fallback stand in the landscape.

### The six slots

Three regeneration, three establishment:

| Slot | Use | Candidate filter |
|---|---|---|
| `planted_pine_regen` | regeneration | pine group, planted, age ≤ 10 |
| `natural_pine_regen` | regeneration | pine group, natural, age ≤ 15 |
| `hardwood_regen` | regeneration | hardwood group, natural, age ≤ 15 |
| `upland_pine_established` | establishment | pine group, age 20–40 |
| `bottomland_hardwood_established` | establishment | oak/gum/cypress + elm/ash/cottonwood, age ≥ 25 |
| `mixed_pine_hardwood_established` | establishment | oak/pine group, age ≥ 20 — **the default** |

### The ladder

An initialization gap walks four rungs, first match wins:

1. **Nearest runnable unit of the same forest-type group, within 5 km.** A real
   neighbouring stand beats any regional median.
2. **Nearest runnable unit of any type, within 2 km.** Much tighter, because proximity is
   the only thing left justifying the donor.
3. **Fixed slot by forest type** — pine, bottomland hardwood, or the mixed default.
4. **Default slot**, when the forest type is unknown.

Upland hardwood routes to the mixed slot rather than getting its own; the pilot's upland
hardwood is largely oak/pine transitional, and a seventh slot would not earn its keep.

### Provenance is not optional

Every tree row carries a `TREE_SOURCE` — `FIA_WEIGHTED_DIRECT`, `IMPUTED_NEAREST`,
`FALLBACK_FIXED`, or `REGEN_FIXED` — and every summary reports area by source. A landscape
where 8% of the acres came from a fixed list is a different result from one where 0.3% did,
and that difference must never be invisible. The config states the three required
reporting cuts; an FVS result without them is not reportable.

### Resolving the slots

`fallback_treelists.yaml` ships `status: unresolved` and no lock file, so asking for a
fixed tree list raises. That is deliberate: substituting an arbitrary list for a missing
pin would be invisible downstream. To resolve (needs the FIA SQLite):

```bash
uv run python -m pipeline.s4_fvs.fallback_treelists --resolve
```

The resolver queries FIA `COND`/`PLOT` for accessible forest land on essentially
single-condition plots that have an FVS-ready tree list, across FL/GA/AL — TreeMap draws
donors across state lines. A slot resolving from fewer than 10 candidates fails rather than
resolving, because a "median plot" drawn from a handful of plots is one arbitrary plot
wearing a rule.

---

## Open decisions

These are stated in the configs and repeated here so they are visible in one place.

- **Hole prevalence is unmeasured.** How much area actually lands on ladder rungs 3 and 4
  has never been quantified for the pilot AOI. If it is material, the fixed lists become a
  headline methods caveat rather than an edge case. **Measure before the first reported
  managed run.**
- **`industrial_min_acres` is a knob, not a constant.** Sensitivity-test it.
- **Regeneration delays** (1 year planted pine, 3 years hardwood) are silvicultural
  judgement. LCMS post-harvest recovery slope could calibrate them.
- **Site index carry.** A fixed tree list brings its donor plot's site index. Whether the
  recipient should keep its own — probably yes, since site index and terrain are per-pixel
  products independent of TreeMap — needs deciding before resolver output is used.
- **Owner-name refinement is disabled.** No owner-name column is confirmed present in
  `FL_5_Co_Parcels`. The pattern list is written and unused until `--audit-parcels`
  confirms a field.
- **Non-NF federal land** charges against the "Federal (NF)" TPO cap, because that is the
  only federal group TPO reports. Small in the pilot; grows on expansion.
- **Prescribed fire is not modelled.** `public_thin_restore` is the mechanical thinning
  half of a thin-and-burn regime. The FVS fire keywords are unverified here and the FFE
  state does not survive a restart barrier
  ([`notes/restart-fidelity-findings.md`](../notes/restart-fidelity-findings.md)). The
  writeup must say so.
